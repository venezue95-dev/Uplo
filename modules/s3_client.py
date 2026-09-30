"""
s3_client.py
============
Cliente S3-compatible con:

1. Subida chunked (multipart upload) con paralelismo y reintentos.
2. Generación de enlace directo (URL pública estática o presignada).
3. Borrado de objetos.
4. Verificación de conectividad (head_bucket) para /status.

Compatible con AWS S3, Cloudflare R2, Backblaze B2, MinIO, Wasabi,
DigitalOcean Spaces, etc.
"""

from __future__ import annotations

import io
import math
import mimetypes
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from config import S3Config
from .progress import ProgressState
from .queue import UploadTask


# ----------------------------------------------------------------------
# Excepciones
# ----------------------------------------------------------------------
class S3Error(RuntimeError):
    """Error específico de S3."""


class ChunkUploadError(S3Error):
    """Un chunk agotó todos los reintentos."""

    def __init__(self, part_label: str, attempts: int, max_attempts: int, error: str):
        self.part_label = part_label
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.error = error
        super().__init__(
            f"Chunk {part_label} falló tras {attempts}/{max_attempts} intentos: {error}"
        )


class UploadCancelled(Exception):
    """Señal para abortar workers cuando el usuario cancela."""


# ----------------------------------------------------------------------
# Especificación de parte (chunk).
# ----------------------------------------------------------------------
class PartSpec:
    __slots__ = ("part_number", "offset", "length")

    def __init__(self, part_number: int, offset: int, length: int):
        self.part_number = part_number
        self.offset = offset
        self.length = length


