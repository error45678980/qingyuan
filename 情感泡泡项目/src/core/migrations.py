"""客户端库迁移的对外入口（架构文档 §3.3）。

执行逻辑在 `migrate_engine.SchemaMigrator`；DDL 与列名映射在 `migration_tables.py`。
本模块只做"客户端专用装配 + 再导出"，保证 `from src.core.migrations import Migrator,
ensure_client_schema, OUTCOME_*` 等既有导入路径不变。
"""

from __future__ import annotations

import os
from typing import Optional

from .constants import SCHEMA_VERSION
from .migrate_engine import SchemaMigrator
from .migration_tables import (
    BUSINESS_TABLES,
    CHAT_CREATE_SQL,
    CLIENT_EXTRA_TABLES,
    CLIENT_INDEX_STATEMENTS,
    CLIENT_TABLE_MIGRATIONS,
    CLIENT_TARGET_COLUMNS,
    KNOWN_LEGACY_TABLES,
    META_CREATE_SQL,
    OUTBOX_CREATE_SQL,
    OUTCOME_ALREADY,
    OUTCOME_FRESH,
    OUTCOME_MIGRATED,
    MigrationResult,
    TableMigration,
    bubble_expressions,
    chat_expressions,
    reply_expressions,
    sql_literals,
)

class Migrator:
    """客户端库迁移器（`~/.Qingyuan/qingyuan.db`）。"""

    def __init__(self, db_path: str, backup_dir: Optional[str] = None) -> None:
        """构造。

        Args:
            db_path: 客户端库路径。
            backup_dir: 备份目录；缺省为库文件同级 `backup/`。
        """
        resolved_backup = backup_dir or os.path.join(
            os.path.dirname(os.path.abspath(db_path)), "backup"
        )
        self._engine = SchemaMigrator(
            db_path=db_path,
            backup_dir=resolved_backup,
            schema_version=SCHEMA_VERSION,
            tables=CLIENT_TABLE_MIGRATIONS,
            target_columns=CLIENT_TARGET_COLUMNS,
            extra_tables=CLIENT_EXTRA_TABLES,
            index_statements=CLIENT_INDEX_STATEMENTS,
        )

    def run(self) -> MigrationResult:
        """执行迁移并返回结果。"""
        return self._engine.run()


def ensure_client_schema(db_path: str, backup_dir: Optional[str] = None) -> MigrationResult:
    """便捷函数：确保客户端库为 v2 结构。

    Args:
        db_path: 客户端库路径。
        backup_dir: 备份目录。

    Returns:
        `MigrationResult`。
    """
    return Migrator(db_path, backup_dir).run()



__all__ = [
    "OUTCOME_FRESH",
    "OUTCOME_MIGRATED",
    "OUTCOME_ALREADY",
    "BUSINESS_TABLES",
    "KNOWN_LEGACY_TABLES",
    "MigrationResult",
    "TableMigration",
    "SchemaMigrator",
    "Migrator",
    "ensure_client_schema",
    "bubble_expressions",
    "reply_expressions",
    "chat_expressions",
    "sql_literals",
]
