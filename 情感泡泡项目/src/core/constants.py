"""全局常量唯一来源。

设计约束（架构文档 §11.5）：
    * 情感 6 值、回复模式、状态、长度上限、轮询间隔**只能**在这里定义；
    * 其他模块一律 `from src.core.constants import EMOTIONS`，禁止就地写字符串字面量；
    * `src/ui/theme.py` 的 `EMOTION_COLORS` / `EMOTION_ICONS` 的 key 必须与本文件
      `EMOTIONS` 完全一致（由 hygiene 测试断言）。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 业务枚举（唯一来源）
# ---------------------------------------------------------------------------

#: 6 个情感标签。顺序即 UI 筛选条的展示顺序，不得随意调整。
EMOTIONS: tuple[str, ...] = (
    "开心",
    "难过",
    "孤独",
    "焦虑",
    "寻求建议",
    "找人倾诉",
)

#: 越界情感值的兜底标签（迁移与容错时使用）。
EMOTION_FALLBACK: str = "找人倾诉"

#: 回复模式：single = 单人只能回复一次；dialog = 可持续对话。
REPLY_MODES: tuple[str, ...] = ("single", "dialog")
DEFAULT_REPLY_MODE: str = "single"

#: 泡泡状态。
#: active       进行中
#: resolved     已解决（作者可见，他人隐藏）
#: saved_public 公开保留（仍出现在泡泡墙）
#: saved_private 私密保存（仅作者可见，内容不上传）
BUBBLE_STATUSES: tuple[str, ...] = (
    "active",
    "resolved",
    "saved_public",
    "saved_private",
)
DEFAULT_STATUS: str = "active"

#: 公开展示的两种状态（泡泡墙数据源）。
PUBLIC_STATUSES: tuple[str, ...] = ("active", "saved_public")

#: 客户端同步状态。
SYNC_STATES: tuple[str, ...] = ("synced", "pending", "failed")
SYNC_STATE_SYNCED: str = "synced"
SYNC_STATE_PENDING: str = "pending"
SYNC_STATE_FAILED: str = "failed"

#: 离线队列状态与操作类型。
OUTBOX_STATES: tuple[str, ...] = ("pending", "syncing", "done", "failed")
OUTBOX_PENDING: str = "pending"
OUTBOX_SYNCING: str = "syncing"
OUTBOX_DONE: str = "done"
OUTBOX_FAILED: str = "failed"

OUTBOX_OP_TYPES: tuple[str, ...] = ("publish", "reply", "chat", "resolve", "save")

#: `mine` 页的 4 个分组。
MINE_GROUPS: tuple[str, ...] = (
    "published",
    "saved_public",
    "saved_private",
    "resolved",
)

# ---------------------------------------------------------------------------
# 长度与数量限制
# ---------------------------------------------------------------------------

MAX_BUBBLE_LEN: int = 500
MAX_REPLY_LEN: int = 200
MAX_CHAT_LEN: int = 200
MAX_NICKNAME_LEN: int = 12
MAX_SEARCH_LEN: int = 50
MAX_ID_LEN: int = 64
MAX_ERROR_SUMMARY_LEN: int = 200

PAGE_SIZE_DEFAULT: int = 50
PAGE_SIZE_MAX: int = 100

#: 预览文本截断长度（`Bubble.content_preview`）。
PREVIEW_LEN: int = 80

# ---------------------------------------------------------------------------
# 服务端
# ---------------------------------------------------------------------------

RATE_LIMIT_PER_MIN: int = 120
RATE_LIMIT_WINDOW_SECONDS: int = 60
SCHEMA_VERSION: int = 2

DEFAULT_SERVER_URL: str = "http://127.0.0.1:5000"
DEFAULT_SERVER_HOST: str = "0.0.0.0"
DEFAULT_SERVER_PORT: int = 5000
SERVER_THREADS: int = 8
DEFAULT_HTTP_TIMEOUT: float = 5.0

#: 未知 client_id 时的匿名前缀（旧端点兼容用）。
LEGACY_ANON_PREFIX: str = "legacy-anon-"

# ---------------------------------------------------------------------------
# 客户端定时器
# ---------------------------------------------------------------------------

ANIMATION_INTERVAL_MS: int = 33
PULL_INTERVAL_MS: int = 15000
OUTBOX_INTERVAL_MS: int = 10000
DETAIL_REFRESH_INTERVAL_MS: int = 5000

#: 离线队列重放参数。
OUTBOX_BATCH_SIZE: int = 20
OUTBOX_MAX_ATTEMPTS: int = 8
OUTBOX_BACKOFF_BASE_SECONDS: int = 5
OUTBOX_BACKOFF_MAX_SECONDS: int = 300
OUTBOX_DONE_RETENTION_DAYS: int = 7
OUTBOX_FAILED_RETENTION_DAYS: int = 30

#: 连续多少次网络失败才判定离线（防抖动）。
OFFLINE_FAILURE_THRESHOLD: int = 2

# ---------------------------------------------------------------------------
# 文案
# ---------------------------------------------------------------------------

APP_TITLE: str = "情缘"
APP_SUBTITLE: str = "把心事交给泡泡，让它飘向懂你的人"

#: 统一的中文错误提示（服务端与客户端共用，避免两套文案）。
ERROR_MESSAGES: dict[str, str] = {
    "bad_request": "请求内容不太对哦",
    "unauthorized": "身份验证失败，请重新进入",
    "forbidden": "你没有这个操作的权限哦",
    "not_found": "找不到这个泡泡了",
    "conflict": "数据刚刚更新过，请稍后再试",
    "rate_limited": "慢一点点，休息一下再试吧",
    "server_error": "服务器打了个盹，稍后再试",
    "network_error": "网络好像断了，先记在本地啦",
}

#: 权限拒绝的具体文案（`permissions.evaluate` 使用）。
DENY_REASON_NOT_VISIBLE: str = "你看不到这个泡泡哦"
DENY_REASON_RESOLVED: str = "这个泡泡已经结束啦"
DENY_REASON_OWN_BUBBLE: str = "这是你自己的泡泡哦～"
DENY_REASON_SINGLE_REPLIED: str = "这个泡泡只能回复一次哦～"
DENY_REASON_NOT_PARTICIPANT: str = "只有回复过这个泡泡的人才能聊天哦"

__all__ = [name for name in dir() if not name.startswith("_")]
