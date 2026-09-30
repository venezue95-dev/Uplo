"""
updater.py
==========
Loop asíncrono que lee ProgressState y refresca el mensaje de Telegram
con el formato visual pedido (caja + ETA + velocidad + parte X/Y).

El loop corre concurrentemente con la descarga/subida (que ocurre en
threads), de modo que la UI se actualiza sin bloquear el trabajo.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from pyrogram.types import Message
from pyrogram.enums import ParseMode

from .progress import (
    ProgressState,
    build_progress_message,
    build_cancelled_message,
)
from .queue import UploadTask


async def progress_updater_loop(
    status_msg: Message,
    task: UploadTask,
    state: ProgressState,
    title: str,
    filename: str,
    icon: str = "🔵",
    keyboard=None,
    detail_fn=None,
) -> None:
    """
    Refresca `status_msg` cada 2.5s hasta que el trabajo termine
    (completado, cancelado o fallido).

    `detail_fn` es una callable opcional que devuelve un string extra
    para la línea de detalle (p. ej. mostrar worker activo).
    """
    while True:
        try:
            await asyncio.sleep(2.5)
        except asyncio.CancelledError:
            return

        # Cancelado por el usuario.
        if task.is_cancelled:
            s = state.get_state()
            text = build_cancelled_message(
                title=title,
                filename=filename,
                current=s["uploaded_bytes"],
                total=state.total_size,
                current_part=s["current_part"],
                total_parts=s["num_parts"],
            )
            await _safe_edit(status_msg, text, keyboard=None)
            return

        s = state.get_state()

        # Trabajo completado -> el llamador construirá el mensaje final.
        if state.total_size > 0 and s["uploaded_bytes"] >= state.total_size:
            return
        if s["completed_parts"] >= s["num_parts"] > 0:
            return

        elapsed = max(s["elapsed"], 0.1)
        speed = s["uploaded_bytes"] / elapsed
        remaining = state.total_size - s["uploaded_bytes"]
        eta = remaining / speed if speed > 0 else None

        detail_text = detail_fn() if detail_fn else ""

        text = build_progress_message(
            title=title,
            filename=filename,
            current=s["uploaded_bytes"],
            total=state.total_size,
            speed=speed,
            eta_seconds=eta,
            current_part=s["current_part"],
            total_parts=s["num_parts"],
            detail=detail_text,
            icon=icon,
        )

        await _safe_edit(status_msg, text, keyboard=keyboard)


async def show_cancelled_final(
    status_msg: Message,
    task: UploadTask,
    state: ProgressState,
    title: str,
    filename: str,
) -> None:
    """Mensaje final de cancelación (sin teclado)."""
    s = state.get_state()
    text = build_cancelled_message(
        title=title,
        filename=filename,
        current=s["uploaded_bytes"],
        total=state.total_size,
        current_part=s["current_part"],
        total_parts=s["num_parts"],
    )
    await _safe_edit(status_msg, text, keyboard=None)


async def _safe_edit(msg: Message, text: str, keyboard=None) -> None:
    """Edita el mensaje silenciosamente (ignora 'message not modified')."""
    try:
        await msg.edit_text(text, reply_markup=keyboard)
    except Exception:
        pass
