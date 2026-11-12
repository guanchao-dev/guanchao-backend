"""潮池 AI 抠图。

小程序只上传原照片，后端用 rembg/u2netp 分割主体，存一张透明 PNG 后返回 URL。
手描兜底仍在前端，AI 不可用时不影响使用。
"""
import asyncio
import threading
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import ensure_consent, get_current_user
from app.core.exceptions import (
    AiUnavailableError,
    BadRequestError,
    InvalidFileError,
    NotFoundError,
    RateLimitError,
)
from app.core.response import ok
from app.core.utils import new_id, to_shanghai_iso
from app.db.base import get_db
from app.db.models import (
    AquariumResident,
    AquariumSetting,
    AquariumSpeciesReward,
    Upload,
    User,
)
from app.schemas import (
    AquariumResidentCreate,
    AquariumResidentImport,
    AquariumResidentUpdate,
    AquariumSettingsUpdate,
)
from app.services import storage
from app.services.ai import is_marine_creature, is_tank_background
from app.services.cutout import cutout_png, cutout_png_qwen
from app.services.image import compress_image

router = APIRouter(tags=["aquarium"])

MAX_SIZE = 5 * 1024 * 1024

# 千问精修按张计费（turbo 约 0.1 元/张），按用户做每日次数限制。
# 单进程内存计数即可：这台机器只跑一个 uvicorn worker，重启后清零也能接受。
_ai_usage: dict[str, tuple[str, int]] = {}
_ai_usage_lock = threading.Lock()


def _check_ai_quota(user_id: str) -> None:
    limit = settings.qwen_cutout_daily_limit
    if limit <= 0:
        return
    today = date.today().isoformat()
    with _ai_usage_lock:
        day, count = _ai_usage.get(user_id, ("", 0))
        if day != today:
            day, count = today, 0
        if count >= limit:
            raise RateLimitError("今天的 AI 精修次数用完了，可以手描或明天再试")
        _ai_usage[user_id] = (day, count + 1)


def _is_image(data: bytes) -> bool:
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


