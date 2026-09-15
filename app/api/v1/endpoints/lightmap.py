"""点亮地图：目录 / 详情 / 打卡点亮 / 进度（游客可玩，登录同步）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_identity
from app.core.exceptions import NotFoundError
from app.core.response import ok
from app.core.utils import new_id
from app.db.base import get_db
from app.db.models import LightMap, LightMapLit
from app.schemas import LightMapVisitRequest

router = APIRouter(tags=["light-map"])


def _in_bbox(lat: float, lng: float, region: dict) -> bool:
    try:
        return (
            float(region.get("minLat", 0)) <= lat <= float(region.get("maxLat", 0))
            and float(region.get("minLng", 0)) <= lng <= float(region.get("maxLng", 0))
        )
    except (TypeError, ValueError):
        return False


@router.get("/light-maps")
async def list_light_maps(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(LightMap).order_by(LightMap.sort))).scalars().all()
    items = [{"id": m.id, "name": m.name, "coverUrl": m.map_url} for m in rows]
    return ok({"list": items})


@router.get("/light-maps/{map_id}")
async def light_map_detail(
    map_id: str,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    m = await db.get(LightMap, map_id)
    if m is None:
        raise NotFoundError("点亮地图不存在")
    lit = set(
        (
            await db.execute(
                select(LightMapLit.region_id).where(
                    LightMapLit.map_id == map_id, LightMapLit.owner_id == owner_id
                )
            )
        ).scalars().all()
    )
    regions = [dict(r, lit=r.get("id") in lit) for r in (m.regions or [])]
    return ok({"id": m.id, "name": m.name, "mapUrl": m.map_url, "regions": regions})


@router.post("/light-maps/{map_id}/visits")
async def light_map_visit(
    map_id: str,
    body: LightMapVisitRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    m = await db.get(LightMap, map_id)
    if m is None:
        raise NotFoundError("点亮地图不存在")
    matched = [
        r.get("id") for r in (m.regions or [])
        if _in_bbox(body.latitude, body.longitude, r) and r.get("id")
    ]
    existing = set(
        (
            await db.execute(
                select(LightMapLit.region_id).where(
                    LightMapLit.map_id == map_id, LightMapLit.owner_id == owner_id
                )
            )
        ).scalars().all()
    )
    newly = [rid for rid in matched if rid not in existing]
    for rid in newly:
        db.add(LightMapLit(id=new_id("lm"), map_id=map_id, region_id=rid, owner_id=owner_id))
    if newly:
        await db.commit()
    return ok(
        {
            "litRegionIds": sorted(existing | set(newly)),
            "newlyLitIds": newly,
            "unlockedMedalIds": [],
        }
    )


@router.get("/light-maps/{map_id}/progress")
async def light_map_progress(
    map_id: str,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    lit = (
        await db.execute(
            select(LightMapLit.region_id).where(
                LightMapLit.map_id == map_id, LightMapLit.owner_id == owner_id
            )
        )
    ).scalars().all()
    return ok({"mapId": map_id, "litRegionIds": list(lit)})
