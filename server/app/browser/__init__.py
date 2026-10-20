"""无头浏览器抓取（Phase 24）：Playwright + 编号 DOM 快照，供对话与流水线共用。

这个包**不**在导入时加载 Playwright：所有 Playwright 调用都发生在 `runtime` 的专用
worker 线程里，且 `playwright` 只作为可选依赖（`uv sync --extra browser`）。未安装时
服务照常启动，只是 `/api/config/features` 报 `browser_available=false`、来源下拉里没有
「任意网站（无头浏览器）」。

分层的理由与边界见 `docs/architecture/browser.md`；安全不变量（SSRF、只读、robots）
在 `guard.py` 顶部注释里逐条列出，改任何导航/子请求路径前先读它。
"""
