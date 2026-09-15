"""社区（观察分享）：话题 / 信息流 / 搜索 / 笔记 / 点赞收藏 / 评论 / 关注 / 主页。

- 游客可读公开内容；写操作必须登录；
- 图片走现有 POST /uploads（scene=community）；
- 正文/评论过不了内容安全返回 50004；
- 未成年人发布为 reviewing，仅作者可见。
"""
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_optional_user
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.response import ok, paginated
from app.core.utils import new_id, to_shanghai_iso
from app.db.base import get_db
from app.db.models import (
    CommunityComment,
    CommunityFollow,
    CommunityNote,
    CommunityNoteFavorite,
    CommunityNoteLike,
    CommunityTopic,
    Species,
    Spot,
    Upload,
    User,
)
from app.schemas import CommunityCommentCreateRequest, CommunityNoteCreateRequest
from app.services.achievements import evaluate_medals
from app.services.content_safety import assert_text_safe

router = APIRouter(tags=["community"])

HOT_KEYWORDS = ["退潮", "赶海", "石老人", "藤壶", "潮间带", "海星", "安全", "礁石", "海藻", "寄居蟹"]


def _upload_url(upload_id: str) -> str:
    return f"/uploads/{upload_id}/content"


def _author(u: User) -> dict:
    return {"id": u.id, "nickname": u.nickname, "avatarUrl": _user_avatar_url(u)}


def _user_avatar_url(u: User) -> str:
    return f"/users/{u.id}/avatar" if u.avatar_key else ""


async def _users_map(db: AsyncSession, user_ids: set[str]) -> dict[str, User]:
    if not user_ids:
        return {}
    rows = (
        await db.execute(select(User).where(User.id.in_(user_ids)))
    ).scalars().all()
    return {u.id: u for u in rows}


async def _topics_map(db: AsyncSession) -> dict[str, CommunityTopic]:
    rows = (await db.execute(select(CommunityTopic))).scalars().all()
    return {t.id: t for t in rows}


async def _liked_set(db: AsyncSession, user_id: str, note_ids: list[str]) -> set[str]:
    if not note_ids:
        return set()
    rows = (
        await db.execute(
            select(CommunityNoteLike.note_id).where(
                CommunityNoteLike.user_id == user_id, CommunityNoteLike.note_id.in_(note_ids)
            )
        )
    ).scalars().all()
    return set(rows)


async def _faved_set(db: AsyncSession, user_id: str, note_ids: list[str]) -> set[str]:
    if not note_ids:
        return set()
    rows = (
        await db.execute(
            select(CommunityNoteFavorite.note_id).where(
                CommunityNoteFavorite.user_id == user_id,
                CommunityNoteFavorite.note_id.in_(note_ids),
            )
        )
    ).scalars().all()
    return set(rows)


def _note_card(
    note: CommunityNote,
    author: User,
    topic_objs: list[CommunityTopic],
    spot_name: str,
    liked: bool,
    favorited: bool,
) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "coverUrl": _upload_url(note.image_upload_ids[0]) if note.image_upload_ids else "",
        "coverHeight": note.cover_height or 0,
        "imageUrls": [_upload_url(uid) for uid in (note.image_upload_ids or [])],
        "topics": [{"id": t.id, "name": t.name} for t in topic_objs],
        "spotId": note.spot_id,
        "spotName": spot_name,
        "speciesId": note.species_id,
        "author": _author(author),
        "likeCount": note.like_count or 0,
        "commentCount": note.comment_count or 0,
        "favoriteCount": note.favorite_count or 0,
        "liked": liked,
        "favorited": favorited,
        "createdAt": to_shanghai_iso(note.created_at),
    }


