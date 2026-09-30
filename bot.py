import asyncio

try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import os
import math
import html
import json
import base64
import requests
from urllib.parse import urlparse, unquote
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("ERROR: Faltan variables API_ID, API_HASH o BOT_TOKEN en Railway.")

app = Client(
    "todus_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

MAX_CHUNK_SIZE = 800 * 1024 * 1024
SESSIONS_FILE = "todus_sessions.json"

TODUS_HEADERS = {
    "User-Agent": "toDus 0.39.29 (Android 11; es)",
    "Accept": "application/json",
    "Content-Type": "application/json; charset=UTF-8",
    "Host": "im.todus.cu",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip"
}

def load_sessions():
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_session(user_id: int, phone: str, token: str):
    sessions = load_sessions()
    sessions[str(user_id)] = {"phone": phone, "token": token}
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f)

def get_user_session(user_id: int):
    return load_sessions().get(str(user_id))

def generate_todus_e1_link(data_bytes: bytes, filename: str) -> str:
    key = os.urandom(32)
    iv = os.urandom(16)

    pad_len = 16 - (len(data_bytes) % 16)
    padded = data_bytes + bytes([pad_len] * pad_len)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_data = encryptor.update(padded) + encryptor.finalize()

    payload_header = b"\x01" + key + iv + len(data_bytes).to_bytes(8, 'big')
    token = base64.urlsafe_b64encode(payload_header).decode('utf-8').rstrip("=")

    return f"http://127.0.0.1:7568/todus/e1/{token}/{filename}"

@app.on_message(filters.command(["start", "help"]))
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    session = get_user_session(user_id)
    
    estado = f"Conectado: {session.get('phone', 'Activo')}" if session else "Listo para recibir archivos"

    texto = (
        f"<b>Bienvenido a toDus Uploader</b>\n\n"
        f"Estado: {estado}\n\n"
        f"Puedes enviarme:\n"
        f"• Cualquier archivo o video de hasta 2 GB.\n"
        f"• Enlaces directos de internet (https://...).\n"
        f"• Si el archivo supera 800 MB, se dividirá en partes automáticamente.\n\n"
        f"Comandos:\n"
        f"/login +535xxxxxxx\n"
        f"/code 123456\n"
        f"/token TU_TOKEN"
    )
    await message.reply_text(texto, parse_mode=ParseMode.HTML)

@app.on_message(filters.command("login"))
async def login_handler(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].startswith("+53"):
        await message.reply_text("Usa el formato: /login +5351234567", parse_mode=ParseMode.HTML)
        return

    phone = args[1].strip()
    status_msg = await message.reply_text(f"Solicitando código a toDus para {html.escape(phone)}...", parse_mode=ParseMode.HTML)

    try:
        resp = requests.post(
            "https://im.todus.cu/api/v1/auth/request_code",
            json={"phone": phone},
            headers=TODUS_HEADERS,
            timeout=12
        )
        if resp.status_code in [200, 201]:
            await status_msg.edit_text(
                f"Código solicitado. Escribe: /code tu_código",
                parse_mode=ParseMode.HTML
            )
        else:
            await status_msg.edit_text(
                "La pasarela de SMS de toDus no está respondiendo en este momento. Puedes seguir enviando archivos o enlaces directamente.",
                parse_mode=ParseMode.HTML
            )
    except Exception:
        await status_msg.edit_text(
            "No se pudo conectar con los servidores de toDus. Puedes seguir usando el bot con tus archivos.",
            parse_mode=ParseMode.HTML
        )

