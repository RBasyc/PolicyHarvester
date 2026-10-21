"""日期解析：MCP 返回的 `2025-06-05` / `2025年6月5日` 之类转成 date；解析不了就当没有。

政务站日期写法比想象中杂：`2026.9.8`、`2026/09/08`、`2026年9月8日`，
以及公文落款里常见的**中文数字**——`二〇二六年九月八日`。字段规则的
`normalize=date`（见 `app/sources/field_rules.py`）依赖这里统一成 ISO 日期，
所以中文数字必须在这里吃掉，而不是让每个来源各自处理。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

# 中文数字（含「〇/○/零」三种零的写法）。ASCII 数字也收进来：政务页常见
# 「2026年九月八日」这种阿拉伯年份 + 中文月日的**混写**，只收 0 不够。
_CN_DIGITS = {
    "〇": 0, "○": 0, "零": 0,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4,
    "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_DIGIT_CHARS = "".join(_CN_DIGITS)
_CN_NUMBER_CHARS = _CN_DIGIT_CHARS + "十"

# 阿拉伯数字日期：允许分隔符两侧有空格，`日` 可省略
_NUMERIC_DATE_RE = re.compile(
    r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)"
)
# 「2026年9月8日」（阿拉伯数字 + 中文单位）
_ARABIC_YEAR_RE = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"
)
# 「二〇二六年九月八日」（年月日全部中文数字）
_CN_YEAR_RE = re.compile(
    rf"([{_CN_DIGIT_CHARS}]{{4}})\s*年\s*([{_CN_NUMBER_CHARS}]{{1,3}})\s*月"
    rf"\s*([{_CN_NUMBER_CHARS}]{{1,3}})\s*日?"
)


def _cn_year(text: str) -> int | None:
    """「二〇二五」→ 2025。年份逐字读，不做十进制进位。"""
    if not text or any(char not in _CN_DIGITS for char in text):
        return None
    return int("".join(str(_CN_DIGITS[char]) for char in text))


def _cn_number(text: str) -> int | None:
    """中文月/日：`九` → 9、`十` → 10、`十一` → 11、`二十三` → 23。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        if "十" in right:  # `十十` 之类：不合法
            return None
        tens = 1 if not left else _CN_DIGITS.get(left)
        ones = 0 if not right else _CN_DIGITS.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(text) == 1:
        return _CN_DIGITS.get(text)
    return None


def _build(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[date, int, int]]:
    """按出现顺序返回文本里的**全部完整日期**：`(date, start, end)`。

    与 `parse_date` 的区别是「不只取第一个」：列表条目里常同时出现标题日期、
    发布时间、正文里的施行日期，调用方需要自己决定取哪一个（例如取最后一个）。
    `start` / `end` 是原文本偏移，便于判断这个日期是不是独占一行。
    """
    raw = str(text or "")
    if not raw:
        return []

    hits: list[tuple[date, int, int]] = []
    for match in _NUMERIC_DATE_RE.finditer(raw):
        year, month, day = (int(group) for group in match.groups())
        parsed = _build(year, month, day)
        if parsed is not None:
            hits.append((parsed, match.start(), match.end()))
    for match in _ARABIC_YEAR_RE.finditer(raw):
        year, month, day = (int(group) for group in match.groups())
        parsed = _build(year, month, day)
        if parsed is not None:
            hits.append((parsed, match.start(), match.end()))
    for match in _CN_YEAR_RE.finditer(raw):
        year = _cn_year(match.group(1))
        month = _cn_number(match.group(2))
        day = _cn_number(match.group(3))
        if year is None or month is None or day is None:
            continue
        parsed = _build(year, month, day)
        if parsed is not None:
            hits.append((parsed, match.start(), match.end()))

    # 三种写法理论上互不重叠；仍按偏移排序并去重，防止同一段文本被两条正则各抓一次。
    hits.sort(key=lambda hit: (hit[1], hit[2]))
    result: list[tuple[date, int, int]] = []
    last_end = -1
    for parsed, start, end in hits:
        if start >= last_end:
            result.append((parsed, start, end))
            last_end = end
    return result


def parse_date(value: Any) -> date | None:
    """把常见的政务日期写法转成 `date`；解析不了返回 None（调用方按「无日期」处理）。

    支持：`2025-06-05`、`2025/6/5`、`2025.6.5`、`2025年6月5日`、
    `二〇二五年六月五日`、`二〇二五 年 六 月 五 日`、`2026年九月八日`
    （阿拉伯年份 + 中文月日混写）；字符串里日期后面还有时间/版次等内容也能按前缀匹配。
    """
    text = str(value or "").strip()
    if not text:
        return None

    match = _NUMERIC_DATE_RE.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return _build(year, month, day)

    match = _ARABIC_YEAR_RE.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return _build(year, month, day)

    match = _CN_YEAR_RE.search(text)
    if match:
        year = _cn_year(match.group(1))
        month = _cn_number(match.group(2))
        day = _cn_number(match.group(3))
        if year is None or month is None or day is None:
            return None
        return _build(year, month, day)

    return None
