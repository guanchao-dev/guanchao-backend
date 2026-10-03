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
        # 所属区（崂山区/黄岛区/…）。热度排名按区分组，前端展示也要用
        ("district", "VARCHAR(32) NOT NULL DEFAULT ''"),
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
    "species_unlocks": [
        ("seen", "BOOLEAN NOT NULL DEFAULT 0"),
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
        added_seen_col = False
        for table, columns in _COLUMN_MIGRATIONS.items():
            def _existing(sync_conn, _t=table) -> set[str]:
                return {c["name"] for c in inspect(sync_conn).get_columns(_t)}

            existing = await conn.run_sync(_existing)
            for name, ddl in columns:
                if name not in existing:
                    await conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl}"))
                    if table == "species_unlocks" and name == "seen":
                        added_seen_col = True

        # species_unlocks.seen 是后加的列：历史记录都是「早就点亮、用户早就见过」的，
        # 不能因为列默认值是 0 就让它们在图鉴里全部闪起来。只在「这次刚加上」时回填一次。
        if added_seen_col:
            await conn.execute(text("UPDATE `species_unlocks` SET `seen` = 1"))

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

        # 5. 图鉴点亮回填见下方 _backfill_species_unlocks —— 单独开连接、单独兜异常，
        #    绝不能因为回填失败而拖垮整个服务启动。

        await conn.commit()

    await _backfill_species_unlocks()


async def _backfill_species_unlocks() -> None:
    """图鉴点亮：从历史观潮记录回填「已确认过的物种」。

    老用户确认过物种，理应一进图鉴就是亮的。只在 species_unlocks 为空时跑一次，
    否则每次启动都要全表扫 watch_records 的 JSON。

    单独开连接 + 整体兜异常：这是锦上添花的数据补齐，绝不能因为它失败而让
    run_migrations 抛出去 —— 那会导致 uvicorn 启动失败、整站不可用。
    背景：第一版写成 `s.id = jt.sid` 直接比较，MySQL 报 1267 Illegal mix of
    collations（JSON_TABLE 造出的列用连接排序规则，与 species.id 的建表排序规则不同），
    结果把服务打挂了。COLLATE 显式指定即可。
    """
    try:
        async with engine.begin() as conn:
            unlocked = (
                await conn.execute(text("SELECT COUNT(*) FROM `species_unlocks`"))
            ).scalar() or 0
            if unlocked:
                return
            # id 用 MD5(owner_id:species_id) 生成 —— 确定性，配合 INSERT IGNORE 保证幂等。
            # JSON_TABLE 把 species 数组展开成行；EXISTS 过滤掉已不在图鉴名录里的旧物种。
            result = await conn.execute(
                text(
                    "INSERT IGNORE INTO `species_unlocks` (`id`, `owner_id`, `species_id`, `unlocked_at`) "
                    "SELECT CONCAT('sunlock_', SUBSTRING(MD5(CONCAT(w.`owner_id`, ':', jt.sid)), 1, 16)), "
                    "       w.`owner_id`, jt.sid, w.`created_at` "
                    "FROM `watch_records` w, "
                    "JSON_TABLE(w.`species`, '$[*]' COLUMNS (sid VARCHAR(64) PATH '$.speciesId')) AS jt "
                    "WHERE w.`species` IS NOT NULL AND jt.sid IS NOT NULL AND jt.sid != '' "
                    "  AND EXISTS (SELECT 1 FROM `species` s "
                    "              WHERE s.`id` = jt.sid COLLATE utf8mb4_general_ci)"
                )
            )
            print(f"[migrate] 图鉴点亮回填完成，新增 {result.rowcount} 条")
    except Exception as exc:  # noqa: BLE001
        print(f"[migrate] 图鉴点亮回填跳过（不影响启动）：{type(exc).__name__}: {exc}")
