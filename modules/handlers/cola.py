"""
cola.py
=======
Comando /cola: lista los procesos activos y recientes del usuario.
"""

from __future__ import annotations

import html
import time

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from config import CONFIG
from modules.queue import QUEUE, TaskStatus, STATUS_ICON
from .basic import AUTHORIZED


def _format_task_line(task) -> str:
    icon = STATUS_ICON.get(task.status, "❓")
    name = html.escape(task.filename)
    line = f"{icon} <code>{name}</code>\n"
    line += f"   Estado: <b>{task.status.value}</b>"
    if task.total_parts:
        line += f" · Partes: {task.current_part}/{task.total_parts}"
    if task.progress > 0 and task.status in (TaskStatus.DOWNLOADING, TaskStatus.UPLOADING):
        line += f" · {task.progress:.1f}%"
    line += "\n"
    if task.s3_url and task.status == TaskStatus.COMPLETED:
        # Truncar URL muy larga.
        url = task.s3_url
        if len(url) > 80:
            url = url[:77] + "..."
        line += f"   URL: <code>{html.escape(url)}</code>\n"
    if task.error and task.status == TaskStatus.FAILED:
        err = task.error
        if len(err) > 80:
            err = err[:77] + "..."
        line += f"   Error: <code>{html.escape(err)}</code>\n"
    return line


def register(app: Client) -> None:
    @app.on_message(filters.command("cola") & AUTHORIZED)
    async def _cola(client: Client, message: Message):
        user_id = message.from_user.id
        tasks = QUEUE.get_user_tasks(user_id)

        if not tasks:
            await message.reply_text(
                "📭 <b>No tienes procesos activos ni recientes.</b>\n\n"
                "Reenvía un archivo o pega una URL para empezar.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Limpiar tareas muy antiguas.
        QUEUE.cleanup_old(max_age_seconds=3600)

        # Últimas 10 tareas.
        recent = tasks[-10:]
        active = [t for t in recent if t.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.UPLOADING)]
        finished = [t for t in recent if t.status not in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.UPLOADING)]

        lines = ["<b>📋 Tus procesos</b>\n"]
        if active:
            lines.append(f"<b>🟢 Activos ({len(active)})</b>")
            for t in active:
                lines.append(_format_task_line(t))
        if finished:
            lines.append(f"\n<b>✅ Recientes ({len(finished)})</b>")
            for t in finished:
                lines.append(_format_task_line(t))

        text = "\n".join(lines)
        await message.reply_text(text, parse_mode=ParseMode.HTML)
