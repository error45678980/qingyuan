"""离线队列仓储与状态机（架构文档 §7.1 ~ §7.3）。

状态机：
    pending --pick--> syncing --2xx--> done
                          |--网络/5xx/429--> pending（指数退避）
                          |--400/403/404--> failed（永久失败）
                          |--409 作者 resolve/save--> done（Q-4 作者优先）
                          |--409 其它--> pending（拉取服务端覆盖本地）

退避：`delay = min(5 * 2**(attempts-1), 300)` → 5, 10, 20, 40, 80, 160, 300, 300…
`attempts >= 8` → `failed`。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Mapping, Optional

from .constants import (
    MAX_ERROR_SUMMARY_LEN,
    OUTBOX_BACKOFF_BASE_SECONDS,
    OUTBOX_BACKOFF_MAX_SECONDS,
    OUTBOX_BATCH_SIZE,
    OUTBOX_DONE,
    OUTBOX_FAILED,
    OUTBOX_MAX_ATTEMPTS,
    OUTBOX_OP_TYPES,
    OUTBOX_PENDING,
    OUTBOX_STATES,
    OUTBOX_SYNCING,
)
from .models import OutboxItem, row_to_outbox, rows_to_outbox
from .repo import Repo
from .timeutil import now_iso, shift_seconds

logger = logging.getLogger("core.outbox")


def backoff_seconds(attempts: int) -> int:
    """计算第 `attempts` 次尝试后的退避秒数。

    Args:
        attempts: 已尝试次数（>= 1）。

    Returns:
        退避秒数，上限 `OUTBOX_BACKOFF_MAX_SECONDS`。
    """
    safe = max(1, int(attempts))
    delay = OUTBOX_BACKOFF_BASE_SECONDS * (2 ** (safe - 1))
    return int(min(delay, OUTBOX_BACKOFF_MAX_SECONDS))


class Outbox:
    """离线队列仓储（复用 `Repo` 的连接与事务，保证唯一写者）。"""

    def __init__(self, repo: Repo) -> None:
        """构造。

        Args:
            repo: 客户端仓储（共享同一 SQLite 连接）。
        """
        self._repo = repo

    # ------------------------------------------------------------------
    # 入队
    # ------------------------------------------------------------------
    def enqueue(
        self,
        *,
        op_type: str,
        bubble_id: str,
        payload: Mapping[str, Any],
        base_version: int = 0,
        op_id: Optional[str] = None,
        now: Optional[str] = None,
    ) -> OutboxItem:
        """入队一条待同步操作。

        Args:
            op_type: `publish` / `reply` / `chat` / `resolve` / `save`。
            bubble_id: 目标泡泡。
            payload: 请求体（会被 JSON 序列化）。
            base_version: 提交时的本地版本（Q-4）。
            op_id: 幂等标识，缺省生成 UUID4。
            now: 当前时间，缺省 `now_iso()`。

        Returns:
            带 `id` 的 `OutboxItem`。

        Raises:
            ValueError: `op_type` 非法或 `bubble_id` 为空。
        """
        if op_type not in OUTBOX_OP_TYPES:
            raise ValueError(f"未知 op_type: {op_type}")
        if not bubble_id:
            raise ValueError("bubble_id 不能为空")
        stamp = now or now_iso()
        identifier = op_id or str(uuid.uuid4())
        self._repo.execute(
            "INSERT INTO outbox (op_id, op_type, bubble_id, payload, base_version, state, "
            "attempts, next_attempt_at, last_error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL, ?, ?)",
            (
                identifier,
                op_type,
                bubble_id,
                json.dumps(dict(payload), ensure_ascii=False),
                int(base_version),
                OUTBOX_PENDING,
                stamp,
                stamp,
                stamp,
            ),
        )
        item = self.get(identifier)
        if item is None:  # pragma: no cover - 插入后必然可读
            raise RuntimeError("入队后无法读取该条目")
        logger.info("入队 op=%s type=%s bubble=%s", identifier, op_type, bubble_id)
        return item

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, op_id: str) -> Optional[OutboxItem]:
        """按 `op_id` 取条目。"""
        row = self._repo.query_one(
            "SELECT id, op_id, op_type, bubble_id, payload, base_version, state, attempts, "
            "next_attempt_at, last_error, created_at, updated_at FROM outbox WHERE op_id = ?",
            (op_id,),
        )
        return row_to_outbox(row) if row is not None else None

    def next_batch(
        self, *, now: Optional[str] = None, limit: int = OUTBOX_BATCH_SIZE
    ) -> list[OutboxItem]:
        """取到期的待发送条目（FIFO）。

        Args:
            now: 当前时间，缺省 `now_iso()`。
            limit: 单次上限。

        Returns:
            `OutboxItem` 列表，按 `id ASC`。
        """
        stamp = now or now_iso()
        rows = self._repo.query_all(
            "SELECT id, op_id, op_type, bubble_id, payload, base_version, state, attempts, "
            "next_attempt_at, last_error, created_at, updated_at FROM outbox "
            "WHERE state = ? AND next_attempt_at <= ? ORDER BY id ASC LIMIT ?",
            (OUTBOX_PENDING, stamp, max(1, int(limit))),
        )
        return rows_to_outbox(rows)

    def list_by_state(self, state: str) -> list[OutboxItem]:
        """按状态列出（测试与排查用）。"""
        if state not in OUTBOX_STATES:
            raise ValueError(f"未知状态: {state}")
        rows = self._repo.query_all(
            "SELECT id, op_id, op_type, bubble_id, payload, base_version, state, attempts, "
            "next_attempt_at, last_error, created_at, updated_at FROM outbox "
            "WHERE state = ? ORDER BY id ASC",
            (state,),
        )
        return rows_to_outbox(rows)

    def list_for_bubble(self, bubble_id: str) -> list[OutboxItem]:
        """列出某泡泡的全部条目。"""
        rows = self._repo.query_all(
            "SELECT id, op_id, op_type, bubble_id, payload, base_version, state, attempts, "
            "next_attempt_at, last_error, created_at, updated_at FROM outbox "
            "WHERE bubble_id = ? ORDER BY id ASC",
            (bubble_id,),
        )
        return rows_to_outbox(rows)

    def pending_count(self) -> int:
        """待同步条数（`pending` + `syncing`）。"""
        row = self._repo.query_one(
            "SELECT COUNT(*) AS n FROM outbox WHERE state IN (?, ?)",
            (OUTBOX_PENDING, OUTBOX_SYNCING),
        )
        return int(row["n"]) if row is not None else 0

    # ------------------------------------------------------------------
    # 状态流转
    # ------------------------------------------------------------------
    def mark_syncing(self, op_id: str, *, now: Optional[str] = None) -> None:
        """`pending` → `syncing`，并 `attempts + 1`。"""
        stamp = now or now_iso()
        self._repo.execute(
            "UPDATE outbox SET state = ?, attempts = attempts + 1, updated_at = ? "
            "WHERE op_id = ?",
            (OUTBOX_SYNCING, stamp, op_id),
        )

    def mark_done(self, op_id: str, *, now: Optional[str] = None) -> None:
        """`syncing` → `done`。"""
        stamp = now or now_iso()
        self._repo.execute(
            "UPDATE outbox SET state = ?, updated_at = ? WHERE op_id = ?",
            (OUTBOX_DONE, stamp, op_id),
        )

    def mark_retry(
        self, op_id: str, *, error: str, now: Optional[str] = None
    ) -> str:
        """`syncing` → `pending`（按退避）或 `failed`（次数用尽）。

        Args:
            op_id: 条目标识。
            error: 最近一次错误摘要（截断到 200 字）。
            now: 当前时间。

        Returns:
            流转后的状态（`pending` 或 `failed`）。
        """
        stamp = now or now_iso()
        item = self.get(op_id)
        if item is None:
            logger.warning("mark_retry 未找到条目 op=%s", op_id)
            return OUTBOX_FAILED
        summary = (error or "")[:MAX_ERROR_SUMMARY_LEN]
        if item.attempts >= OUTBOX_MAX_ATTEMPTS:
            self.mark_failed(op_id, error=summary, now=stamp)
            return OUTBOX_FAILED
        next_at = shift_seconds(stamp, backoff_seconds(item.attempts))
        self._repo.execute(
            "UPDATE outbox SET state = ?, next_attempt_at = ?, last_error = ?, "
            "updated_at = ? WHERE op_id = ?",
            (OUTBOX_PENDING, next_at, summary, stamp, op_id),
        )
        logger.info(
            "重试排队 op=%s attempts=%d next=%s", op_id, item.attempts, next_at
        )
        return OUTBOX_PENDING

    def mark_failed(
        self, op_id: str, *, error: str, now: Optional[str] = None
    ) -> None:
        """→ `failed`（永久失败，保留 30 天供排查）。"""
        stamp = now or now_iso()
        self._repo.execute(
            "UPDATE outbox SET state = ?, last_error = ?, updated_at = ? WHERE op_id = ?",
            (OUTBOX_FAILED, (error or "")[:MAX_ERROR_SUMMARY_LEN], stamp, op_id),
        )

    def reset_stale_syncing(self, *, now: Optional[str] = None) -> int:
        """崩溃恢复：把僵死的 `syncing` 归位 `pending`。

        Returns:
            归位条数。
        """
        stamp = now or now_iso()
        cursor = self._repo.execute(
            "UPDATE outbox SET state = ?, next_attempt_at = ?, updated_at = ? "
            "WHERE state = ?",
            (OUTBOX_PENDING, stamp, stamp, OUTBOX_SYNCING),
        )
        count = int(cursor.rowcount or 0)
        if count:
            logger.info("崩溃恢复：%d 条 syncing 归位 pending", count)
        return count

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def delete(self, op_id: str) -> int:
        """删除条目，返回删除行数。"""
        cursor = self._repo.execute("DELETE FROM outbox WHERE op_id = ?", (op_id,))
        return int(cursor.rowcount or 0)

    def delete_pending_for_bubble(self, bubble_id: str, op_types: tuple[str, ...]) -> int:
        """删除某泡泡指定类型的待发送条目（私密化时取消上传用）。

        Args:
            bubble_id: 泡泡 id。
            op_types: 要取消的 `op_type` 集合。

        Returns:
            删除条数。
        """
        if not op_types:
            return 0
        placeholders = ", ".join("?" for _ in op_types)
        cursor = self._repo.execute(
            f"DELETE FROM outbox WHERE bubble_id = ? AND state IN (?, ?) "
            f"AND op_type IN ({placeholders})",
            (bubble_id, OUTBOX_PENDING, OUTBOX_SYNCING, *op_types),
        )
        return int(cursor.rowcount or 0)

    def purge(self, *, done_before: str, failed_before: str) -> int:
        """清理过期的 `done` / `failed` 条目。

        Args:
            done_before: 早于该时间的 `done` 条目被删除。
            failed_before: 早于该时间的 `failed` 条目被删除。

        Returns:
            删除条数。
        """
        done_cursor = self._repo.execute(
            "DELETE FROM outbox WHERE state = ? AND updated_at < ?",
            (OUTBOX_DONE, done_before),
        )
        failed_cursor = self._repo.execute(
            "DELETE FROM outbox WHERE state = ? AND updated_at < ?",
            (OUTBOX_FAILED, failed_before),
        )
        return int(done_cursor.rowcount or 0) + int(failed_cursor.rowcount or 0)


__all__ = ["Outbox", "backoff_seconds"]
