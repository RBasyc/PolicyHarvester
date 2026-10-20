"""浏览器抓取的安全门禁（SSRF / 只读 / robots / 节流）。

## 不变量（改任何导航或子请求路径之前先读）

1. **每一次导航**（`open` / `click` / `back` / 弹窗跳转 / 批量抽取）都必须先过
   `assert_navigation()`，落地后再用 `assert_final_url()` 复验最终地址。
2. 每个 BrowserContext 都必须挂 `context.route("**/*", route_handler(guard))`：
   子资源同样可能是内网地址（`<img src="http://169.254.169.254/...">`）或 POST 外发。
3. 只读：非 GET/HEAD 一律 abort；页面里注册 service worker / WebSocket 被禁用；
   `accept_downloads=False`；密码/文件输入在动作层拒绝。
4. robots 默认遵守（`SOURCE_RESPECT_ROBOTS`）——浏览器不是绕过 robots 的手段。
5. 域名白名单可选：`BROWSER_ALLOWED_DOMAINS` 为空 = 任意公网站点；设置后只允许列出的域
   及其子域（与 `app/sources/net.domain_allowed` 同一口径）。

## 已知残余风险（文档里如实写，别假装没有）

- Chromium 自己解析 DNS，`check_url` 的解析结果与它实际连接的地址之间仍有 TOCTOU 窗口；
  这里能做的是「每次导航前后各验一次 + 每个新主机在子请求层再验一次」。
- 任意公网浏览天然存在提示词注入面：页面内容按不可信数据包裹，工具循环只读、有预算。
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

from app.config import get_settings

logger = logging.getLogger(__name__)


def _net():
    """延迟导入 `app.sources.net`。

    不能写在模块顶层：`app/sources/__init__.py` 会加载整个来源注册表
    （registry → gov_cn → agent.mcp → agent.tools → browser.tools → guard），
    而 `app.agent.tools` 在启动时装 MCP 也要导入本模块——顶层导入会形成
    `guard → sources → … → guard` 的循环，报 `cannot import name 'BrowserBlocked'`。
    函数内导入拿到的是同一个模块对象，无额外成本（首次之后只是 sys.modules 查找）。
    """
    from app.sources import net

    return net


class BrowserBlocked(RuntimeError):
    """导航或请求被安全策略拒绝（协议 / 私网 / 白名单 / robots / 只读）。"""


# 这些资源类型不参与页面语义，直接拦掉：省带宽，也缩小子资源攻击面
_BLOCKED_RESOURCE_TYPES = {"image", "font", "media", "websocket", "manifest"}
_ALLOWED_SCHEMES = ("http://", "https://")


def _settings():
    return get_settings()


def _allowed_domains() -> list[str] | None:
    return _settings().browser_allowed_domains or None


def _allow_private() -> bool:
    # 与来源抓取共用同一个开关（默认 false）；打开只用于受信内网调试
    return _settings().source_allow_private_hosts


def ensure_navigable_sync(url: str) -> str:
    """同步校验入口地址；不合法抛 `BrowserBlocked`，合法返回（可能补了 https 的）地址。"""
    try:
        return _net().check_url(
            url,
            allowed_domains=_allowed_domains(),
            allow_private=_allow_private(),
        )
    except _net().UnsafeURLError as exc:
        raise BrowserBlocked(str(exc)) from exc


async def assert_navigation(url: str) -> str:
    """导航前校验：协议/公网/白名单 → 异步 DNS 复验 → robots → 同主机节流。

    与 `net.fetch_text` 的语义保持一致：robots 取不到按允许处理，取到且明确禁止则拒绝。
    """
    resolved = ensure_navigable_sync(url)
    parsed = urlsplit(resolved)
    host = (parsed.hostname or "").lower()
    if not host:
        raise BrowserBlocked("地址缺少主机名")

    # 异步路径再解析一次（入口校验用的是同步 DNS；解析结果可能已变）。放进线程，
    # 不阻塞 worker 循环——worker 循环还要驱动 Playwright 的协议管道。
    if not _allow_private():
        try:
            await asyncio.to_thread(
                _net().check_url,
                resolved,
                allowed_domains=_allowed_domains(),
                allow_private=False,
            )
        except _net().UnsafeURLError as exc:
            raise BrowserBlocked(str(exc)) from exc

    if _settings().source_respect_robots:
        try:
            status = await _net().robots_status(resolved)
        except _net().UnsafeURLError as exc:
            raise BrowserBlocked(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - robots 网络故障不阻断（与 net 一致）
            logger.debug("robots.txt 查询失败（%s）：%s", resolved, exc)
        else:
            if not bool(status.get("allowed", True)):
                raise BrowserBlocked(
                    str(status.get("reason") or "robots.txt 不允许抓取该地址")
                )

    await _net().throttle(host)
    return resolved


async def assert_final_url(url: str) -> str:
    """导航落地后的最终地址复验（重定向可能把页面带到白名单/公网之外）。"""
    if not (url or "").startswith(_ALLOWED_SCHEMES):
        raise BrowserBlocked(f"页面停留在非网页地址：{url}")
    return await assert_navigation(url)


class RequestGuard:
    """一个 BrowserContext 的请求门禁；记录最近一次被拦截的原因供工具结果展示。"""

    def __init__(self) -> None:
        self.blocked_reason = ""
        self.blocked_count = 0
        self._checked_hosts: set[str] = set()

    def note_blocked(self, reason: str) -> None:
        self.blocked_reason = reason
        self.blocked_count += 1

    async def check(self, request) -> None:
        """校验一个子请求；不合法抛 `BrowserBlocked`。

        同一主机在首次见到时做完整的 `check_url`（含 DNS），之后按主机复用结论——
        否则每个 CSS/JS 请求都做一次 DNS，会把 worker 循环拖慢到不可用。
        """
        url = str(request.url)
        scheme_ok = url.startswith(_ALLOWED_SCHEMES)
        if url.startswith(("data:", "blob:")):
            if request.resource_type == "document":
                raise BrowserBlocked("拒绝把 data:/blob: 作为页面地址")
            return
        if url.startswith("about:"):
            return
        if not scheme_ok:
            raise BrowserBlocked(f"拒绝非 http/https 请求：{url[:120]}")
        if (request.method or "GET").upper() not in ("GET", "HEAD"):
            raise BrowserBlocked(
                f"只读浏览：已拦截 {request.method} 请求（{url[:120]}）；"
                "需要提交表单的站点请改用「网站来源接入」或站内检索工具"
            )
        if request.resource_type in _BLOCKED_RESOURCE_TYPES:
            raise BrowserBlocked(f"已拦截资源类型 {request.resource_type}")

        host = (urlsplit(url).hostname or "").lower()
        if host in self._checked_hosts:
            return
        try:
            await asyncio.to_thread(
                _net().check_url,
                url,
                allowed_domains=_allowed_domains(),
                allow_private=_allow_private(),
            )
        except _net().UnsafeURLError as exc:
            raise BrowserBlocked(str(exc)) from exc
        self._checked_hosts.add(host)


async def route_handler(request_guard: RequestGuard, route, request) -> None:
    """`context.route("**/*")` 的处理函数：逐个请求过门禁，不合规一律 abort。"""
    if request.resource_type in _BLOCKED_RESOURCE_TYPES:
        # 图片/字体/媒体是「不加载」而不是「不安全」，不记 blocked_reason 免得噪声
        await route.abort("blockedbyclient")
        return
    try:
        await request_guard.check(request)
    except BrowserBlocked as exc:
        request_guard.note_blocked(str(exc))
        logger.info("浏览器请求被拦截：%s", exc)
        await route.abort("blockedbyclient")
        return
    except Exception as exc:  # noqa: BLE001 - 门禁自身异常也按拒绝处理（fail-closed）
        request_guard.note_blocked(f"请求校验异常：{exc}")
        logger.warning("浏览器请求校验异常（按拒绝处理）：%s", exc)
        await route.abort("blockedbyclient")
        return
    try:
        await route.continue_()
    except Exception as exc:  # noqa: BLE001 - 请求可能已被浏览器取消
        logger.debug("继续请求失败（可能已取消）：%s", exc)
