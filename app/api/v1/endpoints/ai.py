from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ensure_consent, get_identity
from app.core.exceptions import NotFoundError
from app.core.response import ok
from app.core.utils import SHANGHAI_TZ, new_id
from app.db.base import get_db
from app.db.models import Guess, Species, Spot, TrashGuess, Upload
from app.schemas import (
    GuessFeedbackRequest,
    SpeciesGuessRequest,
    TideAdviceRequest,
    TrashGuessRequest,
)
from app.services import storage
from app.services.achievements import evaluate_medals
from app.services.ai import primary_candidates, stored_items
from app.services.ai import species_guess as ai_species_guess
from app.services.ai import trash_guess as ai_trash_guess
from app.services.ai import tide_advice
from app.services import weather as weather_svc
from app.services.tide import build_beachcombing_hint, get_tide

router = APIRouter(tags=["ai"])

GUESS_DISCLAIMER = "这是学习提示，不是物种鉴定。请对照图鉴，不要采集、不要伤害生物。"

# 距已知沿海点位多远内算「在海边」（公里）
_COASTAL_RADIUS_KM = 100


def _distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


async def _is_coastal(
    db: AsyncSession, lat: float | None, lng: float | None, spot: Spot | None
) -> bool:
    """判断用户是否在海边。

    优先用前端传来的定位；没传就用点位自身的坐标。都没有则默认按海边处理
    （这是赶海小程序，绝大多数使用场景在海边）。
    """
    if lat is None or lng is None:
        if spot is not None and spot.lat is not None and spot.lng is not None:
            return True  # 选了沿海点位，按海边算
        return True

    spots = (await db.execute(select(Spot))).scalars().all()
    for s in spots:
        if s.lat is None or s.lng is None:
            continue
        if _distance_km(lat, lng, s.lat, s.lng) <= _COASTAL_RADIUS_KM:
            return True
    return False


async def _discard_upload(db: AsyncSession, upload: Upload) -> None:
    """识别完就把用户拍的照片删掉 —— 这类图不该存。

    用户拍照是来「问一下这是什么」的，不是来存图的：留着既占存储、也涉及隐私。
    Guess 只存 upload_id 这个文本，没有任何地方会回读那张图（响应里也没有图片字段），
    所以文件删掉、uploads 行删掉都不影响历史记录。
    """
    key = upload.object_key
    # 先删文件（delete_bytes 是 best-effort，不会抛），再删记录 ——
    # 顺序反了的话万一删文件失败就留下一行指向空文件的孤儿记录。
    if key:
        await storage.delete_bytes(key)
    await db.delete(upload)
    await db.commit()


def _guess_response(guess: Guess) -> dict:
    # items 每项 = 照片里一个不同的生物，各自带自己的候选列表；
    # 老记录存的是扁平候选，stored_items 会把它包成「单物品」。
    items = stored_items(guess.candidates)
    # 没有物品 = AI 判定照片里不是潮间带/海洋生物（不硬凑物种）
    is_sea = len(items) > 0
    obj = guess.object_name or ""
    if is_sea:
        message = ""
    else:
        message = f"这看起来是「{obj}」，不是常见的赶海生物。" if obj else "这不是常见的赶海生物。"
    return {
        "guessId": guess.id,
        "status": guess.status,
        "disclaimer": guess.disclaimer or GUESS_DISCLAIMER,
        "needHumanCheck": True,
        "isSeaCreature": is_sea,
        "objectName": obj,
        "message": message,
        "items": items,
        # 兼容层：老前端 / tools_ai_test 仍读这个字段。新结构下它是「每件物品的代表候选」，
        # 老记录读回时与改造前逐字节一致。
        "candidates": primary_candidates(guess.candidates),
        "observeTip": guess.observe_tip,
        "safetyTip": guess.safety_tip,
        "unlockedMedalIds": [],
    }


async def _nearest_spot(db: AsyncSession, lat: float, lng: float, pool: int = 5) -> Spot | None:
    """就近推荐：在离用户最近的 pool 个点位里，挑热度最高的那个。

    只推最近的一个，经常推到一个冷门点；只推最热的，又可能离得很远。
    取「最近的几个」再按热度选，兼顾近和值得去。
    """
    rows = (await db.execute(select(Spot))).scalars().all()
    cand = []
    for s in rows:
        if s.lat is None or s.lng is None:
            continue
        cand.append((_distance_km(lat, lng, s.lat, s.lng), s))
    if not cand:
        return None
    cand.sort(key=lambda x: x[0])
    near = cand[:pool]
    near.sort(key=lambda x: (x[1].heat or 0), reverse=True)
    return near[0][1]


def _distance_text(km: float | None) -> str:
    if km is None:
        return ""
    return f"{km:.1f} 公里" if km >= 1 else f"{int(km * 1000)} 米"


