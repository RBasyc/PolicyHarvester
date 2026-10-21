# Commands & configuration

> Part of the [docs wiki](../README.md). For the *"don't build or run it yourself"* rule, see
> [building.md](building.md).

## Server (`cd server`)

```bash
uv sync                                        # install deps (incl. dev group: pytest, httpx)
python main.py                                 # dev server with hot reload (port 8000, /docs for Swagger)
uv run python scripts/test_mcp.py              # MCP connectivity smoke test (lists tools, calls get_latest_policies)
uv run python scripts/test_db.py               # DB smoke test (reads crawl_task / v_policy_results / extraction)
uv run python scripts/test_source_onboarding.py [--db|--db-scratch|--live]  # 网站来源接入的验收测试（假站点 + 真实编排；--db-scratch 自动建临时库跑落库用例、用完即删）
uv run python scripts/test_source_import.py    # 页面「导入网址」入口（services/source_import.py）的离线回归
uv run python scripts/test_judge.py            # 入库判别服务（laya）契约 + 提示词矩阵
uv run python scripts/test_ingest_filter.py    # 正文预检阈值校准（真实 MCP 链接）
uv run python scripts/test_browser.py          # 无头浏览器：离线门禁/预算/快照/解析 + 本地假站点集成（Chromium 缺失则跳过）
uv run python scripts/test_dates.py            # 日期信号纯函数：JSON-LD / meta / <time> / 落款 / 误判防护（离线）
uv run python scripts/test_library_search.py    # 政策库只读检索（列来源 / 只读检索 / 取正文 / 工具层坏输入；离线）
```

Headless browser (optional, Phase 24):

```bash
uv sync --extra browser            # install playwright (not part of the default sync)
uv run playwright install chromium # ~150 MB browser binary
# root .env: BROWSER_ENABLED=true, then restart the server
```

Without the extra the server still starts; the 「任意网站（无头浏览器）」 source and the chat
「浏览网页」 toggle stay hidden (`GET /api/config/features` reports `browser_available=false`).
`./start.sh` syncs the extra automatically when `BROWSER_ENABLED=true` and prints a
non-blocking Chromium hint. See [browser.md](../architecture/browser.md).

There is no lint/test suite configured beyond the smoke scripts above.

The running server exposes every route at `/docs` (Swagger). This wiki deliberately keeps **no endpoint table** — it would go stale against the routers. Endpoint semantics live next to the code that owns them: task/policy/library routes in [data-pipeline.md](../architecture/data-pipeline.md) → *Business API*, and the `/api/sources*` family (import / batch import / import list / two SSE streams / enable / disable / trial / health / runs / runs-recent) in [source-onboarding.md](../architecture/source-onboarding.md).

## Web (`cd web`)

```bash
pnpm install
pnpm dev       # vite dev server
pnpm build     # vue-tsc -b && vite build (type-check + build; there is no separate lint/test script)
```

## MCP (`cd tools/China-Central-Policy-MCP`)

```bash
npm install    # node_modules/ is ignored
npm run build  # = tsc; the server loads build/index.js, so src/ edits need this or they are a silent no-op
```

## Configuration

Copy the root-level [.env.example](../../.env.example) to `.env` (both live at the **repo root**, not in `server/`; `app/config.py` resolves the path from `__file__`, so the working directory doesn't matter).

- **LLM** speaks the **OpenAI-compatible protocol**: `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `MODEL_NAME` in the repo-root `.env`. Any `/v1/chat/completions` gateway works (DeepSeek, Kimi/Moonshot, OpenAI, local vLLM). To add a model, append an entry to the `PIPELINE_MODELS` JSON registry (hot-read, no restart).
- The crawl pipeline additionally needs `DATABASE_URL` (default `postgresql://kos:kos@localhost:5433/agent_demo`) and the `server-db-1` container running.
- The model registry is `PIPELINE_MODELS` (JSON) — hot-read, so editing `.env` needs no restart for model changes. See [data-pipeline.md](../architecture/data-pipeline.md) → *Config*.
- **Observability (optional, off by default)**: `LANGFUSE_ENABLED` + `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` + `LANGFUSE_BASE_URL` (or `LANGFUSE_HOST` — both names are accepted) point the chat graph and the extraction pipeline at a **self-hosted** Langfuse/Litefuse instance (`app/tracing.py`, Phase 21). Missing keys, `LANGFUSE_ENABLED=false`, or an uninstalled `langfuse` package all degrade to "no tracing" with one startup log line — never an error. These go through `Settings`, so they need a restart. **Keep the URL local** — pointing it at a cloud endpoint ships conversations and policy full texts to a third party. See [chat-agent.md](../architecture/chat-agent.md) → *Observability*.
- **Prompts are files, not env vars**: they live in the repo-root [`prompts/`](../../prompts/) (`chat` / `chat_browser` / `browser_browse` / `extract` / `extract_json_fallback` / `extract_fields` / `judge`), one `.md` per prompt, loaded by `app/prompts.py`. Editing `chat.md`, `chat_browser.md`, `browser_browse.md` or `judge.md` takes effect on the next call (no restart); `extract*.md` are bound to module constants, so those need a restart. A missing file raises at the call site — intentionally loud. `JUDGE_INSTRUCTIONS` still overrides `judge.md`.
