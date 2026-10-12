# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This is a two-part demo (plus a `design.pen` design file at the root). Comments and docs are primarily in Chinese.

- `server/` — Python backend: FastAPI + LangGraph, managed with **uv** (Python >= 3.12). Hosts **two independent graphs**: the ReAct chat agent and the deterministic crawl/extract pipeline, plus a psycopg3 data-access layer over the `agent_demo` database.
- `web/` — Vue 3 + TypeScript + Vite frontend, managed with **pnpm**. API-driven (module-level store in `web/src/store.ts`, `fetch` + SSE client in `web/src/api/`); the old mock `web/src/data.ts` has been removed. `web/design-export/` contains the pen.dev design exports the UI was built from.
- `database/` — PostgreSQL schema/seed scripts (`schema.sql`, `seed.sql`, `init.sh`, `reset.sh`) for the docker container `server-db-1` (port 5433, db `agent_demo`).

## Commands

### Server (`cd server`)

```bash
uv sync                                        # install deps (incl. dev group: pytest, httpx)
python main.py                                 # dev server with hot reload (port 8000, /docs for Swagger)
uv run python scripts/test_mcp.py              # MCP connectivity smoke test (lists tools, calls get_latest_policies)
uv run python scripts/test_db.py               # DB smoke test (reads crawl_task / v_policy_results / extraction)
```

