"""
url.py
======
Handler para URLs HTTP(S) enviadas al bot.

Flujo (paralelo al de upload.py):
1. Crear UploadTask en QueueManager.
2. Mostrar "🌐 Procesando URL...".
3. Validar límites con HEAD request.
4. Descargar URL con barra de progreso (loop updater).
5. Subir a S3 por chunks en paralelo con retry (loop updater + botón cancelar).
6. Generar enlace directo y responder.
"""

from __future__ import annotations

import asyncio
import html
import os

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
)
from pyrogram.enums import ParseMode

from config import CONFIG
from modules.s3_client import S3Client, S3Error, ChunkUploadError, UploadCancelled
from modules.progress import ProgressState, build_error_message
from modules.queue import QUEUE, UploadTask, TaskStatus
from modules.runtime_settings import RUNTIME_SETTINGS
from modules.updater import progress_updater_loop, show_cancelled_final
from modules.downloader import (
    ensure_dir,
    download_from_url,
    url_to_filename,
    get_url_size,
)
from .basic import AUTHORIZED

_S3 = S3Client(CONFIG.s3)

URL_FILTER = filters.regex(r"^https?://")


def _cancel_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancelar", callback_data=f"cancel:{task_id}")]
    ])


def register(app: Client) -> None:
    @app.on_message(URL_FILTER & AUTHORIZED)
    async def _url_handler(client: Client, message: Message):
        user_id = message.from_user.id
        url = message.text.strip()

        active = QUEUE.get_active_user_tasks(user_id)
        if active:
            await message.reply_text(
                f"⏳ Ya tienes <b>{len(active)}</b> tarea(s) en curso. "
                "Esta se encolará detrás. Usa <code>/cola</code> para ver el estado.",
                parse_mode=ParseMode.HTML,
            )

        raw_name = url_to_filename(url)
        # Para URLs, no conocemos el tamaño hasta el HEAD.
        task = QUEUE.new_task(
            user_id=user_id,
            filename=raw_name,
            file_size=0,
            source="url",
            url=url,
        )

        user_lock = QUEUE.get_user_lock(user_id)
        async with user_lock:
            await _process_url(client, message, task, url, raw_name)


