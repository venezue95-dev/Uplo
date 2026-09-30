"""
progress.py
===========
Utilidades de formateo y barra de progreso profesional.

Formato visual exacto pedido:

  📤 Subiendo 🔵
  📄 filename
  ╭──────────────────────╮
  │ ░░░░░░░░░░░░░░░░░░░░ │   0.0%
  ╰──────────────────────╯
  📦 Progreso: 0B / 176.5 MB
  ⚡️ Velocidad:        0 B/s
  ⏱️ ETA: calculando...   🧮 MB:      0.0/176.5
  ℹ️ Detalle: Parte 0/45, 🛑 Subida cancelada

  ❌ Error crítico en subida
  📄 Archivo: ...
  🔢 Parte fallida: worker_1
  🔁 Intentos realizados: 10/10
  Detalle: ...
"""

from __future__ import annotations

import threading
import time
from typing import Optional


# ----------------------------------------------------------------------
# Formateo de unidades.
# ----------------------------------------------------------------------
def format_bytes(size: float) -> str:
    """'1.50 MB' / '320 KB' (precisión .2)."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def format_bytes_compact(size: float) -> str:
    """Compacto: '0B' / '176.5 MB' / '1.2 GB'. Útil para línea de progreso."""
    if size < 1:
        return "0B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            if unit == 'B':
                return f"{int(size)}B"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.2f} GB"


def format_speed(bps: float) -> str:
    """'0 B/s' / '1.20 MB/s' alineado a la derecha en 12 caracteres."""
    raw = f"{format_bytes(bps)}/s" if bps > 0 else "0 B/s"
    return f"{raw:>12}"


def format_time(seconds: Optional[float]) -> str:
    """Devuelve 'MM:SS' o 'HH:MM:SS'. None -> 'calculando...'."""
    if seconds is None or seconds < 0:
        return "calculando..."
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ----------------------------------------------------------------------
# Barra con caja (box-drawing chars).
# ----------------------------------------------------------------------
BAR_WIDTH = 22  # ancho interior en chars de progreso.


def build_box_bar(percent: float, width: int = BAR_WIDTH) -> str:
    """Devuelve 3 líneas: ╭─...─╮ / │ ▓░...░ │ / ╰─...─╯"""
    percent = max(0.0, min(100.0, percent))
    filled = int(width * (percent / 100))
    empty = width - filled
    bar = "█" * filled + "░" * empty
    dashes = "─" * (width + 2)
    return (
        f"╭{dashes}╮\n"
        f"│ {bar} │\n"
        f"╰{dashes}╯"
    )


# ----------------------------------------------------------------------
# Plantillas de mensaje.
# ----------------------------------------------------------------------
def build_progress_message(
    title: str,
    filename: str,
    current: int,
    total: int,
    speed: float,
    eta_seconds: Optional[float],
    current_part: int,
    total_parts: int,
    detail: str = "",
    icon: str = "🔵",
) -> str:
    """Mensaje principal de progreso (descarga o subida)."""
    percent = (current / total * 100) if total > 0 else 0
    bar_block = build_box_bar(percent)

    eta_str = format_time(eta_seconds) if (eta_seconds and eta_seconds > 0) else "calculando..."
    current_mb = current / 1024 / 1024
    total_mb = total / 1024 / 1024

    # Detalle: si hay partes (subida) -> "Parte X/Y"; si no (descarga) -> solo detail.
    if total_parts > 0:
        detail_line = f"ℹ️ Detalle: Parte {current_part}/{total_parts}"
        if detail:
            detail_line += f", {detail}"
    else:
        detail_line = f"ℹ️ {detail}" if detail else ""

    return (
        f"📤 {title} {icon}\n"
        f"📄 {filename}\n"
        f"{bar_block}   {percent:.1f}%\n"
        f"📦 Progreso: {format_bytes_compact(current)} / {format_bytes_compact(total)}\n"
        f"⚡️ Velocidad: {format_speed(speed)}\n"
        f"⏱️ ETA: {eta_str:<14}🧮 MB: {current_mb:>7.1f}/{total_mb:.1f}\n"
        f"{detail_line}"
    )


def build_cancelled_message(
    title: str,
    filename: str,
    current: int,
    total: int,
    current_part: int,
    total_parts: int,
) -> str:
    """Mensaje cuando el usuario cancela la subida."""
    percent = (current / total * 100) if total > 0 else 0
    bar_block = build_box_bar(percent)
    current_mb = current / 1024 / 1024
    total_mb = total / 1024 / 1024

    return (
        f"📤 {title} 🛑\n"
        f"📄 {filename}\n"
        f"{bar_block}   {percent:.1f}%\n"
        f"📦 Progreso: {format_bytes_compact(current)} / {format_bytes_compact(total)}\n"
        f"⚡️ Velocidad: {format_speed(0)}\n"
        f"⏱️ ETA: cancelado       🧮 MB: {current_mb:>7.1f}/{total_mb:.1f}\n"
        f"ℹ️ Detalle: Parte {current_part}/{total_parts}, 🛑 Subida cancelada"
    )


def build_error_message(
    filename: str,
    failed_part: str,
    attempts: int,
    max_attempts: int,
    error: str,
) -> str:
    """Mensaje de error crítico cuando un chunk agota reintentos."""
    return (
        f"❌ Error crítico en subida\n\n"
        f"📄 Archivo: {filename}\n"
        f"🔢 Parte fallida: {failed_part}\n"
        f"🔁 Intentos realizados: {attempts}/{max_attempts}\n\n"
        f"Detalle: {error}"
    )


# ----------------------------------------------------------------------
# Estado de progreso thread-safe.
# ----------------------------------------------------------------------
class ProgressState:
    """
    Contadores thread-safe compartidos entre los workers (chunk upload)
    y el updater asíncrono que actualiza el mensaje de Telegram.
    """

    __slots__ = (
        "total_size", "num_parts", "uploaded_bytes", "completed_parts",
        "current_part", "start_time", "last_update", "lock",
    )

    def __init__(self, total_size: int, num_parts: int = 0) -> None:
        self.total_size: int = total_size
        self.num_parts: int = num_parts
        self.uploaded_bytes: int = 0
        self.completed_parts: int = 0
        self.current_part: int = 0
        self.start_time: float = time.time()
        self.last_update: float = 0.0
        self.lock = threading.Lock()

    def add_bytes(self, n: int) -> None:
        with self.lock:
            self.uploaded_bytes += n

    def part_started(self, part_num: int) -> None:
        with self.lock:
            if part_num > self.current_part:
                self.current_part = part_num

    def part_completed(self, part_num: int) -> None:
        with self.lock:
            self.completed_parts += 1
            if part_num > self.current_part:
                self.current_part = part_num

    def set_num_parts(self, n: int) -> None:
        with self.lock:
            self.num_parts = n

    def get_state(self) -> dict:
        with self.lock:
            return {
                "uploaded_bytes": self.uploaded_bytes,
                "completed_parts": self.completed_parts,
                "current_part": self.current_part,
                "num_parts": self.num_parts,
                "elapsed": time.time() - self.start_time,
            }
