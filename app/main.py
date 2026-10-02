import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.exceptions import register_exception_handlers
from app.db import models  # noqa: F401  确保模型注册到 Base.metadata
from app.db.base import Base, async_session_factory, engine
from app.db.migrate import run_migrations
from app.db.seed import seed_if_empty

logger = logging.getLogger(__name__)


async def _step(name: str, coro) -> None:
    """跑一个启动步骤，失败只记日志、不阻断启动。

    这些步骤跑在 lifespan 里 —— 一旦抛出去，uvicorn 会直接退出、整站不可用。
    「服务能起来但少张表 / 少个索引」远好于「服务完全起不来」，尤其是没人盯着
    日志的运营期。失败会带着堆栈打到 uvicorn 的错误日志里，排查时看得到。

    （写这段的直接原因：一次回填 SQL 的排序规则冲突让启动失败，站点中断了几分钟。）
    """
    try:
        await coro
    except Exception:  # noqa: BLE001
        logger.exception("启动步骤「%s」失败，服务继续启动", name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 首次启动建表 + 补列 + 灌入种子数据（幂等）
    async with engine.begin() as conn:
        await _step("建表 create_all", conn.run_sync(Base.metadata.create_all))
    await _step("数据库迁移", run_migrations())
    async with async_session_factory() as session:
        await _step("种子数据 seed", seed_if_empty(session))
    yield
    await engine.dispose()


app = FastAPI(title="观潮小程序 API", version="1.0.0", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
