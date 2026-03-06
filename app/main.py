from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.database import engine
from app.redis_client import _pool

import redis.asyncio as aioredis


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: verify DB connectivity
    async with engine.connect():
        pass
    # startup: verify Redis connectivity
    _redis = aioredis.Redis(connection_pool=_pool)
    await _redis.ping()
    await _redis.aclose()

    yield

    # shutdown
    await engine.dispose()
    await _pool.aclose()


app = FastAPI(
    title="OpenClaw API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(v1_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "version": "0.1.0"}
