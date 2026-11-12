"""成就 / 勋章结算。根据用户行为判断并解锁勋章。

条件集中在 `evaluate_medals()` 一个地方；它由「用户刚做完某个动作」的接口调用
（识别 / 观潮点亮 / 垃圾识别 / 登录），返回本次**新解锁**的勋章 id，
调用方塞进响应的 `unlockedMedalIds`。

唯一一个服务端看不见的动作（点进「深蓝百万里」链接）走 `grant_medal()`，
由 POST /achievements/report 直接发。

勋章一共 13 枚（众筹那批）。最早那 5 枚（初次见面 / 探索新星 / 无畏冒险家 /
宝藏猎人 / 解谜达人）已废弃 —— 前端正式版只认这 13 枚，后端不再发放，
并在启动时连同已发记录一起清掉（见 db/seed.py 的 DEPRECATED_MEDALS）。
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import SHANGHAI_TZ, new_id
from app.db.models import (
    Guess,
    Medal,
    Species,
    SpeciesUnlock,
    TrashGuess,
    User,
    UserMedal,
)
from app.services import levels
from app.services.ai import primary_candidates

# 勋章解锁来源（对应 pending-unlocks / unlock-ack 的 source 字段）
MEDAL_SOURCE = {
    # ===== 众筹成就（13 枚）=====
    "medal_6": "login",     # 活动期间登录
    "medal_7": "report",    # 前端上报（点进深蓝百万里）
    "medal_8": "guess",
    "medal_9": "guess",
    "medal_10": "guess",
    "medal_11": "guess",
    "medal_12": "guess",
    "medal_13": "guess",
    "medal_14": "trash",
    "medal_15": "guess",
    "medal_16": "collect",
    "medal_17": "guess",
    "medal_18": "guess",
}

# ===== 两个门槛数字 =====
# 设计表里这两处写得含糊（「很多只螃蟹」/「累计识别（）种生物」），先定这两个数，
# 要改只改这里。
CRAB_TARGET = 10       # 蟹老板：识别到螃蟹的次数
SPECIES_TARGET = 10    # 海洋记录员：累计识别到的不同物种数

# 成就代币：按稀有度给，抽卡用
COIN_BY_RARITY = {
    "common": 20,
    "rare": 50,
    "epic": 100,
    "legendary": 200,
    "hidden": 80,
}

# 「蓝色青年行动」的活动窗口（北京时间，含首尾）。表里备注「后续有机会返场」，
# 所以按「月-日」比对，每年这个窗口自动生效。
BLUE_YOUTH_FROM = (10, 7)
BLUE_YOUTH_TO = (10, 17)

# 几个点名要识别的物种
_SP_STARFISH = "sp_starfish"
_SP_LAVER = "sp_laver"
_SP_RAZOR_CLAM = "sp_razor_clam"
_SP_BARNACLE = "sp_barnacle"


async def _unlock(db: AsyncSession, user: User, medal_ids: list[str]) -> list[str]:
    """发勋章（已解锁的跳过），返回本次新解锁的 id，并加上对应分值。"""
    if not medal_ids:
        return []
    existing = set(
        (await db.execute(select(UserMedal.medal_id).where(UserMedal.user_id == user.id)))
        .scalars()
        .all()
    )
    fresh = [mid for mid in dict.fromkeys(medal_ids) if mid not in existing]
    if not fresh:
        return []
    medals = {
        m.id: m for m in (await db.execute(select(Medal).where(Medal.id.in_(fresh)))).scalars().all()
    }
    newly: list[str] = []
    score_add = 0
    coins_add = 0
    for mid in fresh:
        m = medals.get(mid)
        if m is None:
            continue  # 勋章还没进库（种子缺失），不发，也不报错
        db.add(
            UserMedal(
                id=new_id("um"),
                user_id=user.id,
                medal_id=mid,
                acked=False,
                source=MEDAL_SOURCE.get(mid, "other"),
            )
        )
        newly.append(mid)
        coin_reward = COIN_BY_RARITY.get(m.rarity, 0)
        if isinstance(m.rewards, dict):
            try:
                score_add += int(m.rewards.get("score") or 0)
            except (TypeError, ValueError):
                pass
            try:
                explicit_coins = int(m.rewards.get("coins") or 0)
                if explicit_coins:
                    coin_reward = explicit_coins
            except (TypeError, ValueError):
                pass
        coins_add += coin_reward
    if newly:
        user.score = (user.score or 0) + score_add
        user.coins = (user.coins or 0) + coins_add
        await db.commit()
        # 成就值是经验的来源之一，等级跟着重算（只在变化时才写库）
        await levels.sync_user_level(db, user)
    return newly


async def grant_medal(db: AsyncSession, user: User, medal_id: str) -> list[str]:
    """直接发一个勋章（给「服务端看不见、只能由前端上报」的成就用）。"""
    return await _unlock(db, user, [medal_id])


def _in_blue_youth_window(now: datetime) -> bool:
    return BLUE_YOUTH_FROM <= (now.month, now.day) <= BLUE_YOUTH_TO


async def evaluate_medals(db: AsyncSession, user: User) -> list[str]:
    """结算勋章，返回本次新解锁的 medal id 列表。"""
    trash_guesses = (
        await db.execute(
            select(func.count()).select_from(TrashGuess).where(TrashGuess.user_id == user.id)
        )
    ).scalar() or 0

    # 物种名称 → id 的两张集合表。动态算，别写死 id：以后加物种自动跟着变。
    species_rows = (await db.execute(select(Species))).scalars().all()
    crab_ids = {s.id for s in species_rows if s.category == "crab"}
    snail_ids = {s.id for s in species_rows if (s.name or "").endswith("螺")}

    # 扫这个用户的全部识别记录，一次拿到下面几个条件需要的所有信息。
    # 记录量很小（一次识别一行），换成一堆 COUNT 反而不划算。
    guesses = (await db.execute(select(Guess).where(Guess.user_id == user.id))).scalars().all()
    seen_species: set[str] = set()
    crab_rounds = 0          # 有螃蟹的识别次数
    max_objects = 0          # 单次识别里最多的物体数
    saw_stone = False        # 识别出「石头」这类非生物
    for g in guesses:
        cands = primary_candidates(g.candidates)
        max_objects = max(max_objects, len(cands))
        sids = {str(c.get("speciesId")) for c in cands if c.get("speciesId")}
        seen_species |= sids
        if sids & crab_ids:
            crab_rounds += 1
        if g.object_name and "石" in g.object_name:
            saw_stone = True

    # 图鉴已点亮（按 owner_id —— 和点亮时写入的口径一致）
    unlocked_species = set(
        (
            await db.execute(
                select(SpeciesUnlock.species_id).where(
                    SpeciesUnlock.owner_id == f"user:{user.id}"
                )
            )
        )
        .scalars()
        .all()
    )

    conditions = {
        # ===== 众筹成就（13 枚，前端正式版只认这些）=====
        # medal_7（深蓝百万里）服务端看不见，只能由 report 接口 grant_medal
        "medal_6": _in_blue_youth_window(datetime.now(SHANGHAI_TZ)),
        "medal_8": saw_stone,
        "medal_9": crab_rounds >= 1,
        "medal_10": crab_rounds >= CRAB_TARGET,
        "medal_11": max_objects >= 3,
        "medal_12": _SP_STARFISH in seen_species,
        "medal_13": len(seen_species) >= SPECIES_TARGET,
        "medal_14": trash_guesses >= 1,
        # 集齐「螺」：名录里所有以螺结尾的都要点亮；名录为空时不算达成
        "medal_16": bool(snail_ids) and snail_ids <= unlocked_species,
        "medal_15": _SP_LAVER in seen_species,
        "medal_17": _SP_RAZOR_CLAM in seen_species,
        "medal_18": _SP_BARNACLE in seen_species,
    }
    return await _unlock(db, user, [mid for mid, ok in conditions.items() if ok])
