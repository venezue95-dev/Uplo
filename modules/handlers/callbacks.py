"""
callbacks.py
============
Handlers para CallbackQuery generados por los botones inline:
- cancel:<task_id>      → cancela una subida en curso.
- settings:cycle:<key>  → avanza al siguiente valor cíclico.
- settings:view:<key>   → solo informativo (no owner).
- settings:refresh      → refresca el menú de settings.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery
from pyrogram.enums import ParseMode

from config import CONFIG
from modules.queue import QUEUE
from modules.runtime_settings import RUNTIME_SETTINGS
from .settings import _settings_text, _settings_keyboard
from .basic import is_owner


def register(app: Client) -> None:
    @app.on_callback_query(filters.regex(r"^cancel:(.+)$"))
    async def _cancel_cb(client: Client, cq: CallbackQuery):
        task_id = cq.data.split(":", 1)[1]
        task = QUEUE.get(task_id)
        if not task:
            await cq.answer("⚠️ Tarea no encontrada (puede que ya haya terminado).", show_alert=True)
            return
        if task.user_id != cq.from_user.id:
            await cq.answer("⛔ No tienes permiso para cancelar esta tarea.", show_alert=True)
            return
        if task.status.value in ("completed", "failed", "cancelled"):
            await cq.answer("ℹ️ La tarea ya no está en curso.", show_alert=True)
            return
        task.cancel()
        await cq.answer("🛑 Cancelando subida...")

    @app.on_callback_query(filters.regex(r"^settings:cycle:(.+)$"))
    async def _settings_cycle_cb(client: Client, cq: CallbackQuery):
        if CONFIG.owner_id is not None and not is_owner(cq.from_user.id):
            await cq.answer("⛔ Solo el owner puede modificar la configuración.", show_alert=True)
            return

        key = cq.data.rsplit(":", 1)[-1]
        try:
            new_val = RUNTIME_SETTINGS.cycle(key)
        except KeyError:
            await cq.answer("⚠️ Opción inválida.", show_alert=True)
            return

        await cq.answer(f"✅ {key} = {new_val}")
        # Refrescar el menú con los nuevos valores.
        is_own = is_owner(cq.from_user.id)
        await cq.message.edit_text(
            _settings_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=_settings_keyboard(is_own),
        )

    @app.on_callback_query(filters.regex(r"^settings:view:(.+)$"))
    async def _settings_view_cb(client: Client, cq: CallbackQuery):
        await cq.answer("ℹ️ Solo visualización. Pide al owner que cambie esta opción.")

    @app.on_callback_query(filters.regex(r"^settings:refresh$"))
    async def _settings_refresh_cb(client: Client, cq: CallbackQuery):
        is_own = is_owner(cq.from_user.id)
        await cq.message.edit_text(
            _settings_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=_settings_keyboard(is_own),
        )
