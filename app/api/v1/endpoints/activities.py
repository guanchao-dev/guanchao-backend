"""首页活动轮播（游客可读）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import ok
from app.db.base import get_db
from app.db.models import Activity

router = APIRouter(tags=["activities"])


@router.get("/activities")
async def list_activities(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Activity).order_by(Activity.sort))).scalars().all()
    items = [
        {
            "id": a.id,
            "tag": a.tag,
            "title": a.title,
            "desc": a.desc,
            "theme": a.theme,
            "image": a.image,
            "url": a.url,
            "layout": a.layout or "card",
        }
        for a in rows
    ]
    return ok({"list": items})
