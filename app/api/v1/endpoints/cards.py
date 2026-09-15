"""观潮记录与图鉴卡：生成 / 列表 / 详情 / 收藏 / 删除 / 分享。"""
import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ensure_consent, get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.response import ok, paginated
from app.core.utils import SHANGHAI_TZ, new_id, to_shanghai_iso
from app.db.base import async_session_factory, get_db
from app.db.models import Card, Checkin, Favorite, Guess, Spot, Upload, User
from app.schemas import CreateCardRequest, ShareRequest
from app.services import storage
from app.services.achievements import evaluate_medals
from app.services.ai import generate_card, generate_card_cover, primary_candidates
from app.services.tide import get_tide

router = APIRouter(tags=["cards"])

CARD_DISCLAIMER = "卡片由 AI 根据照片和潮汐生成，供学习记录使用，不是科学鉴定。"


async def _tide_line(spot_id: str | None) -> str:
    if not spot_id:
        return "潮汐数据暂缺"
    tide = await get_tide(spot_id, None)
    label = {"rising": "涨潮中", "falling": "退潮中"}.get(tide["trend"], "潮位平稳")
    return f"潮高约 {tide['currentHeightM']} 米 · {label}"


async def _record_checkin(db: AsyncSession, user: User, spot_id: str | None) -> None:
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    existing = (
        await db.execute(
            select(Checkin).where(Checkin.user_id == user.id, Checkin.checkin_date == today)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(Checkin(id=new_id("ck"), user_id=user.id, spot_id=spot_id or "", checkin_date=today))


async def _spot_name(db: AsyncSession, spot_id: str | None) -> str:
    if not spot_id:
        return ""
    spot = await db.get(Spot, spot_id)
    return spot.name if spot else ""


async def _favorited(db: AsyncSession, user: User, card_id: str) -> bool:
    fav = (
        await db.execute(
            select(Favorite).where(
                Favorite.user_id == user.id,
                Favorite.target_type == "card",
                Favorite.target_id == card_id,
            )
        )
    ).scalar_one_or_none()
    return fav is not None


def _cover_url(card: Card) -> str:
    """卡片封面图访问路径（前端拼接 BASE_URL 使用，BASE_URL 已含 /api/v1）。"""
    return f"/cards/{card.id}/image" if card.cover_key else ""


def _image_media_type(object_key: str) -> str:
    low = object_key.lower()
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        return "image/jpeg"
    if low.endswith(".webp"):
        return "image/webp"
    return "image/png"


async def _generate_cover_background(user_id: str, card_id: str, upload_object_key: str) -> None:
    """后台把用户照片转成卡通插画并回写卡片（不阻塞接口响应），失败则保留原照片。"""
    try:
        original = storage.read_bytes(upload_object_key)
        image_bytes = await generate_card_cover(original)
        if not image_bytes:
            return
        object_key = f"private/{user_id}/card/{card_id}_ai.png"
        storage.save_bytes(object_key, image_bytes)
        async with async_session_factory() as session:
            card = await session.get(Card, card_id)
            if card is not None:
                card.cover_key = object_key
                await session.commit()
    except Exception:
        return


def _ai_image_ready(card: Card) -> bool:
    return bool(card.cover_key) and card.cover_key.endswith("_ai.png")


def _card_full(card: Card, spot_name: str, favorited: bool) -> dict:
    return {
        "id": card.id,
        "status": "done",
        "shareEnabled": card.share_enabled,
        "coverUrl": _cover_url(card),
        "aiImageReady": _ai_image_ready(card),
        "spotName": spot_name,
        "observedAt": card.observed_at,
        "tideLine": card.tide_line,
        "title": card.title,
        "observations": card.observations or [],
        "safetyTip": card.safety_tip,
        "relatedSpecies": card.related_species or [],
        "disclaimer": CARD_DISCLAIMER,
        "favorited": favorited,
    }


@router.post("/cards")
async def create_card(
    body: CreateCardRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ensure_consent(user)
    upload = await db.get(Upload, body.uploadId)
    if upload is None or upload.owner_id != f"user:{user.id}":
        raise NotFoundError("上传不存在")

    guess = None
    if body.guessId:
        guess = await db.get(Guess, body.guessId)
        if guess is None or guess.user_id != user.id:
            raise NotFoundError("猜测记录不存在")

    spot_name = await _spot_name(db, body.spotId)
    tide_line = await _tide_line(body.spotId)
    image_bytes = storage.read_bytes(upload.object_key)
    # primary_candidates：新结构取「每件生物的首选」（天然是不同物种），
    # 老记录（扁平候选）原样返回，语义与改造前一致。
    guess_names = (
        [c.get("name", "") for c in primary_candidates(guess.candidates) if c.get("name")]
        if guess is not None
        else []
    )
    gen = await generate_card(image_bytes, upload.content_type, spot_name or "海边", tide_line, guess_names, body.userNote)

    related = []
    if guess is not None:
        for c in primary_candidates(guess.candidates)[:3]:
            if c.get("speciesId"):
                related.append({"speciesId": c["speciesId"], "name": c.get("name", ""), "relation": "possible"})

    observed_at = body.observedAt or to_shanghai_iso(datetime.now(SHANGHAI_TZ))
    card = Card(
        id=new_id("card"),
        user_id=user.id,
        upload_id=body.uploadId,
        spot_id=body.spotId or "",
        observed_at=observed_at,
        guess_id=body.guessId or "",
        user_note=body.userNote or "",
        title=gen["title"],
        observations=gen["observations"],
        safety_tip=gen["safetyTip"],
        tide_line=tide_line,
        related_species=related,
        cover_key=upload.object_key,
        share_enabled=False,
    )
    db.add(card)
    await _record_checkin(db, user, body.spotId)
    await db.commit()
    await db.refresh(card)

    # AI 图生图在后台进行（约 30-40 秒），把原照片转卡通，不阻塞接口响应
    asyncio.create_task(_generate_cover_background(user.id, card.id, upload.object_key))

    newly = await evaluate_medals(db, user)
    result = _card_full(card, spot_name, False)
    result["unlockedMedalIds"] = newly
    return ok(result)


@router.get("/cards")
async def list_cards(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    total = (
        await db.execute(select(Card.id).where(Card.user_id == user.id))
    ).scalars().all()
    cards = (
        await db.execute(
            select(Card)
            .where(Card.user_id == user.id)
            .order_by(Card.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()
    items = []
    for c in cards:
        spot_name = await _spot_name(db, c.spot_id or None)
        fav = await _favorited(db, user, c.id)
        items.append(
            {
                "id": c.id,
                "title": c.title,
                "coverUrl": _cover_url(c),
                "spotName": spot_name,
                "observedAt": c.observed_at,
                "favorited": fav,
            }
        )
    return ok(paginated(items, page, pageSize, len(total)))


@router.get("/cards/{card_id}")
async def card_detail(
    card_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await db.get(Card, card_id)
    if card is None:
        raise NotFoundError("图鉴卡不存在")
    if card.user_id != user.id and not card.share_enabled:
        raise ForbiddenError("无权查看该图鉴卡")
    spot_name = await _spot_name(db, card.spot_id or None)
    fav = await _favorited(db, user, card_id) if card.user_id == user.id else False
    return ok(_card_full(card, spot_name, fav))


@router.get("/cards/{card_id}/image")
async def card_image(card_id: str, db: AsyncSession = Depends(get_db)):
    """返回图鉴卡封面图（与 /uploads/{id}/content 一致，本地直读）。"""
    card = await db.get(Card, card_id)
    if card is None or not card.cover_key:
        raise NotFoundError("图鉴卡图片不存在")
    try:
        data = storage.read_bytes(card.cover_key)
    except FileNotFoundError:
        raise NotFoundError("图片文件不存在")
    return Response(content=data, media_type=_image_media_type(card.cover_key))


@router.post("/cards/{card_id}/favorite")
async def favorite_card(
    card_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await db.get(Card, card_id)
    if card is None:
        raise NotFoundError("图鉴卡不存在")
    existing = (
        await db.execute(
            select(Favorite).where(
                Favorite.user_id == user.id,
                Favorite.target_type == "card",
                Favorite.target_id == card_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(Favorite(id=new_id("fav"), user_id=user.id, target_type="card", target_id=card_id))
        await db.commit()
    return ok(None, "已收藏")


@router.delete("/cards/{card_id}/favorite")
async def unfavorite_card(
    card_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(Favorite).where(
            Favorite.user_id == user.id,
            Favorite.target_type == "card",
            Favorite.target_id == card_id,
        )
    )
    await db.commit()
    return ok(None, "已取消收藏")


@router.delete("/cards/{card_id}")
async def delete_card(
    card_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await db.get(Card, card_id)
    if card is None:
        raise NotFoundError("图鉴卡不存在")
    if card.user_id != user.id:
        raise ForbiddenError("无权删除该图鉴卡")
    await db.execute(delete(Favorite).where(Favorite.target_type == "card", Favorite.target_id == card_id))
    await db.delete(card)
    await db.commit()
    return ok(None, "已删除")


@router.post("/cards/{card_id}/share")
async def share_card(
    card_id: str,
    body: ShareRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await db.get(Card, card_id)
    if card is None:
        raise NotFoundError("图鉴卡不存在")
    if card.user_id != user.id:
        raise ForbiddenError("无权分享该图鉴卡")
    card.share_enabled = True
    await db.commit()
    return ok(
        {
            "title": f"我在追潮记留下一张「{card.title}」",
            "imageUrl": _cover_url(card),
            "path": f"/pages/home/home?cardId={card.id}",
            "copyText": "今天在海边观察潮间带，生成了一张学习卡片。一起来追潮吧！",
        }
    )