@router.post("/aquarium/cutout/{upload_id}")
async def cutout_from_upload(
    upload_id: str,
    engine: str = "local",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把「已人工审核通过」的历史照片转成潮池贴纸。

    用户上传被 AI 驳回 → 申诉 → 管理员通过后，原图还躺在存储里（没做抠图）。
    这里对那张已通过的图补做抠图，产物直接置为 approved，用户可以放进潮池。
    engine=ai 时用千问图像编辑做一次「AI 精修」。
    """
    up = await db.get(Upload, upload_id)
    if up is None or up.user_id != user.id or up.scene != "aquarium":
        raise NotFoundError("图片不存在")
    if up.status != "approved":
        raise InvalidFileError("图片还没通过审核")
    if not up.object_key:
        raise NotFoundError("图片不存在")

    try:
        data = await storage.read_bytes(up.object_key)
    except FileNotFoundError:
        raise NotFoundError("图片不存在")

    engine = (engine or "local").strip().lower()
    if engine not in ("local", "ai"):
        raise BadRequestError("不支持的抠图方式")

    try:
        if engine == "ai":
            if not settings.dashscope_api_key:
                raise AiUnavailableError("AI 精修未配置，请手动描边")
            _check_ai_quota(user.id)
            png, width, height = await asyncio.to_thread(cutout_png_qwen, data)
        else:
            png, width, height = await asyncio.to_thread(cutout_png, data)
    except (AiUnavailableError, RateLimitError):
        raise
    except Exception as exc:
        raise AiUnavailableError("AI 抠图失败，请稍后再试") from exc

    owner_id = f"user:{user.id}"
    safe_owner = owner_id.replace(":", "_")
    new_id_value = new_id("up")
    object_key = f"private/{safe_owner}/aquarium/{new_id_value}.png"
    await storage.save_bytes(object_key, png)

    db.add(
        Upload(
            id=new_id_value,
            user_id=user.id,
            owner_id=owner_id,
            scene="aquarium",
            content_type="image/png",
            ext="png",
            object_key=object_key,
            status="approved",
            width=width,
            height=height,
        )
    )
    await db.commit()

    return ok(
        {
            "uploadId": new_id_value,
            "url": f"/uploads/{new_id_value}/content",
            "status": "approved",
            "width": width,
            "height": height,
            "engine": engine,
        }
    )


@router.post("/aquarium/audit")
async def aquarium_audit(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """选完照片立刻审核：主体必须是真实拍摄的海洋生物。

    通过就只返回 ok，不落库；不通过就把原图存下来并建一条 rejected 记录，
    用户可以在小程序里申诉（/uploads/{id}/appeal）。
    """
    ensure_consent(user)
    data = await file.read()
    if not data or len(data) > MAX_SIZE:
        raise InvalidFileError("图片不能超过 5MB")
    if not _is_image(data):
        raise InvalidFileError("仅支持 jpg/png/webp")

    try:
        marine, obj_name, box = await is_marine_creature(data)
    except Exception:
        # 审核服务异常放行，后续人工待审
        return ok({"ok": True, "skipped": True})

    if marine:
        return ok({"ok": True, "name": obj_name, "box": box})

    owner_id = f"user:{user.id}"
    safe_owner = owner_id.replace(":", "_")
    upload_id = new_id("up")
    original, ext = compress_image(data)
    content_type = "image/jpeg" if ext == "jpg" else f"image/{ext}"
    object_key = f"private/{safe_owner}/aquarium/{upload_id}.{ext}"
    await storage.save_bytes(object_key, original)

    label = "不是海洋生物"
    if obj_name:
        label = f"不是海洋生物（{obj_name}）"
    label = label[:64]

    db.add(
        Upload(
            id=upload_id,
            user_id=user.id,
            owner_id=owner_id,
            scene="aquarium",
            content_type=content_type,
            ext=ext,
            object_key=object_key,
            status="rejected",
            reject_reason=label,
            reject_source="ai",
        )
    )
    await db.commit()
    return ok({"ok": False, "uploadId": upload_id, "status": "rejected", "rejectReason": label})


@router.post("/aquarium/cutout")
async def aquarium_cutout(
    file: UploadFile = File(...),
    audited: bool = Form(False),
    box: str = Form(""),
    engine: str = Form("local"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI 抠图：multipart 上传原图，返回透明贴纸的 uploadId / url / 宽高。

    engine=local（默认）走本地 u2net；engine=ai 走千问图像编辑「AI 精修」，
    按张计费、约 10 秒，只在用户主动点击时使用。
    """
    ensure_consent(user)
    data = await file.read()
    if not data or len(data) > MAX_SIZE:
        raise InvalidFileError("图片不能超过 5MB")
    if not _is_image(data):
        raise InvalidFileError("仅支持 jpg/png/webp")

    owner_id = f"user:{user.id}"
    safe_owner = owner_id.replace(":", "_")

    # 前端把选图审核时得到的归一化 box 传过来（"x,y,w,h"），先裁到主体再分割
    subject_box = None
    if box:
        try:
            parts = [float(v) for v in box.split(",")]
            if len(parts) == 4:
                bx, by, bw, bh = parts
                if 0 <= bx < 1 and 0 <= by < 1 and 0 < bw <= 1 and 0 < bh <= 1:
                    subject_box = [bx, by, bw, bh]
        except (TypeError, ValueError):
            subject_box = None

    # AI 初审：主体不是海洋生物就直接驳回（用户可申诉，申诉走 /uploads/{id}/appeal）
    # 前端选完照片时已经调过 /aquarium/audit（audited=true），这里跳过，避免重复调用模型。
    status = "pending"
    reject_reason = ""
    reject_source = ""
    try:
        if audited:
            marine, obj_name, ai_box = True, "", None
        else:
            marine, obj_name, ai_box = await is_marine_creature(data)
        if subject_box is None and ai_box:
            subject_box = ai_box
        if not marine:
            status = "rejected"
            reject_source = "ai"
            label = "不是海洋生物"
            if obj_name:
                label = f"不是海洋生物（{obj_name}）"
            reject_reason = label[:64]
    except Exception:
        # 审核服务异常就放行，交给人工待审
        pass

    # 被驳回时不跑抠图（省 2 秒），直接存原图，管理员在后台能看到再决定
    if status == "rejected":
        upload_id = new_id("up")
        original, ext = compress_image(data)
        content_type = "image/jpeg" if ext == "jpg" else f"image/{ext}"
        object_key = f"private/{safe_owner}/aquarium/{upload_id}.{ext}"
        await storage.save_bytes(object_key, original)
        db.add(
            Upload(
                id=upload_id,
                user_id=user.id,
                owner_id=owner_id,
                scene="aquarium",
                content_type=content_type,
                ext=ext,
                object_key=object_key,
                status=status,
                reject_reason=reject_reason,
                reject_source=reject_source,
            )
        )
        await db.commit()
        return ok(
            {
                "uploadId": upload_id,
                "objectKey": object_key,
                "status": status,
                "rejectReason": reject_reason,
            }
        )

    engine = (engine or "local").strip().lower()
    if engine not in ("local", "ai"):
        raise BadRequestError("不支持的抠图方式")

    try:
        if engine == "ai":
            if not settings.dashscope_api_key:
                raise AiUnavailableError("AI 精修未配置，请手动描边")
            _check_ai_quota(user.id)
            png, width, height = await asyncio.to_thread(
                cutout_png_qwen, data, subject_box
            )
        else:
            png, width, height = await asyncio.to_thread(cutout_png, data, subject_box)
    except (AiUnavailableError, RateLimitError):
        raise
    except Exception as exc:
        raise AiUnavailableError("AI 抠图失败，请稍后再试或手动描边") from exc


    safe_owner = owner_id.replace(":", "_")
    upload_id = new_id("up")
    object_key = f"private/{safe_owner}/aquarium/{upload_id}.png"
    # save_bytes 是 async：漏了 await 会「接口 200 但文件没落盘」
    await storage.save_bytes(object_key, png)

    db.add(
        Upload(
            id=upload_id,
            user_id=user.id,
            owner_id=owner_id,
            scene="aquarium",
            content_type="image/png",
            ext="png",
            object_key=object_key,
            status="pending",
            width=width,
            height=height,
        )
    )
    await db.commit()

    return ok(
        {
            "uploadId": upload_id,
            "objectKey": object_key,
            "url": f"/uploads/{upload_id}/content",
            "status": "pending",
            "rejectReason": "",
            "width": width,
            "height": height,
            "engine": engine,
        }
    )


# ===== 潮池住客：账号同步 =====
# 住客图片本体在对象存储（抠图/上传时已落桶），表里只存 object_key，
# 换手机登录后按 user_id 拉回来即可。

_RESIDENT_KINDS = {"fish", "crab", "snail", "jelly"}
_MAX_RESIDENTS = 80
COIN_PER_NEW_SPECIES = 20
_EPOCH = datetime(1970, 1, 1)


def _resident_payload(r: AquariumResident) -> dict:
    created = r.created_at or datetime.utcnow()
    return {
        "id": r.id,
        "name": r.name,
        "kind": r.kind,
        "url": storage.public_url(r.object_key),
        "objectKey": r.object_key,
        "aspect": round(float(r.aspect or 1.0), 4),
        "scale": round(float(r.scale or 1.0), 3),
        "rotation": int(r.rotation or 0),
        "speciesName": r.species_name or "",
        "baseFlip": bool(r.base_flip),
        # 小程序按毫秒时间戳判断「刚放进去，从缸边游进来」
        "createdAtMs": int((created - _EPOCH).total_seconds() * 1000),
        "createdAt": to_shanghai_iso(r.created_at),
    }


async def _resident_object_key(
    user_id: str, payload: AquariumResidentCreate, db: AsyncSession
) -> str:
    """只允许引用当前账号自己目录下的图，防止拿别人的 object_key。"""
    prefix = f"private/user_{user_id}/"
    key = (payload.objectKey or "").strip().lstrip("/")
    if key:
        if not key.startswith(prefix):
            raise InvalidFileError("图片不属于当前账号")
        return key

    upload_id = (payload.uploadId or "").strip()
    if upload_id:
        up = await db.get(Upload, upload_id)
        if up is None or up.user_id != user_id or not up.object_key:
            raise NotFoundError("图片不存在")
        if not up.object_key.startswith(prefix):
            raise InvalidFileError("图片不属于当前账号")
        return up.object_key

    raise BadRequestError("缺少贴纸图片")


def _build_resident(user_id: str, payload: AquariumResidentCreate, object_key: str) -> AquariumResident:
    name = (payload.name or "").strip()[:12] or "小生物"
    kind = payload.kind if payload.kind in _RESIDENT_KINDS else "fish"
    aspect = float(payload.aspect or 1.0)
    if not (0.05 <= aspect <= 20):
        aspect = 1.0
    scale = float(payload.scale or 1.0)
    if not (0.5 <= scale <= 1.8):
        scale = 1.0
    rotation = int(payload.rotation or 0)
    if rotation < -180:
        rotation = -180
    if rotation > 180:
        rotation = 180
    return AquariumResident(
        id=new_id("ar"),
        user_id=user_id,
        name=name,
        kind=kind,
        object_key=object_key,
        aspect=aspect,
        scale=scale,
        rotation=rotation,
        species_name=((payload.speciesName or payload.name or "").strip()[:64]),
        base_flip=bool(payload.baseFlip),
        created_at=datetime.utcnow(),
    )


@router.get("/aquarium/residents")
async def list_aquarium_residents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(AquariumResident)
            .where(AquariumResident.user_id == user.id)
            .order_by(AquariumResident.created_at.asc())
        )
    ).scalars().all()
    return ok({"items": [_resident_payload(r) for r in rows]})


