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

Config: copy `.env.example` to `.env`. Auth is either `ANTHROPIC_API_KEY` **or** `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` (for Anthropic-compatible gateways like Kimi). The crawl pipeline additionally needs `DATABASE_URL` (default `postgresql://kos:kos@localhost:5433/agent_demo`) and the `server-db-1` container running. There is no lint/test suite configured beyond the smoke scripts above.

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
A. Chat (ReAct agent)                 B. Crawl pipeline (deterministic)
START -> agent --(tool calls)--> tools   START -> prepare -> fetch -> (extract | fail) -> summarize -> END
          ^                    |                  |                |
          |------(done)--> END |                  |                `-- per-policy: fulltext -> LLM -> extraction/fact_item
```

### A. Chat agent (`app/agent/`)

Key flows that span multiple files:

- **Tool registration is a two-stage registry** (`app/agent/tools.py`): local `@tool` functions live in `TOOLS`; MCP tools are appended to `MCP_TOOLS` at startup. Both `graph.py` (ToolNode) and `nodes.py` (`bind_tools`) call `all_tools()`, so the chat graph **must be compiled after MCP tools load** — enforced by the FastAPI `lifespan` in `app/main.py` (open DB pool → load MCP tools → `get_graph()`; on shutdown: `shutdown_pipelines()` → `close_pool()`). The chat graph is a process-wide `@lru_cache` singleton, so adding tools at runtime requires restarting. The **pipeline** graph deliberately does *not* depend on that ordering: it resolves tools by name at run time via `find_tool()`, so it compiles on first use.
- **MCP integration** (`app/agent/mcp.py`): loads **China-Central-Policy-MCP** (State Council policy retrieval: `get_latest_policies` / `get_policy_fulltext`) via stdio/`node` from the locally cloned build at `tools/China-Central-Policy-MCP/build/index.js` (default path computed relative to repo root in `app/config.py`, overridable via `MCP_POLICY_*` env vars). Failure degrades gracefully to local tools only — the server still starts. `tool_text()` / `tool_json()` normalise the MCP return shape (str / tuple / content-block list) — reuse them instead of hand-parsing results.
- **Conversation memory**: `InMemorySaver` checkpointer keyed by `thread_id` (request field). Memory is lost on restart; swap for `PostgresSaver` etc. for persistence.
- **LLM auth quirk** (`app/agent/nodes.py`): `ChatAnthropic` doesn't accept `auth_token`, so Bearer-token gateways are passed via `default_headers={"Authorization": "Bearer ..."}`.
- **SSE streaming** (`app/api/routes/agent.py`): `POST /api/chat/stream` emits `data: {"type": "token", "content": ...}` lines, always terminated by `{"type": "done"}`; errors are sent as `{"type": "error"}` events in-stream rather than HTTP errors. `_extract_text` filters content blocks to `text` only, skipping thinking/tool blocks — reuse it when handling message content.

### B. Data layer & crawl pipeline (`app/db/`, `app/pipeline/`)

- **Pool** (`app/db/pool.py`): one process-wide psycopg3 `AsyncConnectionPool` (`dict_row`, `autocommit=True`). `open_pool()` warns rather than raising if the DB is unreachable, so the server still boots. `get_pool()` / `close_pool()` are wired into the lifespan.
- **Repositories** (`app/db/repositories.py`): *all* SQL lives here, parameterized. Two rules matter:
  - **Counters are derived, never incremented.** `refresh_task_counts()` recomputes `crawl_task.total/fetched/extracted/failed_count` from an aggregate over `policy_document`/`extraction` (`UPDATE ... FROM (SELECT ...)`). The progress panel reads `crawl_task` while the result table reads the `v_policy_results` view — recomputation is what keeps those two views from drifting apart.
  - Multi-step writes (e.g. reset extraction + delete facts) go in a single `conn.transaction()`.
- **Pipeline** (`app/pipeline/crawl_graph.py`): the deterministic graph. `POST /api/tasks` persists the task then calls `start_pipeline(task_id)` (fire-and-forget asyncio task). Nodes: `prepare` → `fetch` (per-keyword `get_latest_policies`, dedupe by url, `insert_policies`) → `extract` (semaphore-bounded `asyncio.gather` over `_extract_one`) → `summarize`; MCP failure routes to `fail` with the design's Chinese error copy. `pipeline_extract_concurrency` defaults to 1 so the "第 N/M 篇" stage text stays truthful.
- **Extraction** (`app/pipeline/extract.py`): `extract_policy()` truncates the body to `pipeline_max_body_chars`, tries `llm.with_structured_output(ExtractionPayload)`, and falls back to a lenient JSON parse. Output is 事实/信息/知识 (facts / info{summary,points} / knowledge{concepts,fields,extension}).
- **Business API** (`app/api/routes/tasks.py`, `policies.py`): `POST /api/tasks`, `GET /api/tasks/current|{id}|{id}/logs|{id}/policies`, `GET|POST /api/policies/{id}/extraction|retry|reextract`, and `GET /api/tasks/{id}/stream` (SSE). The stream replays all logs from id 0 then tails; it emits `task` / `log` / `done` / `error` events, polls every 0.6 s, and sends `: ping` heartbeats every 15 s. `retry` (failed only, else 409) and `reextract` both re-enter `run_single_extraction`.
- **Config** (`app/config.py`): `pydantic-settings` reads `.env`, but `load_dotenv()` is also called explicitly because the `anthropic` SDK reads `ANTHROPIC_*` straight from the environment. Pipeline knobs: `DATABASE_URL`, `DB_POOL_*`, `PIPELINE_MAX_POLICIES`, `PIPELINE_PER_KEYWORD_LIMIT`, `PIPELINE_EXTRACT_CONCURRENCY`, `PIPELINE_MAX_BODY_CHARS`, `PIPELINE_MCP_TIMEOUT`.

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
