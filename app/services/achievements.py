"""成就 / 勋章结算。根据用户行为判断并解锁勋章。"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import new_id
from app.db.models import Card, Checkin, Guess, Medal, QuizAttempt, User, UserMedal

# 勋章解锁来源（对应 pending-unlocks / unlock-ack 的 source 字段）
MEDAL_SOURCE = {
    "medal_1": "card",
    "medal_2": "checkin",
    "medal_3": "quiz",
    "medal_4": "card",
    "medal_5": "guess",
}


async def evaluate_medals(db: AsyncSession, user: User) -> list[str]:
    """结算勋章，返回本次新解锁的 medal id 列表。"""
    card_count = (
        await db.execute(select(func.count()).select_from(Card).where(Card.user_id == user.id))
    ).scalar() or 0
    distinct_spots = (
        await db.execute(
            select(func.count(func.distinct(Checkin.spot_id))).where(
                Checkin.user_id == user.id, Checkin.spot_id != ""
            )
        )
    ).scalar() or 0
    safety_passed = (
        await db.execute(
            select(func.count())
            .select_from(QuizAttempt)
            .where(QuizAttempt.user_id == user.id, QuizAttempt.quiz_id == "quiz_safety", QuizAttempt.passed == True)  # noqa: E712
        )
    ).scalar() or 0
    guess_count = (
        await db.execute(select(func.count()).select_from(Guess).where(Guess.user_id == user.id))
    ).scalar() or 0

    conditions = {
        "medal_1": card_count >= 1,     # 首次生成图鉴卡
        "medal_2": distinct_spots >= 3,  # 3 个不同点位打卡
        "medal_3": safety_passed >= 1,   # 完成安全观察闯关
        "medal_4": card_count >= 5,      # 累计 5 张图鉴卡
        "medal_5": guess_count >= 3,     # 对照图鉴完成 3 次猜测
    }

    existing = set(
        (await db.execute(select(UserMedal.medal_id).where(UserMedal.user_id == user.id)))
        .scalars()
        .all()
    )
    medals = {m.id: m for m in (await db.execute(select(Medal))).scalars().all()}
    newly = []
    score_add = 0
    for medal_id, ok in conditions.items():
        if ok and medal_id not in existing:
            db.add(
                UserMedal(
                    id=new_id("um"),
                    user_id=user.id,
                    medal_id=medal_id,
                    acked=False,
                    source=MEDAL_SOURCE.get(medal_id, "other"),
                )
            )
            existing.add(medal_id)
            newly.append(medal_id)
            m = medals.get(medal_id)
            if m is not None and isinstance(m.rewards, dict):
                try:
                    score_add += int(m.rewards.get("score") or 0)
                except (TypeError, ValueError):
                    pass
    if newly:
        user.score = (user.score or 0) + score_add
        await db.commit()
    return newly
