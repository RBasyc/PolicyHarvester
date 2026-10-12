# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This is a two-part demo (plus a `design.pen` design file at the root). Comments and docs are primarily in Chinese.

- `server/` — Python backend: FastAPI + LangGraph agent, managed with **uv** (Python >= 3.12).
- `web/` — Vue 3 + TypeScript + Vite frontend, managed with **pnpm**. Currently a **static mockup**: all data is hardcoded in `web/src/data.ts`; no component calls the backend yet. `web/design-export/` contains the pen.dev design exports the mockup was built from.

## Commands

### Server (`cd server`)

```bash
uv sync                                        # install deps (incl. dev group: pytest, httpx)
python main.py                                 # dev server with hot reload (port 8000, /docs for Swagger)
uv run python scripts/test_mcp.py              # MCP connectivity smoke test (lists tools, calls source_catalog)
```

Config: copy `.env.example` to `.env`. Auth is either `ANTHROPIC_API_KEY` **or** `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` (for Anthropic-compatible gateways like Kimi). There is no lint/test suite configured beyond the smoke script above.

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

The backend is a LangGraph **ReAct agent** exposed over HTTP:

```
START -> agent --(tool calls)--> tools --+
          ^                              |
          |--------(done)-------> END    |
```

Key flows that span multiple files:

- **Tool registration is a two-stage registry** (`app/agent/tools.py`): local `@tool` functions live in `TOOLS`; MCP tools are appended to `MCP_TOOLS` at startup. Both `graph.py` (ToolNode) and `nodes.py` (`bind_tools`) call `all_tools()`, so the graph **must be compiled after MCP tools load** — this ordering is enforced in the FastAPI `lifespan` in `app/main.py` (load MCP tools → `get_graph()`). The graph is a process-wide singleton via `@lru_cache`; adding tools at runtime requires restarting.
- **MCP integration** (`app/agent/mcp.py`): loads **China-Central-Policy-MCP** (State Council policy retrieval: `get_latest_policies` / `get_policy_fulltext`) via stdio/`node` from the locally cloned build at `tools/China-Central-Policy-MCP/build/index.js` (default path computed relative to repo root in `app/config.py`, overridable via `MCP_POLICY_*` env vars). Failure degrades gracefully to local tools only — the server still starts.
- **Conversation memory**: `InMemorySaver` checkpointer keyed by `thread_id` (request field). Memory is lost on restart; swap for `PostgresSaver` etc. for persistence.
- **LLM auth quirk** (`app/agent/nodes.py`): `ChatAnthropic` doesn't accept `auth_token`, so Bearer-token gateways are passed via `default_headers={"Authorization": "Bearer ..."}`.
- **SSE streaming** (`app/api/routes/agent.py`): `POST /api/chat/stream` emits `data: {"type": "token", "content": ...}` lines, always terminated by `{"type": "done"}`; errors are sent as `{"type": "error"}` events in-stream rather than HTTP errors. `_extract_text` filters content blocks to `text` only, skipping thinking/tool blocks — reuse it when handling message content.
- **Config** (`app/config.py`): `pydantic-settings` reads `.env`, but `load_dotenv()` is also called explicitly because the `anthropic` SDK reads `ANTHROPIC_*` straight from the environment.

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
