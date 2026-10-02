"""图鉴点亮（收集玩法）。

用户在 AI 识别结果里点「就是这个物种」确认后，调用这里的 unlock_species 写入
species_unlocks 表；图鉴页据此把已点亮的物种渲染成金色。
"""
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import new_id
from app.db.models import Species, SpeciesUnlock


def _cover_url(species: Species) -> str:
    """与 encyclopedia._cover_url 保持一致（前端拼 BASE_URL 使用）。"""
    return f"/encyclopedia/{species.id}/cover" if species.cover_key else ""


async def unlocked_map(db: AsyncSession, owner_id: str) -> dict[str, bool]:
    """该身份已点亮的物种 -> 是否已看过。

    seen=False 表示刚点亮、用户还没在图鉴里见过 —— 前端据此只给「新获得」的
    物种加闪光，看过一次之后只保留金边。
    """
    rows = (
        await db.execute(
            select(SpeciesUnlock.species_id, SpeciesUnlock.seen).where(
                SpeciesUnlock.owner_id == owner_id
            )
        )
    ).all()
    return {sid: bool(seen) for sid, seen in rows}


async def unlocked_species_ids(db: AsyncSession, owner_id: str) -> set[str]:
    """该身份已点亮的物种 id 集合。"""
    return set((await unlocked_map(db, owner_id)).keys())


async def mark_species_seen(db: AsyncSession, owner_id: str, species_ids: list[str]) -> int:
    """把指定物种标记为「用户已在图鉴里看过」，之后不再闪光。

    幂等：只看还没标记过的。返回本次真正更新了几行。
    """
    ids = [s for s in (species_ids or []) if s]
    if not ids:
        return 0
    res = await db.execute(
        update(SpeciesUnlock)
        .where(
            SpeciesUnlock.owner_id == owner_id,
            SpeciesUnlock.species_id.in_(ids),
            SpeciesUnlock.seen.is_(False),
        )
        .values(seen=True)
    )
    await db.commit()
    return res.rowcount or 0


async def unlock_species(db: AsyncSession, owner_id: str, species_id: str) -> dict | None:
    """点亮一个物种。

    返回本次「新点亮」的物种信息（id / name / coverUrl），供前端弹窗庆祝用。
    下列情况返回 None（不弹窗，也不算错）：

    - species_id 为空，或不在图鉴名录里（AI 可能给出名录外的名字）
    - 该身份此前已点亮过这个物种（幂等）
    - 并发下被唯一键拦下（另一个请求刚点亮）
    """
    if not species_id:
        return None

    species = await db.get(Species, species_id)
    if species is None:
        return None

    already = (
        await db.execute(
            select(SpeciesUnlock.id).where(
                SpeciesUnlock.owner_id == owner_id,
                SpeciesUnlock.species_id == species_id,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        return None

    db.add(SpeciesUnlock(id=new_id("sunlock"), owner_id=owner_id, species_id=species_id))
    try:
        await db.commit()
    except IntegrityError:
        # 查重与写入之间没有锁，并发下两个请求可能同时通过上面的检查。
        # 唯一键 uk_species_unlocks_owner_species 只放行一条，其余在这里被拦下 ——
        # 这属于「已经点亮了」，不是错误，回滚后按已点亮处理。
        await db.rollback()
        return None

    return {"id": species.id, "name": species.name, "coverUrl": _cover_url(species)}
