"""
runtime_settings.py
===================
Configuración mutable en runtime (modificable vía /settings).

Algunas opciones son útiles de cambiar sin reiniciar el bot:
- max_file_mb:    límite por archivo (500 / 800 / 1000 / 2000)
- chunk_size_mb:  tamaño de chunk para multipart (5 / 8 / 16 / 32)
- max_parallel:    workers paralelos (1 / 2 / 3 / 5)
- max_retries:     reintentos por chunk (5 / 10 / 15 / 20)

Se persiste en settings.json para sobrevivir reinicios.
Solo el BOT_OWNER_ID puede modificarlas vía botones inline.
"""

from __future__ import annotations

import json
import os
import threading
from typing import List


SETTINGS_FILE = "settings.json"

# Opciones ciclablables por el menú /settings.
CYCLE_OPTIONS = {
    "max_file_mb": [500, 800, 1000, 2000],
    # Con la librería todus, cada chunk es un archivo completo en el namespace.
    # Chunks más grandes = menos URLs pero menos granularidad de progreso.
    "chunk_size_mb": [10, 50, 100, 200, 800],
    "max_parallel": [1, 2, 3, 5],
    "max_retries": [5, 10, 15, 20],
}

CYCLE_LABELS = {
    "max_file_mb": "📏 Máximo por archivo",
    "chunk_size_mb": "🧩 Tamaño de chunk",
    "max_parallel": "⚡️ Paralelismo",
    "max_retries": "🔁 Reintentos por chunk",
}


class RuntimeSettings:
    """Wrapper thread-safe sobre settings.json."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values = {
            "max_file_mb": 800,
            "chunk_size_mb": 50,
            "max_parallel": 3,
            "max_retries": 10,
        }
        self._load()

    # ------------------------------------------------------------------
    # Acceso
    # ------------------------------------------------------------------
    @property
    def max_file_mb(self) -> int:
        return self._values["max_file_mb"]

    @property
    def chunk_size_mb(self) -> int:
        return self._values["chunk_size_mb"]

    @property
    def max_parallel(self) -> int:
        return self._values["max_parallel"]

    @property
    def max_retries(self) -> int:
        return self._values["max_retries"]

    def as_dict(self) -> dict:
        with self._lock:
            return dict(self._values)

    # ------------------------------------------------------------------
    # Mutación
    # ------------------------------------------------------------------
    def cycle(self, key: str) -> int:
        """Avanza al siguiente valor de la opción cíclica y persiste."""
        if key not in CYCLE_OPTIONS:
            raise KeyError(f"Opción no ciclablable: {key}")
        options: List[int] = CYCLE_OPTIONS[key]
        with self._lock:
            current = self._values[key]
            try:
                idx = options.index(current)
            except ValueError:
                idx = -1
            next_val = options[(idx + 1) % len(options)]
            self._values[key] = next_val
        self._save()
        return next_val

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                for k, v in data.items():
                    if k in self._values:
                        self._values[k] = int(v)
        except Exception:
            # Si está corrupto, ignoramos y usamos defaults.
            pass

    def _save(self) -> None:
        with self._lock:
            data = dict(self._values)
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


RUNTIME_SETTINGS = RuntimeSettings()
