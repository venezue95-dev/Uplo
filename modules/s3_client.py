"""
s3_client.py
============
Wrapper sobre la librería `todus` (https://github.com/nyxthor-dev/todus-client).

La librería gestiona internamente la autenticación con s3.todus.cu:
**NO se necesita token ni credenciales en el .env**.

Cada usuario de Telegram tiene su propio namespace `tg_<user_id>`,
creado automáticamente la primera vez que sube un archivo.

Para archivos grandes, se dividen en chunks y se suben en paralelo
como archivos separados (cada chunk via `ns.upload()`), ya que la
librería no expone chunked upload nativo. Cada chunk recibe su
propia share URL.

Subida con:
- Paralelismo (ThreadPoolExecutor, max_workers=S3_MAX_PARALLEL).
- Reintentos por chunk (hasta S3_MAX_RETRIES) con backoff exponencial.
- Cancelación cooperativa (task.is_cancelled).
"""

from __future__ import annotations

import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from todus import NamespaceManager

from config import CONFIG
from .progress import ProgressState
from .queue import UploadTask


# ----------------------------------------------------------------------
# Excepciones (mismos nombres que antes para compat con handlers)
# ----------------------------------------------------------------------
class S3Error(RuntimeError):
    """Error específico del cliente toDus."""


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
    __slots__ = ("part_number", "offset", "length", "original_name")

    def __init__(self, part_number: int, offset: int, length: int, original_name: str):
        self.part_number = part_number
        self.offset = offset
        self.length = length
        self.original_name = original_name


