"""对话工具的共用小工具：结果压缩 / 统一失败返回 / 日期参数校验。

`onboarding_tools.py`（接一个新网站）与 `ingestion_tools.py`（发起一次入库）都要这几件
东西——复制两份的话，「超长怎么截断」「失败长什么样」迟早两副面孔，而模型是照着这些
约定写下一句话的。所以只有一份，两个模块各自 `as _xxx` 起个别名用。

**工具体内不抛异常**是本仓库所有对话工具的硬约束（见 `onboarding_tools.py` 的模块
docstring）：任何失败都返回 `{"ok": false, "error": "<中文原因>"}`，让模型能读着改。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

# 工具返回值的长度上限（模型看的是结论，不是整页 HTML 分析；超了截断并说明）
MAX_CHARS = 6000


def dump(payload: Any, *, limit: int = MAX_CHARS) -> str:
    """把结果压成紧凑 JSON 文本；超长截断（并明确标注被截断，不假装完整）。"""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > limit:
        return text[:limit] + f" …（结果过长已截断，完整长度 {len(text)} 字符）"
    return text


def error(message: str, **extra: Any) -> str:
    """统一的失败返回（工具不抛异常）。"""
    return dump({"ok": False, "error": message, **extra})


def parse_iso_date(raw: str | None, field: str) -> tuple[date | None, str | None]:
    """解析 `YYYY-MM-DD` 参数；返回 `(date, error)`——两者不会同时有值。"""
    if not raw:
        return None, None
    try:
        return date.fromisoformat(raw.strip()), None
    except ValueError:
        return None, f"{field} 需要 YYYY-MM-DD 格式，收到：{raw}"


def validation_message(exc: Exception) -> str:
    """把 pydantic 的校验错误翻成一句中文（模型要能照着改）。

    pydantic 的 `msg` 形如 "Value error, 未知文种：…"，去掉前缀后与字段名拼在一起，
    模型看到的是「哪个字段错在哪」，而不是一堆英文校验器术语。
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)
    parts: list[str] = []
    for err in errors():
        field = "、".join(str(item) for item in err.get("loc") or []) or "参数"
        message = str(err.get("msg") or "")
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        parts.append(f"{field}：{message}")
    return "；".join(parts) or "参数不合法"
