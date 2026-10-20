"""浏览器会话/上下文的管理：全局并发上限、fresh context、无交互取 HTML。

两件容易踩的事，这里统一处理：

1. **并发上限**：`BROWSER_MAX_SESSIONS` 约束的是**所有打开的 BrowserContext**（对话会话、
   流水线发现、第二阶段的 `fetch_html` 都算）。槽位在打开时 acquire、在 session.close
   时 release；超出了就排队，而不是失败。
2. **每次运行一个全新 context**：不复用 cookie/storage，运行结束/取消/断流都关闭。
   因此 `thread_id` 不会被用来跨请求保留浏览器状态（客户端传的 id 不可信）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.browser import actions, guard, runtime, session
from app.config import get_settings

logger = logging.getLogger(__name__)

_INIT_SCRIPT = r"""
(() => {
  try { navigator.sendBeacon = () => false; } catch (e) {}
  try {
    const DisabledWebSocket = function () {
      throw new Error('WebSocket disabled by read-only browsing policy');
    };
    DisabledWebSocket.prototype = WebSocket.prototype;
    window.WebSocket = DisabledWebSocket;
  } catch (e) {}
})();
"""

_sessions_semaphore: asyncio.Semaphore | None = None
_active: set[session.BrowserSession] = set()


def _semaphore() -> asyncio.Semaphore:
    global _sessions_semaphore
    if _sessions_semaphore is None:
        _sessions_semaphore = asyncio.Semaphore(
            max(1, get_settings().browser_max_sessions)
        )
    return _sessions_semaphore


async def _new_context(browser: Any):
    """创建一个挂了请求门禁的 fresh context（worker 循环内调用）。"""
    settings = get_settings()
    request_guard = guard.RequestGuard()
    context = await browser.new_context(
        accept_downloads=False,
        ignore_https_errors=False,
        locale="zh-CN",
        user_agent=settings.source_user_agent,
        viewport={"width": 1280, "height": 900},
        service_workers="block",
    )
    await context.add_init_script(_INIT_SCRIPT)

    async def _handler(route, request) -> None:
        await guard.route_handler(request_guard, route, request)

    await context.route("**/*", _handler)
    return context, request_guard


async def _create_session(run: Any) -> session.BrowserSession:
    browser = await runtime.ensure_browser()
    context, request_guard = await _new_context(browser)
    try:
        page = await context.new_page()
    except Exception:
        await context.close()
        raise
    state = actions.SessionState(
        context=context, page=page, request_guard=request_guard
    )
    return session.BrowserSession(run, state)


async def open_session(run: Any) -> session.BrowserSession:
    """打开一次交互式运行（对话 / 流水线发现）；占一个 `BROWSER_MAX_SESSIONS` 槽位。"""
    if not runtime.available():
        raise runtime.BrowserUnavailable(runtime.unavailable_reason())

    semaphore = _semaphore()
    await semaphore.acquire()
    try:
        opened = await runtime.run_in_worker(_create_session(run))
    except BaseException:
        semaphore.release()
        raise

    released = False

    def _release() -> None:
        nonlocal released
        if released:
            return
        released = True
        _active.discard(opened)
        semaphore.release()

    opened._release = _release  # 由 session.close() 在关闭后调用
    _active.add(opened)
    return opened


async def fetch_html(url: str, *, timeout: float | None = None) -> dict[str, Any]:
    """无交互取渲染后的 HTML（第二阶段的 `BrowserAnySource.fetch_fulltext` 用）。

    `timeout` 单位为秒（与 `PolicySource.fetch_fulltext(timeout=...)` 对齐）；
    每次调用独立 context，调用结束即关闭。
    """
    if not runtime.available():
        raise runtime.BrowserUnavailable(runtime.unavailable_reason())

    semaphore = _semaphore()
    await semaphore.acquire()
    try:
        return await runtime.run_in_worker(_fetch_html(url, timeout))
    finally:
        semaphore.release()


async def _fetch_html(url: str, timeout: float | None) -> dict[str, Any]:
    settings = get_settings()
    timeout_ms = (
        int(timeout * 1000) if timeout else settings.browser_navigation_timeout_ms
    )
    browser = await runtime.ensure_browser()
    context, request_guard = await _new_context(browser)
    try:
        try:
            target = await guard.assert_navigation(url)
        except guard.BrowserBlocked as exc:
            raise actions.BrowserActionError(f"已阻止打开该网址：{exc}") from exc
        page = await context.new_page()
        try:
            await page.goto(
                target,
                wait_until="domcontentloaded",
                timeout=max(timeout_ms, 5000),
            )
        except Exception as exc:  # noqa: BLE001
            blocked = request_guard.blocked_reason
            raise actions.BrowserActionError(
                f"打开页面失败：{exc}" + (f"（{blocked}）" if blocked else "")
            ) from exc
        await actions.settle(page, initial=True)
        final = page.url
        if final.startswith(("chrome-error:", "about:error")):
            raise actions.BrowserActionError(
                "页面加载失败" + (f"（{request_guard.blocked_reason}）" if request_guard.blocked_reason else "")
            )
        try:
            await guard.assert_final_url(final)
        except guard.BrowserBlocked as exc:
            raise actions.BrowserActionError(f"页面被带到不允许的地址：{exc}") from exc
        try:
            html = await page.content()
        except Exception as exc:  # noqa: BLE001
            raise actions.BrowserActionError(f"读取页面 HTML 失败：{exc}") from exc
        try:
            inner = (await page.inner_text("body", timeout=5000))[:20_000]
        except Exception:  # noqa: BLE001
            inner = ""
        return {
            "url": final,
            "html": html[:4_000_000],
            "inner_text": inner,
            "blocked_reason": request_guard.blocked_reason or None,
        }
    finally:
        try:
            await context.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("关闭抓取上下文失败：%s", exc)


async def shutdown() -> None:
    """lifespan 收尾：关闭所有活跃会话，再关 Chromium / worker 循环。"""
    for opened in list(_active):
        try:
            await opened.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("关闭浏览器会话失败：%s", exc)
    _active.clear()
    await runtime.shutdown()
