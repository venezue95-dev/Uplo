"""
downloader.py
=============
Funciones para descargar el archivo original (desde Telegram o URL)
al disco local antes de subirlo a S3.

Las descargas actualizan el UploadTask y el ProgressState para que
el updater asíncrono pueda mostrar la barra de progreso.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Tuple
from urllib.parse import urlparse, unquote

import requests
from pyrogram import Client
from pyrogram.types import Message

from .progress import ProgressState
from .queue import UploadTask


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def url_to_filename(url: str, fallback: str = "descarga_web.bin") -> str:
    parsed = urlparse(url)
    name = unquote(os.path.basename(parsed.path)) or fallback
    return name


# ----------------------------------------------------------------------
# Helpers para progreso de descarga.
# ----------------------------------------------------------------------
async def _tg_progress_callback(
    current: int,
    total: int,
    status_msg: Message,
    task: UploadTask,
    state: ProgressState,
    label: str,
) -> None:
    """Callback para message.download: actualiza state y task."""
    now = time.time()
    if (now - state.last_update) < 2.5 and current < total:
        return
    state.last_update = now
    state.uploaded_bytes = current
    task.progress = (current / total * 100) if total > 0 else 0
    # El updater externo se encarga de construir el texto.


async def download_telegram_file(
    client: Client,
    message: Message,
    local_path: str,
    status_msg: Message,
    task: UploadTask,
    state: ProgressState,
    filename_label: str,
) -> Tuple[str, int]:
    """Descarga un archivo desde Telegram y devuelve (ruta, tamaño)."""
    tracker = {"start_time": time.time()}

    # Pyrogram llama a progress(current, total, *progress_args).
    async def _cb(current: int, total: int, *args) -> None:
        now = time.time()
        elapsed = max(now - tracker["start_time"], 0.1)
        state.uploaded_bytes = current
        state.total_size = total
        task.progress = (current / total * 100) if total > 0 else 0
        # No actualizamos el mensaje aquí; lo hace el updater externo.

    await message.download(
        file_name=local_path,
        progress=_cb,
    )
    return local_path, os.path.getsize(local_path)


def download_from_url(
    url: str,
    local_path: str,
    task: UploadTask,
    state: ProgressState,
    loop=None,
) -> Tuple[str, int]:
    """Descarga una URL HTTP(S) en streaming y actualiza state."""
    loop = loop or asyncio.get_event_loop()
    tracker = {"start_time": time.time()}

    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total_size = int(r.headers.get("content-length", 0))
        state.total_size = total_size or 0

        downloaded = 0
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                state.uploaded_bytes = downloaded
                task.progress = (downloaded / total_size * 100) if total_size > 0 else 0

                if task.is_cancelled:
                    break

    return local_path, os.path.getsize(local_path)


def get_url_size(url: str) -> int:
    """HEAD para validar tamaño antes de descargar."""
    try:
        r = requests.head(url, allow_redirects=True, timeout=15)
        r.raise_for_status()
        return int(r.headers.get("content-length", 0))
    except Exception:
        return 0