@router.post("/aquarium/residents")
async def create_aquarium_resident(
    payload: AquariumResidentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ensure_consent(user)
    count = await db.scalar(
        select(func.count())
        .select_from(AquariumResident)
        .where(AquariumResident.user_id == user.id)
    )
    if (count or 0) >= _MAX_RESIDENTS:
        raise BadRequestError("潮池住客满了，先送走几位")

    key = await _resident_object_key(user.id, payload, db)
    resident = _build_resident(user.id, payload, key)
    db.add(resident)

    # 从申诉记录加入：标记来源图已用过，防止二次加入
    source_upload_id = (payload.sourceUploadId or "").strip()
    if source_upload_id:
        src_up = await db.get(Upload, source_upload_id)
        if (
            src_up is None
            or src_up.user_id != user.id
            or src_up.scene != "aquarium"
            or src_up.status != "approved"
        ):
            raise BadRequestError("来源图片不可用")
        if src_up.used_at:
            raise BadRequestError("这张图片已经加入过潮池")
        src_up.used_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        src_up.used_resident_id = resident.id

    # 新物种奖励：同一账号同一种物种只发一次
    awarded = 0
    species_key = resident.species_name
    if species_key:
        exists = (
            await db.execute(
                select(AquariumSpeciesReward.id).where(
                    AquariumSpeciesReward.user_id == user.id,
                    AquariumSpeciesReward.species_key == species_key,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(
                AquariumSpeciesReward(
                    id=new_id("asr"),
                    user_id=user.id,
                    species_key=species_key,
                    coins=COIN_PER_NEW_SPECIES,
                    created_at=datetime.utcnow(),
                )
            )
            user.coins = (user.coins or 0) + COIN_PER_NEW_SPECIES
            awarded = COIN_PER_NEW_SPECIES

    await db.commit()
    data = _resident_payload(resident)
    data["coinsAwarded"] = awarded
    data["coins"] = user.coins or 0
    return ok(data)


@router.post("/aquarium/residents/import")
async def import_aquarium_residents(
    payload: AquariumResidentImport,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """本机旧住客一次性迁到账号下（只在前端发现服务端为空时调用）。"""
    ensure_consent(user)
    items = (payload.items or [])[:30]
    created: list[AquariumResident] = []
    for item in items:
        if len(created) >= _MAX_RESIDENTS:
            break
        try:
            key = await _resident_object_key(user.id, item, db)
        except (InvalidFileError, NotFoundError, BadRequestError):
            continue
        resident = _build_resident(user.id, item, key)
        db.add(resident)
        created.append(resident)
    if created:
        await db.commit()
    return ok({"items": [_resident_payload(r) for r in created]})


@router.delete("/aquarium/residents/{resident_id}")
async def delete_aquarium_resident(
    resident_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resident = await db.get(AquariumResident, resident_id)
    if resident is None or resident.user_id != user.id:
        raise NotFoundError("住客不存在")
    await db.delete(resident)
    await db.commit()
    return ok({"deleted": 1})


@router.delete("/aquarium/residents")
async def clear_aquarium_residents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        delete(AquariumResident).where(AquariumResident.user_id == user.id)
    )
    await db.commit()
    return ok({"deleted": result.rowcount or 0})


# ===== 潮池外观：自定义背景 / 白天夜晚 / 横版竖版 =====

_BG_PREFIX_TMPL = "private/user_{uid}/aquarium-bg/"


def _setting_payload(s: AquariumSetting | None) -> dict:
    key = s.background_key if s else ""
    return {
        "backgroundKey": key,
        "backgroundUrl": storage.public_url(key) if key else "",
        "theme": (s.theme if s else "day") or "day",
        "landscape": bool(s.landscape) if s else False,
    }


async def _get_setting(user_id: str, db: AsyncSession) -> AquariumSetting:
    setting = await db.get(AquariumSetting, user_id)
    if setting is None:
        setting = AquariumSetting(user_id=user_id)
        db.add(setting)
        await db.flush()
    return setting


@router.get("/aquarium/settings")
async def get_aquarium_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    setting = await db.get(AquariumSetting, user.id)
    return ok(_setting_payload(setting))


@router.put("/aquarium/settings")
async def update_aquarium_settings(
    payload: AquariumSettingsUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    setting = await _get_setting(user.id, db)

    if payload.theme is not None:
        theme = payload.theme.strip().lower()
        if theme not in ("day", "night"):
            raise BadRequestError("主题只能是 day 或 night")
        setting.theme = theme
    if payload.landscape is not None:
        setting.landscape = bool(payload.landscape)
    if payload.backgroundKey is not None:
        key = payload.backgroundKey.strip().lstrip("/")
        if key:
            prefix = _BG_PREFIX_TMPL.format(uid=user.id)
            if not key.startswith(prefix):
                raise InvalidFileError("背景图不属于当前账号")
        setting.background_key = key

    setting.updated_at = datetime.utcnow()
    await db.commit()
    return ok(_setting_payload(setting))


@router.post("/aquarium/background")
async def upload_aquarium_background(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传自定义潮池背景。先过图片审核，通过才写入设置。"""
    ensure_consent(user)
    data = await file.read()
    if not data or len(data) > MAX_SIZE:
        raise InvalidFileError("图片不能超过 5MB")
    if not _is_image(data):
        raise InvalidFileError("仅支持 jpg/png/webp")

    ok_flag, reason = await is_tank_background(data)
    reason = (reason or "图片未通过合规审核")[:64]

    compressed, ext = compress_image(data)
    content_type = {
        "jpg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(ext, "image/jpeg")
    owner_id = f"user:{user.id}"
    safe_owner = owner_id.replace(":", "_")
    upload_id = new_id("bg")
    object_key = f"private/{safe_owner}/aquarium-bg/{upload_id}.{ext}"
    await storage.save_bytes(object_key, compressed)

    status = "approved" if ok_flag else "rejected"
    db.add(
        Upload(
            id=upload_id,
            user_id=user.id,
            owner_id=owner_id,
            scene="aquarium-bg",
            content_type=content_type,
            ext=ext,
            object_key=object_key,
            status=status,
            reject_reason="" if ok_flag else reason,
            reject_source="" if ok_flag else "ai",
        )
    )

    if ok_flag:
        setting = await _get_setting(user.id, db)
        setting.background_key = object_key
        setting.updated_at = datetime.utcnow()

    await db.commit()

    if not ok_flag:
        return ok(
            {
                "ok": False,
                "status": "rejected",
                "rejectReason": reason,
                "uploadId": upload_id,
            }
        )
    return ok(
        {
            "ok": True,
            "status": "approved",
            "uploadId": upload_id,
            "backgroundKey": object_key,
            "backgroundUrl": storage.public_url(object_key),
        }
    )


@router.put("/aquarium/residents/{resident_id}")
async def update_aquarium_resident(
    resident_id: str,
    payload: AquariumResidentUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resident = await db.get(AquariumResident, resident_id)
    if resident is None or resident.user_id != user.id:
        raise NotFoundError("住客不存在")
    if payload.scale is not None:
        scale = float(payload.scale)
        if not (0.5 <= scale <= 1.8):
            raise BadRequestError("大小超出范围")
        resident.scale = scale
    if payload.rotation is not None:
        rotation = int(payload.rotation)
        if rotation < -180:
            rotation = -180
        if rotation > 180:
            rotation = 180
        resident.rotation = rotation
    await db.commit()
    return ok(_resident_payload(resident))
