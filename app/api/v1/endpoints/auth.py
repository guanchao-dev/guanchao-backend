import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Header, UploadFile
from fastapi.responses import Response
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.uploads import (
    ALLOWED_TYPES,
    EXT_CONTENT_TYPES,
    MAX_SIZE,
    _sniff_ext,
    as_cos_redirect,
)
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import BadRequestError, InvalidFileError, NotFoundError, UnauthorizedError
from app.core.response import ok
from app.core.security import create_access_token, create_refresh_token, hash_token
from app.core.utils import new_id
from app.db.base import get_db
from app.services import levels
from app.services.achievements import evaluate_medals
from app.db.models import (
    AquariumResident,
    AquariumSetting,
    AquariumSpeciesReward,
    Card,
    Checkin,
    CheckinSession,
    CommunityComment,
    CommunityFollow,
    CommunityNote,
    CommunityNoteFavorite,
    CommunityNoteLike,
    ExploreSession,
    Favorite,
    Feedback,
    Guess,
    LightMapLit,
    QuizAttempt,
    RefreshToken,
    Reminder,
    ReportCheckin,
    SpeciesPhoto,
    SpeciesUnlock,
    SpotVisit,
    TrashGuess,
    Upload,
    User,
    UserMedal,
    UserSpot,
    WatchRecord,
)
from app.schemas import RefreshRequest, UpdateNicknameRequest, WechatLoginRequest
from app.services import storage
from app.services.content_safety import assert_text_safe
from app.services.image import compress_image
from app.services.wechat import code2session


def _avatar_url(user: User) -> str:
    """头像访问路径（相对，前端拼 BASE_URL）。"""
    return f"/users/{user.id}/avatar" if user.avatar_key else ""

router = APIRouter(tags=["auth"])

# 没自己填昵称的用户，用「追潮者 + 编号」当默认名（如 追潮者8392）
_NICK_PREFIX = "追潮者"

# 历史上的默认名。这两个名字的用户会被补发一个带编号的名字（一次性清理见
# tools/rename_default_nicknames.py）—— 之前 15 个用户里 11 个都叫「追潮探索者」，
# 列表和排行榜上根本分不清谁是谁。
LEGACY_DEFAULT_NICKS: tuple[str, ...] = ("追潮探索者", "观潮探索者")


async def gen_nickname(db: AsyncSession) -> str:
    """给没填昵称的新用户生成默认名：`追潮者` + **不重复**的编号。

    编号必须唯一 —— 否则又回到「一堆人同名」。4 位不够用了自动加长到 5、6 位；
    每次生成都回库查一次（用户表很小，代价可以忽略）。
    """
    for digits in (4, 5, 6):
        for _ in range(20):
            cand = f"{_NICK_PREFIX}{secrets.randbelow(10 ** digits):0{digits}d}"
            hit = (
                await db.execute(select(User.id).where(User.nickname == cand).limit(1))
            ).scalar_one_or_none()
            if hit is None:
                return cand
    # 极端兜底（前面全撞了）：随机 hex，实际不可能重复
    return f"{_NICK_PREFIX}{secrets.token_hex(4)}"


def _login_user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "nickname": user.nickname,
        "avatarKey": user.avatar_key,
        "avatarUrl": _avatar_url(user),
        "level": user.level,
        "title": user.title,
        "needGuardianConsent": user.need_guardian_consent,
    }


async def _issue_tokens(db: AsyncSession, user: User) -> dict:
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token()
    expires_at = datetime.utcnow() + timedelta(seconds=settings.refresh_token_expire_seconds)
    db.add(
        RefreshToken(
            id=new_id("rt"),
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=expires_at,
        )
    )
    await db.commit()
    return {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresIn": settings.access_token_expire_seconds,
        "user": _login_user_dict(user),
    }


async def _merge_guest_data(db: AsyncSession, user_id: str, client_id: str) -> None:
    """把该设备（X-Client-Id）上的游客观潮 / 点亮进度合并进账号。幂等。"""
    if not client_id:
        return
    target = f"user:{user_id}"
    source = f"client:{client_id}"
    if source == target:
        return
    await db.execute(
        update(WatchRecord).where(WatchRecord.owner_id == source).values(owner_id=target)
    )
    await db.execute(
        update(LightMapLit).where(LightMapLit.owner_id == source).values(owner_id=target)
    )
    await db.commit()


