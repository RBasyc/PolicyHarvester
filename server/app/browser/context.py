"""一次浏览器运行（`BrowserRun`）与它的 `ContextVar`。

工具函数（`app/browser/tools.py`）不接收 run 参数——LangChain 的工具签名要暴露给模型，
多一个内部对象只会污染 schema。所以 run 放在 `ContextVar` 里：

- `/api/chat` 与 `/api/chat/stream` 在创建图任务前 `set`，任务被取消时 `finally` 关闭；
- 流水线的发现循环在自己的任务里 `set` / `reset`；
- 工具的协程继承该 context，读写的是同一个 run。

`thread_id` 是客户端传的，不能拿它跨请求持久化浏览器会话（并发、伪造都存在），
所以每个请求一个 run、一个 fresh context；对话的多轮浏览器状态不保留。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from app.browser.budget import Budget

logger = logging.getLogger(__name__)


@dataclass
class BrowserRun:
    mode: Literal["chat", "pipeline"] = "chat"
    budget: Budget = field(default_factory=Budget)
    # SSE 发射器（同步、不抛异常）。流水线为 None；对话把 tool 事件塞进队列。
    emit: Callable[[dict[str, Any]], None] | None = None
    session: Any = None  # BrowserSession，懒打开
    # 流水线发现循环的产出；对话模式不使用
    collected: list[dict[str, Any]] = field(default_factory=list)
    extracted: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False
    stop_reason: str = ""

    def emit_tool(
        self, name: str, detail: str, status: str, *, ok: bool = False
    ) -> None:
        """发一条 `tool` SSE 事件。发射器故障绝不影响浏览本身。"""
        if self.emit is None:
            return
        try:
            self.emit(
                {
                    "type": "tool",
                    "name": name,
                    "detail": detail,
                    "status": status,
                    "ok": bool(ok),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 事件发不出去不能拖垮工具
            logger.debug("tool 事件发送失败：%s", exc)

    async def ensure_session(self):
        if self.session is None:
            from app.browser.manager import open_session

            self.session = await open_session(self)
        return self.session

    def collect(self, item: dict[str, Any]) -> bool:
        """收集一个候选链接（按 URL 去重）；返回是否新增。"""
        url = str(item.get("url") or "").strip()
        if not url:
            return False
        if any(existing.get("url") == url for existing in self.collected):
            return False
        self.collected.append(item)
        return True

    async def aclose(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            try:
                await session.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("关闭浏览器运行失败：%s", exc)


browser_run_var: ContextVar[BrowserRun | None] = ContextVar(
    "browser_run", default=None
)


def get_browser_run() -> BrowserRun | None:
    return browser_run_var.get()