Config: copy the root-level [.env.example](.env.example) to `.env` (both live at the **repo root**, not in `server/`; `app/config.py` resolves the path from `__file__`, so the working directory doesn't matter). Auth is either `ANTHROPIC_API_KEY` **or** `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` (for Anthropic-compatible gateways like Kimi). The crawl pipeline additionally needs `DATABASE_URL` (default `postgresql://kos:kos@localhost:5433/agent_demo`) and the `server-db-1` container running. There is no lint/test suite configured beyond the smoke scripts above.

### Web (`cd web`)

```bash
pnpm install
pnpm dev       # vite dev server
pnpm build     # vue-tsc -b && vite build (type-check + build; there is no separate lint/test script)
```

## Build & run (do not do this yourself)

The user compiles and runs the project themselves. After writing or changing code:

- **Do NOT** proactively run builds or dev servers (`pnpm build`, `pnpm dev`, `uvicorn`, `python main.py`, `uv sync`, `docker exec ... psql`, etc.).
- **Do NOT** start background/long-running processes to "verify" a change.
- Instead, state what you changed and tell the user what to run and what to expect.
- Quick read-only checks that don't build or start a server are fine when they genuinely help (e.g. a one-off script the user asked for), but prefer describing the verification over performing it.

## Server architecture

The backend exposes **two independent LangGraph graphs** over HTTP plus a database layer:

```
A. Chat (ReAct agent)                 B. Crawl pipeline (deterministic, two stages)
START -> agent --(tool calls)--> tools   stage 1: START -> prepare -> fetch -> (await_confirm | fail) -> END
          ^                    |           stage 2: (user confirms) -> discard unselected -> extract -> summarize -> END
          |------(done)--> END |                        extract: per-policy fulltext -> LLM -> extraction/fact_item
```

### A. Chat agent (`app/agent/`)

Key flows that span multiple files:

- **Tool registration is a two-stage registry** (`app/agent/tools.py`): local `@tool` functions live in `TOOLS`; MCP tools are appended to `MCP_TOOLS` at startup. Both `graph.py` (ToolNode) and `nodes.py` (`bind_tools`) call `all_tools()`, so the chat graph **must be compiled after MCP tools load** — enforced by the FastAPI `lifespan` in `app/main.py` (open DB pool → load MCP tools → `get_graph()`; on shutdown: `shutdown_pipelines()` → `close_pool()`). The chat graph is a process-wide `@lru_cache` singleton, so adding tools at runtime requires restarting. The **pipeline** graph deliberately does *not* depend on that ordering: it resolves tools by name at run time via `find_tool()`, so it compiles on first use.
- **MCP integration** (`app/agent/mcp.py`): loads **China-Central-Policy-MCP** (State Council policy retrieval: `get_latest_policies` / `get_policy_fulltext` / `get_policy_facets`) via stdio/`node` from the build at `tools/China-Central-Policy-MCP/build/index.js` (default path computed relative to repo root in `app/config.py`, overridable via `MCP_POLICY_*` env vars). That directory is **vendored into this repo as plain source** (originally cloned from the public upstream `guangxiangdebizi/China-Central-Policy-MCP`; `get_policy_facets` and the search fixes are local additions, and upstream updates must now be merged by hand — it is no longer a git submodule). `node_modules/` is ignored; run `npm install` there before `npm run build`. **Editing `src/` requires `npm run build` (= `tsc`)** — the server loads `build/index.js`, so an un-built source change is a silent no-op, and `build/` is committed. Failure degrades gracefully to local tools only — the server still starts. `tool_text()` / `tool_json()` normalise the MCP return shape (str / tuple / content-block list) — reuse them instead of hand-parsing results.
  - **gov.cn search quirks** (hard-won, see `TODO.md` Phase 9 对照表): `searchfield` has exactly **one** effective value — `title` (anything else silently disables keyword filtering). Facet filters `bmfl`(发布机构) / `pcodeJiguan`(文种) / `childtype`(主题) / `pubtimeyear`(年份) **only work on channel `t=zhengcelibrary_gw_bm_gb`** — they're dead on `t=zhengcelibrary`. **Repeated filter params are silently ignored** (`bmfl=A&bmfl=B` = no filter), so multi-select must fan out client-side. The API **silently returns the full unfiltered browse list when a strict query matches 0** — client-side re-verification (title-shares-a-character-with-keyword + date-in-window) is mandatory for a truthful "无". Note the title check must **not** require the full keyword as a substring: gov.cn's `searchfield=title` is **tokenized** (`水域` → 水 + 域), so it legitimately returns titles like 「…流域防洪…」「…水污染防治…」 — requiring the exact substring killed every hit and produced "facet shows agencies, yet the run fails with 未检索到". `gongbao`(公报) rows **ignore all facet filters** — drop them when any filter is active (in `getLatestPolicies.ts` **before** the `limit` slice, so un-filterable 公报 rows can't crowd out real matches; the pipeline repeats the check as a safety net). The date window filters on the **displayed 发布日期** (`ptimeStr`/`pubtimeStr`) — `ptime` is an epoch that is often an *earlier* 成文日期, so `getLatestPolicies.ts` must report the **text date first, epoch only as fallback**; reporting the epoch made the pipeline's "date must fall inside the window" re-check discard rows the API had legitimately returned for the window (symptom: the facet shows 1 match, yet the run fails with 未检索到匹配的政策原文). `get_policy_facets` reads `searchVO.extendresult.facetMap`/`groupMap` for the agency/doc-type option lists.
- **Conversation memory**: `InMemorySaver` checkpointer keyed by `thread_id` (request field). Memory is lost on restart; swap for `PostgresSaver` etc. for persistence.
- **LLM auth quirk** (`app/agent/nodes.py`): `ChatAnthropic` doesn't accept `auth_token`, so Bearer-token gateways are passed via `default_headers={"Authorization": "Bearer ..."}`.
- **No forced tool_choice** (`app/pipeline/extract.py`): the configured gateway (`kimi-for-coding`) always has thinking on, and an Anthropic-compatible API rejects "thinking enabled + `tool_choice` naming a tool" with 400 `tool_choice 'specified' is incompatible with thinking enabled`. That is exactly what `llm.with_structured_output(...)` sends, so it can never succeed here — `_extract_via_tool()` binds the Pydantic schema as a tool and lets the model choose (it does call it; unforced tool calls are fine with thinking), then falls back to the lenient JSON-text parse. `thinking={"type": "disabled"}` is *not* a workaround: the gateway accepts the request but returns 0 tool calls. Apply the same rule to any new structured call on this gateway.
- **SSE streaming** (`app/api/routes/agent.py`): `POST /api/chat/stream` emits `data: {"type": "token", "content": ...}` lines, always terminated by `{"type": "done"}`; errors are sent as `{"type": "error"}` events in-stream rather than HTTP errors. `_extract_text` filters content blocks to `text` only, skipping thinking/tool blocks — reuse it when handling message content.

### B. Data layer & crawl pipeline (`app/db/`, `app/pipeline/`)

- **Pool** (`app/db/pool.py`): one process-wide psycopg3 `AsyncConnectionPool` (`dict_row`, `autocommit=True`). `open_pool()` warns rather than raising if the DB is unreachable, so the server still boots. `get_pool()` / `close_pool()` are wired into the lifespan.
- **Repositories** (`app/db/repositories.py`): *all* SQL lives here, parameterized. Two rules matter:
  - **Counters are derived, never incremented.** `refresh_task_counts()` recomputes `crawl_task.total/fetched/extracted/failed_count` from an aggregate over `policy_document`/`extraction` (`UPDATE ... FROM (SELECT ...)`). The progress panel reads `crawl_task` while the result table reads the `v_policy_results` view — recomputation is what keeps those two views from drifting apart.
  - Multi-step writes (e.g. reset extraction + delete facts) go in a single `conn.transaction()`.
- **Pipeline** (`app/pipeline/crawl_graph.py`): the deterministic pipeline, split into **two stages by a human confirmation gate**. Stage 1 (graph A) runs `prepare` → `fetch` → `await_confirm` and **ends**; MCP failure or 0 results routes to `fail` with the design's Chinese error copy. `POST /api/tasks` persists the task then calls `start_pipeline(task_id)` (fire-and-forget asyncio task). The task then sits in `awaiting_confirmation` (non-terminal — SSE stays open, `progress` is 50) until `POST /api/tasks/{id}/confirm` drops the unchecked rows and calls `start_extraction(task_id)`, which runs stage 2: `extract` (semaphore-bounded `asyncio.gather` over `_extract_one`) → `summarize`. **Why two stages**: the graph has no checkpointer (`builder.compile()` with no saver) so `PipelineState` cannot survive a pause — instead the fetched rows live in `policy_document` and `run_extraction_phase()` re-reads them and calls `extract_node`/`summarize_node` directly (they are plain `dict → dict` async functions). `pipeline_extract_concurrency` defaults to 1 so the "第 N/M 篇" stage text stays truthful.
- **Search & filters** (`_search_policies` in the same file): fans out over the `keyword × agency × doc_type` cartesian product (capped by `pipeline_max_search_calls`) because the API **silently ignores repeated filter params**. Filters persist as `crawl_task.agencies` / `doc_types` columns (the two-stage graph rebuilds `PipelineState` from the DB row, so filters must be persisted). `_search_policies` applies **client-side re-verification** — drop items whose title lacks the keyword, whose date falls outside `[date_from, date_to]`, and any `gongbao` row when a filter is active — because the API silently returns the full unfiltered browse list on a 0-match strict query. Results are deduped by `url` and capped by `pipeline_max_policies`.
- **Confirmation state**: `task_status` has a fifth value `awaiting_confirmation`; `schema.sql` carries an idempotent `ALTER TYPE task_status ADD VALUE IF NOT EXISTS ...` so an existing DB only needs `database/init.sh` re-run (no data reset). `fail_stale_tasks()` deliberately sweeps only `pending`/`running`, so a task parked at the gate survives a restart and can still be confirmed.
- **Extraction** (`app/pipeline/extract.py`): `extract_policy()` truncates the body to `pipeline_max_body_chars`, tries a structured tool call (`_extract_via_tool`, deliberately **without** a forced `tool_choice` — see the thinking quirk above), and falls back to a lenient JSON parse. Output is 事实/信息/知识 (facts / info{summary,points} / knowledge{concepts,fields,extension}).
- **Business API** (`app/api/routes/tasks.py`, `policies.py`): `POST /api/tasks`, `POST /api/tasks/{id}/confirm` (409 unless `awaiting_confirmation`; 400 if nothing is selected), `GET /api/tasks/current|{id}|{id}/logs|{id}/policies`, `GET|POST /api/policies/{id}/extraction|retry|reextract`, `GET /api/config/pipeline` (read-only view of the effective `PIPELINE_*` defaults), and `GET /api/tasks/{id}/stream` (SSE). The stream replays all logs from id 0 then tails; it emits `task` / `log` / `done` / `error` events, polls every 0.6 s, and sends `: ping` heartbeats every 15 s. `retry` (failed only, else 409) and `reextract` both re-enter `run_single_extraction`.
- **Cancellation & clearing** (same route file + `app/pipeline/crawl_graph.py`): background tasks are registered per `task_id` in `crawl_graph._running` (`_track()`), so `cancel_pipeline()` / `cancel_all_pipelines()` can target one task. `POST /api/tasks/{id}/cancel` stops the pipeline and lands the task in `failed` with stage `任务已手动终止` (reusing the existing enum — no `schema.sql` change); `DELETE /api/tasks/{id}` deletes one task and `DELETE /api/tasks` clears all of them, both cancelling first because everything cascades via FKs. A manual cancel deliberately overwrites the `服务重启导致任务中断…` stage that `run_pipeline`'s `CancelledError` branch writes, so the cancel must *await* the cancellation before writing its own state. On startup the `lifespan` runs `repo.fail_stale_tasks()` to fail `pending`/`running` rows orphaned by a killed process (otherwise the UI shows "运行中" forever).
- **Config** (`app/config.py`): `pydantic-settings` reads the **repo-root** `.env` (path resolved from `__file__`, so CWD doesn't matter), and `load_dotenv()` is also called explicitly because the `anthropic` SDK reads `ANTHROPIC_*` straight from the environment. Pipeline knobs: `DATABASE_URL`, `DB_POOL_*`, `PIPELINE_MAX_POLICIES`, `PIPELINE_PER_KEYWORD_LIMIT`, `PIPELINE_MAX_SEARCH_CALLS`, `PIPELINE_EXTRACT_CONCURRENCY`, `PIPELINE_MAX_BODY_CHARS`, `PIPELINE_MCP_TIMEOUT`. Five of these can be **overridden per task** from the page's collapsed 高级设置 section (values persist on `crawl_task.pipeline_*` columns; NULL = use the global default) — the second extraction stage re-reads them from the task row, so they must be persisted, not just passed in the create request.

### Error contract

All HTTP errors are `{"detail": "<string>"}` — a `RequestValidationError` handler flattens FastAPI's default 422 error list into one string, so clients can render `detail` directly (the frontend's `ApiError` does exactly this).

## Progress tracking (required)

This repo is being built out phase-by-phase to connect the frontend, backend, and database.

- The plan lives in [PLAN.md](PLAN.md); the task checklist lives in [TODO.md](TODO.md).
- **After completing any phase (or work item within it), you MUST update `TODO.md`**: tick the finished checkboxes, change the phase's status in the 总进度 table and the per-phase `状态` line, and append a row to the 进度日志 table at the bottom (date, phase, event, note).
- Record blockers as `⛔ 阻塞` rather than leaving them unstated.
- Keep `TODO.md` truthful — only mark a phase `✅ 已完成` once its stated acceptance criteria have actually been verified, and include the verification evidence in the log entry.

## Extending the agent

- New local tool: add a `@tool` function in `app/agent/tools.py` and append it to `TOOLS` — no graph changes needed.
- New graph behavior (interrupts, conditional routing): edit `app/agent/graph.py`.
- New state fields: `app/agent/state.py` (use reducers like `add_messages` for mergeable fields).

## Extending the crawl pipeline

- New pipeline stage: add a node in `app/pipeline/crawl_graph.py` and wire the edges (and `_route_after_fetch` if it branches).
- New persisted field: add it to `database/schema.sql`, mirror it in `app/db/repositories.py`, and expose it in the matching `app/schemas/*.py` DTO **and** `web/src/api/types.ts` — the frontend types are a hand-kept mirror, nothing generates them.
- Any write that changes `policy_document`/`extraction` rows must be followed by `refresh_task_counts()` so the derived counters stay honest.
