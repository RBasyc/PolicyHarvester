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

## Server architecture

The backend is a LangGraph **ReAct agent** exposed over HTTP:

```
START -> agent --(tool calls)--> tools --+
          ^                              |
          |--------(done)-------> END    |
```

Key flows that span multiple files:

- **Tool registration is a two-stage registry** (`app/agent/tools.py`): local `@tool` functions live in `TOOLS`; MCP tools are appended to `MCP_TOOLS` at startup. Both `graph.py` (ToolNode) and `nodes.py` (`bind_tools`) call `all_tools()`, so the graph **must be compiled after MCP tools load** — this ordering is enforced in the FastAPI `lifespan` in `app/main.py` (load MCP tools → `get_graph()`). The graph is a process-wide singleton via `@lru_cache`; adding tools at runtime requires restarting.
- **MCP integration** (`app/agent/mcp.py`): loads `chuance-policy-mcp` (Chinese policy retrieval) via stdio/`uvx` using `langchain-mcp-adapters`. Failure degrades gracefully to local tools only — the server still starts.
- **Conversation memory**: `InMemorySaver` checkpointer keyed by `thread_id` (request field). Memory is lost on restart; swap for `PostgresSaver` etc. for persistence.
- **LLM auth quirk** (`app/agent/nodes.py`): `ChatAnthropic` doesn't accept `auth_token`, so Bearer-token gateways are passed via `default_headers={"Authorization": "Bearer ..."}`.
- **SSE streaming** (`app/api/routes/agent.py`): `POST /api/chat/stream` emits `data: {"type": "token", "content": ...}` lines, always terminated by `{"type": "done"}`; errors are sent as `{"type": "error"}` events in-stream rather than HTTP errors. `_extract_text` filters content blocks to `text` only, skipping thinking/tool blocks — reuse it when handling message content.
- **Config** (`app/config.py`): `pydantic-settings` reads `.env`, but `load_dotenv()` is also called explicitly because the `anthropic` SDK reads `ANTHROPIC_*` straight from the environment.

## Extending the agent

- New local tool: add a `@tool` function in `app/agent/tools.py` and append it to `TOOLS` — no graph changes needed.
- New graph behavior (interrupts, conditional routing): edit `app/agent/graph.py`.
- New state fields: `app/agent/state.py` (use reducers like `add_messages` for mergeable fields).
