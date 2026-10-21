"""从渲染后的页面里抽正文与元信息。

任意站点没有选择器配置，所以直接复用配置化来源的**结构解析**：
`html_source.parse_detail_page(html, DetailConfig(), url)`。空选择器链会触发
`_fallback_container` 的结构打分兜底（排除 `<html>/<body>`，按文本量/常见正文
id-class 加权），这比 `body.innerText` 干净得多。

兜底顺序：
1. 结构解析出的正文（≥ 40 字才认）；
2. 页面 `body.innerText`（导航噪声原样保留，但至少不会空手而归）；
3. 两者都空 → 返回空正文，由调用方决定是否报错。

日期用 `app.sources.util.parse_date` 统一解析（支持中文数字等政务写法），解析不了留空。
"""

from __future__ import annotations

import re
from typing import Any

MAX_HTML_CHARS = 4_000_000
MAX_BODY_CHARS = 20_000
MAX_PAGE_TEXT_CHARS = 20_000
_MIN_STRUCTURED_BODY = 40

_BLANK_LINES = re.compile(r"\n{3,}")
_WS = re.compile(r"[ \t　]+")


def _clean(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def extract(
    html: str | None,
    *,
    url: str,
    inner_text: str = "",
) -> dict[str, Any]:
    """HTML + 页面可见文本 → 正文与元信息（不含日期对象，日期给 ISO 字符串）。"""
    # 函数内导入：`app/sources/__init__` 会连带加载来源注册表（registry → gov_cn →
    # agent.mcp → agent.tools → browser.tools → actions → 本模块）。若在模块顶层导入，
    # 服务按 `app.agent.tools` 先于 `app.agent.mcp` 的顺序启动时会形成半初始化循环。
    from app.sources.config import DetailConfig
    from app.sources.date_signals import publish_date_from_label_text
    from app.sources.html_source import parse_detail_page
    from app.sources.util import parse_date

    detail = None
    if html:
        try:
            detail = parse_detail_page(html[:MAX_HTML_CHARS], DetailConfig(), url)
        except Exception:  # noqa: BLE001 - 解析失败退回 innerText，不让单页拖垮运行
            detail = None

    structured = _clean(detail.body) if detail is not None else ""
    if len(structured) >= _MIN_STRUCTURED_BODY:
        body = structured
        body_source = "html"
    else:
        body = _clean(inner_text)
        body_source = "inner_text" if body else "empty"

    whole_text = _clean(inner_text)
    date_value = None
    date_text = None
    date_source = None
    if detail is not None and detail.date_text:
        parsed = parse_date(detail.date_text)
        date_value = parsed.isoformat() if parsed else None
        date_text = detail.date_text
        date_source = detail.date_source
    # HTML 里没解析到页头（JS 渲染/结构兜底失败）时，用浏览器读到的可见文本再找一次
    # 「日期：…来源：…」。可见标签优先于旧选择器/字段规则，理由同 parse_detail_page。
    label_hit = publish_date_from_label_text(whole_text) if whole_text else None
    if label_hit is not None:
        date_value = label_hit.value.isoformat()
        date_text = date_value
        date_source = "label"

    return {
        "url": url,
        "title": (detail.title if detail is not None else None) or None,
        "body": body[:MAX_BODY_CHARS],
        "body_source": body_source,
        "body_truncated": len(body) > MAX_BODY_CHARS,
        "agency": (detail.agency if detail is not None else None) or None,
        "doc_no": (detail.doc_no if detail is not None else None) or None,
        "date": date_value,
        "date_text": date_text or None,
        "date_source": date_source,
        "image_count": (detail.image_count if detail is not None else 0) or 0,
        "attachments": (
            [attachment.as_dict() for attachment in detail.attachments]
            if detail is not None
            else []
        ),
        "metadata": dict(detail.extra) if detail is not None else {},
        "container_selector": (detail.container_selector if detail is not None else None),
        "warnings": list(detail.warnings) if detail is not None else [],
        # 给「结构解析拿不到日期」时的模型兜底用；不落库
        "page_text": (whole_text or body)[:MAX_PAGE_TEXT_CHARS],
    }
