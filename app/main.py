from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.exceptions import register_exception_handlers
from app.db import models  # noqa: F401  确保模型注册到 Base.metadata
from app.db.base import Base, async_session_factory, engine
from app.db.migrate import run_migrations
from app.db.seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 首次启动建表 + 补列 + 灌入种子数据（幂等）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_migrations()
    async with async_session_factory() as session:
        await seed_if_empty(session)
    yield
    await engine.dispose()


app = FastAPI(title="观潮小程序 API", version="1.0.0", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