async def _cards(db: AsyncSession, notes: list[CommunityNote], user: User | None) -> list[dict]:
    if not notes:
        return []
    user_ids = {n.user_id for n in notes}
    spot_ids = {n.spot_id for n in notes if n.spot_id}
    users = await _users_map(db, user_ids)
    topics = await _topics_map(db)
    spot_names: dict[str, str] = {}
    if spot_ids:
        spots = (await db.execute(select(Spot).where(Spot.id.in_(spot_ids)))).scalars().all()
        spot_names = {s.id: s.name for s in spots}
    note_ids = [n.id for n in notes]
    liked = await _liked_set(db, user.id, note_ids) if user else set()
    faved = await _faved_set(db, user.id, note_ids) if user else set()
    items = []
    for n in notes:
        author = users.get(n.user_id)
        if author is None:
            continue
        topic_objs = [topics[t] for t in (n.topic_ids or []) if t in topics]
        items.append(
            _note_card(n, author, topic_objs, spot_names.get(n.spot_id, ""), n.id in liked, n.id in faved)
        )
    return items


COMMUNITY_DISCLAIMER = "这是观察分享，不是物种鉴定。请对照图鉴，不要采集生物。"


async def _note_detail(db: AsyncSession, note: CommunityNote, user: User | None) -> dict:
    cards = await _cards(db, [note], user)
    if not cards:
        raise NotFoundError("笔记不存在")
    card = cards[0]
    is_mine = user is not None and note.user_id == user.id

    author = dict(card["author"])
    author_user = await db.get(User, note.user_id)
    if author_user is not None:
        author["level"] = author_user.level
        author["title"] = author_user.title
    author["me"] = is_mine

    spot = None
    if note.spot_id:
        s = await db.get(Spot, note.spot_id)
        if s is not None:
            spot = {"id": s.id, "name": s.name, "city": s.city}
    species = None
    if note.species_id:
        sp = await db.get(Species, note.species_id)
        if sp is not None:
            species = {"id": sp.id, "name": sp.name}

    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "coverUrl": card["coverUrl"],
        "coverHeight": card["coverHeight"],
        "imageUrls": card["imageUrls"],
        "topics": card["topics"],
        "spot": spot,
        "species": species,
        "author": author,
        "likeCount": card["likeCount"],
        "commentCount": card["commentCount"],
        "favoriteCount": card["favoriteCount"],
        "liked": card["liked"],
        "favorited": card["favorited"],
        "visibility": note.visibility,
        "createdAt": card["createdAt"],
        "disclaimer": COMMUNITY_DISCLAIMER,
        "mine": is_mine,
    }


async def _topic_counts(db: AsyncSession) -> dict[str, int]:
    rows = (
        await db.execute(
            select(CommunityNote.topic_ids).where(CommunityNote.visibility == "public")
        )
    ).scalars().all()
    counts: dict[str, int] = {}
    for tids in rows:
        for t in (tids or []):
            counts[t] = counts.get(t, 0) + 1
    return counts


async def _get_like(db: AsyncSession, user_id: str, note_id: str):
    return (
        await db.execute(
            select(CommunityNoteLike).where(
                CommunityNoteLike.user_id == user_id, CommunityNoteLike.note_id == note_id
            )
        )
    ).scalar_one_or_none()


async def _get_fav(db: AsyncSession, user_id: str, note_id: str):
    return (
        await db.execute(
            select(CommunityNoteFavorite).where(
                CommunityNoteFavorite.user_id == user_id, CommunityNoteFavorite.note_id == note_id
            )
        )
    ).scalar_one_or_none()


@router.get("/community/topics")
async def list_topics(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CommunityTopic).order_by(CommunityTopic.sort))).scalars().all()
    counts = await _topic_counts(db)
    items = [{"id": t.id, "name": t.name, "noteCount": counts.get(t.id, 0)} for t in rows]
    return ok({"list": items})


