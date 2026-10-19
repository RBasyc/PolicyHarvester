"""可选的 JS 渲染取数层（Playwright + Chromium）。

**默认关闭，按来源开启**。政务站绝大多数是服务端渲染，渲染一次的成本（内存、CPU、
耗时）是纯 HTTP 的十几倍；只有页面在浏览器里执行脚本后才有内容时才值得打开。

启用步骤（缺一步就会得到一条明确的 `RenderUnavailable`，不会静默退回原始 HTML）：

1. `cd server && uv sync --extra render`（可选依赖，`uv sync` 默认不会装）
2. `uv run playwright install chromium`（下载浏览器本体）
3. 仓库根 `.env` 设 `SOURCE_RENDER_ENABLED=true` 并重启服务（`Settings` 是启动时读的）
4. 来源配置里写 `"render": {"enabled": true, ...}`

设计边界：

- **渲染只是「取回 HTML」的另一种方式**。输出仍是 HTML 字符串，`htmlparse` 的
  mini-DOM、`analyze`、`FieldRule` 与抓取流水线完全不用知道浏览器存在。
- **安全门禁不因浏览器而放宽**：入口先过 `net.precheck()`（协议 / 白名单 /
  公网地址 / robots.txt），浏览器发出的**每个请求**再由 `_guard_route()` 按同一口径
  校验；非 GET 请求、图片/字体/媒体默认拦截。
- **残余风险要如实记住**：DNS 由 Chromium 自己解析，Python 侧校验的是校验那一刻的
  解析结果（TOCTOU）。`allowed_domains` 仍是主边界，不要把不受信的域名放进来。
- **资源可控**：独立并发上限（`SOURCE_RENDER_CONCURRENCY`，默认 2——别照搬 HTTP 的 4），
  单页超时可配，渲染后 HTML 仍受 `SOURCE_FETCH_MAX_BYTES` 约束。
- **Windows 事件循环隔离**：`app/__init__.py` 为 psycopg 设了 Selector 事件循环，而
  Playwright 在 Windows 上必须跑在 Proactor 循环里（Selector 不支持子进程，驱动起不来）。
  因此 Windows 下渲染请求派到一条专用的 Proactor 线程执行，主循环只 await 结果；
  其他平台仍在当前循环执行。详见下方 *Windows：渲染必须跑在独立的 Proactor 事件循环里*。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import threading
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from app.config import get_settings
from app.sources import net

logger = logging.getLogger(__name__)

INSTALL_HINT = "cd server && uv sync --extra render && uv run playwright install chromium"

# 默认拦截的资源类型：对「取文本」没有帮助，却最吃带宽与内存。
# 不拦 stylesheet——部分站点的懒加载脚本依赖 CSS 加载完成；显隐判断用 state="attached" 规避。
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "font", "media"})


class RenderUnavailable(RuntimeError):
    """渲染能力不可用（开关关闭 / 未安装 playwright / Chromium 启动失败）。"""


class _RenderFailed(Exception):
    """渲染失败且配置允许回退：由外层在主事件循环里改走纯 HTTP。"""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


@dataclass(frozen=True)
class RenderOptions:
    """一次渲染取数的参数（由来源配置的 `RenderConfig` 转换而来）。"""

    enabled: bool = False
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "networkidle"
    wait_for_selector: str | None = None
    wait_ms: int = 0
    scroll: bool = False
    max_scrolls: int = 5
    block_resources: bool = True
    timeout_ms: int = 45_000
    on_error: Literal["raise", "raw_fallback"] = "raise"


# ---------- 请求拦截口径（纯函数，便于离线测试） ----------


def blocked_reason(
    *,
    method: str,
    resource_type: str,
    url: str,
    allowed_domains: list[str] | None,
    block_resources: bool,
) -> str | None:
    """浏览器子请求的放行判定：返回 None = 放行，否则是拒绝原因。

    这里只做**不需要 DNS** 的判定；DNS / 公网校验在 `_guard_route()` 里异步补上。
    """
    if method.upper() not in ("GET", "HEAD"):
        return f"非只读请求（{method}）"
    if block_resources and resource_type in _BLOCKED_RESOURCE_TYPES:
        return f"资源类型 {resource_type} 已按配置拦截"
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return f"不支持的协议 {parsed.scheme or '（无）'}"
    host = (parsed.hostname or "").lower()
    if not host:
        return "地址缺少主机名"
    if not net.domain_allowed(host, allowed_domains):
        return f"主机 {host} 不在来源白名单内"
    return None


async def _guard_route(
    route: Any,
    *,
    allowed_domains: list[str] | None,
    options: RenderOptions,
) -> None:
    """Playwright 请求拦截：拦截所有子请求，口径与 `net` 的入口校验一致。"""
    request = route.request
    reason = blocked_reason(
        method=request.method,
        resource_type=request.resource_type,
        url=request.url,
        allowed_domains=allowed_domains,
        block_resources=options.block_resources,
    )
    if reason is None:
        try:
            # check_url 会做一次同步 DNS；放进线程池，别阻塞事件循环。
            await asyncio.to_thread(
                net.check_url,
                request.url,
                allowed_domains=allowed_domains,
            )
        except net.UnsafeURLError as exc:
            reason = str(exc)
        except Exception as exc:  # noqa: BLE001 - 校验本身出错也按拒绝处理
            reason = f"地址校验异常：{exc}"
    if reason is not None:
        logger.debug("渲染拦截请求（%s）：%s", reason, request.url)
        with contextlib.suppress(Exception):
            await route.abort()
        return
    try:
        await route.continue_()
    except Exception:  # noqa: BLE001 - 页面关闭/导航取消时继续请求会失败，忽略即可
        pass


# ---------- 浏览器生命周期 ----------

_browser: Any = None
_playwright: Any = None
_browser_loop: int | None = None
_browser_lock: asyncio.Lock | None = None
_browser_lock_loop: int | None = None
_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: int | None = None


def _loop_token() -> int:
    return id(asyncio.get_running_loop())


def _browser_guard() -> asyncio.Lock:
    global _browser_lock, _browser_lock_loop
    token = _loop_token()
    if _browser_lock is None or _browser_lock_loop != token:
        _browser_lock = asyncio.Lock()
        _browser_lock_loop = token
    return _browser_lock


def _render_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_loop
    token = _loop_token()
    if _semaphore is None or _semaphore_loop != token:
        _semaphore = asyncio.Semaphore(max(1, get_settings().source_render_concurrency))
        _semaphore_loop = token
    return _semaphore


def _drop_stale_loop_state() -> None:
    """事件循环变化时丢掉旧实例（旧浏览器只能在旧循环里关，这里关不掉）。"""
    global _browser, _playwright, _browser_loop
    token = _loop_token()
    if _browser_loop is not None and _browser_loop != token:
        logger.warning("渲染浏览器绑定的事件循环已变化，丢弃旧实例")
        _browser = None
        _playwright = None
        _browser_loop = None


async def _ensure_browser() -> Any:
    """懒启动 Chromium（进程级单例）；不可用时抛 `RenderUnavailable`。"""
    global _browser, _playwright, _browser_loop
    settings = get_settings()
    if not settings.source_render_enabled:
        raise RenderUnavailable(
            "JS 渲染未启用：在仓库根 .env 设 SOURCE_RENDER_ENABLED=true 并重启服务"
        )
    _drop_stale_loop_state()
    if _browser is not None and _browser.is_connected():
        return _browser

    async with _browser_guard():
        if _browser is not None and _browser.is_connected():
            return _browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # 可选依赖没装
            raise RenderUnavailable(f"未安装 playwright：{INSTALL_HINT}") from exc

        try:
            playwright = await async_playwright().start()
        except Exception as exc:  # noqa: BLE001 - 驱动子进程起不来（含事件循环不支持子进程）
            raise RenderUnavailable(
                f"Playwright 驱动启动失败（{exc.__class__.__name__}: {exc}）：{INSTALL_HINT}"
            ) from exc
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage"],  # 容器里 /dev/shm 太小会崩
            )
        except Exception as exc:  # noqa: BLE001 - 浏览器没下载 / 缺系统库
            with contextlib.suppress(Exception):
                await playwright.stop()
            raise RenderUnavailable(
                f"Chromium 启动失败（{exc.__class__.__name__}: {exc}）：{INSTALL_HINT}"
            ) from exc

        _playwright = playwright
        _browser = browser
        _browser_loop = _loop_token()
        logger.info("渲染浏览器已启动（Chromium, headless）")
        return browser


async def shutdown() -> None:
    """关停渲染层（FastAPI lifespan 收尾调用；未启用时是空操作）。"""
    global _worker_loop, _worker_thread
    loop, thread = _worker_loop, _worker_thread
    _worker_loop = None
    _worker_thread = None
    if loop is not None and loop.is_running():
        # 浏览器对象绑定在渲染线程的循环上，必须回那个循环里关。
        try:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(_shutdown_in_loop(), loop))
        except Exception:  # noqa: BLE001 - 收尾失败不能挡住进程退出
            logger.warning("渲染层收尾失败", exc_info=True)
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            await asyncio.to_thread(thread.join, 5)
        return
    await _shutdown_in_loop()


async def _shutdown_in_loop() -> None:
    global _browser, _playwright, _browser_loop, _browser_lock, _browser_lock_loop
    global _semaphore, _semaphore_loop
    browser, playwright = _browser, _playwright
    _browser = None
    _playwright = None
    _browser_loop = None
    _browser_lock = None
    _browser_lock_loop = None
    _semaphore = None
    _semaphore_loop = None
    if browser is not None:
        with contextlib.suppress(Exception):
            await browser.close()
    if playwright is not None:
        with contextlib.suppress(Exception):
            await playwright.stop()


# ---------- Windows：渲染必须跑在独立的 Proactor 事件循环里 ----------
#
# `app/__init__.py` 为了 psycopg 的异步模式把全局策略切成了 SelectorEventLoop（Windows），
# 而 Playwright 启动浏览器驱动要用 `create_subprocess_exec`——SelectorEventLoop 在 Windows
# 上不支持子进程，会抛一个**空消息的 `NotImplementedError`**（2026-10-19 实机踩到：
# 「渲染抓取失败（NotImplementedError: ）」）。两边都不能改：psycopg 不能用 Proactor，
# Playwright 不能用 Selector。所以渲染单独起一个线程 + Proactor 循环，主循环只等待结果。

_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_thread: threading.Thread | None = None
_worker_start_lock = threading.Lock()


def _needs_worker_loop() -> bool:
    """当前事件循环能否直接拉起 Playwright（Windows 的 Selector 循环不行）。"""
    if sys.platform != "win32":
        return False
    proactor = getattr(asyncio, "ProactorEventLoop", ())
    return not isinstance(asyncio.get_running_loop(), proactor)


def _start_worker_loop() -> asyncio.AbstractEventLoop:
    """懒启动渲染专用的事件循环线程（Proactor；进程内单例）。"""
    global _worker_loop, _worker_thread
    with _worker_start_lock:
        if _worker_loop is not None and _worker_loop.is_running():
            return _worker_loop
        loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()  # type: ignore[attr-defined]
        started = threading.Event()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            started.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        thread = threading.Thread(target=_run, name="render-loop", daemon=True)
        thread.start()
        started.wait(5)
        _worker_loop = loop
        _worker_thread = thread
        logger.info("渲染事件循环已启动（Windows Proactor，独立线程）")
        return loop


async def _run_in_worker(func: Any, /, *args: Any, **kwargs: Any) -> Any:
    """把协程丢进渲染线程的循环执行；取消会向那一侧传播。"""
    loop = _start_worker_loop()
    future = asyncio.run_coroutine_threadsafe(func(*args, **kwargs), loop)
    return await asyncio.wrap_future(future)


# ---------- 渲染取数 ----------


async def fetch_text(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
    options: RenderOptions,
    respect_robots: bool | None = None,
) -> net.FetchResult:
    """用浏览器打开 `url`，返回渲染后的 HTML（`net.FetchResult`，字段口径一致）。"""
    settings = get_settings()
    if not options.enabled:
        raise ValueError("render.fetch_text 只处理已启用的渲染选项")
    if not settings.source_render_enabled:
        raise RenderUnavailable(
            "JS 渲染未启用：在仓库根 .env 设 SOURCE_RENDER_ENABLED=true 并重启服务"
        )

    kwargs = dict(
        allowed_domains=allowed_domains,
        timeout=timeout,
        max_bytes=max_bytes,
        options=options,
        respect_robots=respect_robots,
    )
    try:
        if _needs_worker_loop():
            return await _run_in_worker(_fetch_text_in_loop, url, **kwargs)
        return await _fetch_text_in_loop(url, **kwargs)
    except _RenderFailed as exc:
        # 回退纯 HTTP 必须在**当前（主）循环**执行：`net` 的限流锁 / 并发信号量
        # 可能已绑定主循环，在渲染线程的循环里 await 会报 Future attached to a
        # different loop。
        logger.warning("页面渲染失败，回退纯 HTTP 取数（%s）：%s", url, exc.cause)
        return await net.fetch_text(
            url,
            allowed_domains=allowed_domains,
            timeout=timeout,
            respect_robots=respect_robots,
        )


async def _fetch_text_in_loop(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
    options: RenderOptions,
    respect_robots: bool | None = None,
) -> net.FetchResult:
    # 入口门禁与纯 HTTP 路径同一套：协议 / 白名单 / 公网地址 / robots.txt
    checked = await net.precheck(
        url,
        allowed_domains=allowed_domains,
        respect_robots=respect_robots,
    )
    page_timeout = max(1_000, options.timeout_ms)
    budget = page_timeout / 1000 + 30  # 导航 + 等待 + 滚动 + 取 HTML 的总预算

    try:
        async with asyncio.timeout(budget), _render_semaphore():
            return await _render_once(
                checked,
                allowed_domains=allowed_domains,
                options=options,
                page_timeout=page_timeout,
                max_bytes=max_bytes,
            )
    except net.UnsafeURLError:
        raise
    except RenderUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一映射成 FetchError / 原始 HTML 兜底
        if options.on_error == "raw_fallback":
            # 具体回退放在 `fetch_text` 里执行（可能不在同一个事件循环）。
            raise _RenderFailed(exc) from exc
        if isinstance(exc, net.FetchError):
            raise
        raise net.FetchError(
            f"渲染抓取失败（{exc.__class__.__name__}: {exc}）：{url}"
        ) from exc


async def _render_once(
    url: str,
    *,
    allowed_domains: list[str] | None,
    options: RenderOptions,
    page_timeout: int,
    max_bytes: int | None,
) -> net.FetchResult:
    settings = get_settings()
    browser = await _ensure_browser()
    context = await browser.new_context(
        user_agent=settings.source_user_agent,
        locale="zh-CN",
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=False,
    )
    try:
        async def route_handler(route: Any) -> None:
            await _guard_route(
                route,
                allowed_domains=allowed_domains,
                options=options,
            )

        await context.route("**/*", route_handler)
        page = await context.new_page()
        page.set_default_timeout(page_timeout)
        response = await page.goto(url, wait_until=options.wait_until, timeout=page_timeout)
        status = response.status if response is not None else 0
        if status >= 400:
            raise net.FetchError(f"HTTP {status}：{page.url}")
        if options.wait_for_selector:
            # attached 而非 visible：显隐常由异步 CSS 决定，等可见会把可解析的页面判死。
            await page.wait_for_selector(
                options.wait_for_selector, state="attached", timeout=page_timeout
            )
        if options.wait_ms:
            await page.wait_for_timeout(options.wait_ms)
        if options.scroll:
            await _scroll_to_bottom(page, options.max_scrolls)

        html = await page.content()
        final_url = page.url
    finally:
        # 超时/取消时关闭本身也会被取消；cleanup 必须吞掉 CancelledError，
        # 否则这个 context 会留在长驻浏览器里（外层取消/超时照常继续传播）。
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await context.close()

    raw = html.encode("utf-8")
    limit = max_bytes if max_bytes else settings.source_fetch_max_bytes
    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]
        html = raw.decode("utf-8", errors="ignore")
    return net.FetchResult(
        url=final_url,
        status=status,
        text=html,
        raw=raw,
        content_type="text/html; charset=utf-8",
        bytes_len=len(raw),
        truncated=truncated,
        redirects=0,  # 浏览器自己跟跳转，这里不计数；最终地址在 url 字段
    )


async def _scroll_to_bottom(page: Any, max_scrolls: int) -> None:
    """触发懒加载列表：滚到底、等一拍、高度不再增长就停。"""
    previous = -1
    for _ in range(max(0, max_scrolls)):
        height = await page.evaluate(
            "document.documentElement ? document.documentElement.scrollHeight : 0"
        )
        if height <= previous:
            break
        previous = height
        await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        await page.wait_for_timeout(500)
    await page.evaluate("window.scrollTo(0, 0)")