# ----------------------------------------------------------------------
# Cliente toDus (wrapper sobre NamespaceManager).
# ----------------------------------------------------------------------
class S3Client:
    """Cliente toDus basado en la librería `todus`."""

    _manager: Optional[NamespaceManager] = None
    _manager_lock = threading.Lock()
    # Caché de namespaces por usuario (thread-safe).
    _ns_cache: dict = {}
    _ns_lock = threading.Lock()

    def __init__(self, cfg=None) -> None:
        """
        El argumento `cfg` (S3Config) se acepta por compatibilidad con
        los handlers que lo pasan, pero la librería todus gestiona todo
        internamente. No se necesita ninguna configuración del lado del bot.
        """
        # Forzamos la inicialización del NamespaceManager para fallar temprano
        # si la librería no está instalada correctamente.
        self._get_manager()

    # ------------------------------------------------------------------
    # Singleton NamespaceManager.
    # ------------------------------------------------------------------
    @classmethod
    def _get_manager(cls) -> NamespaceManager:
        """NamespaceManager singleton. La auth va por dentro de la lib."""
        if cls._manager is None:
            with cls._manager_lock:
                if cls._manager is None:
                    cls._manager = NamespaceManager()
        return cls._manager

    def _get_namespace(self, user_id: int, username: str):
        """Obtiene o crea el namespace `tg_<user_id>` del usuario."""
        ns_name = f"tg_{user_id}"
        with self._ns_lock:
            if ns_name in self._ns_cache:
                return self._ns_cache[ns_name]

        manager = self._get_manager()
        try:
            if not manager.exists(ns_name):
                manager.create(ns_name, description=f"@{username or 'unknown'}")
            ns = manager.get_namespace(ns_name)
        except Exception as exc:
            raise S3Error(f"Error creando/obteniendo namespace {ns_name}: {exc}") from exc

        with self._ns_lock:
            self._ns_cache[ns_name] = ns
        return ns

    # ------------------------------------------------------------------
    # Conectividad (para /status).
    # ------------------------------------------------------------------
    def ping(self) -> Tuple[bool, str]:
        """Verifica que NamespaceManager responde. No verifica token."""
        try:
            manager = self._get_manager()
            # Si el manager se construye sin lanzar, la lib está OK.
            return True, "todus client listo (auth gestionada por la librería)"
        except Exception as exc:
            return False, f"Error inicializando todus: {exc}"

    # ------------------------------------------------------------------
    # Subida chunked (chunks = archivos separados en el namespace).
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
        user_id: int,
        username: str,
    ) -> Tuple[str, List[str]]:
        """
        Sube el archivo por chunks paralelos usando ns.upload().

        Cada chunk se escribe a un archivo temporal y se sube con
        `ns.upload(local_path=..., path='telegram', original_name=..., metadata={...})`.
        Tras subir, se genera la share URL de cada parte con
        `ns.share_url(key).url`.

        Devuelve (base_name, [share_urls]).
        """
        file_size = os.path.getsize(local_path)
        num_parts = math.ceil(file_size / chunk_size) or 1
        state.set_num_parts(num_parts)
        task.total_parts = num_parts

        base_name = self._unique_base_name(original_name)

        # Generar specs de partes.
        parts: List[PartSpec] = []
        for i in range(num_parts):
            offset = i * chunk_size
            length = min(chunk_size, file_size - offset)
            # Si solo hay 1 parte, no añadir sufijo .part.NNN.
            if num_parts == 1:
                part_name = original_name
            else:
                ext = os.path.splitext(original_name)[1]
                stem = os.path.splitext(original_name)[0]
                part_name = f"{stem}.part{i+1:03d}{ext}"
            parts.append(PartSpec(
                part_number=i + 1,
                offset=offset,
                length=length,
                original_name=part_name,
            ))

        # Subir en paralelo con reintentos.
        try:
            keys = self._upload_all_parts(
                parts=parts,
                local_path=local_path,
                task=task,
                state=state,
                max_parallel=max_parallel,
                max_retries=max_retries,
                user_id=user_id,
                username=username,
            )
        except (UploadCancelled, ChunkUploadError):
            raise

        # Generar share URLs para cada parte.
        try:
            ns = self._get_namespace(user_id, username)
            urls: List[str] = []
            for key in keys:
                share = ns.share_url(key)
                urls.append(share.url if hasattr(share, "url") else str(share))
        except Exception as exc:
            raise S3Error(f"Error generando share URLs: {exc}") from exc

        return base_name, urls

    def _upload_all_parts(
        self,
        parts: List[PartSpec],
        local_path: str,
        task: UploadTask,
        state: ProgressState,
        max_parallel: int,
        max_retries: int,
        user_id: int,
        username: str,
    ) -> List[str]:
        completed: List[Optional[str]] = [None] * len(parts)
        failed_info = None  # (part, worker_id, ChunkUploadError)

        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futures = {}
            for i, part in enumerate(parts):
                if task.is_cancelled:
                    raise UploadCancelled()
                worker_id = i % max_parallel
                fut = pool.submit(
                    self._upload_part_with_retry,
                    part=part,
                    local_path=local_path,
                    task=task,
                    state=state,
                    worker_id=worker_id,
                    max_retries=max_retries,
                    user_id=user_id,
                    username=username,
                )
                futures[fut] = (part, worker_id)

            for fut in as_completed(futures):
                part, worker_id = futures[fut]
                try:
                    key = fut.result()
                    completed[part.part_number - 1] = key
                    state.part_completed(part.part_number)
                except UploadCancelled:
                    raise
                except ChunkUploadError as exc:
                    failed_info = (part, worker_id, exc)
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break
                except Exception as exc:
                    failed_info = (
                        part, worker_id,
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
        part: PartSpec,
        local_path: str,
        task: UploadTask,
        state: ProgressState,
        worker_id: int,
        max_retries: int,
        user_id: int,
        username: str,
    ) -> str:
        """
        Sube una parte con hasta max_retries intentos usando ns.upload().

        Como ns.upload() toma un local_path, escribimos el chunk a un
        archivo temporal y lo subimos. Tras subir, borramos el temporal.
        """
        # Escribir el chunk a un archivo temporal.
        tmp_dir = os.path.join(CONFIG.download_dir, str(user_id), "chunks")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"chunk_{part.part_number:03d}_{uuid.uuid4().hex[:8]}.bin")

        try:
            with open(local_path, "rb") as src:
                src.seek(part.offset)
                data = src.read(part.length)
            with open(tmp_path, "wb") as dst:
                dst.write(data)
        except Exception as exc:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise ChunkUploadError(
                part_label=f"worker_{worker_id}",
                attempts=0,
                max_attempts=max_retries,
                error=f"Error leyendo chunk: {exc}",
            )

        last_error = "Unknown error"
        attempts = 0

        try:
            for attempt in range(1, max_retries + 1):
                if task.is_cancelled:
                    raise UploadCancelled()

                attempts = attempt
                state.part_started(part.part_number)

                try:
                    ns = self._get_namespace(user_id, username)
                    result = ns.upload(
                        local_path=tmp_path,
                        path="telegram",
                        original_name=part.original_name,
                        metadata={
                            "part": str(part.part_number),
                            "total": str(state.num_parts),
                            "base": "telegram-bot",
                        },
                    )
                    state.add_bytes(part.length)
                    return result.key
                except Exception as exc:
                    last_error = str(exc) or exc.__class__.__name__
                    # Backoff exponencial con tope de 30s.
                    time.sleep(min(2 ** attempt, 30))

            raise ChunkUploadError(
                part_label=f"worker_{worker_id}",
                attempts=attempts,
                max_attempts=max_retries,
                error=last_error,
            )
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Generación de nombre único (sin path separators).
    # ------------------------------------------------------------------
    @staticmethod
    def _unique_base_name(original_name: str) -> str:
        ts = time.strftime("%Y%m%d-%H%M%S")
        short_id = uuid.uuid4().hex[:8]
        safe = "".join(c for c in original_name if c.isalnum() or c in "._-") or "archivo"
        return f"{ts}-{short_id}-{safe}"

    # ------------------------------------------------------------------
    # URL pública (deprecated - usar las URLs devueltas por upload_chunked).
    # ------------------------------------------------------------------
    def get_download_url(self, key: str, user_id: int = 0, username: str = "") -> str:
        """Genera la share URL de un key ya subido."""
        if user_id:
            try:
                ns = self._get_namespace(user_id, username)
                share = ns.share_url(key)
                return share.url if hasattr(share, "url") else str(share)
            except Exception as exc:
                raise S3Error(f"Error generando share URL: {exc}") from exc
        return f"s3://todus/{key}"

    def delete_object(self, key: str, user_id: int = 0, username: str = "") -> None:
        """Borra un objeto del namespace del usuario (si la lib lo permite)."""
        if not user_id:
            raise S3Error("Se requiere user_id para borrar un objeto del namespace")
        try:
            ns = self._get_namespace(user_id, username)
            if hasattr(ns, "delete"):
                ns.delete(key)
        except Exception as exc:
            raise S3Error(f"Error al borrar {key}: {exc}") from exc
