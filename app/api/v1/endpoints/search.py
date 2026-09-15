"""全局搜索：赶海点 + 图鉴（游客可读）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import ok, paginated
from app.db.base import get_db
from app.db.models import Species, Spot

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    kw = keyword.strip()

    spots_q = select(Spot)
    if kw:
        spots_q = spots_q.where(
            or_(
                Spot.name.contains(kw),
                Spot.city.contains(kw),
                Spot.description.contains(kw),
                Spot.observe_hint.contains(kw),
            )
        )
    spots_total = (
        await db.execute(select(func.count()).select_from(spots_q.subquery()))
    ).scalar() or 0
    spots = (
        await db.execute(spots_q.order_by(Spot.id).offset((page - 1) * pageSize).limit(pageSize))
    ).scalars().all()
    spot_items = [
        {"id": s.id, "name": s.name, "city": s.city, "observeHint": s.observe_hint}
        for s in spots
    ]

    species_q = select(Species)
    if kw:
        species_q = species_q.where(
            or_(Species.name.contains(kw), Species.summary.contains(kw))
        )
    species_total = (
        await db.execute(select(func.count()).select_from(species_q.subquery()))
    ).scalar() or 0
    species = (
        await db.execute(species_q.order_by(Species.id).offset((page - 1) * pageSize).limit(pageSize))
    ).scalars().all()
    species_items = [
        {"id": s.id, "name": s.name, "category": s.category, "summary": s.summary}
        for s in species
    ]

    return ok(
        {
            "spots": paginated(spot_items, page, pageSize, spots_total),
            "species": paginated(species_items, page, pageSize, species_total),
        }
    )
