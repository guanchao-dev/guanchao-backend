"""轻量迁移：给已存在的表补齐新增列 / 调整列（create_all 不会改动已存在的表）。

幂等：先查 information_schema，缺失才 ALTER，可重复执行。
"""
from sqlalchemy import inspect, text

from app.db.base import engine

# 表名 -> [(列名, MySQL 列定义)]  —— 新增列
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "user_medals": [
        ("acked", "BOOLEAN NOT NULL DEFAULT 0"),
        ("source", "VARCHAR(16) NOT NULL DEFAULT 'other'"),
    ],
    "spots": [
        ("heat", "INT NOT NULL DEFAULT 0"),
    ],
    "watch_records": [
        ("spot_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
    "uploads": [
        ("owner_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
    "guesses": [
        ("owner_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("object_name", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
    "report_checkins": [
        ("session_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
    "checkin_sessions": [
        ("polygon", "JSON NULL"),
        ("poi_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
    "activities": [
        ("layout", "VARCHAR(16) NOT NULL DEFAULT 'card'"),
    ],
}

# 表名 -> [(列名, 新的 MySQL 列定义)]  —— 调整列（如改为可空）
_MODIFY_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "uploads": [
        ("user_id", "VARCHAR(64) NULL"),
    ],
    "guesses": [
        ("user_id", "VARCHAR(64) NULL"),
    ],
}

# 表名 -> (唯一键名, [列名])  —— 唯一约束
# 加之前会先清理历史重复行（保留每组最早的一条），否则 ADD UNIQUE KEY 会直接失败。
_UNIQUE_KEYS: dict[str, tuple[str, list[str]]] = {
    "report_checkins": (
        "uk_report_checkins_owner_day_session",
        ["owner_id", "checkin_date", "session_id"],
    ),
}


async def run_migrations() -> None:
    async with engine.connect() as conn:
        # 1. 新增列
        for table, columns in _COLUMN_MIGRATIONS.items():
            def _existing(sync_conn) -> set[str]:
                return {c["name"] for c in inspect(sync_conn).get_columns(table)}

            existing = await conn.run_sync(_existing)
            for name, ddl in columns:
                if name not in existing:
                    await conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl}"))

        # 2. 调整列
        for table, columns in _MODIFY_COLUMNS.items():
            def _existing(sync_conn) -> set[str]:
                return {c["name"] for c in inspect(sync_conn).get_columns(table)}

            existing = await conn.run_sync(_existing)
            for name, ddl in columns:
                if name in existing:
                    await conn.execute(text(f"ALTER TABLE `{table}` MODIFY COLUMN `{name}` {ddl}"))

        # 3. 唯一约束
        for table, (key_name, columns) in _UNIQUE_KEYS.items():
            def _has_key(sync_conn, _t=table, _n=key_name) -> bool:
                insp = inspect(sync_conn)
                names = {uc["name"] for uc in insp.get_unique_constraints(_t)}
                names |= {ix["name"] for ix in insp.get_indexes(_t)}
                return _n in names

            if await conn.run_sync(_has_key):
                continue

            cols = ", ".join(f"`{c}`" for c in columns)
            # 先清理历史重复行：每组按 created_at 升序编号，只留 rn=1 的一条。
            # created_at 是秒级精度、并发写入可能撞在同一秒，所以用 id 兜底保证
            # 每组恰好剩一条，否则 ALTER 会因残留重复而失败。
            # 包一层派生表是 MySQL「不能在 DELETE 中直接子查询同表」的绕法。
            await conn.execute(
                text(
                    f"DELETE FROM `{table}` WHERE `id` IN ("
                    f"  SELECT `id` FROM ("
                    f"    SELECT `id`, ROW_NUMBER() OVER ("
                    f"      PARTITION BY {cols} ORDER BY `created_at` ASC, `id` ASC"
                    f"    ) AS rn FROM `{table}`"
                    f"  ) AS _dup WHERE _dup.rn > 1"
                    f")"
                )
            )
            await conn.execute(
                text(f"ALTER TABLE `{table}` ADD UNIQUE KEY `{key_name}` ({cols})")
            )

        # 4. 回填 owner_id（历史数据按用户归属）
        await conn.execute(
            text(
                "UPDATE `uploads` SET owner_id = CONCAT('user:', user_id) "
                "WHERE owner_id = '' AND user_id IS NOT NULL AND user_id != ''"
            )
        )
        await conn.execute(
            text(
                "UPDATE `guesses` SET owner_id = CONCAT('user:', user_id) "
                "WHERE owner_id = '' AND user_id IS NOT NULL AND user_id != ''"
            )
        )
        await conn.commit()
