"""
basic.py
========
Comandos: /start, /help, /ping, /status.

/status verifica tanto el bot de Telegram como la conectividad S3.
"""

from __future__ import annotations

import html
import time

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from config import CONFIG
from modules.s3_client import S3Client
from modules.runtime_settings import RUNTIME_SETTINGS


def is_authorized(user_id: int) -> bool:
    if not CONFIG.allowed_users:
        return True
    return user_id in CONFIG.allowed_users


def is_owner(user_id: int) -> bool:
    return CONFIG.owner_id is not None and user_id == CONFIG.owner_id


def _build_authorized_filter():
    async def _flt(_, __, message: Message) -> bool:
        if not message.from_user:
            return False
        return is_authorized(message.from_user.id)
    return filters.create(_flt)


AUTHORIZED = _build_authorized_filter()


def register(app: Client) -> None:
    s3_client = S3Client(CONFIG.s3)

    @app.on_message(filters.command(["start", "help"]))
    async def _start(client: Client, message: Message):
        user_id = message.from_user.id
        is_auth = is_authorized(user_id)
        is_own = is_owner(user_id)

        estado = (
            "🟢 <b>Bot público</b> (cualquiera puede usarlo)"
            if not CONFIG.allowed_users
            else (
                "🟢 <b>Usuario autorizado</b>" if is_auth
                else "🔴 <b>No autorizado</b>"
            )
        )
        if is_own:
            estado += " 👑 (Owner)"

        s = RUNTIME_SETTINGS
        s3_desc = (
            f"• <b>Bucket:</b> <code>{html.escape(CONFIG.s3.bucket)}</code>\n"
            f"• <b>Endpoint:</b> <code>{html.escape(CONFIG.s3.endpoint_url or 'AWS S3 estándar')}</code>\n"
            f"• <b>Región:</b> <code>{html.escape(CONFIG.s3.region)}</code>\n"
            f"• <b>Enlace:</b> "
            f"<code>{'URL pública' if CONFIG.s3.public_base_url else f'Presigned {CONFIG.s3.presign_expiry}s'}</code>\n"
            f"• <b>Máx archivo:</b> <code>{s.max_file_mb} MB</code>\n"
            f"• <b>Chunk:</b> <code>{s.chunk_size_mb} MB</code> · "
            f"<b>Paralelismo:</b> <code>{s.max_parallel}</code> · "
            f"<b>Reintentos:</b> <code>{s.max_retries}</code>"
        )

        texto = (
            "<b>S3 Uploader Bot</b>\n\n"
            f"<b>Estado:</b> {estado}\n\n"
            f"{s3_desc}\n\n"
            "<b>Cómo usar:</b>\n"
            "1️⃣ Reenvía un archivo (documento/video/audio) → se sube a S3 por chunks.\n"
            "2️⃣ Envía una URL directa → se descarga y se sube a S3.\n"
            "3️⃣ El bot responde con el enlace directo de descarga.\n\n"
            "<b>Comandos:</b>\n"
            "/start o /help — Esta ayuda\n"
            "/status — Verificar estado del bot y la conexión S3\n"
            "/cola — Ver procesos activos y recientes\n"
            "/settings — Configurar opciones (solo owner)\n"
            "/ping — Latencia del bot\n"
            f"<i>Tu ID: <code>{user_id}</code></i>"
        )
        await message.reply_text(texto, parse_mode=ParseMode.HTML)

    @app.on_message(filters.command("status") & AUTHORIZED)
    async def _status(client: Client, message: Message):
        status_msg = await message.reply_text("🔍 <b>Verificando estado del sistema...</b>", parse_mode=ParseMode.HTML)

        # 1) Telegram bot info.
        try:
            me = await client.get_me()
            tg_line = f"✅ <b>@{me.username}</b> (ID <code>{me.id}</code>)"
        except Exception as exc:
            tg_line = f"❌ No se pudo obtener info del bot: {html.escape(str(exc))}"

        # 2) S3 connectivity.
        ok, detail = await _run_sync(s3_client.ping)
        s3_status = "✅" if ok else "❌"

        # 3) Runtime settings.
        s = RUNTIME_SETTINGS

        # 4) Active tasks (global).
        from modules.queue import QUEUE
        all_tasks = list(QUEUE._tasks.values()) if hasattr(QUEUE, "_tasks") else []
        active = [t for t in all_tasks if t.status.value in ("queued", "downloading", "uploading")]

        text = (
            "<b>📊 Estado del Sistema</b>\n\n"
            f"<b>🤖 Telegram Bot</b>\n{tg_line}\n\n"
            f"<b>☁️ Almacenamiento S3</b>\n"
            f"   {s3_status} {html.escape(detail)}\n"
            f"   • Bucket: <code>{html.escape(CONFIG.s3.bucket)}</code>\n"
            f"   • Endpoint: <code>{html.escape(CONFIG.s3.endpoint_url or 'AWS S3 estándar')}</code>\n"
            f"   • Región: <code>{html.escape(CONFIG.s3.region)}</code>\n\n"
            f"<b>⚙️ Configuración runtime</b>\n"
            f"   • Máx archivo: <code>{s.max_file_mb} MB</code>\n"
            f"   • Chunk: <code>{s.chunk_size_mb} MB</code>\n"
            f"   • Paralelismo: <code>{s.max_parallel}</code> workers\n"
            f"   • Reintentos: <code>{s.max_retries}</code>\n\n"
            f"<b>📋 Cola activa</b>\n"
            f"   • Tareas en curso: <code>{len(active)}</code>"
        )
        await status_msg.edit_text(text, parse_mode=ParseMode.HTML)

    @app.on_message(filters.command("ping") & AUTHORIZED)
    async def _ping(client: Client, message: Message):
        start = time.perf_counter()
        msg = await message.reply_text("🏓 Pong...")
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        await msg.edit_text(
            f"🏓 <b>Pong!</b>\nLatencia bot: <code>{elapsed_ms} ms</code>",
            parse_mode=ParseMode.HTML,
        )


async def _run_sync(fn, *args, **kwargs):
    """Ejecuta una función síncrona en un thread."""
    import asyncio
    return await asyncio.to_thread(fn, *args, **kwargs)
