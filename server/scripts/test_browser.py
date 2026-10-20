"""无头浏览器抓取（Phase 24）的冒烟/回归脚本。

直接运行：uv run python scripts/test_browser.py

分两层，都不写库、不起常驻服务：

1. **离线单测**（不需要 Playwright / Chromium）：安全门禁的协议/私网/白名单判定、
   预算计数、快照格式化、正文结构解析、`BrowserAnySource` 的规范化与日期窗口过滤。
   安全判定用 `net.set_resolver` 注入固定公网 IP，因此**不联网也能跑真实的 SSRF 逻辑**。
2. **本地集成**（需要 `uv sync --extra browser` + `uv run playwright install chromium`）：
   起一个本机 HTTP 服务，页面用 JS 动态生成列表；跑 `browser_open → 快照 → collect_links →
   extract_articles`，并验证 `BrowserAnySource.fetch_fulltext`。本机回环地址默认会被
   SSRF 门禁拒绝，所以脚本在导入 app 之前显式打开 `SOURCE_ALLOW_PRIVATE_HOSTS`——
   「私网必须被拒」由离线用例单独验证。

Chromium 缺失时集成层打印安装提示并**跳过**（跳过 ≠ 通过）。本脚本只读浏览本地假站点。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Windows 控制台默认 GBK，列表页里的中文/✓ 会 UnicodeEncodeError——统一切到 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 本地集成用例必须允许回环地址；必须在 `app.config` 首次读取设置之前写入环境变量。
# （真实 shell 环境 > .env：即便 .env 里是 false，也不会覆盖这里的值。）
os.environ["BROWSER_ENABLED"] = "true"
os.environ["SOURCE_ALLOW_PRIVATE_HOSTS"] = "true"
os.environ["BROWSER_HEADLESS"] = "true"
# fetch_fulltext 会再开一个 context；若用户 .env 把上限设成 1，会话占满后会自锁等待
os.environ["BROWSER_MAX_SESSIONS"] = "2"

import app.browser.guard as guard  # noqa: E402
from app.browser.budget import Budget  # noqa: E402
from app.browser.snapshot import PageSnapshot, SnapshotItem  # noqa: E402
from app.browser import content as page_content  # noqa: E402
from app.sources import net  # noqa: E402
from app.sources.browser_any import BrowserAnySource  # noqa: E402

PASSED = 0
FAILED = 0
SKIPPED = 0

PUBLIC_IP = "93.184.216.34"  # example.com 的历史地址；只用于离线判定，不真的访问


def check(condition: bool, label: str, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ {label}" + (f"（{detail}）" if detail else ""))


def skip(label: str, reason: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"  - 跳过 {label}：{reason}")


@contextmanager
def guard_settings(
    *,
    allowed_domains: list[str] | None = None,
    allow_private: bool = False,
    respect_robots: bool = False,
):
    """临时替换 guard 读到的设置（离线用例不需要真实 Settings/网络）。"""
    original = guard._settings
    guard._settings = lambda: SimpleNamespace(
        browser_allowed_domains=list(allowed_domains or []),
        source_allow_private_hosts=allow_private,
        source_respect_robots=respect_robots,
    )
    try:
        yield
    finally:
        guard._settings = original


def _blocks(fn) -> tuple[bool, str]:
    """执行 fn()，返回 (是否被 BrowserBlocked 拒绝, 错误文案)。

    非预期异常不在这里吞掉——冒烟脚本应该带着 traceback 失败，而不是把
    「代码坏了」误记成「安全校验放行」。
    """
    try:
        fn()
    except guard.BrowserBlocked as exc:
        return True, str(exc)
    return False, ""


class _FakeRequest:
    def __init__(self, url: str, method: str = "GET", resource_type: str = "document"):
        self.url = url
        self.method = method
        self.resource_type = resource_type


# ---------------------------------------------------------------- 离线用例 ---


def test_guard() -> None:
    print("\n[安全门禁]")
    net.set_resolver(lambda host: [ipaddress.ip_address(PUBLIC_IP)])
    try:
        with guard_settings():
            for bad in (
                "file:///etc/passwd",
                "javascript:alert(1)",
                "ftp://example.com/file.zip",
                "http://127.0.0.1:8000/api",
                "http://localhost:8000/",
                "http://169.254.169.254/latest/meta-data/",
                "http://[::1]/",
                "http://10.0.0.1/",
                "http://192.168.1.10/",
                "http://metadata.google.internal/",
            ):
                blocked, reason = _blocks(lambda u=bad: guard.ensure_navigable_sync(u))
                check(blocked, f"拒绝 {bad}", reason)
                if not blocked:
                    print(f"      → {reason}")

            ok, reason = _blocks(lambda: guard.ensure_navigable_sync("https://www.example.com/news"))
            check(not ok, "允许公网 https 地址", reason)
            # 无协议输入由 net.check_url 补 https（用户常直接贴域名）
            ok, reason = _blocks(lambda: guard.ensure_navigable_sync("www.example.com/news"))
            check(not ok, "无协议地址自动补 https", reason)

        with guard_settings(allowed_domains=["example.com"]):
            ok, reason = _blocks(
                lambda: guard.ensure_navigable_sync("https://news.example.com/a")
            )
            check(not ok, "白名单命中子域", reason)
            blocked, reason = _blocks(
                lambda: guard.ensure_navigable_sync("https://example.org/a")
            )
            check(blocked, "白名单拒绝非列出的域", reason)

        with guard_settings():
            request_guard = guard.RequestGuard()

            async def _check(url: str, method: str = "GET", resource_type: str = "document"):
                await request_guard.check(_FakeRequest(url, method, resource_type))

            asyncio.run(_check("https://www.example.com/style.css", resource_type="stylesheet"))
            check(True, "子请求：公网 GET 放行")

            blocked, reason = _blocks(
                lambda: asyncio.run(_check("https://www.example.com/api", method="POST"))
            )
            check(blocked, "子请求：POST 被只读策略拦截", reason)

            blocked, reason = _blocks(
                lambda: asyncio.run(_check("http://10.0.0.1/x", resource_type="script"))
            )
            check(blocked, "子请求：内网地址被拦截", reason)

            blocked, reason = _blocks(
                lambda: asyncio.run(_check("ftp://example.com/x"))
            )
            check(blocked, "子请求：非 http(s) 协议被拦截", reason)

            # data:/blob: 只允许非 document 资源
            asyncio.run(_check("data:text/css,body{}", resource_type="stylesheet"))
            check(True, "子请求：data: 样式资源放行")
            blocked, reason = _blocks(
                lambda: asyncio.run(_check("data:text/html,<h1>x</h1>", resource_type="document"))
            )
            check(blocked, "子请求：data: 作为页面地址被拦截", reason)
    finally:
        net.set_resolver(None)


def test_budget() -> None:
    print("\n[预算]")
    budget = Budget(
        max_pages=1,
        max_actions=2,
        max_extracts=0,
        timeout_seconds=60,
        started_at=time.monotonic(),
    )
    check(budget.can_page(), "初始可以打开页面")
    budget.note_page()
    check(not budget.can_page(), "页面预算用尽后拒绝")
    check(budget.can_action(), "动作预算仍可用")
    budget.note_action()
    budget.note_action()
    check(not budget.can_action(), "动作预算用尽后拒绝")
    check(not budget.can_extract(), "抽取预算为 0 时拒绝")
    check("动作 2/2" in budget.line(), "预算行包含动作计数", budget.line())
    summary = budget.summary()
    check(summary["pages"] == 1 and summary["max_actions"] == 2, "summary 字段正确")
    check(not budget.expired(), "未超时")


def test_snapshot() -> None:
    print("\n[编号快照]")
    snapshot = PageSnapshot(
        url="https://example.com/list",
        title="政策列表",
        text="这里是页面正文",
        elements=[
            SnapshotItem(index=0, name="关于测试的通知", role="a", tag="a", href="https://example.com/1"),
            SnapshotItem(index=1, name="搜索", role="button", tag="button", disabled=True),
        ],
        scroll={"y": 0, "height": 1200, "viewport": 900},
    )
    rendered = snapshot.format()
    check("[0] a \"关于测试的通知\"" in rendered, "格式化包含编号与链接名", rendered[:120])
    check("https://example.com/1" in rendered, "格式化包含绝对 href")
    check("（禁用）" in rendered, "格式化标注禁用元素")
    check("页面文本" in rendered and "这里是页面正文" in rendered, "格式化包含页面文本")
    first = snapshot.fingerprint()
    check(len(first) == 40 and first == snapshot.fingerprint(), "指纹稳定")
    snapshot.url = "https://example.com/other"
    check(snapshot.fingerprint() != first, "网址变化后指纹变化")


def test_content() -> None:
    print("\n[正文抽取]")
    html = """
    <html><head><title>关于测试政策的通知</title></head><body>
      <div id="nav">首页 政策 服务 互动</div>
      <div class="article">
        <h1>关于测试政策的通知</h1>
        <p class="meta">测试机关　2026-10-01</p>
        <p>这是第一段正文，用于验证结构解析能够选到正确的正文容器，而不是导航区域。</p>
        <p>这是第二段正文，继续补充足够多的文字，确保超过结构化正文的最短长度门槛。</p>
        <p>这是第三段正文，内容本身没有意义，只用于让本用例稳定地命中结构解析路径。</p>
        <p>这是第四段正文。无头浏览器渲染后的页面往往包含大量模板节点，结构解析必须只挑出真正的文章容器，才能避免把导航、页脚和推荐位混进正文。</p>
        <p>这是第五段正文。补充文字的目的只是让该容器的总长度超过兜底阈值，从而稳定命中结构解析路径，并为标题回退提供 h1。</p>
      </div>
      <div id="footer">版权所有</div>
    </body></html>
    """
    data = page_content.extract(
        html, url="https://example.com/policy/1", inner_text="首页 政策 关于测试政策的通知"
    )
    check("第一段正文" in (data.get("body") or ""), "结构解析取到正文")
    check(data.get("body_source") == "html", "正文来源为 html", str(data.get("body_source")))
    check(bool(data.get("title")), "解析出标题", str(data.get("title")))

    fallback = page_content.extract(
        "", url="https://example.com/policy/2", inner_text="只有页面可见文本。" * 20
    )
    check(
        fallback.get("body_source") == "inner_text" and "只有页面可见文本" in fallback["body"],
        "HTML 不可用时退回 inner_text",
    )


def test_browser_source() -> None:
    print("\n[任意网站来源]")
    from datetime import date

    source = BrowserAnySource()
    item = source.normalize_item(
        {
            "title": "关于测试的通知",
            "url": "https://example.com/1",
            "date": "2026-10-01",
            "agency": "测试机关",
            "doc_no": "测发〔2026〕1号",
            "metadata": {"k": "v"},
        }
    )
    check(item is not None and item.title == "关于测试的通知", "normalize_item 保留标题")
    check(item is not None and item.agency == "测试机关" and item.doc_no == "测发〔2026〕1号", "保留机构/文号")
    check(item is not None and item.date == date(2026, 10, 1), "解析日期")
    check(source.normalize_item({"title": "", "url": "https://example.com/1"}) is None, "缺标题时丢弃")

    def valid(**kwargs: Any) -> bool:
        payload = {"title": "标题", "url": "https://example.com/1", "date": "2026-10-01"}
        payload.update(kwargs)
        return source.validate_item(
            payload,
            keyword=None,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 31),
            facet_active=False,
        )

    check(valid(), "日期窗口内通过")
    check(not valid(date="2026-09-30"), "早于窗口被过滤")
    check(not valid(date="2026-11-01"), "晚于窗口被过滤")
    check(not valid(title=""), "缺标题被过滤")
    check(source.filter_capabilities()["keyword"] == "client", "筛选能力标为取回后生效")


# ---------------------------------------------------------------- 本地集成 ---


_LIST_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>测试政策列表</title></head>
<body>
  <div id="list">正在加载…</div>
  <script>
    // 模拟 JS 渲染：静态 HTML 里没有链接，执行后才出现
    document.getElementById('list').innerHTML =
      '<ul>' +
      '<li><a href="/policy/1">关于测试政策的通知</a></li>' +
      '<li><a href="/policy/2">关于开展测试工作的意见</a></li>' +
      '</ul>';
  </script>
</body></html>
"""