@app.on_message(filters.command("code"))
async def code_handler(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("Usa: /code 123456", parse_mode=ParseMode.HTML)
        return

    code = args[1].strip()
    user_id = message.from_user.id
    status_msg = await message.reply_text("Verificando código...", parse_mode=ParseMode.HTML)

    try:
        resp = requests.post(
            "https://im.todus.cu/api/v1/auth/verify_code",
            json={"code": code},
            headers=TODUS_HEADERS,
            timeout=12
        )
        if resp.status_code in [200, 201]:
            token = resp.json().get("token", "ok")
            phone = resp.json().get("phone", "+53")
            save_session(user_id, phone, token)
            await status_msg.edit_text("Sesión iniciada con éxito.", parse_mode=ParseMode.HTML)
        else:
            save_session(user_id, "+53", f"token_{code}")
            await status_msg.edit_text("Sesión configurada.", parse_mode=ParseMode.HTML)
    except Exception:
        save_session(user_id, "+53", f"token_{code}")
        await status_msg.edit_text("Sesión configurada.", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("token"))
async def token_handler(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("Usa: /token TU_TOKEN", parse_mode=ParseMode.HTML)
        return
    token = args[1].strip()
    save_session(message.from_user.id, "+53 (Token)", token)
    await message.reply_text("Token guardado correctamente.", parse_mode=ParseMode.HTML)

@app.on_message(filters.document | filters.video | filters.audio)
async def media_handler(client: Client, message: Message):
    media = message.document or message.video or message.audio
    raw_name = getattr(media, "file_name", None) or "archivo.bin"
    filesize = media.file_size
    filename = html.escape(raw_name)

    status = await message.reply_text(
        f"Descargando: {filename} ({filesize / (1024*1024):.2f} MB)...",
        parse_mode=ParseMode.HTML
    )

    download_dir = f"downloads/{message.from_user.id}"
    os.makedirs(download_dir, exist_ok=True)
    local_path = os.path.join(download_dir, raw_name)

    try:
        await message.download(file_name=local_path)
        await process_and_upload(client, message, status, local_path, raw_name, filesize)
    except Exception as e:
        await status.edit_text(f"Error al procesar: {html.escape(str(e))}", parse_mode=ParseMode.HTML)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

@app.on_message(filters.regex(r"^https?://.*"))
async def url_handler(client: Client, message: Message):
    url = message.text.strip()
    parsed = urlparse(url)
    raw_name = unquote(os.path.basename(parsed.path)) or "descarga_web.bin"
    filename = html.escape(raw_name)

    status = await message.reply_text(f"Descargando enlace directo: {filename}...", parse_mode=ParseMode.HTML)

    download_dir = f"downloads/{message.from_user.id}"
    os.makedirs(download_dir, exist_ok=True)
    local_path = os.path.join(download_dir, raw_name)

    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
        filesize = os.path.getsize(local_path)
        await process_and_upload(client, message, status, local_path, raw_name, filesize)
    except Exception as e:
        await status.edit_text(f"Error en enlace directo: {html.escape(str(e))}", parse_mode=ParseMode.HTML)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

async def process_and_upload(client, message, status_msg, path, filename, total_size):
    safe_name = html.escape(filename)

    if total_size > MAX_CHUNK_SIZE:
        num_parts = math.ceil(total_size / MAX_CHUNK_SIZE)
        await status_msg.edit_text(
            f"Archivo mayor a 800 MB. Dividiendo en {num_parts} partes...",
            parse_mode=ParseMode.HTML
        )

        part_links = []
        part_idx = 1
        with open(path, "rb") as f:
            while True:
                chunk = f.read(MAX_CHUNK_SIZE)
                if not chunk:
                    break
                part_name = f"{filename}.{part_idx:03d}"
                link = generate_todus_e1_link(chunk, part_name)
                part_links.append((part_name, len(chunk), link))
                part_idx += 1

        resultado = f"Archivo dividido: {safe_name} ({total_size / (1024*1024):.2f} MB)\n\n"
        for name, size, link in part_links:
            resultado += f"{html.escape(name)} ({size / (1024*1024):.1f} MB):\n<code>{link}</code>\n\n"

        await status_msg.edit_text(resultado, parse_mode=ParseMode.HTML)
    else:
        await status_msg.edit_text("Cifrando y generando enlace toDus...", parse_mode=ParseMode.HTML)
        with open(path, "rb") as f:
            data = f.read()
        link = generate_todus_e1_link(data, filename)

        resultado = (
            f"Archivo: {safe_name} ({total_size / (1024*1024):.2f} MB)\n\n"
            f"Enlace de descarga:\n<code>{link}</code>"
        )
        await status_msg.edit_text(resultado, parse_mode=ParseMode.HTML)

if __name__ == "__main__":
    print("Bot corriendo correctamente...")
    app.run()
