import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.config import CORS_ORIGINS
from routers.tiles import router as tiles_router
from application.services import tile_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cargar parquet en background al iniciar el servidor
    threading.Thread(target=tile_service.warmup, daemon=True).start()
    yield


app = FastAPI(title="Vento API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tiles_router, prefix="/api")


@app.get("/health")
def health():
    ready = tile_service.index_ready()
    return {"status": "ok", "index_ready": ready}
