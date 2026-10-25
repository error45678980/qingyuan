"""时间格式唯一出口（架构文档 §11.4）。

| 层 | 格式 | 出口 |
|---|---|---|
| 存储 / 传输 | `YYYY-MM-DDTHH:MM:SS`（本地时间，不做时区换算） | `now_iso()` |
| 展示 | `YYYY-MM-DD HH:MM` | `fmt_display()` |
| 对话内 | `MM-DD HH:MM` | `fmt_short()` |
| 解析容错 | 接受微秒 / `Z` / `+08:00` | `parse_iso()` |

**不做 UTC 换算**：历史数据都是 naive 本地时间，换算会导致展示整体偏移。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

#: 存储/传输格式。
ISO_FORMAT: str = "%Y-%m-%dT%H:%M:%S"
#: 展示格式。
DISPLAY_FORMAT: str = "%Y-%m-%d %H:%M"
#: 对话内短格式。
SHORT_FORMAT: str = "%m-%d %H:%M"

#: `parse_iso` 依次尝试的格式（越靠前越精确）。
_PARSE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def now_iso() -> str:
    """当前本地时间的存储/传输格式字符串。

    Returns:
        形如 `2026-07-21T17:20:03` 的字符串（秒精度，无时区）。
    """
    return datetime.now().strftime(ISO_FORMAT)


def now_display() -> str:
    """当前本地时间的展示格式。"""
    return datetime.now().strftime(DISPLAY_FORMAT)


def parse_iso(value: str) -> datetime:
    """解析历史时间串，返回**naive** 本地 `datetime`。

    容错点：
        * 带微秒（`...:40.905472`）；
        * 带 `Z` 后缀（按字面量去掉，不换算时区）；
        * 带 `+08:00` / `-05:00` 偏移（同样丢弃偏移量，不做换算）。

    Args:
        value: 时间字符串。

    Returns:
        解析后的 naive datetime。

    Raises:
        ValueError: 无法解析时。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"无法解析空时间值: {value!r}")
    text = value.strip()
    # 去掉末尾 Z（UTC 标记），保留字面时间
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1]
    # 去掉时区偏移：形如 +08:00 / -0500（仅在 T 之后、且长度足够时处理）
    if len(text) > 19 and ("+" in text[19:] or "-" in text[19:]):
        head = text[:19]
        tail = text[19:]
        cut = len(tail)
        for idx, char in enumerate(tail):
            if char in "+-":
                cut = idx
                break
        text = head + tail[:cut]
    for fmt in _PARSE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间值: {value!r}")


def try_parse_iso(value: str) -> Optional[datetime]:
    """`parse_iso` 的容错版本，失败返回 `None`。"""
    try:
        return parse_iso(value)
    except ValueError:
        return None


def fmt_display(value: Optional[str]) -> str:
    """把存储格式转成展示格式 `YYYY-MM-DD HH:MM`。

    无法解析时**原样返回**输入（便于排查脏数据，不静默吞掉）。

    Args:
        value: 存储/传输格式时间串，可为空。

    Returns:
        展示字符串；空输入返回空串。
    """
    if not value:
        return ""
    parsed = try_parse_iso(value)
    if parsed is None:
        return value
    return parsed.strftime(DISPLAY_FORMAT)


def fmt_short(value: Optional[str]) -> str:
    """把存储格式转成对话内短格式 `MM-DD HH:MM`。"""
    if not value:
        return ""
    parsed = try_parse_iso(value)
    if parsed is None:
        return value
    return parsed.strftime(SHORT_FORMAT)


def fmt_face(value: Optional[str]) -> str:
    """把存储格式转成"泡泡正面"用的极短时间。

    泡泡是圆形，底部时间带很窄，完整 `MM-DD HH:MM` 会被裁切，故按"近大远小"压缩：

    * 今天 → `HH:MM`（`13:07`）
    * 今年内 → `MM-DD`（`10-24`）
    * 跨年 → `YYYY-MM`（`2025-08`）

    Args:
        value: 存储/传输格式时间串。

    Returns:
        压缩后的时间串；无法解析时原样返回，空输入返回空串。
    """
    if not value:
        return ""
    parsed = try_parse_iso(value)
    if parsed is None:
        return value
    now = datetime.now()
    if parsed.date() == now.date():
        return parsed.strftime("%H:%M")
    if parsed.year == now.year:
        return parsed.strftime("%m-%d")
    return parsed.strftime("%Y-%m")


def shift_seconds(value: str, seconds: float) -> str:
    """在给定时间串上加若干秒，返回存储格式。

    解析失败时以当前时间为基准，保证离线队列不会因脏数据卡死。

    Args:
        value: 基准时间串。
        seconds: 偏移秒数，可为负。

    Returns:
        存储格式时间串。
    """
    base = try_parse_iso(value) or datetime.now()
    return (base + timedelta(seconds=seconds)).strftime(ISO_FORMAT)


def to_iso(moment: datetime) -> str:
    """`datetime` → 存储格式字符串（丢弃时区）。"""
    return moment.replace(tzinfo=None).strftime(ISO_FORMAT)


__all__ = [
    "ISO_FORMAT",
    "DISPLAY_FORMAT",
    "SHORT_FORMAT",
    "now_iso",
    "now_display",
    "parse_iso",
    "try_parse_iso",
    "fmt_display",
    "fmt_short",
    "fmt_face",
    "shift_seconds",
    "to_iso",
]
