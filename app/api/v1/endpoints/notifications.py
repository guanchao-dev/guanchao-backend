"""用户端「消息」——目前只有一种：管理员对我反馈的回复。

为什么不做成通用的通知表：现在只有这一种来源，用 feedback 上的 reply 字段就够了，
不用为一条消息再建一张表和一套写入逻辑。以后要加别的通知（比如投稿审核结果），
在这个模块里往同一个列表里拼即可，前端拿到的还是同一份结构。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.response import ok, paginated
from app.core.utils import to_shanghai_iso
from app.db.base import get_db
from app.db.models import Feedback, Spot, User
from app.schemas import ReadNotificationsRequest

router = APIRouter(tags=["notifications"])


def _replied(user_id: str):
    """「有回复」的条件。reply 是 TEXT NULL 建的列，历史行可能是 NULL，一并排除。"""
    return (
        Feedback.user_id == user_id,
        Feedback.reply.isnot(None),
        Feedback.reply != "",
    )


@router.get("/me/notifications")
async def my_notifications(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我的消息（管理员对我反馈的回复），按回复时间倒序。带未读数给小红点用。"""
    cond = _replied(user.id)

    total = (
        await db.execute(select(func.count()).select_from(Feedback).where(*cond))
    ).scalar() or 0
    unread = (
        await db.execute(
            select(func.count())
            .select_from(Feedback)
            .where(*cond, Feedback.reply_seen.is_(False))
        )
    ).scalar() or 0

    rows = (
        await db.execute(
            select(Feedback)
            .where(*cond)
            .order_by(Feedback.replied_at.desc(), Feedback.id.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()

    # 带上点位名，用户知道是哪条反馈的回复
    spot_ids = {r.spot_id for r in rows if r.spot_id}
    spots: dict[str, Spot] = {}
    if spot_ids:
        found = (await db.execute(select(Spot).where(Spot.id.in_(spot_ids)))).scalars().all()
        spots = {s.id: s for s in found}

    items = []
    for r in rows:
        spot = spots.get(r.spot_id)
        items.append(
            {
                "id": r.id,
                # 用户自己写的内容，回显在上面更像一次「对话」
                "content": r.content or "",
                "reply": r.reply or "",
                "repliedAt": r.replied_at or "",
                "spotId": r.spot_id or "",
                "spotName": spot.name if spot else "",
                "seen": bool(r.reply_seen),
                "createdAt": to_shanghai_iso(r.created_at),
            }
        )
    return ok({**paginated(items, page, pageSize, total), "unread": unread})


@router.get("/me/notifications/unread")
async def unread_count(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """未读消息数（小程序铃铛上的小红点）。"""
    n = (
        await db.execute(
            select(func.count())
            .select_from(Feedback)
            .where(*_replied(user.id), Feedback.reply_seen.is_(False))
        )
    ).scalar() or 0
    return ok({"count": n})


@router.post("/me/notifications/read")
async def mark_read(
    body: ReadNotificationsRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把消息标记为已读。传 ids 只标记这几条，不传就全部标记。"""
    q = select(Feedback).where(*_replied(user.id), Feedback.reply_seen.is_(False))
    ids = (body.ids if body else None) or []
    if ids:
        q = q.where(Feedback.id.in_(ids))
    rows = (await db.execute(q)).scalars().all()
    for r in rows:
        r.reply_seen = True
    if rows:
        await db.commit()
    return ok({"updated": len(rows)})
