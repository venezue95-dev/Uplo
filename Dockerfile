# Dockerfile para toDus S3 Uploader Bot
# Imagen base: Python 3.12 slim para tamaño reducido.
FROM python:3.12-slim AS base

# Evitar __pycache__ y buffers en stdout/stderr (logs en tiempo real).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependencias del sistema: gcc para compilar TgCrypto + git para instalar
# la librería todus directamente desde GitHub.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias Python primero (mejor cache de capas).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del bot.
COPY config.py bot.py ./
COPY modules/ ./modules/

# Crear directorios persistentes.
RUN mkdir -p /app/data /app/downloads

# El bot usará estos volúmenes:
# /app/data       -> sesión de Pyrogram + settings.json
# /app/downloads  -> temporales (se borran al subir)
VOLUME ["/app/data", "/app/downloads"]

# Comando por defecto.
CMD ["python", "-u", "bot.py"]
