"""观潮观察：会话开始 / 添加物种 / 结束 / 记录列表与详情（游客可玩，登录同步）。"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_identity
from app.core.exceptions import ConflictError, NotFoundError
from app.core.response import ok
from app.core.utils import new_id
from app.db.base import get_db
from app.db.models import Spot, WatchRecord
from app.schemas import WatchEndRequest, WatchSpeciesRequest, WatchStartRequest

router = APIRouter(tags=["watch"])

MASCOT_KEYS = ["crab-star", "crab-heart", "crab-cloud", "crab-map", "crab-helmet"]


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


def _record_item(r: WatchRecord, spot_name: str = "") -> dict:
    start = _parse_dt(r.started_at)
    end = _parse_dt(r.ended_at) if r.ended_at else None
    if start is None:
        return {
            "id": r.id,
            "date": "",
            "startedAt": r.started_at,
            "endedAt": r.ended_at,
            "startTime": "",
            "endTime": "",
            "durationText": "",
            "spotName": spot_name,
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
    return {
        "id": r.id,
        "date": date,
        "startedAt": r.started_at,
        "endedAt": r.ended_at,
        "startTime": start_time,
        "endTime": end_time,
        "durationText": duration,
        "spotName": spot_name,
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
    if item["name"] and not any(_item_key(s) == _item_key(item) for s in species):
        species.append(item)
    rec.species = species
    await db.commit()
    return ok({"id": rec.id, "species": species})


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
        for raw in body.species:
            if not isinstance(raw, dict):
                continue
            item = _normalize_watch_item(raw)
            if not item["name"] or _item_key(item) in seen:
                continue
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
    return ok({"list": [_record_item(r, spot_names.get(r.spot_id, "")) for r in rows]})


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
    return ok(_record_item(rec, await _spot_name(db, rec.spot_id)))
