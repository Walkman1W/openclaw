from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from app.api.v1.router import router as v1_router
from app.database import engine, AsyncSessionLocal
from app.redis_client import _pool

import redis.asyncio as aioredis


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify DB connectivity
    async with engine.connect():
        pass

    # Verify Redis connectivity
    _redis = aioredis.Redis(connection_pool=_pool)
    await _redis.ping()
    await _redis.aclose()

    # Bootstrap system accounts (platform treasury + crayfish agent)
    from app.services.bootstrap import ensure_system_accounts
    async with AsyncSessionLocal() as db:
        await ensure_system_accounts(db)

    yield

    # Shutdown
    await engine.dispose()
    await _pool.aclose()


app = FastAPI(
    title="OpenClaw API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(v1_router, prefix="/api/v1")

_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/static/platform.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "version": "0.1.0"}
