"""
upload.py
=========
Handler para archivos recibidos por Telegram (documento/video/audio).

Flujo:
1. Crear UploadTask en QueueManager.
2. Adquirir lock por usuario (cola: una subida a la vez por user).
3. Mostrar "📄 Procesando archivo...".
4. Validar límites (MAX_FILE_MB configurable en runtime).
5. Mostrar "✅ Validando límites...".
6. Descargar archivo con barra de progreso (loop updater).
7. Subir a S3 por chunks en paralelo con retry por chunk (loop updater + botón cancelar).
8. Generar enlace directo y responder al usuario.
9. En caso de error crítico de chunk, mostrar mensaje detallado.
10. En caso de cancelación, mostrar mensaje de cancelación.
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
from modules.downloader import ensure_dir, download_telegram_file
from .basic import AUTHORIZED

# Cliente S3 singleton a nivel de proceso.
_S3 = S3Client(CONFIG.s3)


def _cancel_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """Botón inline para cancelar la subida actual."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancelar", callback_data=f"cancel:{task_id}")]
    ])


def register(app: Client) -> None:
    @app.on_message(
        (filters.document | filters.video | filters.audio) & AUTHORIZED
    )
    async def _media_handler(client: Client, message: Message):
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name or "unknown"

        # Si el usuario tiene ya una tarea en curso, encolar la nueva.
        active = QUEUE.get_active_user_tasks(user_id)
        if active:
            await message.reply_text(
                f"⏳ Ya tienes <b>{len(active)}</b> tarea(s) en curso. "
                "Esta se encolará detrás. Usa <code>/cola</code> para ver el estado.",
                parse_mode=ParseMode.HTML,
            )

        media = message.document or message.video or message.audio
        raw_name = getattr(media, "file_name", None) or "archivo.bin"
        filesize = int(media.file_size or 0)

        # Crear tarea en cola.
        task = QUEUE.new_task(
            user_id=user_id,
            filename=raw_name,
            file_size=filesize,
            source="telegram",
        )

        # Guardar username para que el handler lo use al subir.
        task.chat_id = message.chat.id
        task.status_msg_id = None
        # Stash username en el objeto task (campo extra).
        setattr(task, "_username", username)

        # Adquirir lock por usuario -> cola FIFO.
        user_lock = QUEUE.get_user_lock(user_id)
        async with user_lock:
            await _process_upload(client, message, task, raw_name, filesize, username)


async def _process_upload(
    client: Client,
    message: Message,
    task: UploadTask,
    raw_name: str,
    filesize: int,
    username: str,
) -> None:
    """Lógica completa de descarga + subida con cola y reintentos."""
    user_id = task.user_id
    label = raw_name  # sin escape: el mensaje es texto plano (no HTML)
    max_bytes = RUNTIME_SETTINGS.max_file_mb * 1024 * 1024
    chunk_size = RUNTIME_SETTINGS.chunk_size_mb * 1024 * 1024
    max_parallel = RUNTIME_SETTINGS.max_parallel
    max_retries = RUNTIME_SETTINGS.max_retries

    status_msg = await message.reply_text(
        f"📄 Procesando archivo: {label}"
    )
    task.status_msg_id = status_msg.id
    task.chat_id = message.chat.id

    # ---------- VALIDACIÓN DE LÍMITES ----------
    await status_msg.edit_text(
        f"✅ Validando límites...\n"
        f"📦 Tamaño detectado: {_human_size(filesize)} / "
        f"{_human_size(max_bytes)}"
    )

    if filesize > max_bytes:
        task.mark_failed(
            error=f"Archivo excede el límite de {RUNTIME_SETTINGS.max_file_mb} MB"
        )
        await status_msg.edit_text(
            f"❌ <b>Archivo demasiado grande</b>\n\n"
            f"📄 Archivo: <code>{html.escape(label)}</code>\n"
            f"📦 Tamaño: {_human_size(filesize)}\n"
            f"📏 Límite: {_human_size(max_bytes)}\n\n"
            f"Reduce el archivo o pide al owner subir el límite con /settings.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ---------- DESCARGA ----------
    task.status = TaskStatus.DOWNLOADING
    task.mark_started()

    download_dir = os.path.join(CONFIG.download_dir, str(user_id))
    ensure_dir(download_dir)
    local_path = os.path.join(download_dir, raw_name)

    state = ProgressState(total_size=filesize, num_parts=0)
    updater_task = asyncio.create_task(
        progress_updater_loop(
            status_msg=status_msg,
            task=task,
            state=state,
            title="Descargando",
            filename=label,
            icon="📥",
        )
    )

    try:
        await download_telegram_file(
            client=client,
            message=_find_referenced_message(message),
            local_path=local_path,
            status_msg=status_msg,
            task=task,
            state=state,
            filename_label=label,
        )
    except Exception as exc:
        updater_task.cancel()
        if task.is_cancelled:
            await show_cancelled_final(status_msg, task, state, "Descargando", label)
        else:
            task.mark_failed(error=str(exc))
            await status_msg.edit_text(
                f"❌ Error al descargar: {html.escape(str(exc))}",
                parse_mode=ParseMode.HTML,
            )
        _safe_remove(local_path)
        return
    finally:
        if not updater_task.done():
            updater_task.cancel()

    # Si fue cancelada durante la descarga.
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
        # Ejecutar subida chunked en un thread.
        loop = asyncio.get_event_loop()
        base_name, urls = await asyncio.to_thread(
            _S3.upload_chunked,
            local_path,
            raw_name,
            task,
            upload_state,
            chunk_size,
            max_parallel,
            max_retries,
            user_id,
            username,
        )

        task.mark_completed(base_name, urls)

        # Mensaje final: si hay 1 URL -> simple; si hay varias -> lista.
        if len(urls) == 1:
            urls_block = f"<code>{urls[0]}</code>"
        else:
            urls_block = "\n".join(
                f"  <b>{i+1:02d}.</b> <code>{u}</code>" for i, u in enumerate(urls)
            )

        await status_msg.edit_text(
            f"<b>✅ Archivo subido a toDus S3</b>\n\n"
            f"📄 <b>Nombre:</b> <code>{html.escape(label)}</code>\n"
            f"📦 <b>Tamaño:</b> <code>{_human_size(downloaded_size)}</code>\n"
            f"🧩 <b>Partes:</b> <code>{upload_state.num_parts}</code>\n"
            f"🔑 <b>Base S3:</b> <code>{html.escape(base_name)}</code>\n\n"
            f"<b>🔗 Enlace(s) de descarga directa:</b>\n{urls_block}",
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


def _find_referenced_message(message: Message) -> Message:
    """Si el usuario reenvió un archivo, el documento vive en message.forward_origin
    o directamente en message. Devuelve el mensaje a usar para message.download()."""
    return message


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
