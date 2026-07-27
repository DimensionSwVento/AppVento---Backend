# Vento — Backend (FastAPI). Imagen de producción.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias primero (mejor cacheo de capas). psycopg[binary]/pandas/pyarrow/h3
# traen wheels, así que no hacen falta toolchains de compilación.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Código
COPY . .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

# El entrypoint aplica migraciones (y opcionalmente ingiere el parquet) y luego
# arranca el server (el CMD).
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", "-b", "0.0.0.0:8000", "--timeout", "120"]
