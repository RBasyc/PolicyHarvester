"""日期解析：MCP 返回的 `2025-06-05` / `2025年6月5日` 之类转成 date；解析不了就当没有。"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


def parse_date(value: Any) -> date | None:
    """把 MCP 返回的 `2025-06-05` / `2025年6月5日` 之类转成 date；解析不了就当没有。"""
    text = str(value or "").strip()
    match = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