@router.post("/auth/wechat-login")
async def wechat_login(
    body: WechatLoginRequest,
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    db: AsyncSession = Depends(get_db),
):
    session = await code2session(body.code)
    openid = session["openid"]
    user = (
        await db.execute(select(User).where(User.openid == openid))
    ).scalar_one_or_none()
    if user is None:
        # 默认名带唯一编号：用户在客户端没点「使用微信昵称」时，至少不会一堆人同名
        user = User(id=new_id("u"), openid=openid, nickname=await gen_nickname(db))
        db.add(user)
        await db.commit()
        await db.refresh(user)
    # 封禁账号：拦在发 token 之前。否则他能拿到 token，要等第一次请求才报错，
    # 前端只会显示「请求失败」，说不清原因。
    if user.banned:
        raise UnauthorizedError("账号已被封禁，如有疑问请联系管理员")
    # 登录后合并该设备上的游客数据（只合并一次，后续游客数据仍按 client 归属）
    await _merge_guest_data(db, user.id, body.clientId or x_client_id)
    # 登录即结算一次：有些成就是「登录」本身触发的（如活动期间登录）。
    # 放在发 token 之前，让返回的用户数据带上新的分数。
    await evaluate_medals(db, user)
    # 分数或去过点位可能变了，顺手把等级刷一下（只在变化时才写库）
    await levels.sync_user_level(db, user)
    return ok(await _issue_tokens(db, user))


@router.post("/auth/refresh")
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(body.refreshToken)
    rt = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if rt is None or rt.revoked or rt.expires_at < datetime.utcnow():
        raise UnauthorizedError("刷新令牌无效或已过期")
    user = await db.get(User, rt.user_id)
    if user is None:
        raise UnauthorizedError("用户不存在")
    rt.revoked = True  # 旋转：旧令牌作废
    return ok(await _issue_tokens(db, user))


@router.get("/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    checkin_count = (
        await db.execute(
            select(func.count()).select_from(Checkin).where(Checkin.user_id == user.id)
        )
    ).scalar() or 0
    # 等级 = 成就值 + 去过的点位，读的时候顺手同步一次（只在变化时才写库）。
    lv = await levels.sync_user_level(db, user)
    return ok(
        {
            "id": user.id,
            "nickname": user.nickname,
            "avatarUrl": _avatar_url(user),
            "level": lv["level"],
            "title": user.title,
            "score": user.score,
            "total": user.total,
            # 等级进度：xp 是「成就值 + 点位数 × 60」，levelProgress 是当前级内的百分比
            "xp": lv["xp"],
            "xpToNext": lv["xpToNext"],
            "levelProgress": lv["levelProgress"],
            "visitedSpotCount": lv["visitedSpotCount"],
            "stats": {
                "checkinCount": checkin_count,
            },
        }
    )


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传头像（微信头像），存本地磁盘并更新用户。"""
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise InvalidFileError("图片不能超过 5MB")
    ext = ALLOWED_TYPES.get(file.content_type or "")
    if not ext:
        ext = _sniff_ext(data) or ""
    if not ext:
        raise InvalidFileError("仅支持 jpg/png/webp")
    # 头像按 256px 压。原来跟别处一样走 1280，一个头像 57~118KB，
    # 而界面上只显示几十像素 —— 排行榜一屏 10 个就是 1MB，加载很慢。
    data, ext = compress_image(data, max_side=256)
    content_type = EXT_CONTENT_TYPES.get(ext, "image/jpeg")
    object_key = f"private/{user.id}/avatar/avatar.{ext}"
    await storage.save_bytes(object_key, data)
    user.avatar_key = object_key
    await db.commit()
    return ok({"avatarUrl": _avatar_url(user), "contentType": content_type})


@router.post("/me/nickname")
async def update_nickname(
    body: UpdateNicknameRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改昵称。"""
    nickname = body.nickname.strip()
    if not (1 <= len(nickname) <= 20):
        raise BadRequestError("昵称需 1~20 个字")
    assert_text_safe(nickname)
    user.nickname = nickname
    await db.commit()
    return ok({"nickname": nickname})


