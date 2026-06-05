import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import route, geocode, experiment
from services.http_clients import get_client, close_client

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時: 共有 httpx クライアントをプリ初期化
    get_client()
    yield
    # 終了時: 共有 httpx クライアントをクローズ
    await close_client()


app = FastAPI(title="自転車ナビAPI", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(route.router, prefix="/api")
app.include_router(geocode.router, prefix="/api")
app.include_router(experiment.router, prefix="/api")
