"""
config.py
========
Centraliza la lectura y validación de variables de entorno para el
bot de subida al S3 de toDus (s3.todus.cu).

La librería `todus` (https://github.com/nyxthor-dev/todus-client)
gestiona la autenticación internamente: NO se necesita token ni
credenciales en el .env.

Toda la configuración se carga una sola vez al importar el módulo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


def _split_ints(raw: str) -> List[int]:
    """Convierte '123,456,789' -> [123, 456, 789]. Vacío -> []."""
    if not raw:
        return []
    out: List[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            try:
                out.append(int(chunk))
            except ValueError:
                raise ValueError(
                    f"BOT_ALLOWED_USERS contiene un ID no entero: {chunk!r}"
                )
    return out


@dataclass
class S3Config:
    """
    Configuración residual del S3 de toDus.

    La lib `todus` gestiona el endpoint, auth y namespaces por su cuenta.
    Aquí solo guardamos configuración opcional del lado del bot.
    """
    # Reservado para futuras opciones. La lib todus se encarga de todo.
    pass

    def validate(self) -> None:
        # Nada que validar: la auth va por dentro de la librería todus.
        pass


@dataclass
class TelegramConfig:
    api_id: int
    api_hash: str
    bot_token: str
    session_name: str
    max_file_mb: int

    def validate(self) -> None:
        if not self.api_id:
            raise ValueError("ERROR: Falta API_ID (debes obtenerlo en my.telegram.org).")
        if not self.api_hash:
            raise ValueError("ERROR: Falta API_HASH.")
        if not self.bot_token:
            raise ValueError("ERROR: Falta BOT_TOKEN (habla con @BotFather).")


@dataclass
class AppConfig:
    telegram: TelegramConfig
    s3: S3Config
    allowed_users: List[int]
    owner_id: Optional[int]
    download_dir: str
    work_dir: str
    chunk_size_mb: int
    max_parallel: int
    max_retries: int

    def validate(self) -> None:
        self.telegram.validate()
        self.s3.validate()
        if self.chunk_size_mb < 1:
            raise ValueError("S3_CHUNK_SIZE_MB debe ser >= 1 MB.")
        if self.max_parallel < 1:
            raise ValueError("S3_MAX_PARALLEL debe ser >= 1.")
        if self.max_retries < 1:
            raise ValueError("S3_MAX_RETRIES debe ser >= 1.")


def _get_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _get_int(key: str, default: int = 0) -> int:
    raw = os.getenv(key, str(default)).strip()
    if not raw:
        return default
    return int(raw)


def load_config() -> AppConfig:
    """Lee el entorno y devuelve un AppConfig validado."""

    tg = TelegramConfig(
        api_id=_get_int("API_ID", 0),
        api_hash=_get_str("API_HASH"),
        bot_token=_get_str("BOT_TOKEN"),
        session_name=_get_str("BOT_SESSION_NAME", "s3_bot_session"),
        max_file_mb=_get_int("MAX_FILE_MB", 800),
    )

    s3 = S3Config()

    owner_raw = _get_str("BOT_OWNER_ID")
    owner_id = int(owner_raw) if owner_raw else None

    app_cfg = AppConfig(
        telegram=tg,
        s3=s3,
        allowed_users=_split_ints(_get_str("BOT_ALLOWED_USERS")),
        owner_id=owner_id,
        download_dir=_get_str("DOWNLOAD_DIR", "downloads"),
        work_dir=_get_str("WORK_DIR", "."),
        chunk_size_mb=_get_int("S3_CHUNK_SIZE_MB", 50),
        max_parallel=_get_int("S3_MAX_PARALLEL", 3),
        max_retries=_get_int("S3_MAX_RETRIES", 10),
    )

    app_cfg.validate()
    return app_cfg


CONFIG: AppConfig = load_config()
