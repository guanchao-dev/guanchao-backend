"""观潮观察：会话开始 / 添加物种 / 结束 / 记录列表与详情（游客可玩，登录同步）。"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_identity
from app.core.exceptions import ConflictError, NotFoundError
from app.core.response import ok
from app.core.utils import SHANGHAI_TZ, new_id
from app.db.base import get_db
from app.db.models import Spot, WatchRecord
from app.schemas import WatchEndRequest, WatchSpeciesRequest, WatchStartRequest
from app.services.species_unlock import unlock_species
from app.services.tide import get_tide_window, get_weather

# 观潮记录卡上的日期写法：周六.10.03（与前端 dateLabel 的中文习惯一致）
_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

router = APIRouter(tags=["watch"])

MASCOT_KEYS = ["crab-star", "crab-heart", "crab-cloud", "crab-map", "crab-helmet"]


def _has_trash(items: list | None) -> bool:
    """这批条目里有没有垃圾。老数据没有 kind 字段，一律按生物处理。"""
    return any(str((s or {}).get("kind") or "").lower() == "trash" for s in (items or []))


async def _trash_recorded_today(db: AsyncSession, owner_id: str) -> bool:
    """今天是否已经往观潮记录里记过一次垃圾。

    垃圾识别每天只允许进观潮记录一次 —— 同类条目天天重复会把这本记录淹掉。
    「今天」按观潮会话的 started_at 日期算（客户端上报的是北京时间）；这个字段
    为空的会话（老数据）不参与判断，免得把它误当成「今天已经记过」。
    """
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    rows = (
        await db.execute(
            select(WatchRecord.species).where(
                WatchRecord.owner_id == owner_id,
                WatchRecord.started_at.like(f"{today}%"),
            )
        )
    ).scalars().all()
    return any(_has_trash(_clean_items(row)) for row in rows)


def _parse_dt(s: str) -> datetime | None:
    s = (s or "").strip().replace("T", " ")[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _duration_text(start: datetime, end: datetime) -> str:
    minutes = max(1, round((end - start).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    rest = minutes % 60
    return f"{hours} 小时 {rest} 分钟" if rest else f"{hours} 小时"


def _normalize_watch_item(raw: dict) -> dict:
    """把请求体里的一个条目归一化成记录格式（白名单，未知键静默丢弃）。

    与改造前一致：写入时只保留认得的键。kind 区分「生物」和「垃圾」，
    老客户端不传 kind 就按生物处理，行为不变。
    """
    kind = "trash" if str(raw.get("kind") or "").lower() == "trash" else "species"
    is_trash = kind == "trash"
    try:
        count = int(raw.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    return {
        "kind": kind,
        "name": str(raw.get("name") or "").strip()[:40],
        "time": str(raw.get("time") or "")[:8],
        "speciesId": str(raw.get("speciesId") or "")[:64],
        "guessId": str(raw.get("guessId") or "")[:64],
        "category": str(raw.get("category") or "")[:16] if is_trash else "",
        "categoryLabel": str(raw.get("categoryLabel") or "")[:16] if is_trash else "",
        # 这张照片的垃圾总量档位（little/some/much）。观潮记录里要展示「垃圾量」，
        # 只对垃圾有意义；记录里每天最多一条垃圾，所以挂在条目上不会冲突。
        "amount": str(raw.get("amount") or "")[:8] if is_trash else "",
        "label": str(raw.get("label") or "")[:32],
        # 同一物种 / 同类垃圾在照片里的个数。前端本地缓存存了它，
        # 服务端漏了的话两条来源的记录形状会不一致。
        "count": max(1, min(99, count)),
    }


def _item_key(item: dict) -> str:
    """去重键：kind + name。老数据没有 kind → 视作 species，行为与改造前一致。"""
    return f"{item.get('kind') or 'species'}:{item.get('name') or ''}"


def _out_item(raw) -> dict:
    """读回时补默认值（不删未知键，保证老数据原样透传）；没有名字的条目丢掉。"""
    if not isinstance(raw, dict) or not raw.get("name"):
        return {}
    item = dict(raw)
    item.setdefault("kind", "species")
    for key in ("time", "speciesId", "guessId", "category", "categoryLabel", "label"):
        item.setdefault(key, "")
    try:
        item["count"] = max(1, min(99, int(item.get("count") or 1)))
    except (TypeError, ValueError):
        item["count"] = 1
    return item


def _clean_items(rows) -> list[dict]:
    return [x for x in (_out_item(s) for s in (rows or [])) if x]


def _summary(start_time: str, end_time: str, duration: str, species: list) -> str:
    rows = [s for s in species if isinstance(s, dict) and s.get("name")]
    if not rows:
        return f"这次观潮从 {start_time} 到 {end_time}，一共 {duration}。还没有识别到生物，下次可以拍一张给小螃蟹认一认。"
    # 生物和垃圾分开说，别把塑料瓶也叫成「潮间带伙伴」
    species_names = [s["name"] for s in rows if (s.get("kind") or "species") == "species"]
    trash_names = [s["name"] for s in rows if s.get("kind") == "trash"]
    parts = []
    if species_names:
        parts.append(f"认出了 {len(species_names)} 种潮间带伙伴：{'、'.join(species_names)}")
    if trash_names:
        parts.append(f"还捡到 {len(trash_names)} 件垃圾：{'、'.join(trash_names)}")
    return f"这次观潮从 {start_time} 到 {end_time}，一共 {duration}。" + "；".join(parts) + "。"


def _mascot_key(r: WatchRecord) -> str:
    return MASCOT_KEYS[int(r.id[-2:], 16) % len(MASCOT_KEYS)] if r.id else MASCOT_KEYS[0]


def _record_item(r: WatchRecord, spot_name: str = "", seq: int = 0, tide_text: str = "") -> dict:
    start = _parse_dt(r.started_at)
    end = _parse_dt(r.ended_at) if r.ended_at else None
    if start is None:
        return {
            "id": r.id,
            "seq": seq,
            "date": "",
            "dateText": "",
            "startedAt": r.started_at,
            "endedAt": r.ended_at,
            "startTime": "",
            "endTime": "",
            "timeText": "",
            "durationText": "",
            "spotName": spot_name,
            "tempText": "",
            "weatherText": "",
            "tideText": tide_text,
            "species": _clean_items(r.species),
            "summary": "",
            "mascotKey": _mascot_key(r),
            "unlockedMedalIds": [],
        }
    date = start.strftime("%Y-%m-%d")
    start_time = start.strftime("%H:%M")
    end_time = end.strftime("%H:%M") if end else start_time
    duration = _duration_text(start, end or start)
    species = _clean_items(r.species)
    # 天气现在是 get_weather 的占位实现（固定「多云 24℃」），接真实天气接口后这里自动变真
    weather = get_weather(r.spot_id, date) or {}
    try:
        temp_text = f"{int(weather.get('tempC'))}℃"
    except (TypeError, ValueError):
        temp_text = ""
    return {
        "id": r.id,
        # 观潮编号：按开始时间从早到晚排，第几次观潮
        "seq": seq,
        "date": date,
        # 参考图那种「周六.10.03」写法
        "dateText": f"{_WEEKDAY_CN[start.weekday()]}.{start.strftime('%m.%d')}",
        "startedAt": r.started_at,
        "endedAt": r.ended_at,
        "startTime": start_time,
        "endTime": end_time,
        # 这一格在记录卡上是半栏宽（约 290rpx），带空格的破折号会撑到换行，
        # 所以用紧凑写法
        "timeText": f"{start_time}-{end_time}",
        "durationText": duration,
        "spotName": spot_name,
        "tempText": temp_text,
        "weatherText": str(weather.get("text") or ""),
        # 潮高：只有详情接口会算（要读潮汐缓存），列表里留空
        "tideText": tide_text,
        "species": species,
        "summary": _summary(start_time, end_time, duration, species),
        "mascotKey": _mascot_key(r),
        "unlockedMedalIds": [],
    }


async def _spot_name(db: AsyncSession, spot_id: str) -> str:
    if not spot_id:
        return ""
    spot = await db.get(Spot, spot_id)
    return spot.name if spot else ""


async def _record_seqs(db: AsyncSession, owner_id: str) -> dict[str, int]:
    """记录 id -> 第几次观潮（按开始时间从早到晚编号，1 起）。

    观潮记录卡上要显示「观潮编号」。一次查全量再算下标，比每条记录各查一次
    少 N 次往返。
    """
    rows = (
        await db.execute(
            select(WatchRecord.id)
            .where(WatchRecord.owner_id == owner_id)
            .order_by(WatchRecord.started_at.asc(), WatchRecord.id.asc())
        )
    ).scalars().all()
    return {rid: i + 1 for i, rid in enumerate(rows)}


async def _tide_text(db: AsyncSession, rec: WatchRecord) -> str:
    """观潮开始时的潮高，形如「1.2 米」。拿不到就返回空串（那一格留白）。"""
    start = _parse_dt(rec.started_at)
    if start is None or not rec.spot_id:
        return ""
    try:
        tide = await get_tide_window(rec.spot_id, start, db)
        h = tide.get("currentHeightM")
        return f"{float(h):.1f} 米" if h is not None else ""
    except Exception:  # noqa: BLE001
        # 潮汐取不到不该让整个详情打不开
        return ""


@router.post("/watch/sessions")
async def start_watch(
    body: WatchStartRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    # 幂等：同一设备已有进行中会话，直接返回，不 409
    existing = (
        await db.execute(
            select(WatchRecord)
            .where(WatchRecord.owner_id == owner_id, WatchRecord.ended_at == "")
            .order_by(WatchRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return ok(
            {
                "id": existing.id,
                "startedAt": existing.started_at,
                "spotId": existing.spot_id,
                "species": existing.species or [],
            }
        )
    rec = WatchRecord(
        id=new_id("wr"),
        owner_id=owner_id,
        spot_id=body.spotId or "",
        started_at=body.startedAt,
        ended_at="",
        species=[],
    )
    db.add(rec)
    await db.commit()
    return ok({"id": rec.id, "startedAt": rec.started_at, "spotId": rec.spot_id, "species": []})


@router.post("/watch/sessions/{session_id}/species")
async def add_watch_species(
    session_id: str,
    body: WatchSpeciesRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    rec = await db.get(WatchRecord, session_id)
    if rec is None or rec.owner_id != owner_id:
        raise NotFoundError("观潮会话不存在")
    if rec.ended_at:
        raise ConflictError("未在观潮中")
    species = _clean_items(rec.species)
    item = _normalize_watch_item(body.model_dump())

    # 垃圾识别每天只允许进观潮记录一次（拍照识别的生物不受此限制）。
    # 卡在 append 之前 —— 服务端不留这条记录，前端据 trashDailyLimit 提示用户。
    if item["kind"] == "trash" and await _trash_recorded_today(db, owner_id):
        return ok({"id": rec.id, "species": species, "trashDailyLimit": True})

    if item["name"] and not any(_item_key(s) == _item_key(item) for s in species):
        species.append(item)
    rec.species = species
    await db.commit()

    # 图鉴点亮：用户确认了一种生物 → 如果它在图鉴名录里就点亮。
    # 放在 commit 之后 —— unlock_species 内部会自己 commit（并发冲突时还会 rollback），
    # 先提交保证观潮记录不会被连带回滚。kind='trash' 的垃圾条目不会被误点亮。
    newly_lit = None
    if item["kind"] == "species" and item["speciesId"]:
        newly_lit = await unlock_species(db, owner_id, item["speciesId"])

    return ok({"id": rec.id, "species": species, "newlyLitSpecies": newly_lit})


@router.post("/watch/sessions/{session_id}/end")
async def end_watch(
    session_id: str,
    body: WatchEndRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    rec = await db.get(WatchRecord, session_id)
    if rec is None or rec.owner_id != owner_id:
        raise NotFoundError("观潮会话不存在")
    rec.ended_at = body.endedAt
    # 请求体 species 作补全（以已写入的为准，去重）。走与单条写入同一套白名单，
    # 否则离线时记的条目会在这里丢掉 kind/category 等字段。
    if body.species:
        merged = _clean_items(rec.species)
        seen = {_item_key(s) for s in merged}
        # 「垃圾每天只记一次」在这里也要守：离线记的条目是在结束观潮时才合并进来的，
        # 不在这道口子上拦的话，单条写入那边的限制就白设了。
        trash_taken = _has_trash(merged) or await _trash_recorded_today(db, owner_id)
        for raw in body.species:
            if not isinstance(raw, dict):
                continue
            item = _normalize_watch_item(raw)
            if not item["name"] or _item_key(item) in seen:
                continue
            if item["kind"] == "trash":
                if trash_taken:
                    continue
                trash_taken = True
            merged.append(item)
            seen.add(_item_key(item))
        rec.species = merged
    await db.commit()
    return ok(_record_item(rec, await _spot_name(db, rec.spot_id)))


@router.get("/watch/records")
async def list_watch_records(
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    rows = (
        await db.execute(
            select(WatchRecord)
            .where(WatchRecord.owner_id == owner_id, WatchRecord.ended_at != "")
            .order_by(WatchRecord.ended_at.desc())
        )
    ).scalars().all()
    spot_ids = {r.spot_id for r in rows if r.spot_id}
    spot_names: dict[str, str] = {}
    if spot_ids:
        spots = (await db.execute(select(Spot).where(Spot.id.in_(spot_ids)))).scalars().all()
        spot_names = {s.id: s.name for s in spots}
    seqs = await _record_seqs(db, owner_id)
    return ok(
        {
            "list": [
                _record_item(r, spot_names.get(r.spot_id, ""), seqs.get(r.id, 0)) for r in rows
            ]
        }
    )


@router.get("/watch/records/{record_id}")
async def watch_record_detail(
    record_id: str,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    _, owner_id = identity
    rec = await db.get(WatchRecord, record_id)
    if rec is None or rec.owner_id != owner_id:
        raise NotFoundError("观潮记录不存在")
    seqs = await _record_seqs(db, owner_id)
    return ok(
        _record_item(
            rec,
            await _spot_name(db, rec.spot_id),
            seqs.get(rec.id, 0),
            # 潮高要读潮汐缓存，只在详情里算，列表不做这个开销
            await _tide_text(db, rec),
        )
    )
