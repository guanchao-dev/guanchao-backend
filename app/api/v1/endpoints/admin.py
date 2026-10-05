"""管理后台：审核用户投稿的「宝藏点位」，通过后发布成官方点位。

与前台 spots.py 的区别：这里能跨 owner 查看全部投稿，且能把一条 user_spot
落进官方 spots 表（前台 GET /spots 立刻可见）。整个 router 挂在 require_admin 上，
新增路由不会再漏掉鉴权。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.response import ok, paginated
from app.core.utils import SHANGHAI_TZ, new_id, to_shanghai_iso
from app.db.base import get_db
from app.db.models import Feedback, Spot, SpotHarmonics, UserSpot
from app.schemas import (
    ApproveSubmissionRequest,
    EditSpotRequest,
    HandleFeedbackRequest,
    RejectSubmissionRequest,
    ReplyFeedbackRequest,
)
from app.services import amap
from app.services.content_safety import assert_text_safe
from app.services.tide_predict import harmonics_from_coords

router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])

_VALID_STATUS = {"pending", "approved", "rejected", "all"}

# 发布到 spots 时新点位的默认热度：40 = 文档里的「其他（平列）」档。
# 用 0 会让新点位沉到 GET /spots 列表最底，几乎没人看得到；管理员可在通过时覆盖。
_DEFAULT_HEAT = 40


def _today() -> str:
    """北京时间今天（YYYY-MM-DD），与仓库既有的写法一致。"""
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")


def _admin_item(s: UserSpot) -> dict:
    return {
        "id": s.id,
        "ownerId": s.owner_id,
        "name": s.name,
        "address": s.address,
        "lat": s.lat,
        "lng": s.lng,
        "note": s.note,
        "photoUrl": s.photo_url,
        "status": s.status,
        "reviewNote": s.review_note,
        "approvedSpotId": s.approved_spot_id,
        "reviewedAt": s.reviewed_at,
        "createdAt": to_shanghai_iso(s.created_at),
    }


@router.get("/admin/submissions")
async def list_submissions(
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """列出投稿。status 取 pending / approved / rejected / all。

    路由必须声明在 /admin/submissions/{id} 之前，否则 FastAPI 会拿 {id} 先匹配
    （同 spots.py 里 /spots/mine 的坑，这里目前没有 {id} 路由，先按约定摆好）。
    """
    if status not in _VALID_STATUS:
        raise BadRequestError("status 取值不合法")
    q = select(UserSpot)
    if status != "all":
        q = q.where(UserSpot.status == status)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (
        await db.execute(
            q.order_by(UserSpot.created_at.desc(), UserSpot.id.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()
    return ok(paginated([_admin_item(s) for s in rows], page, pageSize, total))


@router.get("/admin/stats")
async def submission_stats(db: AsyncSession = Depends(get_db)):
    """各状态的投稿数，给后台 tab 上的计数徽章用。"""
    rows = (
        await db.execute(select(UserSpot.status, func.count()).group_by(UserSpot.status))
    ).all()
    counts = {"pending": 0, "approved": 0, "rejected": 0}
    for status, n in rows:
        if status in counts:
            counts[status] = n
    return ok(counts)


@router.get("/admin/submissions/{submission_id}/geo")
async def submission_geo(submission_id: str, db: AsyncSession = Depends(get_db)):
    """按投稿坐标逆地理编码，供后台在「通过」时自动填「城市 / 区」。

    查不到就返回空串，前端再退回用投稿地址串做粗略解析。
    """
    s = await db.get(UserSpot, submission_id)
    if s is None:
        raise NotFoundError("投稿不存在")
    if s.lat is None or s.lng is None:
        return ok({"city": "", "district": "", "address": s.address or ""})
    geo = await amap.regeo(s.lat, s.lng)
    if not geo.get("address"):
        geo["address"] = s.address or ""
    return ok(geo)


@router.post("/admin/submissions/{submission_id}/approve")
async def approve_submission(
    submission_id: str,
    body: ApproveSubmissionRequest,
    db: AsyncSession = Depends(get_db),
):
    """审核通过：把投稿发布成官方点位，同时把投稿标记为 approved。

    两步写在同一次 commit 里，保证「要么都成、要么都不成」。
    """
    # 行锁：防止两个管理员同时点「通过」各插一条 Spot。
    s = (
        await db.execute(select(UserSpot).where(UserSpot.id == submission_id).with_for_update())
    ).scalar_one_or_none()
    if s is None:
        raise NotFoundError("投稿不存在")
    if s.status == "approved":
        raise ConflictError("该投稿已通过审核，请勿重复发布")
    if s.lat is None or s.lng is None:
        raise BadRequestError("该投稿缺少坐标，无法发布")

    city = (body.city or "").strip()
    district = (body.district or "").strip()
    if not city:
        raise BadRequestError("请填写所属城市")
    name = (body.name or s.name or "").strip()
    if not name:
        raise BadRequestError("请填写点位名称")
    # 用户备注当「观察提示」；地址**不能**当「简介」——
    # 简介会在点位详情里展示，塞一串地址既不合适，还会被 spot-guide 的
    # observeHint -> description 兜底链显示到「可能遇到」下面（已经踩过这个坑）。
    # 简介留给管理员填，没填就空着。
    observe_hint = (body.observeHint if body.observeHint is not None else s.note) or ""
    description = (body.description or "").strip()
    assert_text_safe(name, city, district, observe_hint, description)

    reviewed = _today()
    spot = Spot(
        # 前缀取 spot_z：home.py::_resolve_spot 在无参时用 order_by(Spot.id).limit(1)
        # 兜底选「默认点位」。种子里是 spot_qd_* / spot_wh_*，只有排在它们之后的
        # 前缀才不会被新点位顶掉（spot_u_* 的 u < w，会抢走威海的默认点位）。
        id=new_id("spot_z"),
        name=name[:64],
        city=city[:32],
        district=district[:32],
        cover_key=(s.photo_url or "")[:255],
        open_time=(body.openTime or "").strip()[:128],
        age_hint=(body.ageHint or "").strip()[:128],
        safety_tags=body.safetyTags or [],
        observe_hint=observe_hint,
        description=description,
        gear_list=[],
        # 不把 owner_id 写进 source：source 会经 GET /spots/{id} 公开返回，带内部 id 是泄露。
        source=(body.source or "用户投稿").strip()[:255],
        reviewed_at=reviewed,
        lat=s.lat,
        lng=s.lng,
        heat=body.heat if body.heat is not None else _DEFAULT_HEAT,
    )
    s.status = "approved"
    s.reviewed_at = reviewed
    s.approved_spot_id = spot.id
    s.review_note = ""
    db.add(spot)

    # 按坐标从沿海网格算出这个点位自己的调和常数存下来（潮汐接口优先用它），
    # 这样新点位是"真算出来的"，而不是拿几十公里外的点位凑合。
    # 超出网格覆盖范围（用户传得特别远）就跳过，潮汐那边会自动就近兜底。
    harmonics = harmonics_from_coords(s.lat, s.lng)
    if harmonics:
        db.add(
            SpotHarmonics(
                spot_id=spot.id,
                # JSON 里元组会变成数组，读出时再转回 tuple（见 tide._resolve_site）
                data={k: [v[0], v[1]] for k, v in harmonics.items()},
                source="FES2022b-interp",
                lat=s.lat,
                lng=s.lng,
            )
        )

    await db.commit()
    return ok({"submissionId": s.id, "status": "approved", "spotId": spot.id})


@router.get("/admin/spots/{spot_id}")
async def get_published_spot(spot_id: str, db: AsyncSession = Depends(get_db)):
    """读一个已发布官方点位的可编辑字段（后台编辑弹窗用）。"""
    s = await db.get(Spot, spot_id)
    if s is None:
        raise NotFoundError("点位不存在")
    return ok(
        {
            "id": s.id,
            "name": s.name,
            "city": s.city,
            "district": s.district,
            "description": s.description,
            "observeHint": s.observe_hint,
            "heat": s.heat,
            "source": s.source,
            "reviewedAt": s.reviewed_at,
        }
    )


@router.post("/admin/spots/{spot_id}/edit")
async def edit_published_spot(
    spot_id: str, body: EditSpotRequest, db: AsyncSession = Depends(get_db)
):
    """编辑已发布的官方点位。只改显式传了的字段，其余保持不动。"""
    s = await db.get(Spot, spot_id)
    if s is None:
        raise NotFoundError("点位不存在")

    provided = body.model_fields_set
    if "name" in provided and body.name is not None:
        name = body.name.strip()
        if not name:
            raise BadRequestError("点位名称不能为空")
        s.name = name[:64]
    if "city" in provided and body.city is not None:
        city = body.city.strip()
        if not city:
            raise BadRequestError("所属城市不能为空")
        s.city = city[:32]
    if "district" in provided and body.district is not None:
        s.district = body.district.strip()[:32]
    if "description" in provided:
        s.description = (body.description or "").strip()
    if "observeHint" in provided:
        s.observe_hint = (body.observeHint or "").strip()
    if "heat" in provided and body.heat is not None:
        s.heat = int(body.heat)

    assert_text_safe(s.name, s.city, s.district, s.description, s.observe_hint)
    await db.commit()
    return ok(
        {
            "id": s.id,
            "name": s.name,
            "city": s.city,
            "district": s.district,
            "description": s.description,
            "observeHint": s.observe_hint,
            "heat": s.heat,
        }
    )


@router.post("/admin/submissions/{submission_id}/reject")
async def reject_submission(
    submission_id: str,
    body: RejectSubmissionRequest,
    db: AsyncSession = Depends(get_db),
):
    """驳回投稿。

    已通过的也可以再驳回 —— 这时会把之前发布出去的官方点位一并**下架**（删掉），
    否则前台还挂着一条已经被驳回的点位。
    """
    s = await db.get(UserSpot, submission_id)
    if s is None:
        raise NotFoundError("投稿不存在")
    if s.status == "rejected":
        # 幂等：重复驳回直接返回当前状态，不再改 note。
        return ok({"submissionId": s.id, "status": "rejected"})

    unpublished = ""
    if s.status == "approved" and s.approved_spot_id:
        spot = await db.get(Spot, s.approved_spot_id)
        if spot is not None:
            await db.delete(spot)
            unpublished = spot.id
        harm = await db.get(SpotHarmonics, s.approved_spot_id)
        if harm is not None:
            await db.delete(harm)

    s.status = "rejected"
    s.review_note = (body.reason or "").strip()[:255]
    s.reviewed_at = _today()
    await db.commit()
    return ok({"submissionId": s.id, "status": "rejected", "unpublishedSpotId": unpublished})


@router.get("/admin/feedback")
async def list_feedback(
    kind: str = Query("all"),
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """用户反馈列表。

    - kind：`general` 通用反馈（「关于」页来的，没有点位）/ `review` 审核反馈
      （点位详情页来的，带点位）/ `all`
    - status：`pending` 未处理 / `handled` 已处理 / `all`

    带上点位名和城市，管理员一眼能看出是针对哪个点位的。
    """
    if kind not in ("all", "general", "review"):
        raise BadRequestError("kind 取值不合法")
    if status not in ("pending", "handled", "all"):
        raise BadRequestError("status 取值不合法")

    q = select(Feedback)
    if kind == "general":
        q = q.where(Feedback.spot_id == "")
    elif kind == "review":
        q = q.where(Feedback.spot_id != "")
    if status == "pending":
        q = q.where(Feedback.handled.is_(False))
    elif status == "handled":
        q = q.where(Feedback.handled.is_(True))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (
        await db.execute(
            q.order_by(Feedback.created_at.desc(), Feedback.id.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).scalars().all()

    spot_ids = {r.spot_id for r in rows if r.spot_id}
    spots: dict[str, Spot] = {}
    subs: dict[str, UserSpot] = {}
    if spot_ids:
        found = (await db.execute(select(Spot).where(Spot.id.in_(spot_ids)))).scalars().all()
        spots = {s.id: s for s in found}
        # 反向找投稿：点位的存档 id 记在 user_spots.approved_spot_id 上。
        # 这样即使点位已经被「驳回并下架」删掉了（Spot 查不到），
        # 仍然能定位到那条投稿，管理员照样跳得过去。
        subrows = (
            await db.execute(
                select(UserSpot).where(UserSpot.approved_spot_id.in_(spot_ids))
            )
        ).scalars().all()
        subs = {u.approved_spot_id: u for u in subrows if u.approved_spot_id}

    items = []
    for r in rows:
        spot = spots.get(r.spot_id)
        sub = subs.get(r.spot_id)
        items.append(
            {
                "id": r.id,
                "content": r.content,
                "contact": r.contact,
                "handled": bool(r.handled),
                "spotId": r.spot_id,
                "spotName": spot.name if spot else (sub.name if sub else ""),
                "spotCity": spot.city if spot else "",
                "spotLat": spot.lat if spot else None,
                "spotLng": spot.lng if spot else None,
                # 反馈针对的那条投稿（用于后台里跳转）
                "submissionId": sub.id if sub else "",
                "submissionStatus": sub.status if sub else "",
                "reply": r.reply or "",
                "repliedAt": r.replied_at or "",
                "replySeen": bool(r.reply_seen),
                "createdAt": to_shanghai_iso(r.created_at),
            }
        )
    return ok(paginated(items, page, pageSize, total))


@router.get("/admin/feedback/stats")
async def feedback_stats(db: AsyncSession = Depends(get_db)):
    """按「通用 / 审核」× 「未处理 / 已处理」四宫格计数，给后台 tab 角标用。"""
    rows = (
        await db.execute(
            select(Feedback.spot_id, Feedback.handled, func.count()).group_by(
                Feedback.spot_id, Feedback.handled
            )
        )
    ).all()
    counts = {
        "general": {"pending": 0, "handled": 0},
        "review": {"pending": 0, "handled": 0},
    }
    for spot_id, handled, n in rows:
        # spot_id 为空 = 「关于」页来的通用反馈
        bucket = "review" if spot_id else "general"
        counts[bucket]["handled" if handled else "pending"] += n
    return ok(counts)


@router.post("/admin/feedback/{feedback_id}/handle")
async def handle_feedback(
    feedback_id: str, body: HandleFeedbackRequest, db: AsyncSession = Depends(get_db)
):
    """标记反馈是否已处理。"""
    r = await db.get(Feedback, feedback_id)
    if r is None:
        raise NotFoundError("反馈不存在")
    r.handled = bool(body.handled)
    await db.commit()
    return ok({"id": r.id, "handled": bool(r.handled)})


@router.post("/admin/feedback/{feedback_id}/reply")
async def reply_feedback(
    feedback_id: str, body: ReplyFeedbackRequest, db: AsyncSession = Depends(get_db)
):
    """回复用户反馈。

    回复后自动标记为「已处理」，并把 reply_seen 置回 False ——
    用户端「消息」里就会出现一条未读，小红点亮起来。
    """
    r = await db.get(Feedback, feedback_id)
    if r is None:
        raise NotFoundError("反馈不存在")
    reply = (body.reply or "").strip()
    if not reply:
        raise BadRequestError("回复内容不能为空")
    assert_text_safe(reply)

    r.reply = reply[:1000]
    r.replied_at = _today()
    r.reply_seen = False
    r.handled = True
    await db.commit()
    return ok({"id": r.id, "reply": r.reply, "repliedAt": r.replied_at})
