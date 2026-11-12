"""海洋图鉴卡包：代币抽卡。

卡池只放「物理海洋 / 海洋科技 / 其他海洋生物」三类；赶海生物仍然靠识别点亮。
抽到的卡直接写入 species_unlocks，图鉴里同步点亮。
"""
import random

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.exceptions import BadRequestError
from app.core.response import ok
from app.core.utils import new_id
from app.db.base import get_db
from app.db.models import Medal, Species, SpeciesUnlock, User, UserMedal
from app.services.achievements import COIN_BY_RARITY

router = APIRouter(tags=["gacha"])

PACK_COST = 100
PACK_SIZE = 3
STARTER_COINS = 100  # 第一次打开钱包送一包，方便体验
POOL_CATEGORIES = ("physical", "tech", "other_marine")


def _card(s: Species) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "category": s.category,
        "summary": s.summary,
        "coverUrl": f"/encyclopedia/{s.id}/cover" if s.cover_key else "",
    }


@router.get("/wallet")
async def wallet(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 老用户第一次打开钱包时，把已经拿到的勋章按稀有度补一次代币
    if not user.coins_seeded:
        medals = (
            await db.execute(
                select(Medal)
                .join(UserMedal, UserMedal.medal_id == Medal.id)
                .where(UserMedal.user_id == user.id)
            )
        ).scalars().all()
        add = STARTER_COINS
        for m in medals:
            reward = COIN_BY_RARITY.get(m.rarity, 0)
            if isinstance(m.rewards, dict):
                try:
                    explicit = int(m.rewards.get("coins") or 0)
                    if explicit:
                        reward = explicit
                except (TypeError, ValueError):
                    pass
            add += reward
        user.coins = (user.coins or 0) + add
        user.coins_seeded = True
        await db.commit()
    return ok({"coins": user.coins or 0})


@router.post("/gacha/draw")
async def draw_pack(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """花 100 代币开一个卡包，最多 3 张，只抽未点亮的卡。"""
    locked_user = (
        await db.execute(select(User).where(User.id == user.id).with_for_update())
    ).scalar_one()
    if (locked_user.coins or 0) < PACK_COST:
        raise BadRequestError("代币不够，再去潮池收集几种新生物吧")

    owner_id = f"user:{user.id}"
    lit = set(
        (
            await db.execute(
                select(SpeciesUnlock.species_id).where(SpeciesUnlock.owner_id == owner_id)
            )
        ).scalars().all()
    )
    q = select(Species).where(Species.category.in_(POOL_CATEGORIES))
    if lit:
        q = q.where(~Species.id.in_(lit))
    rows = (await db.execute(q)).scalars().all()
    if not rows:
        raise BadRequestError("这个卡包已经集齐了")

    picked = random.sample(rows, min(PACK_SIZE, len(rows)))
    for s in picked:
        db.add(
            SpeciesUnlock(
                id=new_id("su"),
                owner_id=owner_id,
                species_id=s.id,
                seen=False,
            )
        )
    locked_user.coins = (locked_user.coins or 0) - PACK_COST
    await db.commit()

    return ok(
        {
            "coins": locked_user.coins,
            "cost": PACK_COST,
            "cards": [_card(s) for s in picked],
            "remaining": max(0, len(rows) - len(picked)),
        }
    )
