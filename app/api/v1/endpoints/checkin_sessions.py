"""签到活动：组织者地图选点建围栏 → 生成密钥；参与者凭密钥报到；组织者看名单。"""
import secrets
from math import asin, cos, radians, sin, sqrt

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_identity
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.core.response import ok
from app.core.utils import new_id, to_shanghai_iso
from app.db.base import get_db
from app.db.models import CheckinSession, ReportCheckin, User
from app.schemas import CheckinSessionCreateRequest
from app.services import amap
from app.services.content_safety import assert_text_safe
from app.services.geo import haversine_m, normalize_polygon

router = APIRouter(tags=["checkin-sessions"])

# 密钥字符集：去掉容易看错的 0/O/1/I/L
_KEY_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_KEY_LEN = 6
_RADIUS_CHOICES = (200, 500, 1000, 2000, 5000)


def _new_key() -> str:
    return "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_LEN))


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6371000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return int(2 * r * asin(sqrt(a)))


def _distance_text(m: int) -> str:
    return f"{m / 1000:.1f} 公里" if m >= 1000 else f"{m} 米"


async def _owner_nickname(db: AsyncSession, owner_id: str) -> str:
    if not owner_id.startswith("user:"):
        return "活动组织者"
    u = await db.get(User, owner_id[len("user:"):])
    return u.nickname if u is not None else "活动组织者"


async def _session_item(db: AsyncSession, s: CheckinSession, with_count: bool = False) -> dict:
    item = {
        "sessionId": s.id,
        "key": s.key,
        "name": s.name,
        "placeName": s.place_name,
        "address": s.address,
        "lat": s.lat,
        "lng": s.lng,
        "radiusM": s.radius_m,
        "polygon": normalize_polygon(s.polygon),
        "poiId": s.poi_id,
        "fenceType": "polygon" if len(normalize_polygon(s.polygon)) >= 3 else "circle",
        "status": s.status,
        "createdAt": to_shanghai_iso(s.created_at),
    }
    if with_count:
        import sqlalchemy as sa

        item["checkinCount"] = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(ReportCheckin)
                .where(ReportCheckin.session_id == s.id)
            )
        ).scalar() or 0
    return item


