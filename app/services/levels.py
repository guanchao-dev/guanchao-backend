"""用户等级：成就值 + 去过的赶海点位 -> 经验 -> 等级。

经验怎么算
----------
    xp = 成就值 + 去过的点位数 × XP_PER_SPOT

- **成就值** = 用户已解锁勋章的 rewards.score 累计，落在 `users.score`
  （见 services/achievements.py）。
- **去过的点位** = 用户的 观潮记录(watch_records) 与 打卡记录(checkins) 里
  出现过的不同 spot_id 的并集。同一个点只算一次，重复去不再加经验。
- **每去一个点位给 XP_PER_SPOT 点经验**。取 60 —— 和一枚稀有勋章同档：实地跑
  一个点位是这个产品最重的行为，值一枚稀有勋章；但不能盖过成就系统（全部 18 枚
  勋章合计 1320），所以再高就不合适了。

等级门槛（累计经验）
--------------------
见 LEVEL_THRESHOLDS，到顶封顶在最高级（MAX_LEVEL）。满经验 ≈ 3600
（1320 成就值 + 38 个点位 × 60），所以最高级只有集齐勋章又跑遍点位的人到得了。
"""
from __future__ import annotations

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Checkin, SpotVisit, User, WatchRecord

# 每去过一个（新）点位加的经验
XP_PER_SPOT = 60

# 累计经验门槛：第 i 项是「到 Lv.i+1 所需的累计经验」，首项恒为 0
LEVEL_THRESHOLDS: tuple[int, ...] = (0, 60, 150, 300, 500, 800, 1200, 1700, 2300, 3000)

MAX_LEVEL = len(LEVEL_THRESHOLDS)


def level_for(xp: int) -> int:
    """经验 -> 等级（1 起，封顶 MAX_LEVEL）。"""
    level = 1
    for i, need in enumerate(LEVEL_THRESHOLDS):
        if xp >= need:
            level = i + 1
        else:
            break
    return level


def progress_for(xp: int) -> dict:
    """经验 -> 等级 + 进度。到顶后 levelProgress 恒为 100、xpToNext 为 0。"""
    xp = max(0, int(xp or 0))
    level = level_for(xp)
    base = LEVEL_THRESHOLDS[level - 1]
    if level >= MAX_LEVEL:
        return {
            "level": level,
            "xp": xp,
            "levelBase": base,
            "levelNext": None,
            "xpToNext": 0,
            "levelProgress": 100,
        }
    nxt = LEVEL_THRESHOLDS[level]
    span = nxt - base
    percent = round((xp - base) * 100 / span) if span else 99
    # 非满级时封顶 99：差 1 点经验四舍五入会算成 100%，但那一格其实还没满
    return {
        "level": level,
        "xp": xp,
        "levelBase": base,
        "levelNext": nxt,
        "xpToNext": nxt - xp,
        "levelProgress": max(0, min(99, percent)),
    }


async def visited_spot_ids(db: AsyncSession, user_id: str) -> set[str]:
    """这个用户去过的所有赶海点位 id。

    **三个来源取并集**（按 spot_id 去重）：
    - `spot_visits`：显式点亮（观潮结束时前端调 POST /spots/{id}/visit）
    - `watch_records`：观潮记录（按 owner_id 归属，登录用户是 `user:{id}`）
    - `checkins`：打卡记录

    任一有记录就算「去过」。这样老用户的观潮/打卡历史不必补点亮也能算数，
    也不会因为前端什么时候开始调点亮接口而漏算。
    """
    sub = union(
        select(SpotVisit.spot_id.label("sid")).where(
            SpotVisit.user_id == user_id, SpotVisit.spot_id != ""
        ),
        select(WatchRecord.spot_id.label("sid")).where(
            WatchRecord.owner_id == f"user:{user_id}", WatchRecord.spot_id != ""
        ),
        select(Checkin.spot_id.label("sid")).where(
            Checkin.user_id == user_id, Checkin.spot_id != ""
        ),
    ).subquery()
    rows = (await db.execute(select(sub.c.sid))).scalars().all()
    return {sid for sid in rows if sid}


async def visited_spot_count(db: AsyncSession, user_id: str) -> int:
    """去过多少个不同的赶海点位。口径见 visited_spot_ids。"""
    return len(await visited_spot_ids(db, user_id))


async def user_progress(db: AsyncSession, user: User) -> dict:
    """算出一个用户的完整等级信息（不落库）。"""
    visited = await visited_spot_count(db, user.id)
    info = progress_for((user.score or 0) + visited * XP_PER_SPOT)
    info["score"] = user.score or 0
    info["visitedSpotCount"] = visited
    return info


async def sync_user_level(db: AsyncSession, user: User) -> dict:
    """把等级同步到 `users.level`（只在变化时写一次），返回完整进度。

    等级是从成就值 + 去过的点位推出来的派生值，不单独维护。哪里会让这两个数变
    （发勋章、打卡、观潮结束），或者哪里要对外展示等级（/auth/me），调一下这里。
    """
    info = await user_progress(db, user)
    if user.level != info["level"]:
        user.level = info["level"]
        await db.commit()
    return info