async def _process_url(
    client: Client,
    message: Message,
    task: UploadTask,
    url: str,
    raw_name: str,
) -> None:
    label = raw_name
    max_bytes = RUNTIME_SETTINGS.max_file_mb * 1024 * 1024
    chunk_size = RUNTIME_SETTINGS.chunk_size_mb * 1024 * 1024
    max_parallel = RUNTIME_SETTINGS.max_parallel
    max_retries = RUNTIME_SETTINGS.max_retries

    status_msg = await message.reply_text(
        f"🌐 Procesando URL...\n📄 {label}\n🔗 {url}"
    )
    task.status_msg_id = status_msg.id
    task.chat_id = message.chat.id

    # ---------- VALIDACIÓN DE LÍMITES ----------
    await status_msg.edit_text(
        f"✅ Validando límites...\n📄 {label}\n🔗 {url}"
    )

    remote_size = await asyncio.to_thread(get_url_size, url)
    task.file_size = remote_size

    if remote_size > max_bytes:
        task.mark_failed(
            error=f"Archivo excede el límite de {RUNTIME_SETTINGS.max_file_mb} MB"
        )
        await status_msg.edit_text(
            f"❌ <b>Archivo demasiado grande</b>\n\n"
            f"📄 URL: <code>{html.escape(url)}</code>\n"
            f"📦 Tamaño detectado: {_human_size(remote_size)}\n"
            f"📏 Límite: {_human_size(max_bytes)}",
            parse_mode=ParseMode.HTML,
        )
        return

    if remote_size > 0:
        await status_msg.edit_text(
            f"✅ Validación OK\n📄 {label}\n"
            f"📦 Tamaño detectado: {_human_size(remote_size)} / {_human_size(max_bytes)}\n"
            f"🔗 {url}"
        )

    # ---------- DESCARGA ----------
    task.status = TaskStatus.DOWNLOADING
    task.mark_started()

    download_dir = os.path.join(CONFIG.download_dir, str(task.user_id))
    ensure_dir(download_dir)
    local_path = os.path.join(download_dir, raw_name)

    state = ProgressState(total_size=remote_size, num_parts=0)

    loop = asyncio.get_event_loop()
    updater_task = asyncio.create_task(
        progress_updater_loop(
            status_msg=status_msg,
            task=task,
            state=state,
            title="Descargando",
            filename=label,
            icon="🌐",
        )
    )

    try:
        await asyncio.to_thread(
            download_from_url,
            url,
            local_path,
            task,
            state,
            loop,
        )
    except Exception as exc:
        updater_task.cancel()
        if task.is_cancelled:
            await show_cancelled_final(status_msg, task, state, "Descargando", label)
        else:
            task.mark_failed(error=str(exc))
            await status_msg.edit_text(
                f"❌ Error al descargar la URL: {html.escape(str(exc))}",
                parse_mode=ParseMode.HTML,
            )
        _safe_remove(local_path)
        return
    finally:
        if not updater_task.done():
            updater_task.cancel()

    if task.is_cancelled:
        await show_cancelled_final(status_msg, task, state, "Descargando", label)
        _safe_remove(local_path)
        return

    downloaded_size = os.path.getsize(local_path)

    # ---------- SUBIDA A S3 (CHUNKED) ----------
    task.status = TaskStatus.UPLOADING
    task.progress = 0.0

    upload_state = ProgressState(total_size=downloaded_size, num_parts=0)
    cancel_kb = _cancel_keyboard(task.task_id)

    upload_updater = asyncio.create_task(
        progress_updater_loop(
            status_msg=status_msg,
            task=task,
            state=upload_state,
            title="Subiendo",
            filename=label,
            icon="🔵",
            keyboard=cancel_kb,
        )
    )

    try:
        s3_key = await asyncio.to_thread(
            _S3.upload_chunked,
            local_path,
            raw_name,
            task,
            upload_state,
            chunk_size,
            max_parallel,
            max_retries,
        )

        s3_url = await asyncio.to_thread(_S3.get_download_url, s3_key)
        task.mark_completed(s3_key, s3_url)

        await status_msg.edit_text(
            f"<b>✅ Subida desde URL completada</b>\n\n"
            f"🔗 <b>Origen:</b> <code>{html.escape(url)}</code>\n"
            f"📄 <b>Nombre:</b> <code>{html.escape(label)}</code>\n"
            f"📦 <b>Tamaño:</b> <code>{_human_size(downloaded_size)}</code>\n"
            f"🧩 <b>Partes:</b> <code>{upload_state.num_parts}</code>\n"
            f"🔑 <b>Clave S3:</b> <code>{html.escape(s3_key)}</code>\n\n"
            f"<b>🔗 Enlace de descarga directa:</b>\n"
            f"<code>{s3_url}</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=None,
        )

    except UploadCancelled:
        await show_cancelled_final(status_msg, task, upload_state, "Subiendo", label)

    except ChunkUploadError as exc:
        task.mark_failed(
            error=exc.error,
            failed_part=exc.part_label,
            attempts=exc.attempts,
        )
        await status_msg.edit_text(
            build_error_message(
                filename=label,
                failed_part=exc.part_label,
                attempts=exc.attempts,
                max_attempts=exc.max_attempts,
                error=exc.error,
            ),
            reply_markup=None,
        )

    except S3Error as exc:
        task.mark_failed(error=str(exc))
        await status_msg.edit_text(
            f"❌ <b>Error de S3</b>\n\n{html.escape(str(exc))}",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )

    except Exception as exc:
        task.mark_failed(error=str(exc))
        await status_msg.edit_text(
            f"❌ <b>Error inesperado</b>\n\n{html.escape(str(exc))}",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )

    finally:
        if not upload_updater.done():
            upload_updater.cancel()
        _safe_remove(local_path)


def _human_size(num: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if num < 1024.0:
            if unit == 'B':
                return f"{int(num)} B"
            return f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} TB"


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