@router.post("/checkin-sessions")
async def create_checkin_session(
    body: CheckinSessionCreateRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """创建一个签到活动，返回 6 位密钥。

    组织者在地图上选好点（经纬度由 wx.chooseLocation 给出），设定围栏半径，
    参与者凭密钥在自己的设备上签到，服务端校验是否在围栏内。
    """
    _, owner_id = identity
    place_name = (body.placeName or "").strip()
    name = (body.name or "").strip() or "研学报到"
    if not place_name:
        raise BadRequestError("请选择签到地点")
    if body.lat is None or body.lng is None:
        raise BadRequestError("缺少地点坐标")
    assert_text_safe(name, place_name)

    radius = int(body.radiusM or 500)
    if radius not in _RADIUS_CHOICES:
        radius = 500

    # 生成不重复的密钥
    key = ""
    for _ in range(12):
        candidate = _new_key()
        exists = (
            await db.execute(select(CheckinSession.id).where(CheckinSession.key == candidate))
        ).scalar_one_or_none()
        if exists is None:
            key = candidate
            break
    if not key:
        raise BadRequestError("密钥生成失败，请重试")

    s = CheckinSession(
        id=new_id("cks"),
        owner_id=owner_id,
        key=key,
        name=name[:64],
        place_name=place_name[:64],
        address=(body.address or "").strip()[:255],
        lat=float(body.lat),
        lng=float(body.lng),
        radius_m=radius,
        # 有真实边界（高德 AOI）就用多边形围栏，否则用圆形
        polygon=normalize_polygon(body.polygon) if body.polygon else [],
        poi_id=(body.poiId or "").strip()[:64],
    )
    db.add(s)
    await db.commit()
    return ok(await _session_item(db, s, with_count=True))


@router.get("/checkin-sessions/by-key/{key}")
async def session_by_key(key: str, db: AsyncSession = Depends(get_db)):
    """参与者填了密钥后先查活动信息（地点、围栏半径、组织者），确认无误再签到。"""
    s = (
        await db.execute(
            select(CheckinSession).where(CheckinSession.key == (key or "").strip().upper())
        )
    ).scalar_one_or_none()
    if s is None:
        raise NotFoundError("签到密钥无效")
    if s.status != "active":
        raise BadRequestError("该签到活动已结束")
    item = await _session_item(db, s)
    item["ownerNickname"] = await _owner_nickname(db, s.owner_id)
    return ok(item)


@router.get("/checkin-sessions/{session_id}/qrcode")
async def session_qrcode(session_id: str, db: AsyncSession = Depends(get_db)):
    """签到密钥的二维码（PNG）。

    组织者可以把这张图投屏或打印，参与者用「进行签到 → 扫码」直接读密钥。
    二维码内容就是密钥本身（6 位），扫码后前端直接填进密钥框。
    """
    import io

    import qrcode

    s = await db.get(CheckinSession, session_id)
    if s is None:
        raise NotFoundError("签到活动不存在")
    # 已结束的签到：密钥与二维码一并作废，不再生成
    if s.status != "active":
        raise NotFoundError("签到已结束，密钥和二维码已作废")

    img = qrcode.make(s.key, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/checkin-sessions/place-search")
async def place_search(keyword: str = "", city: str = ""):
    """搜索签到地点（高德 POI）。返回候选，带真实边界的会一并给出。

    未配置高德 Key 时返回空列表，前端继续用「地图选点 + 圆形围栏」。
    """
    if not keyword.strip():
        return ok({"list": [], "amapEnabled": amap.enabled()})
    items = await amap.search_poi(keyword, city, limit=10)
    for it in items:
        if not it.get("polygon"):
            pts = await amap.get_aoi_polygon(it.get("poiId") or "")
            if pts:
                it["polygon"] = pts
        it["hasBoundary"] = len(it.get("polygon") or []) >= 3
    return ok({"list": items, "amapEnabled": amap.enabled()})


@router.get("/checkin-sessions/mine")
async def my_checkin_sessions(
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """我创建的签到活动（按创建时间倒序）。"""
    _, owner_id = identity
    rows = (
        await db.execute(
            select(CheckinSession)
            .where(CheckinSession.owner_id == owner_id)
            .order_by(CheckinSession.created_at.desc())
        )
    ).scalars().all()
    items = [await _session_item(db, s, with_count=True) for s in rows]
    return ok({"list": items})


@router.get("/checkin-sessions/{session_id}/records")
async def session_records(
    session_id: str,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """某个签到的报到名单——只给创建者看（参与者填的信息回传给组织者）。"""
    _, owner_id = identity
    s = await db.get(CheckinSession, session_id)
    if s is None:
        raise NotFoundError("签到活动不存在")
    if s.owner_id != owner_id:
        raise ForbiddenError("只有活动创建者能查看名单")
    rows = (
        await db.execute(
            select(ReportCheckin)
            .where(ReportCheckin.session_id == session_id)
            .order_by(ReportCheckin.created_at.asc())
        )
    ).scalars().all()
    items = [
        {
            "checkinId": r.id,
            "name": r.name,
            "studentNo": r.student_no,
            "date": r.checkin_date,
            "latGrid": r.lat_grid,
            "lngGrid": r.lng_grid,
            "distanceM": _haversine_m(s.lat, s.lng, r.lat_grid, r.lng_grid)
            if (r.lat_grid is not None and r.lng_grid is not None)
            else None,
            "createdAt": to_shanghai_iso(r.created_at),
        }
        for r in rows
    ]
    for it in items:
        if it["distanceM"] is not None:
            it["distanceText"] = _distance_text(it["distanceM"])
    result = await _session_item(db, s, with_count=True)
    result["list"] = items
    return ok(result)


@router.post("/checkin-sessions/{session_id}/close")
async def close_checkin_session(
    session_id: str,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """结束签到（仅创建者）。结束后密钥失效，不能再签到。"""
    _, owner_id = identity
    s = await db.get(CheckinSession, session_id)
    if s is None:
        raise NotFoundError("签到活动不存在")
    if s.owner_id != owner_id:
        raise ForbiddenError("只有活动创建者能结束签到")
    s.status = "closed"
    await db.commit()
    return ok(await _session_item(db, s, with_count=True))