@router.get("/users/{user_id}/avatar")
async def user_avatar(user_id: str, db: AsyncSession = Depends(get_db)):
    """返回用户头像（公开，社区卡片/主页可直接 <image> 加载）。

    COS 开着就直接 302 跳直链 —— 排行榜一屏 10 个头像，走服务器转发每张
    200~700ms，直连只要 ~100ms（见 uploads.py::as_cos_redirect）。
    """
    u = await db.get(User, user_id)
    if u is None or not u.avatar_key:
        raise NotFoundError("头像不存在")
    redirect = await as_cos_redirect(u.avatar_key)
    if redirect is not None:
        return redirect
    try:
        data = await storage.read_bytes(u.avatar_key)
    except FileNotFoundError:
        raise NotFoundError("头像不存在")
    low = u.avatar_key.lower()
    media = "image/webp" if low.endswith(".webp") else (
        "image/jpeg" if low.endswith((".jpg", ".jpeg")) else "image/png"
    )
    return Response(content=data, media_type=media)


@router.post("/me/delete")
async def delete_me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """注销账号：删除该用户的全部数据（学习记录 / 照片 / 观潮记录 / 点亮进度）及磁盘文件。

    注意删除顺序：先清子表再删 users，否则会撞外键约束。
    """
    uid = user.id
    owner = f"user:{uid}"

    # 1) 先收集要删除的磁盘文件（上传图 / 图鉴照片 / 卡片封面 / 头像）
    object_keys: list[str] = []
    for key in (
        await db.execute(select(Upload.object_key).where(Upload.owner_id == owner))
    ).scalars().all():
        if key:
            object_keys.append(key)
    for key in (
        await db.execute(select(SpeciesPhoto.object_key).where(SpeciesPhoto.user_id == uid))
    ).scalars().all():
        if key:
            object_keys.append(key)
    for key in (
        await db.execute(select(Card.cover_key).where(Card.user_id == uid))
    ).scalars().all():
        if key:
            object_keys.append(key)
    if user.avatar_key:
        object_keys.append(user.avatar_key)

    # 2) 该用户的社区笔记：先清挂在笔记下的赞/藏/评论（包含他人产生的）
    note_ids = (
        await db.execute(select(CommunityNote.id).where(CommunityNote.user_id == uid))
    ).scalars().all()
    if note_ids:
        await db.execute(delete(CommunityNoteLike).where(CommunityNoteLike.note_id.in_(note_ids)))
        await db.execute(delete(CommunityNoteFavorite).where(CommunityNoteFavorite.note_id.in_(note_ids)))
        await db.execute(delete(CommunityComment).where(CommunityComment.note_id.in_(note_ids)))

    # 3) 该用户自己产生的社区互动
    await db.execute(delete(CommunityComment).where(CommunityComment.user_id == uid))
    await db.execute(delete(CommunityNoteLike).where(CommunityNoteLike.user_id == uid))
    await db.execute(delete(CommunityNoteFavorite).where(CommunityNoteFavorite.user_id == uid))
    await db.execute(
        delete(CommunityFollow).where(
            or_(CommunityFollow.follower_id == uid, CommunityFollow.followee_id == uid)
        )
    )
    await db.execute(delete(CommunityNote).where(CommunityNote.user_id == uid))

    # 4) 观潮记录 / 点亮进度 / 图鉴收集（按 owner_id 归属）
    await db.execute(delete(WatchRecord).where(WatchRecord.owner_id == owner))
    await db.execute(delete(LightMapLit).where(LightMapLit.owner_id == owner))
    await db.execute(delete(SpeciesUnlock).where(SpeciesUnlock.owner_id == owner))
    await db.execute(delete(UserSpot).where(UserSpot.owner_id == owner))
    await db.execute(delete(CheckinSession).where(CheckinSession.owner_id == owner))
    await db.execute(delete(ReportCheckin).where(ReportCheckin.owner_id == owner))
    await db.execute(delete(SpotVisit).where(SpotVisit.user_id == uid))

    # 5) 其余带 user_id 外键的表
    for model in (
        Reminder, ExploreSession, Favorite, SpeciesPhoto, Card, Guess,
        Checkin, QuizAttempt, UserMedal, Feedback, Upload, RefreshToken,
        TrashGuess, AquariumResident, AquariumSetting, AquariumSpeciesReward,
    ):
        await db.execute(delete(model).where(model.user_id == uid))

    # 6) 最后删用户本体
    await db.delete(user)
    await db.commit()

    # 7) 删磁盘文件（best-effort，失败不影响注销结果）
    for key in object_keys:
        try:
            await storage.delete_bytes(key)
        except Exception:
            pass

    return ok(None, "已注销")
