"""编号 DOM 快照（移植自 webagent 的做法，不是视觉截图）。

模型不「看」页面，而是读一份结构化清单：页面上每个**可见可交互元素**拿到一个数字编号
（同时写到 DOM 的 `data-agent-id`），后续 `browser_click(ref=12)` 就按编号操作。
这样模型不需要 CSS 选择器，也不需要猜测坐标。

几点实现约定：

- 每次快照先清掉旧的 `data-agent-id`，再按**当前可见性**重新编号——编号只对最新一次
  快照有效，动作之后必须重建（工具结果里会自动附上最新快照）。
- 遍历主文档 + **open** shadow root；跨域 iframe 里的内容不进入快照（浏览器同源策略），
  这是已知限制。同源 iframe 目前也不展开（保持实现简单、编号稳定）。
- 上限：最多约 220 个元素、页面文本 8k 字符。快照会进入对话历史，必须封顶。
- 页面文本是不可信数据，由工具层用 `<untrusted_page_content>` 包裹后再交给模型。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

MAX_ELEMENTS = 220
MAX_TEXT_CHARS = 8000


_SNAPSHOT_JS = r"""
() => {
  const MAX_ELEMENTS = 220;
  const out = [];
  document.querySelectorAll('[data-agent-id]').forEach((el) => el.removeAttribute('data-agent-id'));

  const roots = [document];
  document.querySelectorAll('*').forEach((el) => {
    if (el.shadowRoot) roots.push(el.shadowRoot);
  });

  const selector = [
    'a[href]', 'button', 'input', 'textarea', 'select', 'summary',
    '[role="button"]', '[role="link"]', '[role="tab"]', '[role="menuitem"]',
    '[contenteditable="true"]', '[onclick]', '[tabindex]'
  ].join(',');

  const visible = (el) => {
    if (!(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width >= 2 && rect.height >= 2;
  };

  const nameOf = (el) => {
    const candidates = [
      el.getAttribute('aria-label'), el.getAttribute('title'),
      el.getAttribute('alt'), el.innerText, el.value,
      el.getAttribute('placeholder'), el.getAttribute('name')
    ];
    for (const raw of candidates) {
      const text = (raw || '').replace(/\s+/g, ' ').trim();
      if (text) return text.slice(0, 120);
    }
    return el.tagName.toLowerCase();
  };

  for (const root of roots) {
    if (out.length >= MAX_ELEMENTS) break;
    const nodes = root.querySelectorAll(selector);
    for (const el of nodes) {
      if (out.length >= MAX_ELEMENTS) break;
      if (!visible(el)) continue;
      const tag = el.tagName.toLowerCase();
      if (tag === 'script' || tag === 'style' || tag === 'noscript') continue;
      const id = String(out.length);
      el.setAttribute('data-agent-id', id);
      const item = {
        id: Number(id),
        tag: tag,
        role: (el.getAttribute('role') || tag).slice(0, 24),
        name: nameOf(el),
      };
      if (tag === 'a') {
        const raw = el.getAttribute('href') || '';
        if (raw && !raw.startsWith('javascript:') && !raw.startsWith('mailto:') && !raw.startsWith('#')) {
          try { item.href = new URL(raw, location.href).href; } catch (e) {}
        }
      }
      if (tag === 'input') {
        item.input_type = (el.getAttribute('type') || 'text').toLowerCase();
        item.disabled = !!el.disabled;
      } else if (tag === 'button' || tag === 'select' || tag === 'textarea') {
        item.disabled = !!el.disabled;
      }
      out.push(item);
    }
  }

  const bodyText = document.body ? (document.body.innerText || '') : '';
  return {
    url: location.href,
    title: document.title || '',
    elements: out,
    text: bodyText,
    scroll: {
      y: Math.round(window.scrollY || 0),
      height: Math.round(document.documentElement.scrollHeight || 0),
      viewport: Math.round(window.innerHeight || 0),
    },
  };
}
"""


@dataclass
class SnapshotItem:
    index: int
    name: str
    role: str
    tag: str
    href: str | None = None
    input_type: str | None = None
    disabled: bool = False

    def describe(self) -> str:
        parts = [f"[{self.index}] {self.role} \"{self.name}\""]
        if self.input_type:
            parts.append(f"input[{self.input_type}]")
        if self.disabled:
            parts.append("（禁用）")
        if self.href:
            parts.append(f"→ {self.href}")
        return " ".join(parts)


@dataclass
class PageSnapshot:
    url: str
    title: str
    text: str
    elements: list[SnapshotItem] = field(default_factory=list)
    scroll: dict[str, int] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """页面指纹：网址 + 元素清单；用于识别「动作之后页面其实没变」。"""
        raw = self.url + "\n" + "\n".join(
            f"{item.index}|{item.name}|{item.href or ''}" for item in self.elements
        )
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()

    def format(self, *, max_elements: int = 180, max_text: int = MAX_TEXT_CHARS) -> str:
        """渲染成给模型看的文本清单（调用方再包 `<untrusted_page_content>`）。"""
        lines = [
            f"页面标题：{self.title or '（无）'}",
            f"当前网址：{self.url}",
        ]
        if self.scroll:
            lines.append(
                f"滚动位置：{self.scroll.get('y', 0)} / "
                f"总高 {self.scroll.get('height', 0)}（视口 {self.scroll.get('viewport', 0)}）"
            )
        lines.append("可交互元素（编号 → 元素；编号只对本次快照有效）：")
        if not self.elements:
            lines.append("（未发现可交互元素）")
        for item in self.elements[:max_elements]:
            lines.append("  " + item.describe())
        if len(self.elements) > max_elements:
            lines.append(f"  ……另有 {len(self.elements) - max_elements} 个元素未列出")

        text = (self.text or "").strip()
        if text:
            lines.append("页面文本（节选，可能被截断）：")
            if len(text) > max_text:
                text = text[:max_text] + "\n……（页面文本已截断）"
            lines.append(text)
        return "\n".join(lines)


async def take_snapshot(page: Any) -> PageSnapshot:
    """在 page 上执行快照脚本并转成 `PageSnapshot`（worker 循环内调用）。"""
    raw = await page.evaluate(_SNAPSHOT_JS)
    elements = [
        SnapshotItem(
            index=int(item.get("id", index)),
            name=str(item.get("name") or ""),
            role=str(item.get("role") or item.get("tag") or ""),
            tag=str(item.get("tag") or ""),
            href=(str(item["href"]) if item.get("href") else None),
            input_type=(str(item["input_type"]) if item.get("input_type") else None),
            disabled=bool(item.get("disabled")),
        )
        for index, item in enumerate(raw.get("elements") or [])
    ]
    scroll_raw = raw.get("scroll") or {}
    scroll = {str(k): int(v or 0) for k, v in scroll_raw.items()}
    return PageSnapshot(
        url=str(raw.get("url") or page.url),
        title=str(raw.get("title") or ""),
        text=str(raw.get("text") or "")[:MAX_TEXT_CHARS],
        elements=elements[:MAX_ELEMENTS],
        scroll=scroll,
    )
