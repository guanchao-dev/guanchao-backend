from math import asin, cos, radians, sin, sqrt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_identity
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.response import ok, paginated
from app.core.utils import new_id, to_shanghai_iso
from app.db.base import get_db
from app.db.models import Spot, SpotVisit, Upload, User, UserSpot
from app.schemas import UserSpotCreateRequest, VisitSpotRequest
from app.services import levels, storage
from app.services.spot_search import spot_keyword_order, spot_keyword_where
from app.services.content_safety import assert_text_safe

# 一条投稿最多几张图
_MAX_PHOTOS = 3

router = APIRouter(tags=["spots"])

# 单个身份最多能上传多少个宝藏点位（防刷）
_MAX_USER_SPOTS = 100


def _user_spot_item(s: UserSpot) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "address": s.address,
        "lat": s.lat,
        "lng": s.lng,
        "note": s.note,
        "photoUrl": s.photo_url,
        # 用户上传的照片（最多 3 张）。photoUrl 保留成第一张，兼容只认它的老调用方。
        "photos": [storage.public_url(k) for k in (s.photo_keys or []) if k],
        # 审核状态：前端「我的点位」据此显示 审核中/已通过/未通过 徽章。
        "status": s.status or "pending",
        "reviewNote": s.review_note,
        "reviewedAt": s.reviewed_at,
        "createdAt": to_shanghai_iso(s.created_at),
    }


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6371000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return int(2 * r * asin(sqrt(a)))


def _spot_list_item(s: Spot, lat: float | None, lng: float | None) -> dict:
    has_loc = lat is not None and lng is not None and s.lat is not None and s.lng is not None
    distance = _haversine(lat, lng, s.lat, s.lng) if has_loc else None
    # 照片：优先 photo_keys（用户投稿审核通过的多图）；没有就退回老的单图 cover_key
    photos = [storage.public_url(k) for k in (s.photo_keys or []) if k]
    if not photos and s.cover_key:
        photos = [storage.public_url(s.cover_key)]
    return {
        "id": s.id,
        "name": s.name,
        "city": s.city,
        # 所属区（崂山区/黄岛区/…）。热度排名按区分组，前端也用它做二级展示
        "district": s.district or "",
        "heat": s.heat or 0,
        "latitude": s.lat,
        "longitude": s.lng,
        "distanceM": distance,
        "coverUrl": photos[0] if photos else "",
        "photos": photos,
        "openTime": s.open_time,
        "ageHint": s.age_hint,
        "safetyTags": s.safety_tags or [],
        "observeHint": s.observe_hint,
        "species": [],
    }