# ----------------------------------------------------------------------
# Cliente base.
# ----------------------------------------------------------------------
class S3Client:
    """Cliente boto3 + helpers de URL."""

    def __init__(self, cfg: S3Config):
        self._cfg = cfg
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint_url or None,
            region_name=cfg.region or "us-east-1",
            aws_access_key_id=cfg.access_key,
            aws_secret_access_key=cfg.secret_key,
            config=BotoConfig(
                s3={"addressing_style": "path" if cfg.force_path_style else "auto"},
                retries={"max_attempts": 5, "mode": "adaptive"},
                connect_timeout=10,
                read_timeout=120,
            ),
        )

    # ------------------------------------------------------------------
    # Utilidades estáticas.
    # ------------------------------------------------------------------
    @staticmethod
    def _unique_key(prefix: str, filename: str) -> str:
        ts = time.strftime("%Y%m%d-%H%M%S")
        short_id = uuid.uuid4().hex[:8]
        safe_name = os.path.basename(filename.replace("\\", "/")).replace(" ", "_")
        return f"{prefix.strip('/')}/{ts}-{short_id}/{safe_name}"

    @staticmethod
    def _guess_extra_args(filename: str) -> dict:
        content_type, _ = mimetypes.guess_type(filename)
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        if not content_type or content_type in (
            "application/zip",
            "application/x-tar",
            "application/x-rar-compressed",
            "application/octet-stream",
        ) or content_type.startswith("application/"):
            extra["ContentDisposition"] = "attachment"
        return extra

    # ------------------------------------------------------------------
    # Conectividad (para /status).
    # ------------------------------------------------------------------
    def ping(self) -> Tuple[bool, str]:
        """Comprueba acceso al bucket. Devuelve (ok, mensaje)."""
        try:
            self._client.head_bucket(Bucket=self._cfg.bucket)
            return True, f"Bucket '{self._cfg.bucket}' accesible"
        except (BotoCoreError, ClientError) as exc:
            return False, f"Error accediendo al bucket: {exc}"
        except Exception as exc:
            return False, f"Error inesperado: {exc}"

    # ------------------------------------------------------------------
    # Subida chunked.
    # ------------------------------------------------------------------
    def upload_chunked(
        self,
        local_path: str,
        original_name: str,
        task: UploadTask,
        state: ProgressState,
        chunk_size: int,
        max_parallel: int,
        max_retries: int,
    ) -> str:
        """
        Subida multipart con paralelismo y reintentos por chunk.
        Devuelve la clave S3 final. Lanza ChunkUploadError / UploadCancelled.
        """
        file_size = os.path.getsize(local_path)
        chunk_size = max(chunk_size, 5 * 1024 * 1024)  # mínimo S3 multipart
        num_parts = math.ceil(file_size / chunk_size) or 1
        state.set_num_parts(num_parts)
        task.total_parts = num_parts

        key = self._unique_key(self._cfg.prefix, original_name)
        extra_args = self._guess_extra_args(original_name)

        # 1) Crear multipart upload.
        try:
            create_resp = self._client.create_multipart_upload(
                Bucket=self._cfg.bucket,
                Key=key,
                **extra_args,
            )
            upload_id = create_resp["UploadId"]
        except (BotoCoreError, ClientError) as exc:
            raise S3Error(f"Error al iniciar multipart upload: {exc}") from exc

        # 2) Subir partes en paralelo.
        try:
            completed_parts = self._upload_all_parts(
                key=key,
                upload_id=upload_id,
                local_path=local_path,
                file_size=file_size,
                chunk_size=chunk_size,
                num_parts=num_parts,
                task=task,
                state=state,
                max_parallel=max_parallel,
                max_retries=max_retries,
            )

            # 3) Completar multipart.
            self._client.complete_multipart_upload(
                Bucket=self._cfg.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": completed_parts},
            )
        except (UploadCancelled, ChunkUploadError, S3Error):
            # Abortar multipart para no dejar partes huérfanas.
            try:
                self._client.abort_multipart_upload(
                    Bucket=self._cfg.bucket,
                    Key=key,
                    UploadId=upload_id,
                )
            except Exception:
                pass
            raise
        except Exception:
            try:
                self._client.abort_multipart_upload(
                    Bucket=self._cfg.bucket,
                    Key=key,
                    UploadId=upload_id,
                )
            except Exception:
                pass
            raise

        return key

    def _upload_all_parts(
        self,
        key: str,
        upload_id: str,
        local_path: str,
        file_size: int,
        chunk_size: int,
        num_parts: int,
        task: UploadTask,
        state: ProgressState,
        max_parallel: int,
        max_retries: int,
    ) -> list:
        """Sube todas las partes en paralelo y devuelve lista para Complete."""
        completed = [None] * num_parts
        failed_info = None  # (part, worker_id, ChunkUploadError)

        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futures = {}
            for i in range(num_parts):
                if task.is_cancelled:
                    raise UploadCancelled()
                offset = i * chunk_size
                length = min(chunk_size, file_size - offset)
                part = PartSpec(i + 1, offset, length)
                worker_id = i % max_parallel  # identificador estable por slot
                fut = pool.submit(
                    self._upload_part_with_retry,
                    key=key,
                    upload_id=upload_id,
                    part=part,
                    local_path=local_path,
                    task=task,
                    state=state,
                    worker_id=worker_id,
                    max_retries=max_retries,
                )
                futures[fut] = (part, worker_id)

            for fut in as_completed(futures):
                part, worker_id = futures[fut]
                try:
                    etag, attempts = fut.result()
                    completed[part.part_number - 1] = {
                        "PartNumber": part.part_number,
                        "ETag": etag,
                    }
                    state.part_completed(part.part_number)
                except UploadCancelled:
                    raise
                except ChunkUploadError as exc:
                    failed_info = (part, worker_id, exc)
                    # Cancelar el resto
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break
                except Exception as exc:
                    failed_info = (
                        part,
                        worker_id,
                        ChunkUploadError(
                            part_label=f"worker_{worker_id}",
                            attempts=max_retries,
                            max_attempts=max_retries,
                            error=str(exc),
                        ),
                    )
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break

        if failed_info:
            _, worker_id, err = failed_info
            raise ChunkUploadError(
                part_label=f"worker_{worker_id}",
                attempts=err.attempts,
                max_attempts=err.max_attempts,
                error=err.error,
            )

        return completed

    def _upload_part_with_retry(
        self,
        key: str,
        upload_id: str,
        part: PartSpec,
        local_path: str,
        task: UploadTask,
        state: ProgressState,
        worker_id: int,
        max_retries: int,
    ) -> Tuple[str, int]:
        """Sube una parte con hasta max_retries intentos. Devuelve (ETag, intentos)."""
        last_error = "Unknown error"
        attempts = 0

        for attempt in range(1, max_retries + 1):
            if task.is_cancelled:
                raise UploadCancelled()

            attempts = attempt
            state.part_started(part.part_number)
            try:
                # Leer chunk desde disco.
                with open(local_path, "rb") as f:
                    f.seek(part.offset)
                    data = f.read(part.length)

                # Subir parte.
                resp = self._client.upload_part(
                    Bucket=self._cfg.bucket,
                    Key=key,
                    PartNumber=part.part_number,
                    UploadId=upload_id,
                    Body=data,
                    ContentLength=len(data),
                )
                state.add_bytes(len(data))
                return resp["ETag"], attempts
            except (BotoCoreError, ClientError, IOError, OSError) as exc:
                last_error = str(exc) or exc.__class__.__name__
                # Backoff exponencial con tope de 30s.
                time.sleep(min(2 ** attempt, 30))
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                time.sleep(min(2 ** attempt, 30))

        raise ChunkUploadError(
            part_label=f"worker_{worker_id}",
            attempts=attempts,
            max_attempts=max_retries,
            error=last_error,
        )

    # ------------------------------------------------------------------
    # Generación de enlace directo.
    # ------------------------------------------------------------------
    def get_download_url(self, key: str) -> str:
        if self._cfg.public_base_url:
            base = self._cfg.public_base_url.rstrip("/")
            return f"{base}/{key.lstrip('/')}"

        if self._cfg.presign_expiry <= 0:
            return f"s3://{self._cfg.bucket}/{key}"

        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._cfg.bucket, "Key": key},
                ExpiresIn=self._cfg.presign_expiry,
            )
        except (BotoCoreError, ClientError) as exc:
            raise S3Error(f"Error al generar enlace firmado: {exc}") from exc

    # ------------------------------------------------------------------
    # Borrado.
    # ------------------------------------------------------------------
    def delete_object(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._cfg.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise S3Error(f"Error al borrar {key}: {exc}") from exc
