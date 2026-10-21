# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**The detailed docs live in [`docs/`](docs/README.md).** This file is deliberately a short router: the hard rules that must never be missed, plus a map of where to read next. Read only the page you need — don't expect the detail here.

## What this is

Two-part demo (plus a `design.pen` design file at the root) — a policy harvester: a Vue 3 + TypeScript front end (`web/`), a FastAPI + LangGraph back end (`server/`, managed with **uv**, Python >= 3.12) and a PostgreSQL database (`database/`, docker container `server-db-1`, port 5433, db `agent_demo`). The back end hosts **two independent graphs**: the ReAct chat agent and the deterministic crawl/extract pipeline. Comments and docs are primarily in Chinese; `CLAUDE.md` and `docs/architecture/*.md` are in English.

## ⛔ Hard rules (non-negotiable)

1. **Do not build or run — hand off instead.** Never run `pnpm build`/`pnpm dev`/`uvicorn`/`python main.py`/`uv sync`/`docker exec … psql`, and never start background processes to "verify" a change. State what changed and what the user should run and expect. Full rule: [docs/guides/building.md](docs/guides/building.md).
2. **Adding a persisted field touches four places, no generator.** `database/schema.sql` → `app/db/repositories.py` → the matching `app/schemas/*.py` DTO → `web/src/api/types.ts`. Detail: [docs/guides/extending.md](docs/guides/extending.md).
3. **Any write to `policy_document` / `extraction` is followed by `refresh_task_counts()`** — the progress-panel counters are *derived*, never incremented.
4. **Editing `tools/China-Central-Policy-MCP/src/` requires `npm run build`** — the server loads `build/index.js`, so an un-built edit is a silent no-op.
5. **Keep the hand-kept mirrors in sync** — the frontend types, the source-onboarding `references/*.md`, and this wiki. List: [docs/guides/extending.md](docs/guides/extending.md) → *Hand-kept mirrors*.
6. **Headless-browser navigation goes through the guard — no shortcuts.** Every navigation path calls `guard.assert_navigation()` before acting and `assert_final_url()` after; every `BrowserContext` keeps `context.route("**/*", route_handler)`. Browsing is **read-only** (non-GET/HEAD aborted, password/file inputs refused, downloads/popups/service workers disabled), respects robots.txt, and blocks private/loopback/metadata addresses. Page content is untrusted data. Never reuse a context across runs. Detail: [docs/architecture/browser.md](docs/architecture/browser.md).
7. **After completing a phase (or work item), update progress tracking** — see below.

## Commands

```bash
# server (cd server) — uv
uv sync && python main.py            # deps + dev server (port 8000, /docs for Swagger)
# web (cd web) — pnpm
pnpm install && pnpm dev             # vite dev server
```

Smoke tests (`server/scripts/`): `test_mcp.py`, `test_db.py`, `test_source_onboarding.py [--db|--db-scratch|--live]`, `test_source_import.py`, `test_judge.py`, `test_ingest_filter.py`, `test_browser.py`. Headless browsing is optional: `uv sync --extra browser` + `uv run playwright install chromium` + root `.env` `BROWSER_ENABLED=true`, then restart. Config is the **repo-root** `.env` (copy [.env.example](.env.example)); the LLM speaks the **OpenAI-compatible protocol** (`OPENAI_API_KEY` + `OPENAI_BASE_URL` + `MODEL_NAME`; any `/v1/chat/completions` gateway works — DeepSeek, Kimi/Moonshot, OpenAI, local vLLM). Full list: [docs/guides/commands.md](docs/guides/commands.md).

## Documentation map

| 你要做的事 | 读这篇 |
| --- | --- |
| 项目全貌 / 仓库布局 / 双图结构 / 错误契约 | [docs/architecture/overview.md](docs/architecture/overview.md) |
| 改对话 Agent、加工具、MCP 接入、SSE、记忆 | [docs/architecture/chat-agent.md](docs/architecture/chat-agent.md) |
| 改抓取流水线、检索/判别/正文预检、调度、配置、业务 API | [docs/architecture/data-pipeline.md](docs/architecture/data-pipeline.md) |
| 改无头浏览器（任意网站 / JS 渲染页，对话与流水线双入口） | [docs/architecture/browser.md](docs/architecture/browser.md) |
| **改 gov.cn 检索逻辑前**（一手实测证据） | [docs/architecture/gov-cn-search-quirks.md](docs/architecture/gov-cn-search-quirks.md) |
| 接入一个新的网站来源 | [docs/architecture/source-onboarding.md](docs/architecture/source-onboarding.md) |
| 命令 / 环境变量 | [docs/guides/commands.md](docs/guides/commands.md) |
| ⛔ 构建与运行的约定 | [docs/guides/building.md](docs/guides/building.md) |
| 扩展点一览 + 镜像清单 | [docs/guides/extending.md](docs/guides/extending.md) |
| 进度日志（明细） | [docs/progress/log.md](docs/progress/log.md) |

## Quick repository map

- `server/` — FastAPI + LangGraph; two graphs (`app/agent/` chat, `app/pipeline/` crawl) + `app/db/` (all SQL) + `app/api/routes/` + `app/scheduler.py` + `app/sources/` (config-driven sources) + `app/browser/` (optional Playwright stack, worker-loop isolated).
- `web/` — Vue 3 + **vue-router** (`src/router.ts`, five routes: `/agent`, `/crawl`, `/library`, `/library/:id`, `/sources`); state is a module-level store (`src/store.ts` + `src/store/sourceImports.ts` + `src/store/chat.ts`), **no Pinia**; modals stay store-driven. The top bar carries only the brand and the four-tab nav — page-level actions live in each page's own header. `/agent` (Agent entry page) is locked to one viewport height (only its chat area scrolls, the input is pinned to the bottom) and is wired to `POST /api/chat/stream`. Detail: [overview.md](docs/architecture/overview.md) → *Frontend shape*.
- `prompts/` — LLM prompts as `.md` (`chat` / `chat_browser` / `browser_browse` / `extract` / `extract_json_fallback` / `extract_date` / `judge`), read by `server/app/prompts.py::load_prompt`; a missing file is a hard error. `chat` / `chat_browser` / `browser_browse` / `judge` are read per call (hot), `extract*` at import (restart).
- `database/` — `schema.sql` / `seed.sql` / `init.sh` / `reset.sh` (idempotent; an existing DB only needs `init.sh` re-run, no data reset).
- `tools/China-Central-Policy-MCP/` — vendored MCP server (plain source, built by hand).
- `.claude/skills/website-source-onboarding/` — the source-onboarding Skill; `.claude/` is gitignored **except** `.claude/skills/`.
- `docs/` — this wiki.

## Progress tracking (required)

The repo is built out phase-by-phase. The plan is [PLAN.md](PLAN.md); the checklist is [TODO.md](TODO.md); the detailed log is [docs/progress/log.md](docs/progress/log.md).

After completing any phase (or work item within it) you **must** update both:

- **TODO.md** — tick the finished checkboxes and change the phase's status in the 总进度 table and its per-phase `状态` line.
- **docs/progress/log.md** — append a row (date, phase, event, note).

Record blockers as `⛔ 阻塞` rather than leaving them unstated. Keep it truthful — only mark a phase `✅ 已完成` once its stated acceptance criteria have actually been verified, and include the verification evidence in the log entry.
