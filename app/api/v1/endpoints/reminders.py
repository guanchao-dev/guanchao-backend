"""潮汐提醒：用户在日历里选某天某时间，提前 1 小时提醒。

当前只落地存储 + 增删查接口；实际「发消息」待接入微信订阅消息模板后实现。
（见 send_due_reminders 的 TODO。）
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.response import ok
from app.core.utils import SHANGHAI_TZ, new_id
from app.db.base import get_db
from app.db.models import Reminder, User
from app.schemas import ReminderCreateRequest

router = APIRouter(tags=["reminders"])

REMIND_BEFORE_MIN = 60  # 提前 60 分钟提醒


def _parse_beijing(s: str) -> datetime:
    """把前端传来的北京时间字符串解析成 naive datetime（北京时间）。"""
    s = (s or "").strip().replace("T", " ").replace("Z", "")
    try:
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        raise BadRequestError("提醒时间格式不正确")


def _fmt_beijing(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:00+08:00")


def _now_beijing() -> datetime:
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


def _reminder_item(r: Reminder) -> dict:
    return {
        "id": r.id,
        "spotId": r.spot_id,
        "remindAt": _fmt_beijing(r.remind_at),
        "notifyAt": _fmt_beijing(r.remind_at - timedelta(minutes=REMIND_BEFORE_MIN)),
        "note": r.note,
        "status": r.status,
    }


@router.post("/reminders")
async def create_reminder(
    body: ReminderCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    remind_at = _parse_beijing(body.remindAt)
    if remind_at <= _now_beijing():
        raise BadRequestError("提醒时间须晚于当前时间")
    reminder = Reminder(
        id=new_id("rm"),
        user_id=user.id,
        spot_id=body.spotId or "",
        remind_at=remind_at,
        note=body.note or "赶海提醒",
    )
    db.add(reminder)
    await db.commit()
    return ok(_reminder_item(reminder))


@router.get("/reminders")
async def list_reminders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Reminder)
            .where(Reminder.user_id == user.id, Reminder.status == "pending")
            .order_by(Reminder.remind_at.asc())
        )
    ).scalars().all()
    return ok({"list": [_reminder_item(r) for r in rows]})


@router.delete("/reminders/{reminder_id}")
async def delete_reminder(
    reminder_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.get(Reminder, reminder_id)
    if r is None or r.user_id != user.id:
        raise NotFoundError("提醒不存在")
    await db.delete(r)
    await db.commit()
    return ok(None, "已取消提醒")


async def send_due_reminders(db: AsyncSession) -> int:
    """扫描到期提醒并发送微信订阅消息（提前 REMIND_BEFORE_MIN 分钟）。

    TODO(wechat-subscribe): 待小程序后台配置好「订阅消息」模板、拿到模板 ID 后，
    在此处实现：
      1. 查 openid（users 表）+ 模板 ID（settings.wechat_subscribe_tmpl）；
      2. 调微信 subscribeMessage.send 接口，data 填「潮汐提醒」内容；
      3. 成功把 status 置为 sent。
    当前无模板，仅把已到期且未发送的提醒标记为 sent，避免后续重复扫描。
    """
    now = _now_beijing()
    notify_at = now - timedelta(minutes=REMIND_BEFORE_MIN)
    rows = (
        await db.execute(
            select(Reminder).where(
                Reminder.status == "pending", Reminder.remind_at <= notify_at
            )
        )
    ).scalars().all()
    for r in rows:
        # 占位：真正发消息见上方 TODO
        r.status = "sent"
    if rows:
        await db.commit()
    return len(rows)