@router.get("/community/feed")
async def feed(
    tab: str = Query("recommend"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(CommunityNote).where(CommunityNote.visibility == "public")
    if tab == "following":
        if user is None:
            return ok(paginated([], page, pageSize, 0))
        followee_ids = select(CommunityFollow.followee_id).where(
            CommunityFollow.follower_id == user.id
        )
        q = q.where(CommunityNote.user_id.in_(followee_ids))
    if tab == "latest":
        q = q.order_by(CommunityNote.created_at.desc())
    else:
        q = q.order_by(CommunityNote.like_count.desc(), CommunityNote.created_at.desc())
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    notes = (
        await db.execute(q.offset((page - 1) * pageSize).limit(pageSize))
    ).scalars().all()
    items = await _cards(db, notes, user)
    return ok(paginated(items, page, pageSize, total))


@router.get("/community/search/hot")
async def search_hot():
    items = [{"keyword": k, "heat": 1000 - i * 37} for i, k in enumerate(HOT_KEYWORDS)]
    return ok({"list": items})


@router.get("/community/search/suggest")
async def search_suggest(keyword: str = Query("")):
    kw = keyword.strip()
    base = HOT_KEYWORDS if kw else HOT_KEYWORDS[:5]
    items = [{"keyword": k} for k in base if kw in k]
    return ok({"list": items})


@router.get("/community/search")
async def search(
    keyword: str = Query(""),
    type: str = Query("all"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    kw = keyword.strip()

    notes_q = select(CommunityNote).where(CommunityNote.visibility == "public")
    if kw:
        notes_q = notes_q.where(
            or_(CommunityNote.title.contains(kw), CommunityNote.content.contains(kw))
        )
    notes_total = (
        await db.execute(select(func.count()).select_from(notes_q.subquery()))
    ).scalar() or 0
    notes = (
        await db.execute(
            notes_q.order_by(CommunityNote.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()
    note_items = await _cards(db, notes, user)

    users_q = select(User)
    if kw:
        users_q = users_q.where(User.nickname.contains(kw))
    user_rows = (await db.execute(users_q.limit(20))).scalars().all()
    user_items = [
        {"id": u.id, "nickname": u.nickname, "avatarUrl": _user_avatar_url(u), "level": u.level, "title": u.title}
        for u in user_rows
    ]

    topics_q = select(CommunityTopic).order_by(CommunityTopic.sort)
    if kw:
        topics_q = topics_q.where(CommunityTopic.name.contains(kw))
    topic_rows = (await db.execute(topics_q)).scalars().all()
    counts = await _topic_counts(db)
    topic_items = [{"id": t.id, "name": t.name, "noteCount": counts.get(t.id, 0)} for t in topic_rows]

    return ok(
        {
            "notes": paginated(note_items, page, pageSize, notes_total),
            "users": {"list": user_items},
            "topics": {"list": topic_items},
        }
    )


@router.get("/community/notes/{note_id}")
async def note_detail(
    note_id: str,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    note = await db.get(CommunityNote, note_id)
    if note is None:
        raise NotFoundError("笔记不存在")
    if note.visibility != "public" and not (user is not None and note.user_id == user.id):
        raise NotFoundError("笔记不存在")
    return ok(await _note_detail(db, note, user))


def _cover_height_estimate(note_id: str, upload: Upload | None) -> int:
    if upload is not None and upload.height:
        return upload.height
    h = int(note_id[-4:], 16) if note_id else 0
    return 240 + (h % 160)  # 240~399，给瀑布流分列用


@router.post("/community/notes")
async def create_note(
    body: CommunityNoteCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not (1 <= len(body.uploadIds) <= 9):
        raise BadRequestError("图片数量须为 1~9 张")
    title = body.title.strip()
    content = body.content.strip()
    if not (4 <= len(title) <= 30):
        raise BadRequestError("标题须 4~30 字")
    if not (10 <= len(content) <= 1000):
        raise BadRequestError("正文须 10~1000 字")
    topic_ids = body.topicIds or []
    if len(topic_ids) > 3:
        raise BadRequestError("话题最多 3 个")
    assert_text_safe(title, content)

    if idempotency_key:
        existing = (
            await db.execute(
                select(CommunityNote).where(
                    CommunityNote.user_id == user.id,
                    CommunityNote.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return ok(await _note_detail(db, existing, user))

    # 校验上传图归属且已 approved
    uploads = []
    for uid in body.uploadIds:
        up = await db.get(Upload, uid)
        if up is None or up.user_id != user.id:
            raise NotFoundError("上传不存在")
        if up.status != "approved":
            raise BadRequestError("图片尚未审核通过")
        uploads.append(up)

    # 校验话题 / 点位 / 图鉴（可选字段须已存在）
    if topic_ids:
        topics = await _topics_map(db)
        for tid in topic_ids:
            if tid not in topics:
                raise BadRequestError(f"话题不存在：{tid}")
    if body.spotId:
        if await db.get(Spot, body.spotId) is None:
            raise BadRequestError("点位不存在")
    if body.speciesId:
        if await db.get(Species, body.speciesId) is None:
            raise BadRequestError("图鉴条目不存在")

    visibility = body.visibility if body.visibility in ("public", "private") else "public"
    if user.need_guardian_consent:
        visibility = "reviewing"

    note_id = new_id("note")
    note = CommunityNote(
        id=note_id,
        user_id=user.id,
        title=title,
        content=content,
        topic_ids=topic_ids,
        spot_id=body.spotId or "",
        species_id=body.speciesId or "",
        image_upload_ids=body.uploadIds,
        cover_height=_cover_height_estimate(note_id, uploads[0] if uploads else None),
        visibility=visibility,
        idempotency_key=idempotency_key or "",
    )
    db.add(note)
    await db.commit()

    newly = await evaluate_medals(db, user)
    detail = await _note_detail(db, note, user)
    detail["unlockedMedalIds"] = newly
    return ok(detail)


def _like_fav_state(note: CommunityNote, liked: bool, favorited: bool) -> dict:
    return {
        "liked": liked,
        "favorited": favorited,
        "likeCount": note.like_count or 0,
        "favoriteCount": note.favorite_count or 0,
    }


@router.post("/community/notes/{note_id}/like")
async def like_note(
    note_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await db.get(CommunityNote, note_id)
    if note is None:
        raise NotFoundError("笔记不存在")
    existing = await _get_like(db, user.id, note_id)
    if existing is None:
        db.add(CommunityNoteLike(id=new_id("nlike"), user_id=user.id, note_id=note_id))
        note.like_count = (note.like_count or 0) + 1
        await db.commit()
    favorited = await _get_fav(db, user.id, note_id) is not None
    return ok(_like_fav_state(note, True, favorited))


@router.delete("/community/notes/{note_id}/like")
async def unlike_note(
    note_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await db.get(CommunityNote, note_id)
    if note is None:
        raise NotFoundError("笔记不存在")
    existing = await _get_like(db, user.id, note_id)
    if existing is not None:
        await db.delete(existing)
        note.like_count = max(0, (note.like_count or 0) - 1)
        await db.commit()
    favorited = await _get_fav(db, user.id, note_id) is not None
    return ok(_like_fav_state(note, False, favorited))


@router.post("/community/notes/{note_id}/favorite")
async def favorite_note(
    note_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await db.get(CommunityNote, note_id)
    if note is None:
        raise NotFoundError("笔记不存在")
    existing = await _get_fav(db, user.id, note_id)
    if existing is None:
        db.add(CommunityNoteFavorite(id=new_id("nfav"), user_id=user.id, note_id=note_id))
        note.favorite_count = (note.favorite_count or 0) + 1
        await db.commit()
    liked = await _get_like(db, user.id, note_id) is not None
    return ok(_like_fav_state(note, liked, True))


@router.delete("/community/notes/{note_id}/favorite")
async def unfavorite_note(
    note_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await db.get(CommunityNote, note_id)
    if note is None:
        raise NotFoundError("笔记不存在")
    existing = await _get_fav(db, user.id, note_id)
    if existing is not None:
        await db.delete(existing)
        note.favorite_count = max(0, (note.favorite_count or 0) - 1)
        await db.commit()
    liked = await _get_like(db, user.id, note_id) is not None
    return ok(_like_fav_state(note, liked, False))


@router.get("/community/notes/{note_id}/comments")
async def list_comments(
    note_id: str,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    note = await db.get(CommunityNote, note_id)
    if note is None:
        raise NotFoundError("笔记不存在")
    total = (
        await db.execute(
            select(func.count()).select_from(CommunityComment).where(CommunityComment.note_id == note_id)
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            select(CommunityComment)
            .where(CommunityComment.note_id == note_id)
            .order_by(CommunityComment.created_at.asc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()
    users = await _users_map(db, {c.user_id for c in rows})
    items = []
    for c in rows:
        author = users.get(c.user_id)
        items.append(
            {
                "id": c.id,
                "content": c.content,
                "createdAt": to_shanghai_iso(c.created_at),
                "author": _author(author) if author else None,
                "replyTo": {"id": c.reply_to_id, "nickname": c.reply_to_nickname}
                if c.reply_to_id
                else None,
            }
        )
    return ok(paginated(items, page, pageSize, total))


@router.post("/community/notes/{note_id}/comments")
async def create_comment(
    note_id: str,
    body: CommunityCommentCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await db.get(CommunityNote, note_id)
    if note is None:
        raise NotFoundError("笔记不存在")
    content = body.content.strip()
    if not (2 <= len(content) <= 200):
        raise BadRequestError("评论须 2~200 字")
    assert_text_safe(content)

    reply_to_id = ""
    reply_to_nickname = ""
    if body.replyToId:
        reply_to = await db.get(CommunityComment, body.replyToId)
        if reply_to is None or reply_to.note_id != note_id:
            raise BadRequestError("回复的评论不存在")
        reply_to_id = reply_to.id
        reply_user = await db.get(User, reply_to.user_id)
        reply_to_nickname = reply_user.nickname if reply_user else ""

    cmt = CommunityComment(
        id=new_id("cmt"),
        note_id=note_id,
        user_id=user.id,
        content=content,
        reply_to_id=reply_to_id,
        reply_to_nickname=reply_to_nickname,
    )
    db.add(cmt)
    note.comment_count = (note.comment_count or 0) + 1
    await db.commit()
    return ok(
        {
            "id": cmt.id,
            "content": cmt.content,
            "createdAt": to_shanghai_iso(cmt.created_at),
            "author": _author(user),
            "replyTo": {"id": reply_to_id, "nickname": reply_to_nickname} if reply_to_id else None,
        }
    )


@router.delete("/community/comments/{comment_id}")
async def delete_comment(
    comment_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cmt = await db.get(CommunityComment, comment_id)
    if cmt is None:
        raise NotFoundError("评论不存在")
    if cmt.user_id != user.id:
        raise ForbiddenError("无权删除该评论")
    note = await db.get(CommunityNote, cmt.note_id)
    await db.delete(cmt)
    if note is not None:
        note.comment_count = max(0, (note.comment_count or 0) - 1)
    await db.commit()
    return ok(None, "已删除")


@router.delete("/community/notes/{note_id}")
async def delete_note(
    note_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await db.get(CommunityNote, note_id)
    if note is None:
        raise NotFoundError("笔记不存在")
    if note.user_id != user.id:
        raise ForbiddenError("无权删除该笔记")
    await db.execute(delete(CommunityNoteLike).where(CommunityNoteLike.note_id == note_id))
    await db.execute(delete(CommunityNoteFavorite).where(CommunityNoteFavorite.note_id == note_id))
    await db.execute(delete(CommunityComment).where(CommunityComment.note_id == note_id))
    await db.delete(note)
    await db.commit()
    return ok(None, "已删除")


async def _follower_count(db: AsyncSession, user_id: str) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(CommunityFollow).where(CommunityFollow.followee_id == user_id)
        )
    ).scalar() or 0


@router.post("/community/users/{user_id}/follow")
async def follow_user(
    user_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user_id == user.id:
        raise BadRequestError("不能关注自己")
    target = await db.get(User, user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    existing = (
        await db.execute(
            select(CommunityFollow).where(
                CommunityFollow.follower_id == user.id, CommunityFollow.followee_id == user_id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(CommunityFollow(id=new_id("flw"), follower_id=user.id, followee_id=user_id))
        await db.commit()
    return ok({"followed": True, "followerCount": await _follower_count(db, user_id)})


@router.delete("/community/users/{user_id}/follow")
async def unfollow_user(
    user_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    existing = (
        await db.execute(
            select(CommunityFollow).where(
                CommunityFollow.follower_id == user.id, CommunityFollow.followee_id == user_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.commit()
    return ok({"followed": False, "followerCount": await _follower_count(db, user_id)})


@router.get("/community/users/{user_id}")
async def user_profile(
    user_id: str,
    tab: str = Query("notes"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    if user_id == "me":
        if user is None:
            raise UnauthorizedError("未登录")
        target = user
    else:
        target = await db.get(User, user_id)
        if target is None:
            raise NotFoundError("用户不存在")
    me = user is not None and user.id == target.id

    notes_q = select(CommunityNote).where(CommunityNote.user_id == target.id)
    if not me:
        notes_q = notes_q.where(CommunityNote.visibility == "public")
    total = (await db.execute(select(func.count()).select_from(notes_q.subquery()))).scalar() or 0
    notes = (
        await db.execute(
            notes_q.order_by(CommunityNote.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()
    items = await _cards(db, notes, user)

    like_total = (
        await db.execute(
            select(func.coalesce(func.sum(CommunityNote.like_count), 0)).where(
                CommunityNote.user_id == target.id
            )
        )
    ).scalar() or 0
    fav_total = (
        await db.execute(
            select(func.coalesce(func.sum(CommunityNote.favorite_count), 0)).where(
                CommunityNote.user_id == target.id
            )
        )
    ).scalar() or 0
    follower_count = await _follower_count(db, target.id)
    following_count = (
        await db.execute(
            select(func.count()).select_from(CommunityFollow).where(CommunityFollow.follower_id == target.id)
        )
    ).scalar() or 0
    followed = False
    if user is not None and not me:
        followed = (
            await db.execute(
                select(CommunityFollow).where(
                    CommunityFollow.follower_id == user.id,
                    CommunityFollow.followee_id == target.id,
                )
            )
        ).scalar_one_or_none() is not None

    profile = {
        "id": target.id,
        "nickname": target.nickname,
        "avatarUrl": _user_avatar_url(target),
        "level": target.level,
        "title": target.title,
        "noteCount": total,
        "likeCount": like_total,
        "favoriteCount": fav_total,
        "followerCount": follower_count,
        "followingCount": following_count,
        "followed": followed,
        "me": me,
    }
    return ok({"user": profile, "notes": paginated(items, page, pageSize, total)})


@router.get("/community/me/notes")
async def my_notes(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(CommunityNote).where(CommunityNote.user_id == user.id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    notes = (
        await db.execute(
            q.order_by(CommunityNote.created_at.desc()).offset((page - 1) * pageSize).limit(pageSize)
        )
    ).scalars().all()
    items = await _cards(db, notes, user)
    return ok(paginated(items, page, pageSize, total))


@router.get("/community/me/favorites")
async def my_favorites(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    fav_q = select(CommunityNoteFavorite).where(CommunityNoteFavorite.user_id == user.id)
    total = (await db.execute(select(func.count()).select_from(fav_q.subquery()))).scalar() or 0
    favs = (
        await db.execute(
            fav_q.order_by(CommunityNoteFavorite.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()
    note_ids = [f.note_id for f in favs]
    notes = []
    if note_ids:
        notes = (
            await db.execute(select(CommunityNote).where(CommunityNote.id.in_(note_ids)))
        ).scalars().all()
    order = {nid: i for i, nid in enumerate(note_ids)}
    notes.sort(key=lambda n: order.get(n.id, 0))
    items = await _cards(db, notes, user)
    return ok(paginated(items, page, pageSize, total))
