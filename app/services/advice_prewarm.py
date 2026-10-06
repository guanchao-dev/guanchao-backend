"""出行建议的每日预生成：每天凌晨 4 点把当天 24 小时潮位喂给 AI 生成一次，存库。

背景
----
小程序不允许跟 AI 对话，所以「出行建议」不能是「用户点一次、后端调一次 AI」。
这里每天定点跑一遍，结果写进 `spot_advice` 表；接口侧只读这份存好的
（见 services/ai.py::advice_for_request）。

为什么生成时的「参考时刻」要定在当天 12:00
------------------------------------------
`build_beachcombing_hint()` 在 06:00 前会返回 `blocked=True`，而 `tide_advice()`
遇到 blocked 会直接返回「现在不适合赶海」并且**根本不调 AI**。凌晨 4 点生成时若拿
「此刻」当 now，就会一整天都存下「不适合赶海」。所以统一用当天 12:00 作参考时刻
（`/home/today` 处理 `?date=` 时也是这个口径），生成出来的才是「今天」的建议。

定时方式
--------
后端进程内后台任务，随 uvicorn 启动（见 app/main.py 的 lifespan）。
单进程部署（start.bat 没带 --workers），不会重复执行。
服务器如果 4 点时没在运行，靠启动补生成兜底（run_daily_loop 开头会先跑一次）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import SHANGHAI_TZ
from app.db.base import async_session_factory
from app.db.models import Spot, SpotAdvice
from app.services import weather as weather_svc
from app.services.ai import tide_advice
from app.services.tide import get_tide

logger = logging.getLogger(__name__)

# 每天在这个点（北京时间）生成
RUN_HOUR = 4
# 生成时用的「参考时刻」的小时数，理由见模块 docstring
REFERENCE_HOUR = 12
# 单个点位的 AI 调用最长等多久（_chat 默认 12s 超时，这里兜底整个点位）
_GENERATE_TIMEOUT = 45


def today_str(now: datetime | None = None) -> str:
    """当前北京时间日期 YYYY-MM-DD。"""
    return (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d")


def reference_dt(date_str: str) -> datetime:
    """生成某天的建议时用的参考时刻（该日 12:00，北京时间）。"""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=REFERENCE_HOUR, tzinfo=SHANGHAI_TZ
    )


def seconds_until_next_run(now: datetime | None = None) -> float:
    """距下一个 RUN_HOUR:00（北京时间）还有多少秒。纯函数，方便单测。"""
    now = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    target = now.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def load_advice(db: AsyncSession, spot_id: str, date_str: str) -> SpotAdvice | None:
    """取某点位某天的预生成建议；没有返回 None（调用方退回规则文案）。"""
    return (
        await db.execute(
            select(SpotAdvice).where(SpotAdvice.spot_id == spot_id, SpotAdvice.date == date_str)
        )
    ).scalar_one_or_none()


async def _upsert(db: AsyncSession, spot_id: str, date_str: str, advice: dict) -> None:
    row = await load_advice(db, spot_id, date_str)
    # headline 列是 String(128)，留点余量自己截断，别让 MySQL 去截
    headline = str(advice.get("headline") or "")[:120]
    body = str(advice.get("body") or "")
    generated_by = str(advice.get("generatedBy") or "rule")
    if row is None:
        db.add(
            SpotAdvice(
                id=f"adv_{spot_id}_{date_str}",
                spot_id=spot_id,
                date=date_str,
                headline=headline,
                body=body,
                generated_by=generated_by,
            )
        )
    else:
        row.headline = headline
        row.body = body
        row.generated_by = generated_by


async def _generate_one(spot: dict, date_str: str, ref: datetime, sem: asyncio.Semaphore) -> bool:
    """生成一个点位的建议并落库。**自己开 session** —— AsyncSession 不能跨协程并发用。"""
    async with sem:
        try:
            async with async_session_factory() as db:
                tide = await get_tide(spot["id"], date_str, db=db, lat=spot["lat"], lng=spot["lng"])
                weather = await weather_svc.current(spot["lat"], spot["lng"], when=ref)
                spot_info = {
                    "name": spot["name"],
                    "city": spot["city"],
                    "age_hint": spot["age_hint"],
                    "safety_tags": spot["safety_tags"],
                }
                advice = await asyncio.wait_for(
                    tide_advice(tide, weather, spot_info, ref), timeout=_GENERATE_TIMEOUT
                )
                await _upsert(db, spot["id"], date_str, advice)
                await db.commit()
            return True
        except Exception:  # noqa: BLE001 单个点位失败不能拖垮整批
            logger.exception("预生成出行建议失败：%s %s", spot["id"], date_str)
            return False


async def generate_for_date(date_str: str, *, force: bool = False, concurrency: int = 4) -> dict:
    """给所有点位生成某天的建议。

    - 已有该 (点位, 日期) 记录则跳过（force=True 时覆盖重生成）
    - 单个点位失败只记日志，不影响其它点位
    返回 {date, total, generated, skipped, failed}
    """
    ref = reference_dt(date_str)
    async with async_session_factory() as db:
        rows = (await db.execute(select(Spot).order_by(Spot.id))).scalars().all()
        spots = [
            {
                "id": s.id,
                "name": s.name,
                "city": s.city,
                "age_hint": s.age_hint,
                "safety_tags": s.safety_tags or [],
                "lat": s.lat,
                "lng": s.lng,
            }
            for s in rows
        ]
        if force:
            done: set[str] = set()
        else:
            done = set(
                (
                    await db.execute(
                        select(SpotAdvice.spot_id).where(SpotAdvice.date == date_str)
                    )
                )
                .scalars()
                .all()
            )

    todo = [s for s in spots if s["id"] not in done]
    stats = {
        "date": date_str,
        "total": len(spots),
        "generated": 0,
        "skipped": len(spots) - len(todo),
        "failed": 0,
    }
    if not todo:
        logger.info("出行建议预生成 %s：全部已存在，跳过 %d 个", date_str, len(spots))
        return stats

    logger.info("出行建议预生成 %s：开始，%d 个点位", date_str, len(todo))
    sem = asyncio.Semaphore(max(1, concurrency))
    results = await asyncio.gather(*(_generate_one(s, date_str, ref, sem) for s in todo))
    stats["generated"] = sum(1 for ok in results if ok)
    stats["failed"] = len(results) - stats["generated"]
    logger.info("出行建议预生成 %s：完成 %s", date_str, stats)
    return stats


async def run_daily_loop() -> None:
    """后台常驻：每天 RUN_HOUR 生成当天建议；服务启动时先补一次。"""
    while True:
        try:
            now = datetime.now(SHANGHAI_TZ)
            # 启动补生成：已经过了今天的 4 点就补跑。幂等 —— 已生成的话只是一次查询。
            if now.hour >= RUN_HOUR:
                await generate_for_date(today_str(now))

            delay = seconds_until_next_run(datetime.now(SHANGHAI_TZ))
            logger.info("出行建议预生成：%.0f 秒后跑（%02d:00）", delay, RUN_HOUR)
            await asyncio.sleep(delay)
            await generate_for_date(today_str())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 循环里任何异常都不该让任务退出
            logger.exception("出行建议预生成任务出错，10 分钟后重试")
            await asyncio.sleep(600)
