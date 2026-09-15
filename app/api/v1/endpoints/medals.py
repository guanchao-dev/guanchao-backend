from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_optional_user
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.response import ok, paginated
from app.core.utils import new_id, to_shanghai_iso
from app.db.base import get_db
from app.db.models import CommunityFollow, Medal, User, UserMedal
from app.schemas import ShareRequest, UnlockAckRequest

router = APIRouter(tags=["achievements"])


async def _unlocked_map(db: AsyncSession, user: User | None) -> dict[str, object]:
    if user is None:
        return {}
    rows = (
        await db.execute(select(UserMedal).where(UserMedal.user_id == user.id))
    ).scalars().all()
    return {m.medal_id: m.unlocked_at for m in rows}


@router.get("/achievements/overview")
async def overview(user: User | None = Depends(get_optional_user), db: AsyncSession = Depends(get_db)):
    medals = (await db.execute(select(Medal))).scalars().all()
    medal_total = len(medals)
    total_score = sum(
        int(m.rewards.get("score") or 0) if isinstance(m.rewards, dict) else 0 for m in medals
    )
    unlocked = await _unlocked_map(db, user)
    if user is None:
        return ok(
            {
                "score": 0,
                "total": total_score,
                "percent": 0,
                "unlockedCount": 0,
                "medalTotal": medal_total,
                "level": 1,
                "title": "海洋探索家",
                "levelProgress": 0,
            }
        )
    percent = round(user.score * 100 / total_score) if total_score else 0
    return ok(
        {
            "score": user.score,
            "total": total_score,
            "percent": percent,
            "unlockedCount": len(unlocked),
            "medalTotal": medal_total,
            "level": user.level,
            "title": user.title,
            "levelProgress": percent,
        }
    )