_DETAIL_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>关于测试政策的通知</title></head>
<body>
  <div class="article">
    <h1>关于测试政策的通知</h1>
    <p class="meta">测试机关　2026-10-01</p>
    <p>这是第一段正文，用于验证无头浏览器可以打开详情页，并把渲染后的 HTML 交给结构解析。</p>
    <p>这是第二段正文，继续补充足够多的文字，确保清洗后的正文长度超过最小门槛。</p>
    <p>这是第三段正文，内容本身没有意义，只用于让本用例稳定地命中结构解析路径。</p>
    <p>这是第四段正文。真实详情页的正文长度远大于测试样例，这里补充文字是为了让结构兜底达到最小长度阈值，从而验证标题与正文的完整解析链路。</p>
    <p>这是第五段正文。只有容器文本量达标，解析器才会认定它是一篇文章，而不是一段导航或页脚。</p>
  </div>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的命名约定
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            content_type = "text/plain; charset=utf-8"
        elif self.path.startswith("/policy/"):
            body = _DETAIL_PAGE.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            body = _LIST_PAGE.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # 本地冒烟不刷访问日志


def _payload(raw: Any) -> dict[str, Any]:
    """工具直接 ainvoke 时返回字符串；兼容将来包成 ToolMessage 的情况。"""
    if isinstance(raw, str):
        return json.loads(raw)
    content = getattr(raw, "content", None)
    if isinstance(content, str):
        return json.loads(content)
    return json.loads(str(raw))


