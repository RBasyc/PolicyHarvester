# AGENTS.md

PolicyHarvester: Vue 3 SPA (`web/`) + FastAPI/LangGraph API (`server/`, uv, Python ≥3.12) + PostgreSQL (`database/`) + a vendored gov.cn MCP server (`tools/China-Central-Policy-MCP/`). Code comments, UI copy, `TODO.md` and the progress log are Chinese; `docs/architecture` and `docs/guides` are English — match the surrounding file. `CLAUDE.md` is the router, `docs/` is the wiki; read only the page you need instead of guessing.

## Hard rules (from `CLAUDE.md` / `docs/guides/building.md`)

- **Do not build or run the stack to "verify".** No `pnpm build|dev`, `uv sync`, `python main.py`, `uvicorn`, `docker exec … psql`, no background servers. The user runs it. Hand off: files changed + exact command + what to look for.
- Adding a persisted field touches 4 places, no generator: `database/schema.sql` → `server/app/db/repositories.py` → matching `server/app/schemas/*.py` → `web/src/api/types.ts` (hand-kept mirror).
- `schema.sql` is re-runnable and patches old DBs with `ALTER TABLE … ADD COLUMN IF NOT EXISTS`; add such a patch whenever you add a column, and tell the user to re-run `bash database/init.sh`.
- Any write that changes `policy_document` / `extraction` rows must be followed by `repo.refresh_task_counts(task_id)` — progress counters are derived, never incremented.
- Editing `tools/China-Central-Policy-MCP/src/` is a silent no-op until `npm run build` (`build/index.js` is what the API loads); MCP tools are loaded at server startup, so restart too.
- Browser code: every navigation path calls `guard.assert_navigation()` before and `assert_final_url()` after; keep `context.route("**/*", route_handler)`; browsing is read-only; never reuse a context across runs. See `docs/architecture/browser.md`.
- Finish work by ticking `TODO.md` (checkbox + phase status) and appending one row to `docs/progress/log.md` (`日期 | Phase | 事件 | 说明`). That log is append-only: correct mistakes in a new row instead of rewriting history. Keep the hand-kept mirrors in `docs/guides/extending.md` in sync (DTO ↔ `types.ts`, source-onboarding `references/*.md`, filter capabilities, wiki).

## Architecture facts that are not obvious from filenames

- The crawl pipeline is **two-phase**: `build_pipeline_graph()` stops at `await_confirm` (`awaiting_confirmation`); extraction happens later in `run_extraction_phase()` only after user confirmation. The scheduler deliberately bypasses that gate.
- Two independent graphs: `server/app/agent/` (ReAct chat + MCP tools) and `server/app/pipeline/` (search → judge/preflight → confirm → extract). All SQL lives in `server/app/db/repositories.py`; HTTP routes in `server/app/api/routes/`.
- New websites are **data, not crawlers**: write a `SourceConfig` (skill: `.claude/skills/website-source-onboarding/`). Only add an `engine`/parser when the config subset genuinely cannot express the site. gov.cn quirks: `docs/architecture/gov-cn-search-quirks.md`.
- Prompts are root-level `prompts/*.md`, loaded by `server/app/prompts.py`. `chat`, `chat_browser`, `browser_browse`, `judge` are read per call (hot); `extract*` are module constants (server restart).
- `design.pen` is an encrypted pen.dev file — open it with the pencil MCP tools, never with Read/Grep.
- Sub-READMEs drift: `web/README.md` is still the Vite template, and `server/README.md` / `database/README.md` predate sources, browser, scheduler and derived counters. Trust `docs/architecture/*` and the code over those files.
- `.claude/` is gitignored **except** `.claude/skills/`; don't delete or move those skills. Ignore `.claude/worktrees/` when searching — it holds stale full copies of the repo (duplicate filenames will pollute grep results).

## Commands (hand these off, don't run them)

```bash
./start.sh               # repo root: self-check + deps + DB + both servers (Windows: start.bat)
./start.sh check|stop    # env check only / stop services
cd server && uv sync && python main.py        # :8000, Swagger at /docs
cd web && pnpm install && pnpm dev            # :5173, /api proxied to :8000
cd web && pnpm build                          # vue-tsc -b && vite build (type-check included)
bash database/init.sh                         # idempotent; reset.sh is destructive; SEED=1 for demo rows
```

## Verification reality

There is no CI, lint config, or pytest test suite (pytest is installed but unused). Verification = standalone smoke scripts run from `server/`:

```bash
uv run python scripts/test_mcp.py
uv run python scripts/test_db.py
uv run python scripts/test_sources.py
uv run python scripts/test_source_onboarding.py [--db|--db-scratch|--live]
uv run python scripts/test_source_import.py
uv run python scripts/test_judge.py
uv run python scripts/test_ingest_filter.py
uv run python scripts/test_browser.py
uv run python scripts/test_library_search.py
```

Many need network access, the `server-db-1` container, or a running MCP; `--db-scratch` creates and drops a throwaway DB. Report which script to run and the expected pass signal instead of executing it.

## Configuration quirks

- `.env` lives at the **repo root**, not in `server/`; `app/config.py` resolves it from `__file__`, so cwd does not matter.
- LLM speaks the OpenAI-compatible protocol: `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `MODEL_NAME`. Pipeline models live in the hot-read `PIPELINE_MODELS` JSON registry (no restart).
- DB default `postgresql://kos:kos@localhost:5433/agent_demo`, docker container `server-db-1`.
- Headless browser is an optional extra: `uv sync --extra browser` + `uv run playwright install chromium` + `BROWSER_ENABLED=true`; without it the browser source and the chat 「浏览网页」 toggle hide themselves.

## Frontend quirks

- State is module-level stores in `src/store.ts`, `src/store/chat.ts`, `src/store/sourceImports.ts` — **no Pinia**, no component-local copies of shared state.
- All HTTP/SSE goes through `src/api/client.ts`. Task/import progress uses `EventSource`; `/api/chat/stream` is POST + fetch `ReadableStream` (EventSource cannot POST).
- Routes live in `src/router.ts` (dev = history mode, prod = hash mode). `/agent` is locked to one viewport: only the chat column scrolls, the composer stays pinned.
