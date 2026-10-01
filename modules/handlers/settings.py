"""
settings.py
===========
Comando /settings: muestra la configuración actual con botones inline
para cambiar opciones cíclicamente.

Solo el BOT_OWNER_ID puede modificar; el resto solo visualiza.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
)
from pyrogram.enums import ParseMode

from config import CONFIG
from modules.runtime_settings import (
    RUNTIME_SETTINGS,
    CYCLE_OPTIONS,
    CYCLE_LABELS,
)
from .basic import AUTHORIZED, is_owner


def _mask_token(token: str) -> str:
    """Muestra el token con solo primeros/últimos caracteres."""
    if not token:
        return "(vacío)"
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]} ({len(token)} chars)"


def _settings_text() -> str:
    s = RUNTIME_SETTINGS
    return (
        "<b>⚙️ Configuración</b>\n\n"
        f"📏 <b>Máximo por archivo:</b> <code>{s.max_file_mb} MB</code>\n"
        f"🧩 <b>Tamaño de chunk:</b> <code>{s.chunk_size_mb} MB</code>\n"
        f"⚡️ <b>Paralelismo:</b> <code>{s.max_parallel} workers</code>\n"
        f"🔁 <b>Reintentos por chunk:</b> <code>{s.max_retries}</code>\n\n"
        "<b>🔒 Sistema</b>\n"
        f"• Modo: <code>{'Privado' if CONFIG.allowed_users else 'Público'}</code>\n"
        f"• Cliente toDus: <code>lib todus (auth interna)</code>\n"
        f"• Namespace: <code>tg_&lt;user_id&gt;</code> por usuario\n\n"
        "<i>Toca un botón para cambiar (solo owner).</i>"
    )


def _settings_keyboard(is_owner_user: bool) -> InlineKeyboardMarkup:
    s = RUNTIME_SETTINGS
    rows = []
    for key in ("max_file_mb", "chunk_size_mb", "max_parallel", "max_retries"):
        label = CYCLE_LABELS[key]
        value = getattr(s, key)
        unit = "MB" if key in ("max_file_mb", "chunk_size_mb") else ""
        text = f"{label}: {value} {unit}".strip()
        cb = f"settings:cycle:{key}" if is_owner_user else f"settings:view:{key}"
        rows.append([InlineKeyboardButton(text, callback_data=cb)])
    rows.append([InlineKeyboardButton("🔄 Refrescar", callback_data="settings:refresh")])
    return InlineKeyboardMarkup(rows)


def register(app: Client) -> None:
    @app.on_message(filters.command("settings") & AUTHORIZED)
    async def _settings(client: Client, message: Message):
        is_own = is_owner(message.from_user.id)
        text = _settings_text()
        if not is_own and CONFIG.owner_id is not None:
            text += "\n\n<i>ℹ️ Solo el owner puede modificar las opciones.</i>"
        kb = _settings_keyboard(is_own)
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