@router.post("/ai/tide-advice")
async def ai_tide_advice(body: TideAdviceRequest, db: AsyncSession = Depends(get_db)):
    """出行建议。

    前端传「坐标 + 日期」，返回三样：推荐时间、离场时间、一个推荐地点。
    不适合赶海时 suitableNow 为 false，那三样都为 null。
    """
    # 定位：优先按坐标就近推荐；没给坐标就退回按点位 id（兼容老调用）
    if body.lat is not None and body.lng is not None:
        spot = await _nearest_spot(db, body.lat, body.lng)
    else:
        spot = await db.get(Spot, body.spotId) if body.spotId else None
    if spot is None:
        raise NotFoundError("点位不存在")

    tide = await get_tide(spot.id, body.date, lat=spot.lat, lng=spot.lng)
    weather = await weather_svc.current(spot.lat, spot.lng)
    now = datetime.now(SHANGHAI_TZ)
    spot_info = {
        "name": spot.name,
        "city": spot.city,
        "age_hint": spot.age_hint,
        "safety_tags": spot.safety_tags,
    }
    advice = await tide_advice(tide, weather, spot_info, now)

    # 补上「推荐时间 / 离场时间 / 推荐地点」这三样。
    # bestWindow 与 leaveBefore 潮汐服务里已经算好了（低潮前后最合适），
    # 这里只是把它们摘出来给前端，不重新计算。
    suitable = advice.get("suitableNow") is True
    win = (build_beachcombing_hint(tide, now).get("bestWindow") or {}) if suitable else {}
    km = (
        _distance_km(body.lat, body.lng, spot.lat, spot.lng)
        if body.lat is not None and body.lng is not None and spot.lat is not None and spot.lng is not None
        else None
    )
    advice["bestTimeFrom"] = win.get("start") if suitable else None
    advice["bestTimeTo"] = win.get("end") if suitable else None
    advice["recommendedSpot"] = (
        {
            "id": spot.id,
            "name": spot.name,
            "district": spot.district or "",
            "heat": spot.heat or 0,
            "distanceM": int(km * 1000) if km is not None else None,
            "distanceText": _distance_text(km),
        }
        if suitable
        else None
    )
    return ok(advice)


@router.post("/ai/species-guess")
async def create_species_guess(
    body: SpeciesGuessRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    user, owner_id = identity
    if user is not None:
        ensure_consent(user)
    upload = await db.get(Upload, body.uploadId)
    if upload is None or upload.owner_id != owner_id:
        raise NotFoundError("上传不存在")
    spot = await db.get(Spot, body.spotId) if body.spotId else None
    # 图鉴名录：让 AI 对照参考，不符合就别硬套
    species = (await db.execute(select(Species).order_by(Species.id))).scalars().all()
    species_list = [
        {"id": s.id, "name": s.name, "aka": s.aka or [], "hint": s.look or s.summary}
        for s in species
    ]
    # 地理限制：按用户定位判断是否在海边，过滤掉当地不可能出现的物种
    coastal = await _is_coastal(db, body.lat, body.lng, spot)
    image_bytes = await storage.read_bytes(upload.object_key)

    result = await ai_species_guess(
        image_bytes,
        upload.content_type,
        species_list,
        spot.name if spot else ("海边" if coastal else "内陆"),
        body.tideHeightM,
        body.tideTrend,
        coastal=coastal,
    )
    guess = Guess(
        id=new_id("guess"),
        user_id=user.id if user is not None else None,
        owner_id=owner_id,
        upload_id=body.uploadId,
        spot_id=body.spotId or "",
        status="done",
        candidates=result["items"],
        object_name=result.get("objectName", ""),
        observe_tip=result["observeTip"],
        safety_tip=result["safetyTip"],
        disclaimer=result["disclaimer"],
    )
    db.add(guess)
    await db.commit()
    await db.refresh(guess)

    newly = await evaluate_medals(db, user) if user is not None else []
    resp = _guess_response(guess)
    resp["unlockedMedalIds"] = newly
    # 识别完了，把用户拍的那张删掉（见 _discard_upload）
    await _discard_upload(db, upload)
    return ok(resp)


@router.post("/ai/trash-guess")
async def create_trash_guess(
    body: TrashGuessRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """垃圾识别：识出是什么垃圾 + 属于哪一类（可回收 / 有害 / 厨余 / 其他）。

    与物种识别分开：同一条上传链路，识别逻辑和返回结构不同。
    """
    user, owner_id = identity
    if user is not None:
        ensure_consent(user)
    upload = await db.get(Upload, body.uploadId)
    if upload is None or upload.owner_id != owner_id:
        raise NotFoundError("上传不存在")
    spot = await db.get(Spot, body.spotId) if body.spotId else None
    image_bytes = await storage.read_bytes(upload.object_key)

    result = await ai_trash_guess(
        image_bytes,
        upload.content_type,
        spot.name if spot else "海边",
    )

    # 只记「确实识别出垃圾」的 —— 给「深蓝小卫士」成就判断用。
    # 对着鼠标拍照会返回 isTrash=False，那种不算。垃圾识别原来什么都不落库。
    if result.get("isTrash"):
        db.add(
            TrashGuess(
                id=new_id("tg"),
                owner_id=owner_id,
                user_id=user.id if user is not None else None,
                amount=str(result.get("amount") or "")[:16],
                categories=[
                    i.get("category")
                    for i in (result.get("items") or [])
                    if isinstance(i, dict) and i.get("category")
                ],
            )
        )
        await db.commit()

    # 识别完了，把用户拍的那张删掉（见 _discard_upload）
    await _discard_upload(db, upload)

    newly = await evaluate_medals(db, user) if user is not None else []
    result["unlockedMedalIds"] = newly
    return ok(result)


@router.get("/ai/species-guess/{guess_id}")
async def get_species_guess(
    guess_id: str,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    guess = await db.get(Guess, guess_id)
    if guess is None or guess.owner_id != owner_id:
        raise NotFoundError("猜测记录不存在")
    return ok(_guess_response(guess))


@router.post("/ai/species-guess/{guess_id}/feedback")
async def guess_feedback(
    guess_id: str,
    body: GuessFeedbackRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    guess = await db.get(Guess, guess_id)
    if guess is None or guess.owner_id != owner_id:
        raise NotFoundError("猜测记录不存在")
    # 首期仅回执，反馈用于后续改进模型，不向学生展示准确率排行。
    return ok(None, "感谢反馈")
