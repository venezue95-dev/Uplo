"""Registro central de handlers del bot."""

from . import basic, upload, url, cola, settings, callbacks  # noqa: F401

__all__ = ["basic", "upload", "url", "cola", "settings", "callbacks"]
