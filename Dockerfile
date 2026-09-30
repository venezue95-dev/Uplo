FROM python:3.11-slim

# Evitar __pycache__ y buffers en logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Herramientas mínimas para dependencias
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar librerías de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código
COPY config.py bot.py ./
COPY modules/ ./modules/

# Crear carpetas de datos y descargas
RUN mkdir -p /app/data /app/downloads

# Ejecutar el bot
CMD ["python", "-u", "bot.py"]
