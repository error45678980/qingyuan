"""迁移引擎：建新表 → 按列名拷贝 → 换表（架构文档 §3.3）。

核心保证：
    * **先备份**（M1，失败即中止，不触碰原库）；
    * **单事务**（M3 `BEGIN IMMEDIATE`）+ `PRAGMA foreign_keys=OFF`；
    * **按列名拷贝**（M5），绝不 `SELECT *`、绝不用位置下标；
    * **行数 + 内容双重断言**（M6/M7），不等则 ROLLBACK；
    * **幂等**（M0 命中即返回 `AlreadyMigrated`，跑两次结果一致）；
    * 迁移后自检失败自动从备份还原（M12）。

客户端与服务端共用本引擎；客户端对外入口见 `migrations.Migrator`。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Sequence

from .errors import MigrationError
from .migration_tables import (
    BUSINESS_TABLES,
    KNOWN_LEGACY_TABLES,
    OUTCOME_ALREADY,
    OUTCOME_FRESH,
    OUTCOME_MIGRATED,
    MigrationResult,
    TableMigration,
)

logger = logging.getLogger("core.migrations")


@dataclass(frozen=True)
class _DbState:
    """迁移前的库状态快照。"""

    tables: tuple[str, ...]
    bubble_columns: tuple[str, ...]
    schema_version: Optional[int]
    counts: Mapping[str, int]
    legacy_columns: Mapping[str, tuple[str, ...]]


# ---------------------------------------------------------------------------
# 迁移引擎
# ---------------------------------------------------------------------------


class SchemaMigrator:
    """通用"建新表 → 按列名拷贝 → 换表"引擎（客户端与服务端共用）。"""

    def __init__(
        self,
        *,
        db_path: str,
        backup_dir: str,
        schema_version: int,
        tables: Sequence[TableMigration],
        target_columns: Mapping[str, tuple[str, ...]],
        extra_tables: Sequence[tuple[str, str]] = (),
        index_statements: Sequence[str] = (),
        legacy_columns_key: str = "bubbles",
        extra_meta: Optional[Mapping[str, str]] = None,
    ) -> None:
        """构造迁移器。

        Args:
            db_path: 数据库路径。
            backup_dir: 文件级备份目录（只写不删）。
            schema_version: 目标 schema 版本。
            tables: 需要"旧→新"拷贝的业务表声明。
            target_columns: 各业务表的目标列顺序。
            extra_tables: 额外直接创建的表 `(表名, 建表语句模板)`。
            index_statements: 迁移后建立的索引语句。
            legacy_columns_key: 用于判定"是否已迁移"的探测表名。
            extra_meta: 需要写入 `meta` 的额外键值。
        """
        self._db_path = os.path.abspath(db_path)
        self._backup_dir = os.path.abspath(backup_dir)
        self._schema_version = int(schema_version)
        self._tables = tuple(tables)
        self._target_columns = dict(target_columns)
        self._extra_tables = tuple(extra_tables)
        self._index_statements = tuple(index_statements)
        self._legacy_columns_key = legacy_columns_key
        self._extra_meta = dict(extra_meta or {})

    # -- 公共入口 ---------------------------------------------------------
    def run(self) -> MigrationResult:
        """执行迁移（幂等）。

        Returns:
            `MigrationResult`。

        Raises:
            MigrationError: 备份失败 / 断言失败 / 自检失败。
        """
        exists = os.path.isfile(self._db_path)
        state = self._read_state() if exists else self._empty_state()
        if exists and self._is_migrated(state):
            logger.info("库已是 v%d，跳过迁移: %s", self._schema_version, self._db_path)
            return MigrationResult(
                outcome=OUTCOME_ALREADY,
                schema_version=self._schema_version,
                backup_path=None,
                before=state.counts,
                after=state.counts,
                legacy_columns=state.legacy_columns,
            )
        if not state.tables:
            return self._fresh_init()
        return self._migrate(state)

    # -- 状态探测 ---------------------------------------------------------
    def _empty_state(self) -> _DbState:
        """不存在的库视为空状态。"""
        return _DbState(
            tables=(),
            bubble_columns=(),
            schema_version=None,
            counts={name: 0 for name in BUSINESS_TABLES},
            legacy_columns={},
        )

    def _read_state(self) -> _DbState:
        """读取列名、版本、行数与旧列序。"""
        conn = self._connect()
        try:
            tables = tuple(
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                ).fetchall()
            )
            counts = {name: 0 for name in BUSINESS_TABLES}
            legacy_columns: dict[str, tuple[str, ...]] = {}
            for name in tables:
                if name.startswith("sqlite_"):
                    continue
                legacy_columns[name] = tuple(
                    row["name"] for row in conn.execute(f"PRAGMA table_info({name})").fetchall()
                )
                if name in BUSINESS_TABLES:
                    row = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()
                    counts[name] = int(row["n"])
            version: Optional[int] = None
            if "meta" in tables:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", ("schema_version",)
                ).fetchone()
                if row is not None:
                    try:
                        version = int(row["value"])
                    except (TypeError, ValueError):
                        version = None
        finally:
            conn.close()
        return _DbState(
            tables=tables,
            bubble_columns=legacy_columns.get(self._legacy_columns_key, ()),
            schema_version=version,
            counts=counts,
            legacy_columns=legacy_columns,
        )

    def _is_migrated(self, state: _DbState) -> bool:
        """M0：新列存在且版本号一致即视为已迁移。"""
        if "author_client_id" not in state.bubble_columns:
            return False
        return state.schema_version == self._schema_version

    # -- 全新库 -----------------------------------------------------------
    def _fresh_init(self) -> MigrationResult:
        """空库直接建最终结构。"""
        directory = os.path.dirname(self._db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table in self._tables:
                    conn.execute(table.create_sql.format(table=table.name, ref=table.fk_parent))
                for name, template in self._extra_tables:
                    conn.execute(template.format(table=name, ref=""))
                for statement in self._index_statements:
                    conn.execute(statement)
                self._write_meta(conn, backup_path=None, before={}, after={}, legacy={})
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                conn.execute("ROLLBACK")
                raise MigrationError(f"初始化数据库失败: {exc}") from exc
            self._enable_wal(conn)
        finally:
            conn.close()
        counts = {name: 0 for name in BUSINESS_TABLES}
        logger.info("初始化新库完成: %s", self._db_path)
        return MigrationResult(
            outcome=OUTCOME_FRESH,
            schema_version=self._schema_version,
            backup_path=None,
            before=counts,
            after=counts,
            legacy_columns={},
        )

    # -- 旧库迁移 ---------------------------------------------------------
    def _migrate(self, state: _DbState) -> MigrationResult:
        """M1 ~ M12 完整流程。"""
        backup_path = self._backup()  # M1
        before = dict(state.counts)  # M2
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")  # M3
            try:
                self._create_v2(conn)  # M4
                self._copy_into_v2(conn, state)  # M5
                self._assert_counts(conn, state)  # M6
                self._assert_content(conn, state)  # M7
                self._swap_tables(conn)  # M8
                self._create_indexes(conn)  # M9
                self._assert_foreign_keys(conn)  # M9
                self._write_meta(
                    conn,
                    backup_path=backup_path,
                    before=before,
                    after=before,
                    legacy=state.legacy_columns,
                )  # M10
                conn.execute("COMMIT")  # M11
            except sqlite3.Error as exc:
                conn.execute("ROLLBACK")
                raise MigrationError(f"迁移失败已回滚: {exc}") from exc
            except MigrationError:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

        self._drop_legacy_tables()  # M11
        after = self._count_business_tables()  # M12
        if after != before:
            self._restore(backup_path)
            raise MigrationError(
                f"迁移后自检失败（迁移前 {before} / 迁移后 {after}），已从备份还原"
            )
        self._set_wal_mode()
        logger.info(
            "迁移完成: %s before=%s backup=%s", self._db_path, before, backup_path
        )
        return MigrationResult(
            outcome=OUTCOME_MIGRATED,
            schema_version=self._schema_version,
            backup_path=backup_path,
            before=before,
            after=after,
            legacy_columns=state.legacy_columns,
        )

    def _create_v2(self, conn: sqlite3.Connection) -> None:
        """M4：先 DROP 再建 `*_v2`（幂等重试）。"""
        for table in reversed(self._tables):
            conn.execute(f"DROP TABLE IF EXISTS {table.name}_v2")
        for table in self._tables:
            ref = f"{table.fk_parent}_v2" if table.fk_parent else table.name
            conn.execute(table.create_sql.format(table=f"{table.name}_v2", ref=ref))
        for name, template in self._extra_tables:
            conn.execute(template.format(table=name, ref=""))

    def _copy_into_v2(self, conn: sqlite3.Connection, state: _DbState) -> None:
        """M5：按列名拷贝，绝不 `SELECT *`。"""
        for table in self._tables:
            if table.name not in state.tables:
                logger.warning("旧库缺少表 %s，跳过拷贝", table.name)
                continue
            columns = frozenset(state.legacy_columns.get(table.name, ()))
            select_list, params = table.build_select(columns)
            target = ", ".join(self._target_columns[table.name])
            sql = (
                f"INSERT INTO {table.name}_v2 ({target}) "
                f"SELECT {select_list} FROM {table.name}"
            )
            conn.execute(sql, params)

    def _assert_counts(self, conn: sqlite3.Connection, state: _DbState) -> None:
        """M6：行数必须一致。"""
        for table in self._tables:
            if table.name not in state.tables:
                continue
            expected = int(state.counts.get(table.name, 0))
            actual = int(
                conn.execute(f"SELECT COUNT(*) AS n FROM {table.name}_v2").fetchone()["n"]
            )
            if expected != actual:
                raise MigrationError(
                    f"表 {table.name} 行数不一致：旧 {expected} / 新 {actual}"
                )

    def _assert_content(self, conn: sqlite3.Connection, state: _DbState) -> None:
        """M7：双向 `EXCEPT` 比对，任一侧非空即失败（这是"字段错位"的拦网）。

        注意：旧库侧用**映射表达式**（旧列名 → 目标值），新库侧用**目标列名**。
        两侧列顺序必须一一对应，否则比对无意义。
        """
        for table in self._tables:
            if table.name not in state.tables:
                continue
            columns = frozenset(state.legacy_columns.get(table.name, ()))
            select_list, params = table.build_select(columns)
            target_list = ", ".join(self._target_columns[table.name])
            forward = (
                f"SELECT {select_list} FROM {table.name} "
                f"EXCEPT SELECT {target_list} FROM {table.name}_v2"
            )
            backward = (
                f"SELECT {target_list} FROM {table.name}_v2 "
                f"EXCEPT SELECT {select_list} FROM {table.name}"
            )
            if conn.execute(forward, params).fetchone() is not None:
                raise MigrationError(f"表 {table.name} 内容比对失败（旧库有而新库无）")
            if conn.execute(backward, params).fetchone() is not None:
                raise MigrationError(f"表 {table.name} 内容比对失败（新库有而旧库无）")

    def _swap_tables(self, conn: sqlite3.Connection) -> None:
        """M8：旧表改名为 `*_legacy_v1`，`*_v2` 改名为正式名。

        注意重命名顺序：先改旧表、再改新表。`replies_v2` / `chat_messages_v2`
        的外键写的是 `REFERENCES bubbles_v2`，改名时会自动重写为 `REFERENCES bubbles`，
        因此不会留下悬空外键。
        """
        for table in self._tables:
            if not self._table_exists(conn, table.name):
                continue
            conn.execute(f"ALTER TABLE {table.name} RENAME TO {table.name}_legacy_v1")
        for table in self._tables:
            conn.execute(f"ALTER TABLE {table.name}_v2 RENAME TO {table.name}")

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        """M9：建索引。"""
        for statement in self._index_statements:
            conn.execute(statement)

    def _assert_foreign_keys(self, conn: sqlite3.Connection) -> None:
        """M9：`PRAGMA foreign_key_check` 必须为空。"""
        rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        if rows:
            raise MigrationError(f"迁移后存在外键违规 {len(rows)} 处")

    def _write_meta(
        self,
        conn: sqlite3.Connection,
        *,
        backup_path: Optional[str],
        before: Mapping[str, int],
        after: Mapping[str, int],
        legacy: Mapping[str, tuple[str, ...]],
    ) -> None:
        """M10：写 `schema_version` 与 `migration_log`。"""
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(self._schema_version)),
        )
        ignored = [name for name in KNOWN_LEGACY_TABLES if name in legacy]
        payload = {
            "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "schema_version": self._schema_version,
            "backup": backup_path or "",
            "before": dict(before),
            "after": dict(after),
            "legacy_columns": {name: list(cols) for name, cols in legacy.items()},
        }
        if ignored:
            payload["ignored_legacy_table"] = ",".join(ignored)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("migration_log", json.dumps(payload, ensure_ascii=False)),
        )
        for key, value in self._extra_meta.items():
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )

    def _drop_legacy_tables(self) -> None:
        """M11：删除 `*_legacy_v1`（失败仅告警，不回滚）。"""
        conn = self._connect()
        try:
            for table in reversed(self._tables):
                name = f"{table.name}_legacy_v1"
                if not self._table_exists(conn, name):
                    continue
                try:
                    conn.execute(f"DROP TABLE {name}")
                except sqlite3.Error as exc:  # 仅告警，不影响可用性
                    logger.warning("删除遗留表 %s 失败: %s", name, exc)
        finally:
            conn.close()

    # -- 工具 -------------------------------------------------------------
    def _backup(self) -> str:
        """M1：文件级备份并校验大小。"""
        try:
            os.makedirs(self._backup_dir, exist_ok=True)
            stem = os.path.splitext(os.path.basename(self._db_path))[0]
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = os.path.join(self._backup_dir, f"{stem}_{stamp}.db")
            suffix = 1
            while os.path.exists(target):
                target = os.path.join(self._backup_dir, f"{stem}_{stamp}_{suffix}.db")
                suffix += 1
            shutil.copy2(self._db_path, target)
            if os.path.getsize(target) != os.path.getsize(self._db_path):
                raise MigrationError("备份文件大小与原库不一致")
        except OSError as exc:
            raise MigrationError(f"迁移前备份失败，已中止（未修改原库）: {exc}") from exc
        logger.info("迁移前备份: %s", target)
        return target

    def _restore(self, backup_path: str) -> None:
        """M12：从备份还原。"""
        try:
            shutil.copy2(backup_path, self._db_path)
            logger.error("已从备份还原: %s", backup_path)
        except OSError as exc:
            logger.exception("从备份还原失败: %s", exc)

    def _count_business_tables(self) -> dict[str, int]:
        """统计业务表行数。"""
        conn = self._connect()
        try:
            counts: dict[str, int] = {}
            for name in BUSINESS_TABLES:
                if self._table_exists(conn, name):
                    row = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()
                    counts[name] = int(row["n"])
                else:
                    counts[name] = 0
            return counts
        finally:
            conn.close()

    def _enable_wal(self, conn: sqlite3.Connection) -> None:
        """在无事务状态下启用 WAL。"""
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error as exc:  # WAL 不可用不致命
            logger.warning("启用 WAL 失败: %s", exc)

    def _set_wal_mode(self) -> None:
        """迁移完成后切换 WAL 模式。"""
        conn = self._connect()
        try:
            self._enable_wal(conn)
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        """建连接：`isolation_level=None` 才能手写 `BEGIN IMMEDIATE`。"""
        conn = sqlite3.connect(self._db_path, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        """判断表是否存在。"""
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return row is not None


