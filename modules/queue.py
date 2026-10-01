"""
queue.py
========
Sistema de cola para tareas de subida.

Cada UploadTask es una unidad de trabajo que:
- Se registra en el QueueManager.
- Tiene un hilo/event-loop que la procesa (descarga + subida chunked).
- Puede ser cancelada desde un botón inline (cancel_flag).
- Expone estado para /cola y /status.

El QueueManager además provee un asyncio.Lock por usuario para que
las subidas de un mismo usuario se procesen secuencialmente (evita
saturar red y disco) sin bloquear a otros usuarios.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class TaskStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


STATUS_ICON = {
    TaskStatus.QUEUED: "⏳",
    TaskStatus.DOWNLOADING: "📥",
    TaskStatus.UPLOADING: "📤",
    TaskStatus.COMPLETED: "✅",
    TaskStatus.FAILED: "❌",
    TaskStatus.CANCELLED: "🛑",
}


@dataclass
class UploadTask:
    """Estado completo de una tarea de subida."""
    task_id: str
    user_id: int
    filename: str
    file_size: int
    source: str                                   # 'telegram' | 'url'
    url: Optional[str] = None
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0                          # 0-100
    current_part: int = 0
    total_parts: int = 0
    failed_part: Optional[str] = None              # p. ej. "worker_1"
    attempts: int = 0
    max_attempts: int = 10
    error: Optional[str] = None
    s3_key: Optional[str] = None
    s3_url: Optional[str] = None
    s3_urls: List[str] = field(default_factory=list)  # todas las URLs de partes
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    status_msg_id: Optional[int] = None
    chat_id: Optional[int] = None

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_flag.is_set()

    def cancel(self) -> None:
        """Marca la tarea como cancelada y señaliza a los workers."""
        self.cancel_flag.set()
        if self.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            self.status = TaskStatus.CANCELLED

    def mark_started(self) -> None:
        self.started_at = time.time()

    def mark_completed(self, base_name: str, urls: List[str]) -> None:
        self.s3_key = base_name
        self.s3_urls = list(urls)
        # Para retro-compatibilidad: la primera URL si solo hay una, vacío si varias.
        self.s3_url = urls[0] if len(urls) == 1 else ""
        self.status = TaskStatus.COMPLETED
        self.completed_at = time.time()

    def mark_failed(self, error: str,
                    failed_part: Optional[str] = None,
                    attempts: int = 0) -> None:
        self.error = error
        self.failed_part = failed_part
        self.attempts = attempts
        self.status = TaskStatus.FAILED
        self.completed_at = time.time()


class QueueManager:
    """Registro central de tareas + locks por usuario."""

    def __init__(self) -> None:
        self._tasks: Dict[str, UploadTask] = {}
        self._user_locks: Dict[int, asyncio.Lock] = {}
        self._registry_lock = threading.Lock()

    def new_task(
        self,
        user_id: int,
        filename: str,
        file_size: int,
        source: str,
        url: Optional[str] = None,
    ) -> UploadTask:
        task = UploadTask(
            task_id=uuid.uuid4().hex[:12],
            user_id=user_id,
            filename=filename,
            file_size=file_size,
            source=source,
            url=url,
        )
        with self._registry_lock:
            self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> Optional[UploadTask]:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.cancel()
        return True

    def get_user_tasks(self, user_id: int) -> List[UploadTask]:
        """Devuelve las tareas del usuario ordenadas por creación."""
        with self._registry_lock:
            return sorted(
                (t for t in self._tasks.values() if t.user_id == user_id),
                key=lambda t: t.created_at,
            )

    def get_active_user_tasks(self, user_id: int) -> List[UploadTask]:
        """Solo tareas activas (queued / downloading / uploading)."""
        return [
            t for t in self.get_user_tasks(user_id)
            if t.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.UPLOADING)
        ]

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        """Lock por usuario para procesar tareas secuencialmente."""
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    def cleanup_old(self, max_age_seconds: int = 3600) -> None:
        """Elimina tareas terminadas con más de max_age_seconds."""
        now = time.time()
        with self._registry_lock:
            to_remove = [
                tid for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
                and t.completed_at
                and (now - t.completed_at) > max_age_seconds
            ]
            for tid in to_remove:
                del self._tasks[tid]


# Singleton global.
QUEUE = QueueManager()
