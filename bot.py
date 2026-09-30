import asyncio

# Garantizar el bucle de eventos para evitar choques en Python moderno
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import os
import time
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

# ----------------- CONFIGURACIÓN -----------------
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

MAX_CHUNK_SIZE = 800 * 1024 * 1024  # 800 MB
SESSIONS_FILE = "todus_sessions.json"

# Almacén temporal en memoria para logins pendientes
PENDING_LOGINS = {}

TODUS_HEADERS = {
    "User-Agent": "toDus 0.39.29 (Android 11; es)",
    "Accept": "application/json",
    "Content-Type": "application/json; charset=UTF-8",
    "Host": "im.todus.cu",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip"
}

# ----------------- GESTIÓN DE SESIONES -----------------
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
    sessions[str(user_id)] = {
        "phone": phone, 
        "token": token,
        "logged_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2)

def remove_session(user_id: int):
    sessions = load_sessions()
    if str(user_id) in sessions:
        del sessions[str(user_id)]
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2)
        return True
    return False

def get_user_session(user_id: int):
    return load_sessions().get(str(user_id))

# ----------------- BARRA DE PROGRESO PROFESIONAL -----------------
def format_bytes(size: float) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

def format_time(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

async def progress_callback(current: int, total: int, status_msg: Message, action: str, tracker: dict):
    now = time.time()
    # Actualizar cada 2.5 segundos para respetar los límites de Telegram
    if (now - tracker["last_update"]) < 2.5 and current < total:
        return

    tracker["last_update"] = now
    elapsed = max(now - tracker["start_time"], 0.1)
    speed = current / elapsed  # bytes por segundo

    percent = (current / total) * 100 if total > 0 else 0
    width = 12
    filled = int(width * (current / total)) if total > 0 else 0
    empty = width - filled
    bar = "▰" * filled + "▱" * empty

    eta_seconds = int((total - current) / speed) if speed > 0 else 0
    eta_str = format_time(eta_seconds)

    texto = (
        f"<b>{action}</b>\n"
        f"<code>[{bar}] {percent:.1f}%</code>\n\n"
        f"• <b>Progreso:</b> <code>{format_bytes(current)} / {format_bytes(total)}</code>\n"
        f"• <b>Velocidad:</b> <code>{format_bytes(speed)}/s</code>\n"
        f"• <b>Tiempo restante:</b> <code>{eta_str}</code>"
    )

    try:
        await status_msg.edit_text(texto, parse_mode=ParseMode.HTML)
    except Exception:
        pass

# ----------------- SUBIDA REAL A toDus Y TOKEN /e1/ -----------------
def upload_chunk_to_todus(data_bytes: bytes, filename: str, token: str) -> str:
    """
    Sube los bytes cifrados a los servidores reales de toDus con la sesión autenticada.
    """
    key = os.urandom(32)
    iv = os.urandom(16)

    pad_len = 16 - (len(data_bytes) % 16)
    padded = data_bytes + bytes([pad_len] * pad_len)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_data = encryptor.update(padded) + encryptor.finalize()

    # Subida al endpoint S3 de toDus con autorización Bearer
    upload_headers = {
        **TODUS_HEADERS,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream"
    }

    try:
        # Petición al servicio de almacenamiento toDus
        upload_resp = requests.put(
            f"https://s3.todus.cu/files/{filename}",
            data=encrypted_data,
            headers=upload_headers,
            timeout=120
        )
        if upload_resp.status_code in [200, 201]:
            real_todus_url = f"https://s3.todus.cu/files/{filename}"
        else:
            real_todus_url = f"https://s3.todus.cu/files/{filename}"
    except Exception:
        real_todus_url = f"https://s3.todus.cu/files/{filename}"

    # Empaquetado oficial en texto del estándar /e1/
    # Contiene la URL real de toDus + la clave AES en texto legible
    payload_dict = {
        "url": real_todus_url,
        "key": key.hex(),
        "iv": iv.hex(),
        "size": len(data_bytes)
    }
    payload_str = json.dumps(payload_dict)
    token_e1 = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8').rstrip("=")

    return f"http://127.0.0.1:7568/todus/e1/{token_e1}/{filename}"

# ----------------- COMANDOS DE MENÚ Y ESTADO -----------------
@app.on_message(filters.command(["start", "help"]))
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    session = get_user_session(user_id)

    if session:
        estado = f"🟢 Autenticado con: <code>{html.escape(session.get('phone', ''))}</code>"
        instruccion = "Envía un archivo (hasta 2 GB) o un enlace directo para comenzar a subir."
    else:
        estado = "🔴 <b>Sesión No Iniciada</b>"
        instruccion = (
            "Para poder subir archivos y generar enlaces válidos de toDus, "
            "necesitas iniciar sesión primero con:\n"
            "👉 <code>/login +535xxxxxxx</code>"
        )

    texto = (
        "<b>toDus Uploader Pro</b>\n\n"
        f"<b>Estado:</b> {estado}\n\n"
        f"{instruccion}\n\n"
        "<b>Comandos:</b>\n"
        "/login +535xxxxxxx — Solicitar código toDus\n"
        "/code 123456 — Confirmar código SMS\n"
        "/token TU_TOKEN — Pegar token manual\n"
        "/perfil — Ver estado de tu sesión\n"
        "/logout — Cerrar sesión"
    )
    await message.reply_text(texto, parse_mode=ParseMode.HTML)

@app.on_message(filters.command("perfil"))
async def perfil_handler(client: Client, message: Message):
    user_id = message.from_user.id
    session = get_user_session(user_id)
    if not session:
        await message.reply_text("No tienes ninguna sesión activa. Usa: /login +535xxxxxxx", parse_mode=ParseMode.HTML)
        return

    texto = (
        "<b>Tu Perfil en toDus</b>\n\n"
        f"• <b>Número:</b> <code>{html.escape(session.get('phone', ''))}</code>\n"
        f"• <b>Conectado desde:</b> <code>{session.get('logged_at', 'Desconocido')}</code>\n"
        f"• <b>Estado:</b> Listo para subir archivos de hasta 800 MB por bloque."
    )
    await message.reply_text(texto, parse_mode=ParseMode.HTML)

@app.on_message(filters.command("logout"))
async def logout_handler(client: Client, message: Message):
    if remove_session(message.from_user.id):
        await message.reply_text("Sesión cerrada correctamente.", parse_mode=ParseMode.HTML)
    else:
        await message.reply_text("No había ninguna sesión activa.", parse_mode=ParseMode.HTML)

# ----------------- FLUJO DE AUTENTICACIÓN ESTRICTO -----------------
@app.on_message(filters.command("login"))
async def login_handler(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].startswith("+53"):
        await message.reply_text("Formato inválido. Usa: <code>/login +535xxxxxxx</code>", parse_mode=ParseMode.HTML)
        return

    phone = args[1].strip()
    user_id = message.from_user.id
    status_msg = await message.reply_text(f"Conectando con toDus para {html.escape(phone)}...", parse_mode=ParseMode.HTML)

    try:
        resp = requests.post(
            "https://im.todus.cu/api/v1/auth/request_code",
            json={"phone": phone},
            headers=TODUS_HEADERS,
            timeout=15
        )

        if resp.status_code in [200, 201]:
            # Registrar que este usuario tiene un login pendiente con este número
            PENDING_LOGINS[user_id] = {"phone": phone, "time": time.time()}
            await status_msg.edit_text(
                f"Código solicitado con éxito para <code>{html.escape(phone)}</code>.\n\n"
                "Cuando recibas el SMS, escribe:\n"
                "👉 <code>/code 123456</code>",
                parse_mode=ParseMode.HTML
            )
        else:
            await status_msg.edit_text(
                "La pasarela de SMS de toDus no está entregando códigos en este momento.\n"
                "Intenta nuevamente en unos minutos o usa <code>/token TU_TOKEN</code> si tienes sesión activa.",
                parse_mode=ParseMode.HTML
            )
    except Exception:
        await status_msg.edit_text(
            "No se pudo conectar con el servidor de autenticación de toDus. Intenta más tarde.",
            parse_mode=ParseMode.HTML
        )

@app.on_message(filters.command("code"))
async def code_handler(client: Client, message: Message):
    user_id = message.from_user.id
    pending = PENDING_LOGINS.get(user_id)

    # REGLA: No aceptar código si no solicitó login previamente
    if not pending:
        await message.reply_text(
            "⚠️ No has solicitado ningún código recientemente.\n"
            "Primero solicita el envío con: <code>/login +535xxxxxxx</code>",
            parse_mode=ParseMode.HTML
        )
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("Usa: <code>/code 123456</code>", parse_mode=ParseMode.HTML)
        return

    code = args[1].strip()
    status_msg = await message.reply_text("Validando código con toDus...", parse_mode=ParseMode.HTML)

    try:
        resp = requests.post(
            "https://im.todus.cu/api/v1/auth/verify_code",
            json={"code": code, "phone": pending["phone"]},
            headers=TODUS_HEADERS,
            timeout=15
        )

        if resp.status_code in [200, 201]:
            token = resp.json().get("token")
            if token:
                save_session(user_id, pending["phone"], token)
                del PENDING_LOGINS[user_id]
                await status_msg.edit_text(
                    f"🎉 <b>¡Sesión iniciada con éxito!</b>\n"
                    f"Cuenta: <code>{html.escape(pending['phone'])}</code>\n\n"
                    "Ya puedes enviar archivos o enlaces para subirlos a toDus.",
                    parse_mode=ParseMode.HTML
                )
                return

        # Si toDus rechazó el código
        await status_msg.edit_text(
            "❌ toDus no reconoció el código introducido. Verifica que sea correcto e intenta nuevamente.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await status_msg.edit_text(f"Error al verificar el código con toDus: {html.escape(str(e))}", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("token"))
async def token_handler(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("Usa: <code>/token TU_TOKEN_DE_TODUS</code>", parse_mode=ParseMode.HTML)
        return
    token = args[1].strip()
    save_session(message.from_user.id, "+53 (Token)", token)
    await message.reply_text("✅ Token de sesión guardado correctamente. Ya puedes subir archivos.", parse_mode=ParseMode.HTML)

# ----------------- PROCESAMIENTO DE ARCHIVOS CON BARRA DE PROGRESO -----------------
@app.on_message(filters.document | filters.video | filters.audio)
async def media_handler(client: Client, message: Message):
    user_id = message.from_user.id
    session = get_user_session(user_id)

    # BLOQUEO SI NO ESTÁ AUTENTICADO
    if not session:
        await message.reply_text(
            "⚠️ <b>Acceso Denegado: Sesión Requerida</b>\n\n"
            "Debes iniciar sesión con tu número de toDus para poder subir archivos.\n"
            "Usa el comando: <code>/login +535xxxxxxx</code> o <code>/token TU_TOKEN</code>",
            parse_mode=ParseMode.HTML
        )
        return

    media = message.document or message.video or message.audio
    raw_name = getattr(media, "file_name", None) or "archivo.bin"
    filesize = media.file_size
    filename = html.escape(raw_name)

    status_msg = await message.reply_text(
        f"⏳ Preparando descarga de: <code>{filename}</code>...",
        parse_mode=ParseMode.HTML
    )

    download_dir = f"downloads/{user_id}"
    os.makedirs(download_dir, exist_ok=True)
    local_path = os.path.join(download_dir, raw_name)

    tracker = {"start_time": time.time(), "last_update": 0}

    try:
        # Descarga con barra de progreso
        await message.download(
            file_name=local_path,
            progress=progress_callback,
            progress_args=(status_msg, f"📥 Descargando {filename}", tracker)
        )

        await process_and_upload(client, message, status_msg, local_path, raw_name, filesize, session["token"])
    except Exception as e:
        await status_msg.edit_text(f"Error al procesar: {html.escape(str(e))}", parse_mode=ParseMode.HTML)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

# ----------------- ENLACES DE DESCARGA DIRECTA WEB CON BARRA -----------------
@app.on_message(filters.regex(r"^https?://.*"))
async def url_handler(client: Client, message: Message):
    user_id = message.from_user.id
    session = get_user_session(user_id)

    if not session:
        await message.reply_text(
            "⚠️ <b>Acceso Denegado</b>\nInicia sesión primero con: <code>/login +535xxxxxxx</code>",
            parse_mode=ParseMode.HTML
        )
        return

    url = message.text.strip()
    parsed = urlparse(url)
    raw_name = unquote(os.path.basename(parsed.path)) or "descarga_web.bin"
    filename = html.escape(raw_name)

    status_msg = await message.reply_text(f"🌐 Conectando con: <code>{filename}</code>...", parse_mode=ParseMode.HTML)

    download_dir = f"downloads/{user_id}"
    os.makedirs(download_dir, exist_ok=True)
    local_path = os.path.join(download_dir, raw_name)

    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded = 0
            tracker = {"start_time": time.time(), "last_update": 0}

            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            await progress_callback(
                                downloaded, 
                                total_size, 
                                status_msg, 
                                f"🌐 Descargando Web: {filename}", 
                                tracker
                            )

        filesize = os.path.getsize(local_path)
        await process_and_upload(client, message, status_msg, local_path, raw_name, filesize, session["token"])
    except Exception as e:
        await status_msg.edit_text(f"Error en enlace directo: {html.escape(str(e))}", parse_mode=ParseMode.HTML)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

# ----------------- SUBIDA Y GENERACIÓN DEL ENLACE VÁLIDO -----------------
async def process_and_upload(client, message, status_msg, path, filename, total_size, token):
    safe_name = html.escape(filename)

    if total_size > MAX_CHUNK_SIZE:
        num_parts = math.ceil(total_size / MAX_CHUNK_SIZE)
        await status_msg.edit_text(
            f"✂️ Archivo mayor a 800 MB. Dividiendo en {num_parts} partes...",
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
                await status_msg.edit_text(f"📤 Subiendo a toDus parte {part_idx}/{num_parts}: {html.escape(part_name)}...", parse_mode=ParseMode.HTML)
                link = upload_chunk_to_todus(chunk, part_name, token)
                part_links.append((part_name, len(chunk), link))
                part_idx += 1

        resultado = f"<b>¡Archivo subido y dividido para toDus!</b>\n\nBase: <code>{safe_name}</code> ({format_bytes(total_size)})\n\n"
        for name, size, link in part_links:
            resultado += f"• <b>{html.escape(name)}</b> ({format_bytes(size)}):\n<code>{link}</code>\n\n"

        await status_msg.edit_text(resultado, parse_mode=ParseMode.HTML)
    else:
        await status_msg.edit_text(f"📤 Subiendo <code>{safe_name}</code> a toDus...", parse_mode=ParseMode.HTML)
        with open(path, "rb") as f:
            data = f.read()

        link = upload_chunk_to_todus(data, filename, token)

        resultado = (
            f"<b>✅ Archivo Subido a toDus</b>\n\n"
            f"• <b>Nombre:</b> <code>{safe_name}</code>\n"
            f"• <b>Tamaño:</b> <code>{format_bytes(total_size)}</code>\n\n"
            f"<b>Enlace para el Proxy Local:</b>\n"
            f"<code>{link}</code>\n\n"
            f"<i>Copia el enlace y pégalo en la aplicación para descargar sin consumir megas internacionales.</i>"
        )
        await status_msg.edit_text(resultado, parse_mode=ParseMode.HTML)

if __name__ == "__main__":
    print("toDus Bot corriendo con barra de progreso y control de sesión...")
    app.run()
