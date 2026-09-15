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

        # 3. 回填 owner_id（历史数据按用户归属）
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
