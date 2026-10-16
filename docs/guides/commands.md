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
```

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

- **Auth** is either `ANTHROPIC_API_KEY` **or** `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` (for Anthropic-compatible gateways like Kimi).
- The crawl pipeline additionally needs `DATABASE_URL` (default `postgresql://kos:kos@localhost:5433/agent_demo`) and the `server-db-1` container running.
- The model registry is `PIPELINE_MODELS` (JSON) — hot-read, so editing `.env` needs no restart for model changes. See [data-pipeline.md](../architecture/data-pipeline.md) → *Config*.
