"""本地测试 AI 识别：同一张图连测多次看稳定性 + 图鉴匹配是否正确。"""
import asyncio

from sqlalchemy import select

from app.db.base import async_session_factory
from app.db.models import Species
from app.services.ai import species_guess


async def main():
    async with async_session_factory() as s:
        rows = (await s.execute(select(Species.id, Species.name, Species.aka, Species.look))).all()
    species = [{"id": i, "name": n, "aka": a or [], "hint": lk} for i, n, a, lk in rows]
    # 本地库还没有新物种时补上（服务器已种子化）
    if not any(x["id"] == "sp_korean_rockfish" for x in species):
        species.append({
            "id": "sp_korean_rockfish", "name": "许氏平鲉",
            "aka": ["黑头鱼", "黑鲪", "黑石鲈"],
            "hint": "身体侧扁、头较大；幼鱼体侧青绿带金属光泽，布满细密斑点，眼睛大而亮。",
        })

    imgs = [("许氏平鲉鱼苗", "data/uploads/pictorial/sp_korean_rockfish.jpg"),
            ("海星", "data/uploads/pictorial/sp_starfish.jpg")]
    for label, path in imgs:
        with open(path, "rb") as f:
            img = f.read()
        print("=== %s ===" % label)
        for i in range(3):
            r = await species_guess(img, "image/jpeg", species, "青岛·石老人")
            out = " | ".join(
                "%s %s%s" % (c["probabilityText"], c["name"], "(图鉴)" if c["inEncyclopedia"] else "")
                for c in r["candidates"]
            )
            print("  第%d次: %s" % (i + 1, out))


if __name__ == "__main__":
    asyncio.run(main())
