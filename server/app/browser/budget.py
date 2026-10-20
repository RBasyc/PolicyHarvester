"""单次浏览器运行的预算：页数 / 动作数 / 抽取数 / 总时长。

浏览器比 HTTP 抓取贵得多（Chromium 内存 + 每步 1–3 秒的 settle），而且对话模型可能在
一个开放式任务里无限点下去。预算的作用是给模型一个**确定的上限**，并把剩余量写进每个
工具结果，让它在耗尽前先总结已有结果，而不是等到工具报错才收手。

计数在**主事件循环**里做（工具的协程就跑在主循环），不存在跨线程竞争。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.config import get_settings


@dataclass
class Budget:
    """一次运行的资源上限；字段为 None 时在 `__post_init__` 里取 .env 默认值。"""

    max_pages: int | None = None
    max_actions: int | None = None
    max_extracts: int | None = None
    timeout_seconds: int | None = None

    pages: int = 0
    actions: int = 0
    extracts: int = 0
    started_at: float = 0.0

    def __post_init__(self) -> None:
        settings = get_settings()
        if self.max_pages is None:
            self.max_pages = settings.browser_max_pages
        if self.max_actions is None:
            self.max_actions = settings.browser_max_actions
        if self.max_extracts is None:
            self.max_extracts = settings.browser_max_extracts
        if self.timeout_seconds is None:
            self.timeout_seconds = settings.browser_timeout_seconds
        if not self.started_at:
            self.started_at = time.monotonic()

    # ---------- 查询 ----------

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def expired(self) -> bool:
        return bool(self.timeout_seconds and self.elapsed >= self.timeout_seconds)

    def can_page(self) -> bool:
        return not self.expired() and self.pages < int(self.max_pages or 0)

    def can_action(self) -> bool:
        return not self.expired() and self.actions < int(self.max_actions or 0)

    def can_extract(self) -> bool:
        return not self.expired() and self.extracts < int(self.max_extracts or 0)

    # ---------- 计数 ----------

    def note_page(self) -> None:
        self.pages += 1

    def note_action(self) -> None:
        self.actions += 1

    def note_extract(self, count: int = 1) -> None:
        self.extracts += max(1, count)

    # ---------- 展示 ----------

    def line(self) -> str:
        """给模型看的一行预算说明（附在每个工具结果里）。"""
        return (
            f"预算：动作 {self.actions}/{self.max_actions} · "
            f"页面 {self.pages}/{self.max_pages} · "
            f"抽取 {self.extracts}/{self.max_extracts} · "
            f"已用 {int(self.elapsed)}/{self.timeout_seconds} 秒"
        )

    def summary(self) -> dict:
        return {
            "pages": self.pages,
            "max_pages": self.max_pages,
            "actions": self.actions,
            "max_actions": self.max_actions,
            "extracts": self.extracts,
            "max_extracts": self.max_extracts,
            "elapsed_seconds": round(self.elapsed, 1),
            "timeout_seconds": self.timeout_seconds,
            "expired": self.expired(),
        }
