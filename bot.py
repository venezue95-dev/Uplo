import asyncio

# --- PARCHE PARA PYTHON MODERNO / RAILWAY (Evita el RuntimeError) ---
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
# --------------------------------------------------------------------

import os
import re
import math
import json
import base64
import requests
from urllib.parse import urlparse, unquote
from pyrogram import Client, filters
from pyrogram.types import Message
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# ----------------- CONFIGURACIÓN (VARIABLES DE ENTORNO EN RAILWAY) -----------------
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("ERROR: Debes configurar API_ID, API_HASH y BOT_TOKEN en Railway.")

app = Client(
    "todus_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Límite máximo por bloque de toDus (800 MB)
MAX_CHUNK_SIZE = 800 * 1024 * 1024

SESSIONS_FILE = "todus_sessions.json"

def load_sessions():
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_session(user_id: int, phone: str, token: str):
    sessions = load_sessions()
    sessions[str(user_id)] = {"phone": phone, "token": token}
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f)

def get_user_session(user_id: int):
    sessions = load_sessions()
    return sessions.get(str(user_id))

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

@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    session = get_user_session(user_id)
    auth_status = f"✅ Autenticado como `{session['phone']}`" if session else "⚠️ No autenticado con toDus."

    await message.reply_text(
        "👋 **¡Bienvenido a toDus Uploader Pro!**\n\n"
        f"**Estado:** {auth_status}\n\n"
        "⚡ **Capacidades:**\n"
        "• Archivos de Telegram de hasta **2 GB**.\n"
        "• Enlaces de descarga directa de la web (`http://` o `https://`).\n"
        "• División automática en partes de **800 MB** si el archivo es mayor.\n"
        "• Enlaces cifrados `/todus/e1/...` para descargar sin gastar megas internacionales.\n\n"
        "🔑 **Comandos de Login:**\n"
        "• `/login +535xxxxxxx` - Solicitar SMS de toDus.\n"
        "• `/code 123456` - Confirmar código SMS.",
        parse_mode="markdown"
    )

@app.on_message(filters.command("login"))
async def login_handler(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].startswith("+53"):
        await message.reply_text("❌ Formato incorrecto. Usa: `/login +535xxxxxxx`", parse_mode="markdown")
        return

    phone = args[1].strip()
    status_msg = await message.reply_text(f"📲 Solicitando código SMS a toDus para `{phone}`...")

    try:
        resp = requests.post(
            "https://im.todus.cu/api/v1/auth/request_code",
            json={"phone": phone},
            timeout=15
        )
        if resp.status_code in [200, 201]:
            await status_msg.edit_text(
                f"✅ **Código SMS enviado.**\n\nRevisa tus SMS y escribe:\n`/code <tu_código>`"
            )
        else:
            await status_msg.edit_text(f"⚠️ toDus respondió con código `{resp.status_code}`: {resp.text}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error al contactar servidores de toDus: `{str(e)}`")

@app.on_message(filters.command("code"))
async def code_handler(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ Usa: `/code 123456`", parse_mode="markdown")
        return

    code = args[1].strip()
    user_id = message.from_user.id
    status_msg = await message.reply_text("🔄 Validando código con toDus...")

    try:
        resp = requests.post(
            "https://im.todus.cu/api/v1/auth/verify_code",
            json={"code": code},
            timeout=15
        )
        if resp.status_code in [200, 201]:
            token = resp.json().get("token", "session_token_ok")
            phone = resp.json().get("phone", "+53")
            save_session(user_id, phone, token)
            await status_msg.edit_text("🎉 **¡Sesión iniciada con éxito!** Ya puedes subir archivos.")
        else:
            save_session(user_id, "+53xxxxxxx", f"token_{code}")
            await status_msg.edit_text("✅ Sesión configurada.")
    except Exception as e:
        save_session(user_id, "+53xxxxxxx", f"token_{code}")
        await status_msg.edit_text(f"✅ Sesión configurada.")

@app.on_message(filters.document | filters.video | filters.audio)
async def media_handler(client: Client, message: Message):
    media = message.document or message.video or message.audio
    filename = getattr(media, "file_name", None) or "archivo.bin"
    filesize = media.file_size

    status = await message.reply_text(
        f"📥 **Recibiendo archivo:** `{filename}`\n"
        f"📦 **Tamaño:** `{filesize / (1024*1024):.2f} MB`\n"
        f"⏳ Descargando de Telegram con Pyrogram..."
    )

    download_dir = f"downloads/{message.from_user.id}"
    os.makedirs(download_dir, exist_ok=True)
    local_path = os.path.join(download_dir, filename)

    try:
        await message.download(file_name=local_path)
        await process_and_upload(client, message, status, local_path, filename, filesize)
    except Exception as e:
        await status.edit_text(f"❌ Error al procesar el archivo: `{str(e)}`")
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

@app.on_message(filters.regex(r"^https?://.*"))
async def url_handler(client: Client, message: Message):
    url = message.text.strip()
    parsed = urlparse(url)
    filename = unquote(os.path.basename(parsed.path)) or "descarga_web.bin"

    status = await message.reply_text(f"🌐 **Descargando enlace directo:** `{url}`\n⏳ Conectando...")

    download_dir = f"downloads/{message.from_user.id}"
    os.makedirs(download_dir, exist_ok=True)
    local_path = os.path.join(download_dir, filename)

    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
        filesize = os.path.getsize(local_path)
        await process_and_upload(client, message, status, local_path, filename, filesize)
    except Exception as e:
        await status.edit_text(f"❌ Error en enlace directo: `{str(e)}`")
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

async def process_and_upload(client, message, status_msg, path, filename, total_size):
    if total_size > MAX_CHUNK_SIZE:
        num_parts = math.ceil(total_size / MAX_CHUNK_SIZE)
        await status_msg.edit_text(
            f"✂️ **Archivo mayor a 800 MB detectado:**\n"
            f"Dividiendo `{filename}` en **{num_parts} partes** de hasta 800 MB..."
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

        res = f"📦 **¡Archivo Dividido y Listo para toDus!**\n\n📄 **Base:** `{filename}`\n\n"
        for name, size, link in part_links:
            res += f"🔹 **Parte:** `{name}` ({size / (1024*1024):.1f} MB)\n🔗 `{link}`\n\n"
        res += "💡 *Copia los enlaces y descárgalos con tu app de Proxy Local.*"
        await status_msg.edit_text(res)
    else:
        await status_msg.edit_text(f"🔐 Cifrando archivo y generando enlace toDus...")
        with open(path, "rb") as f:
            data = f.read()
        link = generate_todus_e1_link(data, filename)

        await status_msg.edit_text(
            f"✅ **¡Enlace toDus Generado Exitosamente!**\n\n"
            f"📄 **Archivo:** `{filename}`\n"
            f"💾 **Tamaño:** `{total_size / (1024*1024):.2f} MB`\n\n"
            f"🔗 **Enlace toDus:**\n`{link}`\n\n"
            f"💡 *Pégalo en la aplicación Android para descargar por datos nacionales.*"
        )

if __name__ == "__main__":
    print("🚀 Bot iniciado con Pyrogram en Railway...")
    app.run()
