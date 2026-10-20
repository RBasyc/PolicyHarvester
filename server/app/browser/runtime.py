"""浏览器专用事件循环与 Chromium 单例。

## 为什么要单独开一个线程

Windows 上 `app/__init__.py` 把全局事件循环策略设成 `WindowsSelectorEventLoopPolicy`
（psycopg 的要求），而 Playwright 的 asyncio 驱动**必须**跑在 Proactor 循环上。
不能为了浏览器把全局策略改回去——那会把数据库连接打坏。

因此这里起一个 daemon 线程，线程内自建循环（Windows = `ProactorEventLoop`，其它平台 =
普通新循环）。主循环只持有**不透明句柄**，所有 Playwright 对象都留在 worker 循环里；
每个动作通过 `run_in_worker()` 派发（`run_coroutine_threadsafe` + `asyncio.wrap_future`）。

## 生命周期

- 进程内 Chromium 只有一份（`ensure_browser()` 懒启动，第一次使用时才装/起浏览器）；
- `available()` 只查「开关 + playwright 包是否存在」，**不启动任何进程**——`/api/config/
  features` 与来源列表每个请求都会调它；
- `shutdown()` 由 lifespan 收尾调用：先关浏览器，再停 worker 循环。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import threading
from typing import Any, Awaitable, TypeVar

from app.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BrowserUnavailable(RuntimeError):
    """浏览器引擎不可用（开关关闭 / 未安装 playwright / Chromium 启动失败）。"""


_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_loop_ready = threading.Event()
_loop_lock = threading.Lock()

_playwright: Any = None  # Playwright 对象（worker 循环内创建）
_browser: Any = None  # Browser 对象（worker 循环内创建）
_launch_lock: asyncio.Lock | None = None  # 懒建在 worker 循环里（不能在主循环建）


# ---------- 可用性（同步；不启动浏览器） ----------


def available() -> bool:
    """浏览器功能是否可用：`BROWSER_ENABLED=true` 且装了 playwright。"""
    if not get_settings().browser_enabled:
        return False
    return _module_installed()


def unavailable_reason() -> str:
    """给用户看的原因（可用时返回空串）。文案里带上安装步骤。"""
    settings = get_settings()
    if not settings.browser_enabled:
        return (
            "无头浏览器未启用：请在仓库根目录 .env 设置 BROWSER_ENABLED=true 后重启服务"
        )
    if not _module_installed():
        return (
            "未安装 Playwright：请在 server/ 下执行 "
            "`uv sync --extra browser` 与 `uv run playwright install chromium`，然后重启服务"
        )
    return ""


def _module_installed() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):  # pragma: no cover - 极端环境
        return False


# ---------- worker 循环 ----------


def _loop_main() -> None:
    global _loop
    if sys.platform == "win32":
        # psycopg 需要 Selector 策略，Playwright 需要 Proactor：只在**这个线程**里建
        # Proactor，不调用 asyncio.set_event_loop_policy（那会影响全局）。
        loop: asyncio.AbstractEventLoop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)
    _loop_ready.set()
    try:
        loop.run_forever()
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()
            _loop = None


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """确保 worker 循环线程在跑，返回它的 loop（主循环/任意线程都可调用）。"""
    global _thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop_ready.clear()
        _thread = threading.Thread(target=_loop_main, name="browser-loop", daemon=True)
        _thread.start()
    if not _loop_ready.wait(timeout=10):
        raise BrowserUnavailable("浏览器 worker 线程启动超时")
    if _loop is None:  # pragma: no cover - 线程启动后立刻退出
        raise BrowserUnavailable("浏览器 worker 线程启动失败")
    return _loop


async def run_in_worker(coro: Awaitable[T]) -> T:
    """把一个协程派发到浏览器 worker 循环执行，并等待结果。

    外层任务被取消时，`wrap_future` 会取消 concurrent future，进而取消 worker 里的
    任务（Playwright 动作随之中断）。
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
    return await asyncio.wrap_future(future)


# ---------- Chromium 单例（只能在 worker 循环里调用） ----------


async def ensure_browser() -> Any:
    """拿到共享 Chromium（首次调用时启动）；失败转成带安装提示的 `BrowserUnavailable`。"""
    global _playwright, _browser, _launch_lock

    if _browser is not None and _browser.is_connected():
        return _browser

    if _launch_lock is None:
        _launch_lock = asyncio.Lock()

    async with _launch_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        if not available():
            raise BrowserUnavailable(unavailable_reason())

        settings = get_settings()
        try:
            from playwright.async_api import async_playwright  # 延迟导入：可选依赖

            if _playwright is None:
                _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=settings.browser_headless,
                args=[
                    # 不继承系统代理：避免 HTTP_PROXY 把「公网校验通过的地址」代理到别处
                    "--no-proxy-server",
                    "--disable-dev-shm-usage",  # 容器里 /dev/shm 太小会导致 Chromium 崩溃
                    "--disable-background-networking",
                    "--disable-sync",
                    "--no-first-run",
                    "--disable-default-apps",
                ],
            )
        except BrowserUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 安装缺失 / 启动失败都归到同一类
            await _close_quietly()
            raise BrowserUnavailable(
                "Chromium 启动失败（多半是未安装浏览器内核）：请在 server/ 下执行 "
                "`uv run playwright install chromium` 后重试。"
                f"原始错误：{exc}"
            ) from exc

        logger.info(
            "无头浏览器已启动（headless=%s，会话上限 %d）",
            settings.browser_headless,
            settings.browser_max_sessions,
        )
        return _browser


async def _close_quietly() -> None:
    """关闭浏览器与 Playwright（幂等；worker 循环内调用）。"""
    global _playwright, _browser, _launch_lock
    if _browser is not None:
        try:
            await _browser.close()
        except Exception as exc:  # noqa: BLE001 - 收尾失败只记日志
            logger.debug("关闭 Chromium 失败：%s", exc)
        _browser = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception as exc:  # noqa: BLE001
            logger.debug("停止 Playwright 失败：%s", exc)
        _playwright = None
    _launch_lock = None


# ---------- 关停 ----------


async def shutdown() -> None:
    """lifespan 收尾：关浏览器、停 worker 循环。幂等，可重复调用。"""
    global _thread

    loop = _loop
    if loop is not None and loop.is_running():
        try:
            await run_in_worker(_close_quietly())
        except Exception as exc:  # noqa: BLE001 - 关停失败不该阻断进程退出
            logger.warning("浏览器关停时出错：%s", exc)
        loop.call_soon_threadsafe(loop.stop)
        thread = _thread
        if thread is not None:
            thread.join(timeout=10)
    _thread = None
