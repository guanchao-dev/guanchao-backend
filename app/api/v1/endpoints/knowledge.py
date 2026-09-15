"""科普知识：列表 / 详情（游客可读）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.response import ok
from app.db.base import get_db
from app.db.models import Knowledge

router = APIRouter(tags=["knowledge"])


@router.get("/knowledge")
async def list_knowledge(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Knowledge).order_by(Knowledge.sort))).scalars().all()
    items = [
        {"id": k.id, "title": k.title, "summary": k.summary, "tags": k.tags or []}
        for k in rows
    ]
    return ok({"list": items})


@router.get("/knowledge/{knowledge_id}")
async def knowledge_detail(knowledge_id: str, db: AsyncSession = Depends(get_db)):
    k = await db.get(Knowledge, knowledge_id)
    if k is None:
        raise NotFoundError("科普文章不存在")
    return ok(
        {
            "id": k.id,
            "title": k.title,
            "summary": k.summary,
            "body": k.body,
            "tags": k.tags or [],
        }
    )
