"""`BrowserSession`：主循环手里的门面，Playwright 对象全在 worker 循环里。

主循环只允许调用这里的方法；每个方法都把协程派发到 worker 循环（`runtime.run_in_worker`），
拿到结果后把最新快照缓存在门面上供工具层读取。这样 Windows 上 psycopg 的 Selector 策略
和 Playwright 需要的 Proactor 循环可以并存，互不改全局。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from app.browser import actions, runtime
from app.browser.snapshot import PageSnapshot

logger = logging.getLogger(__name__)


class BrowserSession:
    def __init__(
        self,
        run: Any,
        state: actions.SessionState,
        *,
        release: Callable[[], None] | None = None,
    ) -> None:
        self._run = run
        self._state = state
        self._release = release
        self._closed = False
        self._snapshot: PageSnapshot | None = None

    # ---------- 查询 ----------

    @property
    def last_snapshot(self) -> PageSnapshot | None:
        return self._snapshot

    @property
    def closed(self) -> bool:
        return self._closed

    # ---------- 派发 ----------

    async def _call(self, coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        if self._closed:
            raise actions.BrowserActionError("浏览器会话已关闭，请重新打开网址")
        result = await runtime.run_in_worker(coro)
        if isinstance(result, dict):
            snapshot = result.pop("snapshot", None)
            if isinstance(snapshot, PageSnapshot):
                self._snapshot = snapshot
        return result

    async def open(self, url: str) -> dict[str, Any]:
        return await self._call(actions.navigate(self._state, url))

    async def snapshot(self) -> dict[str, Any]:
        return await self._call(actions.resnapshot(self._state))

    async def click(self, ref: int) -> dict[str, Any]:
        return await self._call(actions.click(self._state, ref))

    async def type_text(
        self, ref: int, text: str, submit: bool = False
    ) -> dict[str, Any]:
        return await self._call(actions.type_text(self._state, ref, text, submit))

    async def select_option(self, ref: int, option: str) -> dict[str, Any]:
        return await self._call(actions.select_option(self._state, ref, option))

    async def press(self, key: str) -> dict[str, Any]:
        return await self._call(actions.press(self._state, key))

    async def scroll(
        self, direction: str = "down", pages: float = 1.0
    ) -> dict[str, Any]:
        return await self._call(actions.scroll(self._state, direction, pages))

    async def back(self) -> dict[str, Any]:
        return await self._call(actions.go_back(self._state))

    async def wait(self, seconds: float = 1.0) -> dict[str, Any]:
        return await self._call(actions.wait(self._state, seconds))

    async def extract_page(self) -> dict[str, Any]:
        return await self._call(actions.extract_page(self._state))

    async def extract_articles(self, refs: list[int]) -> dict[str, Any]:
        return await self._call(actions.extract_articles(self._state, refs))

    # ---------- 关闭 ----------

    async def close(self) -> None:
        """关闭这个 context 并归还并发槽位（幂等；断流/取消路径也会走到）。"""
        if self._closed:
            return
        self._closed = True
        try:
            await runtime.run_in_worker(actions.close(self._state))
        except Exception as exc:  # noqa: BLE001 - worker 已停 / context 已断
            logger.debug("浏览器会话关闭失败：%s", exc)
        finally:
            self._state = None  # type: ignore[assignment]
            release, self._release = self._release, None
            if release is not None:
                release()
