from math import asin, cos, radians, sin, sqrt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.response import ok, paginated
from app.db.base import get_db
from app.db.models import Spot

router = APIRouter(tags=["spots"])


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6371000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return int(2 * r * asin(sqrt(a)))


def _spot_list_item(s: Spot, lat: float | None, lng: float | None) -> dict:
    has_loc = lat is not None and lng is not None and s.lat is not None and s.lng is not None
    distance = _haversine(lat, lng, s.lat, s.lng) if has_loc else None
    return {
        "id": s.id,
        "name": s.name,
        "city": s.city,
        "heat": s.heat or 0,
        "latitude": s.lat,
        "longitude": s.lng,
        "distanceM": distance,
        "coverUrl": s.cover_key,
        "photos": [s.cover_key] if s.cover_key else [],
        "openTime": s.open_time,
        "ageHint": s.age_hint,
        "safetyTags": s.safety_tags or [],
        "observeHint": s.observe_hint,
        "species": [],
    }


@router.get("/spots")
async def list_spots(
    city: str | None = Query(None),
    keyword: str | None = Query(None),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    q = select(Spot)
    if city:
        q = q.where(Spot.city == city)
    if keyword:
        kw = keyword.strip()
        q = q.where(
            or_(
                Spot.name.contains(kw),
                Spot.city.contains(kw),
                Spot.observe_hint.contains(kw),
                Spot.description.contains(kw),
            )
        )
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (
        await db.execute(q.order_by(Spot.heat.desc(), Spot.id).offset((page - 1) * pageSize).limit(pageSize))
    ).scalars().all()
    items = [_spot_list_item(s, lat, lng) for s in rows]
    return ok(paginated(items, page, pageSize, total))


@router.get("/spots/{spot_id}")
async def spot_detail(spot_id: str, db: AsyncSession = Depends(get_db)):
    s = await db.get(Spot, spot_id)
    if s is None:
        raise NotFoundError("点位不存在")
    item = _spot_list_item(s, None, None)
    item.update(
        {
            "description": s.description,
            "gearList": s.gear_list or [],
            "source": s.source,
            "reviewedAt": s.reviewed_at,
        }
    )
    return ok(item)


@router.get("/gear")
async def gear():
    """赶海装备清单。scene 为地形场景：rocky 礁石 / mudflat 泥滩 / sandy 沙滩。

    - scenes：该装备适用于哪些场景（用于筛选显示）；
    - mustHave：该装备在哪些场景是「必带」（是 scenes 的子集），前端按当前选中场景标必带。
    """
    return ok(
        {
            "list": [
                {"name": "防滑鞋", "why": "礁石湿滑，防滑鞋是第一安全保障", "scenes": ["rocky", "mudflat", "sandy"], "mustHave": ["rocky"]},
                {"name": "长筒雨靴", "why": "泥滩泥泞易陷脚，长筒靴防水防陷", "scenes": ["mudflat"], "mustHave": ["mudflat"]},
                {"name": "遮阳帽", "why": "沙滩日晒强，遮阳防晒", "scenes": ["rocky", "sandy"], "mustHave": ["sandy"]},
                {"name": "防风外套", "why": "海边风大，注意保暖", "scenes": ["rocky"], "mustHave": []},
                {"name": "观察盒", "why": "透明盒便于近距离观察，看完记得放回", "scenes": ["rocky", "sandy"], "mustHave": []},
                {"name": "放大镜", "why": "看看藤壶的小脚和海藻的纹理", "scenes": ["rocky"], "mustHave": []},
                {"name": "小水桶", "why": "装海水观察小生物", "scenes": ["mudflat", "sandy"], "mustHave": []},
                {"name": "小铲子", "why": "挖蛤蜊、蛏子用，注意别破坏滩涂", "scenes": ["mudflat", "sandy"], "mustHave": []},
            ]
        }
    )