async def _integration(base: str) -> None:
    print("\n[本地集成（Playwright + Chromium）]")
    from app.browser import runtime
    from app.browser import tools as browser_tools
    from app.browser.context import BrowserRun, browser_run_var

    if not runtime.available():
        skip("本地集成", runtime.unavailable_reason())
        return

    run = BrowserRun(mode="pipeline")
    token = browser_run_var.set(run)
    attempted = False
    try:
        attempted = True
        opened = _payload(await browser_tools.browser_open.ainvoke({"url": base + "/"}))
        if not opened.get("ok"):
            error = str(opened.get("error") or "")
            if "未安装浏览器内核" in error or "Chromium 启动失败" in error:
                skip(
                    "本地集成",
                    "Chromium 未安装：在 server/ 下执行 "
                    "`uv run playwright install chromium` 后重跑",
                )
                return
            check(False, "browser_open 打开本地列表页", error)
            return

        check(True, "browser_open 打开本地列表页")
        snapshot = run.session.last_snapshot if run.session else None
        check(snapshot is not None and "测试政策列表" in snapshot.title, "等待 JS 渲染后取得页面标题")
        link = None
        if snapshot is not None:
            link = next(
                (el for el in snapshot.elements if el.tag == "a" and "测试政策" in el.name),
                None,
            )
        check(link is not None, "快照里出现 JS 动态生成的链接")
        if link is None:
            return

        collected = _payload(
            await browser_tools.browser_collect_links.ainvoke({"refs": [link.index]})
        )
        check(bool(collected.get("ok")) and collected.get("added") == 1, "collect_links 收集到候选")
        check(
            any(str(entry.get("url", "")).endswith("/policy/1") for entry in run.collected),
            "候选 URL 来自快照里的真实 href",
            str(run.collected),
        )

        extracted = _payload(
            await browser_tools.browser_extract_articles.ainvoke({"refs": [link.index]})
        )
        articles = extracted.get("articles") or []
        check(bool(articles), "extract_articles 返回正文", str(extracted.get("errors") or ""))
        if articles:
            body = str(articles[0].get("body") or "")
            check("第一段正文" in body, "批量抽取取到详情页正文")
            check(
                body.startswith("<untrusted_page_content>")
                and body.rstrip().endswith("</untrusted_page_content>"),
                "正文被包裹为不可信页面内容",
            )

        # 第二阶段的取全文路径（独立 context + 同一套门禁）
        result = await BrowserAnySource().fetch_fulltext(base + "/policy/1", timeout=20)
        check("第一段正文" in result.body, "BrowserAnySource.fetch_fulltext 取到正文")
        check(result.title is not None, "fetch_fulltext 解析出标题", str(result.title))
    finally:
        browser_run_var.reset(token)
        await run.aclose()
        if attempted:
            await runtime.shutdown()


def _serve() -> tuple[ThreadingHTTPServer, int, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="test-browser-http", daemon=True)
    thread.start()
    return server, server.server_address[1], thread


def test_integration() -> None:
    server, port, thread = _serve()
    try:
        asyncio.run(_integration(f"http://127.0.0.1:{port}"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------- 入口 ---


def main() -> int:
    print("无头浏览器冒烟测试（Phase 24）")
    test_guard()
    test_budget()
    test_snapshot()
    test_content()
    test_browser_source()
    test_integration()

    print(f"\n结果：{PASSED} 通过 · {FAILED} 失败 · {SKIPPED} 跳过")
    if FAILED:
        print("有失败用例——修复后重跑。")
        return 1
    if SKIPPED:
        print("已跳过集成层（不算通过）：按上面的提示装好 Chromium 后重跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
