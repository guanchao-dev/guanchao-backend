"""打卡记录。

- GET  /records/checkins     生成图鉴卡时记的打卡（同一自然日只计 1 次）
- POST /records/checkin      研学报到签到（姓名 + 学号 + 粗粒度位置）
- GET  /records/report-checkins  我的研学报到记录
"""
from datetime import datetime
from math import asin, cos, radians, sin, sqrt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_identity
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.response import ok, paginated
from app.core.utils import SHANGHAI_TZ, new_id, to_shanghai_iso
from app.db.base import get_db
from app.db.models import Checkin, CheckinSession, ReportCheckin, Spot, User
from app.schemas import ReportCheckinRequest
from app.services import amap
from app.services.content_safety import assert_text_safe
from app.services.geo import distance_text, haversine_m, in_fence, normalize_polygon

router = APIRouter(tags=["records"])

# 位置粗化网格：0.003° ≈ 300 米。服务端只存取整后的网格值。
CHECKIN_GRID_DEG = 0.003
# 距离最近赶海点超过这个距离，视为「不在赶海点附近」
NEARBY_SPOT_MAX_M = 3000


def _grid(v: float | None) -> float | None:
    if v is None:
        return None
    try:
        return round(round(float(v) / CHECKIN_GRID_DEG) * CHECKIN_GRID_DEG, 4)
    except (TypeError, ValueError):
        return None


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6371000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return int(2 * r * asin(sqrt(a)))


def _distance_text(m: int) -> str:
    if m >= 1000:
        return f"{m / 1000:.1f} 公里"
    return f"{m} 米"


async def _nearby_spot(db: AsyncSession, lat: float | None, lng: float | None) -> dict | None:
    """找离签到位置最近的赶海点，供前端显示「定到哪了」。"""
    if lat is None or lng is None:
        return None
    spots = (await db.execute(select(Spot))).scalars().all()
    best = None
    for s in spots:
        if s.lat is None or s.lng is None:
            continue
        d = _haversine_m(lat, lng, s.lat, s.lng)
        if best is None or d < best["distanceM"]:
            best = {
                "id": s.id,
                "name": s.name,
                "city": s.city,
                "distanceM": d,
                "distanceText": _distance_text(d),
            }
    if best is None:
        return None
    best["nearby"] = best["distanceM"] <= NEARBY_SPOT_MAX_M
    return best


async def _checkin_item(db: AsyncSession, r: ReportCheckin) -> dict:
    nearby = await _nearby_spot(db, r.lat_grid, r.lng_grid)
    session = await db.get(CheckinSession, r.session_id) if r.session_id else None
    return {
        "checkinId": r.id,
        "sessionId": r.session_id,
        "sessionName": session.name if session else "",
        "sessionPlace": session.place_name if session else "",
        "name": r.name,
        "studentNo": r.student_no,
        "date": r.checkin_date,
        "latGrid": r.lat_grid,
        "lngGrid": r.lng_grid,
        "nearbySpot": nearby,
        "createdAt": to_shanghai_iso(r.created_at),
    }


@router.get("/records/checkins")
async def checkins(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Checkin).where(Checkin.user_id == user.id).order_by(Checkin.checkin_date.desc())
        )
    ).scalars().all()
    items = [{"date": c.checkin_date, "spotId": c.spot_id} for c in rows]
    return ok({"list": items, "total": len(items)})


@router.post("/records/checkin")
async def create_checkin(
    body: ReportCheckinRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """研学报到签到。游客可调（身份由表单自带，不依赖账号）。

    同一设备/用户同一天重复提交 → 幂等返回首次结果，不报 409。
    """
    name = (body.name or "").strip()
    student_no = (body.studentNo or "").strip()
    if not name:
        raise BadRequestError("请填写姓名")
    if not student_no:
        raise BadRequestError("请填写学号")
    if len(name) > 20:
        raise BadRequestError("姓名过长")
    if len(student_no) > 32:
        raise BadRequestError("学号过长")
    assert_text_safe(name, student_no)

    _, owner_id = identity
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")

    # 带密钥 = 参加某个签到活动：先校验密钥与围栏
    session = None
    key = (body.sessionKey or "").strip().upper()
    if key:
        session = (
            await db.execute(select(CheckinSession).where(CheckinSession.key == key))
        ).scalar_one_or_none()
        if session is None:
            raise NotFoundError("签到密钥无效")
        if session.status != "active":
            raise BadRequestError("该签到活动已结束")
        if body.lat is None or body.lng is None:
            raise BadRequestError("请先获取当前位置")
        ok_fence, _, why = in_fence(
            body.lat, body.lng, session.lat, session.lng,
            session.radius_m, normalize_polygon(session.polygon),
        )
        if not ok_fence:
            raise BadRequestError(f"当前位置不在签到范围内（{why}）")

    # 幂等：同一天、同一个活动只计一次
    dup_q = select(ReportCheckin).where(
        ReportCheckin.owner_id == owner_id,
        ReportCheckin.checkin_date == today,
        ReportCheckin.session_id == (session.id if session else ""),
    )
    existing = (await db.execute(dup_q)).scalar_one_or_none()
    if existing is not None:
        result = await _checkin_item(db, existing)
        result["alreadyChecked"] = True
        return ok(result)

    rec = ReportCheckin(
        id=new_id("rck"),
        owner_id=owner_id,
        session_id=session.id if session else "",
        name=name,
        student_no=student_no,
        lat_grid=_grid(body.lat),
        lng_grid=_grid(body.lng),
        checkin_date=today,
    )
    db.add(rec)
    await db.commit()
    result = await _checkin_item(db, rec)
    result["alreadyChecked"] = False
    return ok(result)


@router.get("/records/report-checkins")
async def list_report_checkins(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """我的研学报到记录（按日期倒序）。"""
    _, owner_id = identity
    total = (
        await db.execute(
            select(func.count())
            .select_from(ReportCheckin)
            .where(ReportCheckin.owner_id == owner_id)
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            select(ReportCheckin)
            .where(ReportCheckin.owner_id == owner_id)
            .order_by(ReportCheckin.checkin_date.desc(), ReportCheckin.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()
    items = [await _checkin_item(db, r) for r in rows]
    return ok(paginated(items, page, pageSize, total))
