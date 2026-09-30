"""
config.py
========
Centraliza la lectura y validación de variables de entorno.
Soporta cualquier almacenamiento compatible con S3:
AWS S3, Cloudflare R2, Backblaze B2, MinIO, Wasabi,
DigitalOcean Spaces, Linode Object Storage, etc.

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
    """Configuración del bucket S3-compatible."""
    endpoint_url: Optional[str]
    region: str
    access_key: str
    secret_key: str
    bucket: str
    prefix: str
    public_base_url: Optional[str]
    presign_expiry: int
    force_path_style: bool

    def validate(self) -> None:
        # Se desactiva la obligación de tener S3_ACCESS_KEY y S3_SECRET_KEY al arrancar:
        # if not self.access_key or not self.secret_key:
        #     raise ValueError("ERROR: Faltan S3_ACCESS_KEY o S3_SECRET_KEY en el entorno.")
        if not self.bucket:
            raise ValueError("ERROR: Falta S3_BUCKET en el entorno.")
        if self.presign_expiry < 0:
            raise ValueError("S3_PRESIGN_EXPIRY no puede ser negativo.")


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

    # Defaults for chunked uploader; can be overridden at runtime via /settings
    chunk_size_mb: int
    max_parallel: int
    max_retries: int

    def validate(self) -> None:
        self.telegram.validate()
        self.s3.validate()
        if self.chunk_size_mb < 5:
            # AWS S3 minimum part size is 5 MB; some implementations may allow less,
            # but 5 is a safe universal minimum.
            raise ValueError("S3_CHUNK_SIZE_MB debe ser >= 5 (mínimo de S3 multipart).")
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


def _get_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def load_config() -> AppConfig:
    """Lee el entorno y devuelve un AppConfig validado."""
    tg = TelegramConfig(
        api_id=_get_int("API_ID", 0),
        api_hash=_get_str("API_HASH"),
        bot_token=_get_str("BOT_TOKEN"),
        session_name=_get_str("BOT_SESSION_NAME", "s3_bot_session"),
        # Máximo por archivo: 800 MB por defecto (límite del bot, no de S3).
        max_file_mb=_get_int("MAX_FILE_MB", 800),
    )

    s3 = S3Config(
        # Por defecto apunta al S3 de toDus (s3.todus.cu).
        # Se puede sobreescribir con S3_ENDPOINT_URL pero normalmente no hace falta.
        endpoint_url=_get_str("S3_ENDPOINT_URL", "https://s3.todus.cu") or None,
        region=_get_str("S3_REGION", "us-east-1"),
        access_key=_get_str("S3_ACCESS_KEY"),
        secret_key=_get_str("S3_SECRET_KEY"),
        bucket=_get_str("S3_BUCKET", "todus"),
        prefix=_get_str("S3_PREFIX", "uploads").strip("/"),
        public_base_url=_get_str("S3_PUBLIC_BASE_URL") or None,
        presign_expiry=_get_int("S3_PRESIGN_EXPIRY", 7 * 24 * 3600),
        force_path_style=_get_bool("S3_FORCE_PATH_STYLE", True),
    )

    owner_raw = _get_str("BOT_OWNER_ID")
    owner_id = int(owner_raw) if owner_raw else None

    app_cfg = AppConfig(
        telegram=tg,
        s3=s3,
        allowed_users=_split_ints(_get_str("BOT_ALLOWED_USERS")),
        owner_id=owner_id,
        download_dir=_get_str("DOWNLOAD_DIR", "downloads"),
        work_dir=_get_str("WORK_DIR", "."),
        chunk_size_mb=_get_int("S3_CHUNK_SIZE_MB", 8),
        max_parallel=_get_int("S3_MAX_PARALLEL", 3),
        max_retries=_get_int("S3_MAX_RETRIES", 10),
    )

    app_cfg.validate()
    return app_cfg


CONFIG: AppConfig = load_config()
