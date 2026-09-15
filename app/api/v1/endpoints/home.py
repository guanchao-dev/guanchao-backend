from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_optional_user
from app.core.exceptions import NotFoundError
from app.core.response import ok
from app.core.utils import SHANGHAI_TZ
from app.db.base import get_db
from app.db.models import Spot
from app.services.ai import tide_advice
from app.services.tide import (
    build_beachcombing_hint,
    build_fallback_advice,
    get_tide_window,
    get_weather,
    tide_calendar,
)

router = APIRouter(tags=["home"])

FEATURES = [
    {"key": "tideCalendar", "title": "潮汐日历", "enabled": True},
    {"key": "nearbySpots", "title": "附近赶海点", "enabled": True},
    {"key": "encyclopedia", "title": "海洋图鉴", "enabled": True},
    {"key": "gear", "title": "赶海装备", "enabled": True},
]


async def _resolve_spot(db: AsyncSession, spot_id: str | None, city: str | None) -> Spot | None:
    if spot_id:
        return await db.get(Spot, spot_id)
    if city:
        return (
            await db.execute(
                select(Spot).where(Spot.city == city).order_by(Spot.id).limit(1)
            )
        ).scalar_one_or_none()
    return (await db.execute(select(Spot).order_by(Spot.id).limit(1))).scalar_one_or_none()


@router.get("/home/today")
async def home_today(
    city: str | None = Query(None),
    spotId: str | None = Query(None),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    date: str | None = Query(None),
    withAdvice: int = Query(1),
    user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """今日潮汐。

    withAdvice=0 时跳过 AI 出行建议（改用规则模板），只取潮汐/天气。
    潮汐表、日历等不展示建议的页面用它，能省掉 1~2 秒的 AI 调用。
    """
    spot = await _resolve_spot(db, spotId, city)
    if spot is None:
        raise NotFoundError("暂无赶海点")
    now = datetime.now(SHANGHAI_TZ)
    if date:
        try:
            now = datetime.strptime(date, "%Y-%m-%d").replace(hour=12, tzinfo=SHANGHAI_TZ)
        except ValueError:
            pass
    tide = await get_tide_window(spot.id, now, db)
    weather = get_weather(spot.id, date)
    spot_info = {
        "name": spot.name,
        "city": spot.city,
        "age_hint": spot.age_hint,
        "safety_tags": spot.safety_tags,
    }
    if withAdvice:
        advice = await tide_advice(tide, weather, spot_info, now)
    else:
        advice = build_fallback_advice(tide, weather, now)
    beachcombing = build_beachcombing_hint(tide, now)
    return ok(
        {
            "place": {"spotId": spot.id, "name": spot.name, "city": spot.city},
            "tide": tide,
            "weather": weather,
            "advice": advice,
            "beachcombing": beachcombing,
            "features": FEATURES,
        }
    )


@router.get("/tide/calendar")
async def calendar(
    spotId: str = Query(...),
    month: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    spot = await db.get(Spot, spotId)
    if spot is None:
        raise NotFoundError("点位不存在")
    return ok(tide_calendar(spotId, month))
