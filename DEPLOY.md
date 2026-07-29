# Despliegue — Vento Backend

Guía de instalación desde actualizar el repo hasta producción. Dos caminos:
**Docker** (recomendado) o **bare-metal + systemd**.

## Requisitos previos
- **Postgres** accesible (base + rol para la app).
- **Keycloak** con realm `vento` (obligatorio: la autenticación no es opcional).
- El **parquet** que entrega el equipo de investigación → `data/master_variables.h3.parquet`.

## Configuración (`.env`)
```bash
cp .env.example .env
```
Edita al menos:
```bash
DATABASE_URL=postgresql+psycopg://USER:PASS@HOST:5432/vento
KEYCLOAK_ISSUER=https://<keycloak>/realms/vento
INTERNAL_API_SECRET=<secreto-largo>      # debe coincidir con el del frontend Next
CORS_ORIGINS=https://<dominio-frontend>
PARQUET_PATH=./data/master_variables.h3.parquet
```

---

## Opción A — Docker (recomendado)

```bash
git checkout develop && git pull

# coloca el parquet
cp master_variables.h3.parquet data/master_variables.h3.parquet

# levanta db + app (aplica migraciones al arrancar)
docker compose up -d --build

# crea/pobla la tabla hexagon desde el parquet (ingesta destructiva)
docker compose run --rm app python -m tools.ingest_parquet

# verificar
curl http://localhost:8000/api/health      # → {"status":"ok","index_ready":true}
```
Para ingerir el parquet automáticamente al arrancar: descomenta `RUN_INGEST: "true"` en `docker-compose.yml`.

---

## Opción B — Bare-metal + systemd

```bash
git checkout develop && git pull

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# base y rol en Postgres (una vez)
sudo -u postgres psql -c "CREATE ROLE vento LOGIN PASSWORD 'PASS';"
sudo -u postgres psql -c "CREATE DATABASE vento OWNER vento;"

# migraciones (crea las 3 tablas)
alembic upgrade head

# coloca el parquet y crea/pobla la tabla hexagon
cp master_variables.h3.parquet data/master_variables.h3.parquet
python -m tools.ingest_parquet

# servicio
sudo cp deploy/vento-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vento-backend
```
El backend vive en una **red privada (VPC)** y lo consume el **BFF Next** desde otra
instancia privada, así que escucha en `0.0.0.0:8000`. El aislamiento lo da el
**security group** (puerto 8000 solo desde la subred/IP del BFF) — no hay ingress
público. Auth obligatoria (`INTERNAL_API_SECRET` + Keycloak) como defensa extra.

---

## Crear/actualizar la tabla `hexagon` desde el parquet

| Necesidad | Comando |
|---|---|
| Migraciones (crea las 3 tablas) | `alembic upgrade head` |
| **Crear + poblar `hexagon` desde el parquet** | `python -m tools.ingest_parquet` |
| Recargar tras un parquet nuevo (destructivo) | `python -m tools.ingest_parquet [ruta]` |
| Ver la estructura real del parquet | `python -m tools.inspect_parquet` |

> `ingest_parquet` es **destructivo**: borra `hexagon` y la recarga. Para
> mantenimiento incremental usa las funciones CRUD de
> `infrastructure/db/hexagon_repository.py`.

## Notas
- **RAM**: cada worker de gunicorn carga su propio índice H3 en memoria al hacer
  warmup. Dimensiona `-w` según el tamaño del parquet.
- **Migraciones nuevas**: `alembic revision --autogenerate -m "..."` tras cambiar
  los modelos, revisa el archivo generado y commítealo.
