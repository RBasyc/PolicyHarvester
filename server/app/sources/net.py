"""受控 HTTP 抓取客户端（网站来源接入的取数底座）。

来源接入 Skill 会拿到**用户提交的任意 URL**，因此抓取入口必须先过安全与礼貌校验：

- 只允许 `http` / `https`；
- 目标主机解析出的地址必须落在**公网**（拒 localhost / 私网 / 链路本地 / 保留段 /
  云元数据地址）——防 SSRF；
- 重定向**逐跳复验**（`follow_redirects=False` 手写循环）：否则目标站点一个 302
  就能把请求打到内网，这一条比入口校验更要紧；
- 来源配置的 `allowed_domains` 二次收敛（白名单，可防「跳去别的站点」）；
- `robots.txt` 尊重（`urllib.robotparser`，按 host 缓存）——用户明确要求遵守站点访问许可；
- 同域最小请求间隔 + 全局并发上限（礼貌抓取，避免打崩对方站点）；
- 响应体积上限、单次超时。

`source_allow_private_hosts=true` 可放行内网地址（受信内网站点 / 本机联调），**默认拒绝**。
本模块不做业务解析，只负责「把网页取回来」——解析在 `htmlparse` / `html_source`。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class UnsafeURLError(ValueError):
    """目标地址被安全策略拒绝（协议 / 主机 / 白名单 / robots）。"""


class FetchError(RuntimeError):
    """取数失败（网络错误、超时、HTTP 4xx/5xx）。"""


# 主机名黑名单：这些名字即使解析到公网也不该抓（本机别名 / 云元数据服务）
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
}
# 相对路径写法（`//host/path`）也要拦
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


# ---------- 目标地址校验 ----------


def _blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """私网 / 环回 / 链路本地 / 保留 / 组播 / 未指定 —— 一律视为不可达。"""
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


# DNS 解析器可由测试替换（离线跑真实校验逻辑：不联网，但 IP 判定仍是真的）
_resolver: "Callable[[str], list[ipaddress._BaseAddress]] | None" = None


def set_resolver(fn: "Callable[[str], list[ipaddress._BaseAddress]] | None") -> None:
    """替换 DNS 解析器（`None` = 恢复系统解析）。

    用途：离线测试与配置预检时，把主机名映射到固定 IP，从而**不联网也能跑完整的
    SSRF 判定**（协议 / 主机名 / 白名单 / 私网地址四条都是真的在判）。
    """
    global _resolver
    _resolver = fn


def _parse_ips(infos: Any) -> list[ipaddress._BaseAddress]:
    ips: list[ipaddress._BaseAddress] = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip not in ips:
            ips.append(ip)
    return ips


def resolve_host(host: str) -> list[ipaddress._BaseAddress]:
    """把主机名解析成 IP 列表（同步；解析不了返回空列表）。

    `check_url()` 用它做静态校验（配置注册时调用）；异步抓取路径用
    `_resolve_host_async()`（走事件循环的 DNS，不阻塞 loop）。
    """
    if _resolver is not None:
        return _resolver(host)
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return _parse_ips(infos)


async def _resolve_host_async(host: str) -> list[ipaddress._BaseAddress]:
    if _resolver is not None:
        return _resolver(host)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return _parse_ips(infos)


def domain_allowed(host: str, allowed_domains: list[str] | None) -> bool:
    """域名白名单匹配：`host == domain` 或 `host` 是其子域。白名单为空 = 不限制。"""
    if not allowed_domains:
        return True
    host = (host or "").lower().rstrip(".")
    for raw in allowed_domains:
        domain = (raw or "").lower().strip().lstrip(".").rstrip(".")
        if not domain:
            continue
        if host == domain or host.endswith("." + domain):
            return True
    return False


def check_url(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    allow_private: bool | None = None,
) -> str:
    """同步校验目标 URL；不合法直接抛 `UnsafeURLError`，合法则原样返回。

    `allow_private=None` 时取 `.env` 的 `SOURCE_ALLOW_PRIVATE_HOSTS`（默认 false）。
    """
    if allow_private is None:
        allow_private = get_settings().source_allow_private_hosts
    stripped = (url or "").strip()
    if not stripped:
        raise UnsafeURLError("地址为空")
    if not _SCHEME_RE.match(stripped):
        # 无协议的输入（用户常直接贴 `www.xxx.gov.cn`）补 https 再判
        stripped = "https://" + stripped.lstrip("/")
    parsed = urlsplit(stripped)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"只允许 http/https 地址，收到：{parsed.scheme or '（无协议）'}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError("地址缺少主机名")
    if host in _BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeURLError(f"拒绝访问本机/内部主机名：{host}")
    if not domain_allowed(host, allowed_domains):
        raise UnsafeURLError(
            f"主机 {host} 不在来源白名单内（{'、'.join(allowed_domains or [])}）"
        )
    if not allow_private:
        try:
            # 字面量 IP 直接拿到地址；域名走一次 DNS
            ips = [ipaddress.ip_address(host)]
        except ValueError:
            ips = resolve_host(host)
        if not ips:
            raise UnsafeURLError(f"主机名无法解析：{host}")
        for ip in ips:
            if _blocked_ip(ip):
                raise UnsafeURLError(
                    f"拒绝访问非公网地址：{host} → {ip}"
                    "（确需抓内网站点请设 SOURCE_ALLOW_PRIVATE_HOSTS=true）"
                )
    return stripped


# ---------- 抓取结果 ----------


@dataclass
class FetchResult:
    """一次抓取的结果（`text` 已按响应头 / meta / 常见中文编码解好码）。"""

    url: str  # 重定向后的最终地址（溯源用，落库记这个）
    status: int
    text: str
    raw: bytes = b""  # 原始字节（附件落盘与哈希用；fetch_text 解完码仍保留）
    content_type: str | None = None
    bytes_len: int = 0
    truncated: bool = False  # 超过 SOURCE_FETCH_MAX_BYTES 被截断
    redirects: int = 0  # 跳了几跳（逐跳都过了校验）


# ---------- 礼貌抓取：同域节流 + 全局并发上限 ----------

_host_locks: dict[str, asyncio.Lock] = {}
_last_request_at: dict[str, float] = {}
_fetch_semaphore: asyncio.Semaphore | None = None


def _host_lock(host: str) -> asyncio.Lock:
    lock = _host_locks.get(host)
    if lock is None:
        lock = _host_locks[host] = asyncio.Lock()
    return lock


def _semaphore() -> asyncio.Semaphore:
    global _fetch_semaphore
    if _fetch_semaphore is None:
        _fetch_semaphore = asyncio.Semaphore(max(1, get_settings().source_fetch_concurrency))
    return _fetch_semaphore


async def _throttle(host: str) -> None:
    """同一主机两次请求之间至少间隔 `SOURCE_REQUEST_INTERVAL_MS`（礼貌抓取）。"""
    interval = max(0, get_settings().source_request_interval_ms) / 1000
    if interval <= 0:
        return
    async with _host_lock(host):
        elapsed = time.monotonic() - _last_request_at.get(host, 0.0)
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        _last_request_at[host] = time.monotonic()


async def throttle(host: str) -> None:
    """同主机请求节流（公开入口）。

    浏览器（`app/browser/guard.py`）不是 httpx 通道，但礼貌抓取的窗口应与网站来源
    抓取共用同一份「上次请求时间」，否则同一次任务里 HTTP 与浏览器各算各的。
    """
    await _throttle(host)


# ---------- robots.txt ----------

_robots_cache: dict[str, RobotFileParser | None] = {}


async def _robots_for(client: httpx.AsyncClient, scheme: str, host: str) -> RobotFileParser | None:
    """取回并解析 `robots.txt`（按 host 缓存；取不到 = None = 不限制）。"""
    key = f"{scheme}://{host}"
    if key in _robots_cache:
        return _robots_cache[key]
    parser: RobotFileParser | None = None
    try:
        resp = await client.get(f"{key}/robots.txt", timeout=5.0)
        if resp.status_code == 200:
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
    except Exception as exc:  # noqa: BLE001 - robots 取不到不阻断（与主流爬虫一致）
        logger.debug("robots.txt 读取失败（%s）：%s", key, exc)
    _robots_cache[key] = parser
    return parser


def _robots_allows(parser: RobotFileParser | None, url: str) -> bool:
    if parser is None:
        return True
    try:
        return parser.can_fetch(get_settings().source_user_agent, url)
    except Exception:  # noqa: BLE001 - 规则畸形按放行处理
        return True


def clear_caches() -> None:
    """清空主机节流 / robots 缓存（测试与「重新勘察」用）。"""
    _host_locks.clear()
    _last_request_at.clear()
    _robots_cache.clear()


async def robots_status(url: str) -> dict[str, object]:
    """单独查一次 robots.txt 的允许情况（勘察报告用：把「站点怎么说」摆给用户看）。

    取不到 robots.txt / 解析失败一律按**允许**处理（与主流爬虫一致），返回值里
    `found=False` 表明这不是站点明确允许，而是没有规则可依。
    """
    parsed = urlsplit(check_url(url, allowed_domains=None, allow_private=None))
    host = parsed.hostname or ""
    async with httpx.AsyncClient(
        timeout=5.0, follow_redirects=False, trust_env=False,
        headers={"User-Agent": get_settings().source_user_agent},
    ) as client:
        parser = await _robots_for(client, parsed.scheme, host)
    if parser is None:
        return {"checked": True, "found": False, "allowed": True, "reason": "未取到 robots.txt（按允许处理）"}
    allowed = _robots_allows(parser, url)
    return {
        "checked": True,
        "found": True,
        "allowed": allowed,
        "reason": "" if allowed else f"robots.txt 禁止抓取 {url}",
    }


# ---------- 抓取 ----------


async def fetch_bytes(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
    respect_robots: bool | None = None,
) -> FetchResult:
    """抓取一个 URL 的**原始字节**（附件下载用），`text` 留空、字节在 `raw` 里。"""
    return await _fetch_raw(
        url,
        allowed_domains=allowed_domains,
        timeout=timeout,
        max_bytes=max_bytes,
        respect_robots=respect_robots,
    )


async def fetch_text(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
    respect_robots: bool | None = None,
) -> FetchResult:
    """抓取一个 URL 并解码成文本（HTML 页面用，编码嗅探见 `htmlparse.decode_html`）。"""
    result = await _fetch_raw(
        url,
        allowed_domains=allowed_domains,
        timeout=timeout,
        max_bytes=max_bytes,
        respect_robots=respect_robots,
    )
    from app.sources.htmlparse import decode_html  # 局部导入：解析层只在此处用到

    result.text = decode_html(result.raw, result.content_type)
    return result


async def _fetch_raw(
    url: str,
    *,
    allowed_domains: list[str] | None,
    timeout: float | None,
    max_bytes: int | None,
    respect_robots: bool | None,
) -> FetchResult:
    """实际取数：逐跳重定向 + 每跳安全复验 + 限频 + 体积上限。"""
    settings = get_settings()
    allow_private = settings.source_allow_private_hosts
    timeout = timeout if timeout else settings.source_fetch_timeout
    max_bytes = max_bytes if max_bytes else settings.source_fetch_max_bytes
    respect_robots = settings.source_respect_robots if respect_robots is None else respect_robots

    current = check_url(url, allowed_domains=allowed_domains, allow_private=allow_private)
    redirects = 0
    async with _semaphore():
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,  # 手写循环：每一跳都要过 check_url
            headers={"User-Agent": settings.source_user_agent},
            trust_env=False,  # 内网/白名单地址不受 HTTP_PROXY 劫持（与 judge 同一口径）
        ) as client:
            while True:
                parsed = urlsplit(current)
                host = parsed.hostname or ""
                if respect_robots:
                    parser = await _robots_for(client, parsed.scheme, host)
                    if not _robots_allows(parser, current):
                        raise UnsafeURLError(f"robots.txt 不允许抓取：{current}")
                # 域名解析出的地址在异步路径上再验一次（入口校验用的是同步 DNS，
                # 解析结果可能已变；真正的 TOCTOU 防护仍受限于 httpx 无法固定 peer 地址）
                if not allow_private:
                    for ip in await _resolve_host_async(host):
                        if _blocked_ip(ip):
                            raise UnsafeURLError(
                                f"拒绝访问非公网地址：{host} → {ip}"
                                "（确需抓内网站点请设 SOURCE_ALLOW_PRIVATE_HOSTS=true）"
                            )
                await _throttle(host)
                try:
                    async with client.stream("GET", current) as resp:
                        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                            location = resp.headers.get("location")
                            if not location:
                                raise FetchError(f"重定向缺少 Location：{current}")
                            redirects += 1
                            if redirects > settings.source_fetch_max_redirects:
                                raise FetchError(f"重定向次数超过 {settings.source_fetch_max_redirects}：{url}")
                            nxt = urljoin(current, location)
                            # 逐跳复验：协议 / 主机 / 白名单 / 公网地址
                            current = check_url(
                                nxt, allowed_domains=allowed_domains, allow_private=allow_private
                            )
                            continue
                        if resp.status_code >= 400:
                            raise FetchError(f"HTTP {resp.status_code}：{current}")
                        chunks: list[bytes] = []
                        total = 0
                        truncated = False
                        async for chunk in resp.aiter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                truncated = True
                                break
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                        return FetchResult(
                            url=str(resp.url),
                            status=resp.status_code,
                            text="",
                            raw=raw,
                            content_type=resp.headers.get("content-type"),
                            bytes_len=len(raw),
                            truncated=truncated,
                            redirects=redirects,
                        )
                except (httpx.HTTPError, OSError) as exc:
                    raise FetchError(f"抓取失败（{exc.__class__.__name__}: {exc}）：{current}") from exc