@router.get("/spots")
async def list_spots(
    city: str | None = Query(None),
    keyword: str | None = Query(None),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    q = select(Spot)
    if city:
        q = q.where(Spot.city == city)
    # 关键词搜索：匹配名称/区/城市/提示/描述，并按命中位置排（见 services/spot_search.py）。
    # 不传 keyword 时保持原来的「按热度排」。
    kw = (keyword or "").strip()
    order_by = [Spot.heat.desc(), Spot.id]
    if kw:
        q = q.where(spot_keyword_where(kw))
        order_by = spot_keyword_order(kw)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (
        await db.execute(
            q.order_by(*order_by).offset((page - 1) * pageSize).limit(pageSize)
        )
    ).scalars().all()
    items = [_spot_list_item(s, lat, lng) for s in rows]
    return ok(paginated(items, page, pageSize, total))


@router.post("/spots")
async def create_user_spot(
    body: UserSpotCreateRequest,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """上传一个「宝藏点位」。游客可调，身份同签到（user:{id} 或 client:{id}）。

    注意：这里写的是 user_spots 表，与官方策展的 spots 完全分开，不会出现在 GET /spots 里。
    """
    name = (body.name or "").strip()
    address = (body.address or "").strip()
    note = (body.note or "").strip()
    if not name:
        raise BadRequestError("请填写点位名称")
    if body.lat is None or body.lng is None:
        raise BadRequestError("请在地图上选择点位")
    assert_text_safe(name, address, note)

    _, owner_id = identity
    # 只数「有效」投稿：被驳回的不该继续占用配额，否则用户被驳回几次后就传不动了。
    count = (
        await db.execute(
            select(func.count())
            .select_from(UserSpot)
            .where(UserSpot.owner_id == owner_id, UserSpot.status != "rejected")
        )
    ).scalar() or 0
    if count >= _MAX_USER_SPOTS:
        raise BadRequestError(f"上传的点位已达上限（{_MAX_USER_SPOTS} 个）")

    # 照片（可选，最多 3 张）：逐个校验归属和状态，存 object key 而不是用户传的 URL ——
    # key 由服务端解析，避免客户端塞任意地址进来。
    upload_ids = [u for u in (body.photoUploadIds or []) if u]
    if len(upload_ids) > _MAX_PHOTOS:
        raise BadRequestError(f"最多上传 {_MAX_PHOTOS} 张照片")
    photo_keys: list[str] = []
    for uid in upload_ids:
        up = await db.get(Upload, uid)
        if up is None or up.owner_id != owner_id:
            raise NotFoundError("图片不存在")
        if up.status != "approved":
            raise BadRequestError("图片尚未上传完成")
        if up.object_key:
            photo_keys.append(up.object_key)

    s = UserSpot(
        id=new_id("usp"),
        owner_id=owner_id,
        name=name[:64],
        address=address[:255],
        note=note[:1000],
        photo_url=(body.photoUrl or "").strip()[:255],
        photo_keys=photo_keys,
        lat=float(body.lat),
        lng=float(body.lng),
    )
    db.add(s)
    await db.commit()
    return ok(_user_spot_item(s))


@router.get("/spots/mine")
async def my_user_spots(
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """我上传过的宝藏点位（按时间倒序）。

    ⚠️ 这个路由必须声明在 /spots/{spot_id} 之前。否则 FastAPI 会让 {spot_id} 先匹配，
    把 "mine" 当成点位 ID 去查库，结果恒为 404。
    """
    _, owner_id = identity
    rows = (
        await db.execute(
            select(UserSpot)
            .where(UserSpot.owner_id == owner_id)
            .order_by(UserSpot.created_at.desc())
        )
    ).scalars().all()
    return ok({"list": [_user_spot_item(s) for s in rows]})


@router.get("/spots/treasure")
async def list_treasure_spots(
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """「宝藏地点」：用户投稿、管理员审核通过后发布出来的点位。

    与官方策展点位的区分不靠 spots.source 的文案（管理员可改），而是靠
    user_spots.approved_spot_id 反向关联 —— 只有走过审核的记录才会出现在这里。

    ⚠️ 必须声明在 /spots/{spot_id} 之前，否则 "treasure" 会被当成点位 ID。
    """
    q = (
        select(Spot)
        .join(UserSpot, UserSpot.approved_spot_id == Spot.id)
        .where(UserSpot.status == "approved")
    )
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (
        await db.execute(
            q.order_by(UserSpot.reviewed_at.desc(), Spot.id.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()
    return ok(paginated([_spot_list_item(s, lat, lng) for s in rows], page, pageSize, total))


@router.get("/spots/visited")
async def list_visited_spots(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我点亮过的赶海点（「我的赶海点」页）。

    ⚠️ 必须声明在 /spots/{spot_id} 之前，否则 "visited" 会被当成点位 ID。
    返回**全部**点位并逐个打 `visited` 标记，外加 total / visitedCount ——
    前端据此画进度条和点亮墙。「去过」的口径见 services/levels.py::visited_spot_ids
    （显式点亮 ∪ 观潮记录 ∪ 打卡记录）。
    """
    spots = (
        await db.execute(select(Spot).order_by(Spot.heat.desc(), Spot.id))
    ).scalars().all()
    visited_ids = await levels.visited_spot_ids(db, user.id)
    items = []
    for s in spots:
        photos = [storage.public_url(k) for k in (s.photo_keys or []) if k]
        if not photos and s.cover_key:
            photos = [storage.public_url(s.cover_key)]
        items.append(
            {
                "id": s.id,
                "name": s.name,
                "city": s.city,
                "district": s.district or "",
                "icon": photos[0] if photos else "",
                "visited": s.id in visited_ids,
            }
        )
    return ok(
        {
            "total": len(items),
            "visitedCount": sum(1 for i in items if i["visited"]),
            "list": items,
        }
    )


@router.post("/spots/{spot_id}/visit")
async def visit_spot(
    spot_id: str,
    body: VisitSpotRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """点亮一个赶海点（幂等）。观潮结束时前端会调。

    同一用户同一点位只记一次；**每去一个新的点位就多一份经验**
    （`XP_PER_SPOT`，见 services/levels.py），所以新点亮时会顺手把等级重算一遍，
    返回值里直接带上最新经验/等级，前端不用再查一次。
    """
    spot = await db.get(Spot, spot_id)
    if spot is None:
        raise NotFoundError("点位不存在")
    existing = (
        await db.execute(
            select(SpotVisit).where(SpotVisit.user_id == user.id, SpotVisit.spot_id == spot_id)
        )
    ).scalar_one_or_none()
    newly = existing is None
    if newly:
        db.add(
            SpotVisit(
                id=new_id("sv"),
                user_id=user.id,
                spot_id=spot_id,
                session_id=str((body.sessionId if body else "") or "")[:64],
                first_visited_at=str((body.visitedAt if body else "") or "")[:32],
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            # 「先查后插」不是原子的：并发下两个请求会同时判定为「新点亮」，
            # 靠 uk_spot_visits_user_spot 在库层拦住，这里当成已点亮处理，别抛 500。
            await db.rollback()
            newly = False
    if newly:
        # 去过的点位多了一个 → 经验/等级跟着变（只在变化时才写库）
        info = await levels.sync_user_level(db, user)
    else:
        info = await levels.user_progress(db, user)
    return ok(
        {
            "visited": True,
            "newlyVisited": newly,
            "spotId": spot_id,
            "visitedSpotCount": info["visitedSpotCount"],
            "xp": info["xp"],
            "level": info["level"],
        }
    )


@router.delete("/spots/{spot_id}")
async def delete_user_spot(
    spot_id: str,
    identity: tuple = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
):
    """删除自己的宝藏点位。别人的点位一律当作不存在（不泄露是否真实存在）。"""
    _, owner_id = identity
    s = await db.get(UserSpot, spot_id)
    if s is None or s.owner_id != owner_id:
        raise NotFoundError("点位不存在")
    await db.delete(s)
    await db.commit()
    return ok({"id": spot_id})


@router.get("/spots/{spot_id}")
async def spot_detail(spot_id: str, db: AsyncSession = Depends(get_db)):
    s = await db.get(Spot, spot_id)
    if s is None:
        raise NotFoundError("点位不存在")
    item = _spot_list_item(s, None, None)
    item.update(
        {
            "description": s.description,
            "gearList": s.gear_list or [],
            "source": s.source,
            "reviewedAt": s.reviewed_at,
        }
    )
    return ok(item)


@router.get("/gear")
async def gear():
    """赶海装备清单。scene 为地形场景：rocky 礁石 / mudflat 泥滩 / sandy 沙滩。

    - scenes：该装备适用于哪些场景（用于筛选显示）；
    - mustHave：该装备在哪些场景是「必带」（是 scenes 的子集），前端按当前选中场景标必带。
    """
    return ok(
        {
            "list": [
                {"name": "防滑鞋", "why": "礁石湿滑，防滑鞋是第一安全保障", "scenes": ["rocky", "mudflat", "sandy"], "mustHave": ["rocky"]},
                {"name": "长筒雨靴", "why": "泥滩泥泞易陷脚，长筒靴防水防陷", "scenes": ["mudflat"], "mustHave": ["mudflat"]},
                {"name": "遮阳帽", "why": "沙滩日晒强，遮阳防晒", "scenes": ["rocky", "sandy"], "mustHave": ["sandy"]},
                {"name": "防风外套", "why": "海边风大，注意保暖", "scenes": ["rocky"], "mustHave": []},
                {"name": "观察盒", "why": "透明盒便于近距离观察，看完记得放回", "scenes": ["rocky", "sandy"], "mustHave": []},
                {"name": "放大镜", "why": "看看藤壶的小脚和海藻的纹理", "scenes": ["rocky"], "mustHave": []},
                {"name": "小水桶", "why": "装海水观察小生物", "scenes": ["mudflat", "sandy"], "mustHave": []},
                {"name": "小铲子", "why": "挖蛤蜊、蛏子用，注意别破坏滩涂", "scenes": ["mudflat", "sandy"], "mustHave": []},
            ]
        }
    )
