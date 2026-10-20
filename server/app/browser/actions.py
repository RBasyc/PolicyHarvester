"""浏览器动作（全部在 worker 循环里执行）。

每个动作的固定节拍：

1. 动作前按需校验目标地址（click 的 href、navigate 的 url）——`guard.assert_navigation`；
2. 执行 Playwright 动作，等待页面稳定（`domcontentloaded` + `networkidle` + 一小段静置）；
3. 复验最终地址，重建编号快照。

本模块最上面**不导入 Playwright**：服务在未安装可选依赖时必须能正常启动。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.browser import content as page_content
from app.browser import guard as browser_guard
from app.browser.snapshot import PageSnapshot, take_snapshot
from app.config import get_settings

logger = logging.getLogger(__name__)

_MAX_TEXT = 20_000
_MAX_HTML = 4_000_000


class BrowserActionError(RuntimeError):
    """动作失败（元素失效 / 页面打不开 / 输入被拒等），文案可直接给模型看。"""


@dataclass
class SessionState:
    """worker 侧的会话状态。主循环只能通过 `BrowserSession` 间接操作它。"""

    context: Any
    page: Any
    request_guard: browser_guard.RequestGuard
    snapshot: PageSnapshot | None = None
    pages: list[Any] = field(default_factory=list)  # 弹窗/工作页（关闭时统一由 context 清理）
    extracted: int = 0


def _is_timeout(exc: Exception) -> bool:
    return "Timeout" in type(exc).__name__


async def settle(page: Any, *, initial: bool = False) -> None:
    """等页面稳定。所有等待都吞异常：超时 = 页面还在加载，不该让动作直接失败。"""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    try:
        await page.wait_for_load_state(
            "networkidle", timeout=6000 if initial else 2500
        )
    except Exception:  # noqa: BLE001 - 长轮询站点永远不 idle
        pass
    try:
        await page.wait_for_timeout(600 if initial else 300)
    except Exception:  # noqa: BLE001
        pass


async def rebuild(state: SessionState) -> PageSnapshot:
    snapshot = await take_snapshot(state.page)
    state.snapshot = snapshot
    return snapshot


def _find_item(state: SessionState, ref: int):
    if state.snapshot is None:
        raise BrowserActionError("还没有页面快照，请先调用 browser_snapshot")
    try:
        wanted = int(ref)
    except (TypeError, ValueError) as exc:
        raise BrowserActionError(f"编号必须是整数：{ref!r}") from exc
    for item in state.snapshot.elements:
        if item.index == wanted:
            return item
    raise BrowserActionError(
        f"编号 {wanted} 不在最新快照里（页面可能已变化），请先调用 browser_snapshot 查看新编号"
    )


async def _locator(state: SessionState, ref: int):
    item = _find_item(state, ref)
    locator = state.page.locator(f'[data-agent-id="{item.index}"]')
    try:
        count = await locator.count()
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"元素已失效：{exc}") from exc
    if count == 0:
        raise BrowserActionError("元素已失效（页面已刷新或跳转），请重新查看快照")
    return locator.first, item


async def _goto(page: Any, url: str) -> None:
    settings = get_settings()
    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=settings.browser_navigation_timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"打开页面失败：{exc}") from exc


async def _final_url(state: SessionState) -> str:
    final = state.page.url
    if final.startswith(("chrome-error:", "about:error")):
        blocked = state.request_guard.blocked_reason
        raise BrowserActionError(
            "页面加载失败"
            + (f"（{blocked}）" if blocked else "")
            + "，请检查网址或换一个入口"
        )
    try:
        await browser_guard.assert_final_url(final)
    except browser_guard.BrowserBlocked as exc:
        raise BrowserActionError(f"页面被带到不允许的地址：{exc}") from exc
    return final


# ---------- 导航类动作 ----------


async def navigate(state: SessionState, url: str) -> dict[str, Any]:
    try:
        target = await browser_guard.assert_navigation(url)
    except browser_guard.BrowserBlocked as exc:
        raise BrowserActionError(f"已阻止打开该网址：{exc}") from exc

    await _goto(state.page, target)
    await settle(state.page, initial=True)
    final = await _final_url(state)
    snapshot = await rebuild(state)
    return {
        "url": final,
        "title": snapshot.title,
        "blocked_reason": state.request_guard.blocked_reason or None,
        "snapshot": snapshot,
    }


async def click(state: SessionState, ref: int) -> dict[str, Any]:
    locator, item = await _locator(state, ref)
    if item.disabled:
        raise BrowserActionError(f"[{ref}] 是禁用元素，无法点击")
    if item.href:
        try:
            await browser_guard.assert_navigation(item.href)
        except browser_guard.BrowserBlocked as exc:
            raise BrowserActionError(f"已阻止点击该链接：{exc}") from exc

    popup = None
    clicked = False
    try:
        async with state.context.expect_page(timeout=1200) as popup_info:
            await locator.click(timeout=8000)
            clicked = True
    except Exception as exc:  # noqa: BLE001
        if not (clicked and _is_timeout(exc)):
            raise BrowserActionError(f"点击 [{ref}] 失败：{exc}") from exc
    else:
        popup = await popup_info.value

    note = ""
    if popup is not None:
        state.pages.append(popup)
        try:
            await popup.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        popup_url = popup.url
        try:
            await popup.close()
        except Exception:  # noqa: BLE001
            pass
        note = f"点击打开了新窗口：{popup_url}（已关闭弹窗"
        # 新窗口是 _blank 目标时，主页面通常没动；把主页面导航到弹窗地址，
        # 这样后续快照/抽取看到的就是用户真正点开的内容。
        if popup_url.startswith(("http://", "https://")):
            try:
                await browser_guard.assert_navigation(popup_url)
            except browser_guard.BrowserBlocked as exc:
                note += f"；该地址被阻止：{exc}）"
            else:
                await _goto(state.page, popup_url)
                note += "；已切换到该页面）"
        else:
            note += "）"
    else:
        await settle(state.page)

    final = await _final_url(state)
    snapshot = await rebuild(state)
    return {
        "url": final,
        "title": snapshot.title,
        "clicked": item.name,
        "note": note,
        "blocked_reason": state.request_guard.blocked_reason or None,
        "snapshot": snapshot,
    }


async def type_text(
    state: SessionState, ref: int, text: str, submit: bool = False
) -> dict[str, Any]:
    locator, item = await _locator(state, ref)
    if item.input_type in {"password", "file", "hidden"}:
        raise BrowserActionError(
            f"[{ref}] 是 {item.input_type} 输入框，只读浏览策略不允许填写"
        )
    if item.disabled:
        raise BrowserActionError(f"[{ref}] 是禁用输入框，无法填写")
    try:
        await locator.fill(text, timeout=5000)
    except Exception:  # noqa: BLE001 - contenteditable 等场景退回键盘输入
        try:
            await locator.click(timeout=5000)
            await state.page.keyboard.type(text, delay=15)
        except Exception as exc:  # noqa: BLE001
            raise BrowserActionError(f"输入失败：{exc}") from exc
    try:
        if submit:
            await locator.press("Enter", timeout=5000)
            await settle(state.page)
        else:
            await state.page.wait_for_timeout(150)
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"提交输入失败：{exc}") from exc

    final = await _final_url(state)
    snapshot = await rebuild(state)
    return {
        "url": final,
        "title": snapshot.title,
        "submitted": bool(submit),
        "blocked_reason": state.request_guard.blocked_reason or None,
        "snapshot": snapshot,
    }


async def select_option(state: SessionState, ref: int, option: str) -> dict[str, Any]:
    locator, item = await _locator(state, ref)
    if item.tag != "select":
        raise BrowserActionError(f"[{ref}] 不是下拉框（{item.tag}）")
    try:
        try:
            await locator.select_option(label=option, timeout=5000)
        except Exception:  # noqa: BLE001 - 兼容按 value 选择
            await locator.select_option(value=option, timeout=5000)
        await state.page.wait_for_timeout(300)
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"选择失败：{exc}") from exc
    final = await _final_url(state)
    snapshot = await rebuild(state)
    return {
        "url": final,
        "title": snapshot.title,
        "selected": option,
        "snapshot": snapshot,
    }


async def press(state: SessionState, key: str) -> dict[str, Any]:
    key = (key or "").strip()
    if not key or len(key) > 40:
        raise BrowserActionError("按键名无效（例如 Enter / PageDown / Control+A）")
    try:
        await state.page.keyboard.press(key, timeout=5000)
        await settle(state.page)
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"按键失败：{exc}") from exc
    final = await _final_url(state)
    snapshot = await rebuild(state)
    return {"url": final, "title": snapshot.title, "snapshot": snapshot}


async def scroll(
    state: SessionState, direction: str = "down", pages: float = 1.0
) -> dict[str, Any]:
    direction = (direction or "down").lower()
    try:
        if direction == "top":
            await state.page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await state.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif direction in ("down", "up"):
            amount = max(0.25, min(float(pages or 1.0), 5.0))
            height = await state.page.evaluate("window.innerHeight || 800")
            dy = int(height * amount) * (1 if direction == "down" else -1)
            await state.page.mouse.wheel(0, dy)
        else:
            raise BrowserActionError("direction 只能是 down / up / top / bottom")
        await state.page.wait_for_timeout(400)
    except BrowserActionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"滚动失败：{exc}") from exc
    snapshot = await rebuild(state)
    return {
        "url": state.page.url,
        "title": snapshot.title,
        "scroll": snapshot.scroll,
        "snapshot": snapshot,
    }


async def go_back(state: SessionState) -> dict[str, Any]:
    settings = get_settings()
    try:
        response = await state.page.go_back(
            wait_until="domcontentloaded",
            timeout=settings.browser_navigation_timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"后退失败：{exc}") from exc
    if response is None:
        raise BrowserActionError("没有可后退的浏览历史")
    await settle(state.page)
    final = await _final_url(state)
    snapshot = await rebuild(state)
    return {"url": final, "title": snapshot.title, "snapshot": snapshot}


async def wait(state: SessionState, seconds: float = 1.0) -> dict[str, Any]:
    seconds = max(0.2, min(float(seconds or 1.0), 5.0))
    try:
        await state.page.wait_for_timeout(int(seconds * 1000))
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"等待失败：{exc}") from exc
    snapshot = await rebuild(state)
    return {
        "url": state.page.url,
        "title": snapshot.title,
        "snapshot": snapshot,
    }


async def resnapshot(state: SessionState) -> dict[str, Any]:
    snapshot = await rebuild(state)
    return {
        "url": snapshot.url,
        "title": snapshot.title,
        "elements": len(snapshot.elements),
        "snapshot": snapshot,
    }


# ---------- 抽取 ----------


async def _page_text(page: Any) -> str:
    try:
        return (await page.inner_text("body", timeout=5000))[:_MAX_TEXT]
    except Exception:  # noqa: BLE001
        return ""


async def _extract_current(page: Any, *, url: str) -> dict[str, Any]:
    try:
        html = await page.content()
    except Exception as exc:  # noqa: BLE001
        raise BrowserActionError(f"读取页面 HTML 失败：{exc}") from exc
    inner = await _page_text(page)
    return page_content.extract(html[:_MAX_HTML], url=url, inner_text=inner)


async def extract_page(state: SessionState) -> dict[str, Any]:
    final = await _final_url(state)
    data = await _extract_current(state.page, url=final)
    if not data.get("body"):
        raise BrowserActionError("当前页面没有抽到正文（可能是空壳页或加载未完成）")
    data["blocked_reason"] = state.request_guard.blocked_reason or None
    return data


async def extract_articles(
    state: SessionState, refs: list[int], *, limit: int = 5
) -> dict[str, Any]:
    """在当前快照的链接编号里批量打开详情页并抽正文。

    用一个独立的 worker page（同一个 context，因此同样过 route 门禁），不改变主页面。
    """
    unique: list[int] = []
    for ref in refs or []:
        try:
            value = int(ref)
        except (TypeError, ValueError):
            continue
        if value not in unique:
            unique.append(value)
    unique = unique[: max(1, limit)]
    if not unique:
        raise BrowserActionError("请给出至少一个当前快照里的链接编号")

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    worker = await state.context.new_page()
    state.pages.append(worker)
    try:
        for ref in unique:
            try:
                item = _find_item(state, ref)
                if item.tag != "a" or not item.href:
                    raise BrowserActionError(f"[{ref}] 不是带链接的元素")
                try:
                    target = await browser_guard.assert_navigation(item.href)
                except browser_guard.BrowserBlocked as exc:
                    raise BrowserActionError(f"已阻止打开 {item.href}：{exc}") from exc
                await _goto(worker, target)
                await settle(worker)
                if worker.url.startswith(("chrome-error:", "about:error")):
                    raise BrowserActionError("详情页加载失败")
                try:
                    await browser_guard.assert_final_url(worker.url)
                except browser_guard.BrowserBlocked as exc:
                    raise BrowserActionError(f"详情页地址不被允许：{exc}") from exc
                data = await _extract_current(worker, url=worker.url)
                data["link_title"] = item.name
                data["ref"] = ref
                items.append(data)
            except Exception as exc:  # noqa: BLE001 - 单篇失败继续其余链接
                errors.append(f"[{ref}] {exc}")
    finally:
        try:
            await worker.close()
        except Exception:  # noqa: BLE001
            pass

    state.extracted += len(items)
    if not items:
        raise BrowserActionError(
            "所有链接都抽取失败：" + "；".join(errors[:5])
        )
    return {
        "articles": items,
        "errors": errors,
        "blocked_reason": state.request_guard.blocked_reason or None,
    }


async def close(state: SessionState) -> None:
    try:
        await state.context.close()
    except Exception as exc:  # noqa: BLE001 - 关停失败只记日志
        logger.debug("关闭浏览器上下文失败：%s", exc)