@router.get("/achievements/pending-unlocks")
async def pending_unlocks(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回已解锁但尚未确认（未弹过祝贺）的勋章；没有则空列表，不 404。"""
    rows = (
        await db.execute(
            select(UserMedal).where(UserMedal.user_id == user.id, UserMedal.acked == False)  # noqa: E712
        )
    ).scalars().all()
    medals = {m.id: m for m in (await db.execute(select(Medal))).scalars().all()}
    items = []
    for um in rows:
        m = medals.get(um.medal_id)
        if m is None:
            continue
        items.append(
            {
                "medalId": um.medal_id,
                "title": m.title,
                "displayTitle": m.display_title,
                "description": m.description,
                "iconUrl": m.icon_key,
                "rarity": m.rarity,
                "source": um.source or "other",
                "unlockedAt": to_shanghai_iso(um.unlocked_at),
            }
        )
    return ok({"list": items})


@router.post("/medals/{medal_id}/unlock-ack")
async def unlock_ack(
    medal_id: str,
    body: UnlockAckRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """确认「已看到祝贺弹窗」。重复 ack 返回当前状态，不重复发奖。"""
    m = await db.get(Medal, medal_id)
    if m is None:
        raise NotFoundError("勋章不存在")
    um = (
        await db.execute(
            select(UserMedal).where(UserMedal.user_id == user.id, UserMedal.medal_id == medal_id)
        )
    ).scalar_one_or_none()
    if um is None:
        raise NotFoundError("尚未解锁该勋章")
    already_acked = um.acked
    um.acked = True
    if body.source:
        um.source = body.source
    await db.commit()
    return ok(
        {
            "medalId": medal_id,
            "acked": True,
            "alreadyAcked": already_acked,
            "rewards": m.rewards or {},
        }
    )


@router.get("/medals")
async def list_medals(
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    medals = (await db.execute(select(Medal).order_by(Medal.sort))).scalars().all()
    unlocked = await _unlocked_map(db, user)
    items = [
        {
            "id": m.id,
            "title": m.title,
            "rarity": m.rarity,
            "iconUrl": m.icon_key,
            "locked": m.id not in unlocked,
            "unlockedAt": to_shanghai_iso(unlocked.get(m.id)),
        }
        for m in medals
    ]
    return ok({"list": items})


@router.get("/medals/{medal_id}")
async def medal_detail(
    medal_id: str,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    m = await db.get(Medal, medal_id)
    if m is None:
        raise NotFoundError("勋章不存在")
    unlocked = await _unlocked_map(db, user)
    is_unlocked = medal_id in unlocked
    requirements = [
        {"text": r.get("text", ""), "done": is_unlocked} for r in (m.requirements or [])
    ]
    return ok(
        {
            "id": m.id,
            "title": m.title,
            "displayTitle": m.display_title,
            "rarity": m.rarity,
            "iconUrl": m.icon_key,
            "locked": not is_unlocked,
            "description": m.description,
            "requirements": requirements,
            "rewards": m.rewards or {},
        }
    )


@router.post("/medals/{medal_id}/share")
async def share_medal(
    medal_id: str,
    body: ShareRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    m = await db.get(Medal, medal_id)
    if m is None:
        raise NotFoundError("勋章不存在")
    return ok(
        {
            "title": f"我在追潮记点亮了「{m.display_title}」勋章",
            "imageUrl": m.icon_key,
            "path": f"/pages/achieve/achieve?medalId={m.id}",
            "copyText": f"我在追潮记点亮了「{m.display_title}」勋章，一起来探索海岸吧！",
        }
    )


async def _follower_count(db: AsyncSession, user_id: str) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(CommunityFollow)
            .where(CommunityFollow.followee_id == user_id)
        )
    ).scalar() or 0


@router.post("/users/{user_id}/follow")
async def follow_user(
    user_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """关注某个用户（用于「好友榜」）。重复关注幂等，不报 409。"""
    if user_id == user.id:
        raise BadRequestError("不能关注自己")
    target = await db.get(User, user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    existing = (
        await db.execute(
            select(CommunityFollow).where(
                CommunityFollow.follower_id == user.id,
                CommunityFollow.followee_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(CommunityFollow(id=new_id("flw"), follower_id=user.id, followee_id=user_id))
        await db.commit()
    return ok({"followed": True, "followerCount": await _follower_count(db, user_id)})


@router.delete("/users/{user_id}/follow")
async def unfollow_user(
    user_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消关注。未关注时也返回成功，不报 404。"""
    target = await db.get(User, user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    await db.execute(
        delete(CommunityFollow).where(
            CommunityFollow.follower_id == user.id,
            CommunityFollow.followee_id == user_id,
        )
    )
    await db.commit()
    return ok({"followed": False, "followerCount": await _follower_count(db, user_id)})


@router.get("/achievements/friends")
async def friends(user: User = Depends(get_current_user)):
    # 首版无社交关系，仅返回自己，不公开全站排行。
    return ok(
        {
            "list": [
                {
                    "userId": user.id,
                    "nickname": user.nickname,
                    "avatarUrl": f"/users/{user.id}/avatar" if user.avatar_key else "",
                    "level": user.level,
                    "score": user.score,
                    "me": True,
                }
            ]
        }
    )


@router.get("/achievements/leaderboard")
async def leaderboard(
    scope: str = Query("all"),  # all=全站榜 | friends=我关注的人
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    limit: int | None = Query(None, ge=1, le=100),  # 兼容旧调用：只取前 N 名
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """排行榜。

    - scope=all（默认）：全站用户，按积分倒序，分页。
    - scope=friends：当前用户关注的人。未登录或没关注过时返回空列表，不报错。
    - 每行带 followed：当前用户是否已关注该用户。
    """
    size = limit or pageSize
    if limit:
        page = 1

    q = select(User)
    if scope == "friends":
        if user is None:
            return ok(paginated([], page, size, 0))
        followee_ids = list(
            (
                await db.execute(
                    select(CommunityFollow.followee_id).where(
                        CommunityFollow.follower_id == user.id
                    )
                )
            ).scalars().all()
        )
        # 好友榜里包含「我自己」，方便和好友对比
        q = q.where(User.id.in_(list({user.id, *followee_ids})))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (
        await db.execute(
            q.order_by(User.score.desc(), User.id)
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()

    # 当前用户已关注的人（用于每行的 followed 标记）
    followed_ids: set[str] = set()
    if user is not None and rows:
        followed_ids = set(
            (
                await db.execute(
                    select(CommunityFollow.followee_id).where(
                        CommunityFollow.follower_id == user.id,
                        CommunityFollow.followee_id.in_([u.id for u in rows]),
                    )
                )
            ).scalars().all()
        )

    items = []
    for i, u in enumerate(rows):
        items.append(
            {
                "rank": (page - 1) * size + i + 1,
                "userId": u.id,
                "nickname": u.nickname,
                "avatarUrl": f"/users/{u.id}/avatar" if u.avatar_key else "",
                "level": u.level,
                "score": u.score,
                "me": user is not None and u.id == user.id,
                "followed": u.id in followed_ids,
            }
        )
    return ok(paginated(items, page, size, total))
