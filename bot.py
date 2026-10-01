"""
bot.py
======
Entry point del S3 Uploader Bot.

Responsabilidades:
1. Crear el cliente Pyrogram a partir de la configuración validada.
2. Registrar todos los módulos de handlers
   (basic / upload / url / cola / settings / callbacks).
3. Iniciar el bot en modo long-polling.
"""

from __future__ import annotations

import asyncio
import logging
import sys

# Asegurar que exista un event loop antes de importar pyrogram.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client

from config import CONFIG
from modules.handlers import basic, upload, url, cola, settings, callbacks


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("s3_bot")


def build_app() -> Client:
    return Client(
        CONFIG.telegram.session_name,
        api_id=CONFIG.telegram.api_id,
        api_hash=CONFIG.telegram.api_hash,
        bot_token=CONFIG.telegram.bot_token,
        workdir=CONFIG.work_dir,
    )


def register_handlers(app: Client) -> None:
    basic.register(app)
    cola.register(app)
    settings.register(app)
    upload.register(app)
    url.register(app)
    callbacks.register(app)
    log.info("Handlers registrados: basic, cola, settings, upload, url, callbacks")


def main() -> int:
    log.info("Inicializando S3 Uploader Bot...")
    app = build_app()
    register_handlers(app)

    log.info(
        "Storage → toDus S3 (auth gestionada por la librería todus) | "
        "chunk=%dMB | paralelo=%d | reintentos=%d",
        CONFIG.chunk_size_mb,
        CONFIG.max_parallel,
        CONFIG.max_retries,
    )

    if CONFIG.allowed_users:
        log.info(
            "Modo privado | usuarios autorizados: %s",
            ", ".join(str(u) for u in CONFIG.allowed_users),
        )
    else:
        log.info("Modo público: cualquier usuario de Telegram puede usar el bot.")

    if CONFIG.owner_id:
        log.info("Bot owner ID: %d (puede cambiar /settings)", CONFIG.owner_id)

    log.info("Iniciando long-polling... Ctrl+C para detener.")
    app.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("Detenido por el usuario.")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        log.exception("Error fatal: %s", exc)
        sys.exit(1)
