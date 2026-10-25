"""情缘核心层（L0~L3）。

本包**不得** import PyQt5 或 Flask（`sync.py` / `sync_actions.py` 为纯 Python 实现）。

子模块职责：
    constants         常量唯一来源（情感、状态、模式、长度、间隔）
    errors            异常家族（AppError 及其子类）
    timeutil          时间格式唯一出口
    config            路径解析 + config.json 读写
    logging_setup     日志与全局异常钩子
    models            具名数据模型与 Row→dataclass 转换（规则 D-2 唯一入口）
    dto               dataclass ↔ JSON（BubbleDTO / ReplyDTO / ChatDTO）
    migration_tables  客户端 DDL 与"旧列 → 新列"映射声明
    migrate_engine    通用迁移引擎（客户端与服务端共用）
    migrations        客户端迁移对外入口（Migrator / ensure_client_schema）
    repo              客户端仓储（只读查询 + 写入 API）
    permissions       权限判定唯一实现
    api               HTTP 出口（requests 唯一使用者）+ ApiResult
    outbox            离线队列仓储与状态机
    sync              同步引擎（拉取 / 在线探测 / 生命周期）
    sync_actions      同步引擎的写操作与队列重放（SyncActionsMixin）
"""

__all__ = [
    "constants",
    "errors",
    "timeutil",
    "config",
    "logging_setup",
    "models",
    "dto",
    "migration_tables",
    "migrate_engine",
    "migrations",
    "repo",
    "permissions",
    "api",
    "outbox",
    "sync",
    "sync_actions",
]
