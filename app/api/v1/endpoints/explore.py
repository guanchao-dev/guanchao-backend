"""场地探索：场地框 / 探索会话 / 二维码解锁 / 分享。

精确经纬度只留在手机端，后端不收 points / latitude / longitude / 轨迹图。
"""
from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ensure_consent, get_current_user
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.response import ok
from app.core.utils import new_id
from app.db.base import get_db
from app.db.models import ExploreSession, Spot, User
from app.schemas import ExploreQrUnlockRequest, ExploreSessionCreateRequest
from app.services.achievements import evaluate_medals
from app.services.explore import QR_CODES, badge_for, venue_config

router = APIRouter(tags=["explore"])


@router.get("/explore/venues/{venue_id}")
async def venue(venue_id: str, db: AsyncSession = Depends(get_db)):
    spot = await db.get(Spot, venue_id)
    cfg = venue_config(venue_id, spot)
    return ok(
        {
            "venueId": venue_id,
            "name": cfg["name"],
            "city": cfg["city"],
            "center": cfg["center"],
            "bbox": cfg["bbox"],
            "gridSizeM": cfg["gridSizeM"],
            "scale": cfg["scale"],
            "needGuardian": cfg["needGuardian"],
        }
    )


@router.post("/explore/sessions")
async def create_session(
    body: ExploreSessionCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.mode not in ("solo", "group"):
        raise BadRequestError("mode 不合法")
    if body.mode == "solo":
        ensure_consent(user)  # 儿童未获监护人同意时 solo 返回 40301
    ratio = body.exploreRatio if body.exploreRatio is not None else 0.0
    if not (0 <= ratio <= 1):
        raise BadRequestError("exploreRatio 须为 0~1")

    if idempotency_key:
        existing = (
            await db.execute(
                select(ExploreSession).where(
                    ExploreSession.user_id == user.id,
                    ExploreSession.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            _spot = await db.get(Spot, existing.venue_id)
            return ok(_session_result(existing, venue_config(existing.venue_id, _spot)["name"]))

    spot = await db.get(Spot, body.venueId)
    cfg = venue_config(body.venueId, spot)
    grid_ids = body.gridIds or []
    badge_title = badge_for(ratio)

    session = ExploreSession(
        id=new_id("exp"),
        user_id=user.id,
        venue_id=body.venueId,
        mode=body.mode,
        grid_ids=grid_ids,
        explore_ratio=ratio,
        badge_title=badge_title,
        started_at=body.startedAt or "",
        ended_at=body.endedAt or "",
        idempotency_key=idempotency_key or "",
    )
    db.add(session)
    await db.commit()

    newly = await evaluate_medals(db, user)
    result = _session_result(session, cfg["name"])
    result["unlockedMedalIds"] = newly
    return ok(result)


def _session_result(session: ExploreSession, venue_name: str) -> dict:
    return {
        "sessionId": session.id,
        "venueName": venue_name,
        "exploreRatio": session.explore_ratio,
        "gridCount": len(session.grid_ids or []),
        "badgeTitle": session.badge_title,
        "unlockedMedalIds": [],
    }


@router.post("/explore/qr-unlock")
async def qr_unlock(
    body: ExploreQrUnlockRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    qr = QR_CODES.get(body.code.strip())
    if qr is None:
        raise NotFoundError("二维码无效")
    return ok(
        {
            "venueId": qr["venueId"],
            "gridIds": qr["gridIds"],
            "exploreRatio": qr["exploreRatio"],
            "badgeTitle": badge_for(qr["exploreRatio"]),
        }
    )


@router.get("/explore/sessions/{session_id}/share")
async def share_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(ExploreSession, session_id)
    if session is None or session.user_id != user.id:
        raise NotFoundError("探索会话不存在")
    spot = await db.get(Spot, session.venue_id)
    cfg = venue_config(session.venue_id, spot)
    venue_name = cfg["name"]
    percent = int(round(session.explore_ratio * 100))
    return ok(
        {
            "title": f"我在{venue_name}探索了 {percent}%",
            "venueName": venue_name,
            "badgeTitle": session.badge_title,
            "exploreRatio": session.explore_ratio,
            "path": f"/pages/explore/explore?venueId={session.venue_id}",
        }
    )
