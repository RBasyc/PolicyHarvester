# D. Headless browser (`app/browser/`, Phase 24)

> Part of the [docs wiki](../README.md). Companion pages:
> [chat-agent.md](chat-agent.md) (the chat entry), [data-pipeline.md](data-pipeline.md)
> (the crawl entry), [source-onboarding.md](source-onboarding.md) (the selector-based
> alternative).

A **read-only Playwright/Chromium** stack that lets the project browse **arbitrary public
websites** — including JS-rendered pages the static `HtmlListSource` cannot read. It is an
*optional* capability: without the `browser` extra the server starts normally and both entries
simply disappear.

There are **two entrances** and one shared action layer:

- **Chat** (`/agent`): whenever the engine is available the chat model can call `browser_*`
  tools (no per-turn toggle since 2026-10-22).
- **Pipeline** (`/crawl`): source 「任意网站（无头浏览器）」 + an entry URL + a natural-language
  goal; a compact ReAct discovery loop collects candidate detail pages, then the **existing**
  judge/preflight/confirm/extract stages take over.

Both reuse the project's own LangGraph/ReAct loop — this is **not** a port of
`C:\Users\MP\Desktop\webagent`'s planner/executor; what was borrowed is the numbered-DOM
snapshot approach and the action vocabulary.

```
FastAPI main loop (Windows: SelectorEventLoopPolicy, for psycopg)
├─ /api/chat[/stream]   → BrowserRun per request → browser_* tools → BrowserSession
└─ /api/tasks (browse)  → browser_discovery.discover → the same tools
                     │
                     ▼ runtime.run_in_worker()  (asyncio.run_coroutine_threadsafe)
browser-loop thread (Windows: ProactorEventLoop)
  Chromium singleton → fresh BrowserContext per run → context.route("**/*") guard → Page
```

## Why a separate worker loop

`server/app/__init__.py` installs `WindowsSelectorEventLoopPolicy` because psycopg requires it;
Playwright's asyncio driver requires a **Proactor** loop on Windows. Instead of changing the
global policy, `runtime.py` starts a daemon thread named `browser-loop` with its own loop, and
every Playwright object stays there. The main loop only holds an opaque handle; each action is
dispatched through `run_in_worker()`. This is also why `runtime.available()` only checks
"switch on + `playwright` importable" — it must stay cheap and must never launch Chromium.

`BrowserRun` (session, budget, collected/extracted, SSE emitter) lives in the
`browser_run_var` **`ContextVar`**, not in tool arguments (tool schemas are model-facing).
Tools read the contextvar; a request/pipeline run sets it and closes the session in `finally`.

## Files

| File | Responsibility |
| --- | --- |
| `runtime.py` | Worker loop, Chromium singleton, `run_in_worker()`, `available()` / `unavailable_reason()`, `shutdown()` |
| `guard.py` | Navigation check (`net.check_url` → async DNS recheck → robots → per-host throttle) and the per-context `route_handler` |
| `session.py` | `BrowserSession` facade; dispatches every action to the worker loop and caches the latest snapshot |
| `snapshot.py` | Numbered snapshot: main document + open shadow roots, `data-agent-id`, elements/text/title/scroll, SHA-1 fingerprint; capped at ~220 elements / 8k text chars |
| `actions.py` | `navigate/click/type/select/press/scroll/back/wait/snapshot/extract_page/extract_articles`; every action rebuilds the snapshot |
| `content.py` | Renderer HTML → body/meta via `html_source.parse_detail_page(html, DetailConfig(), url)` (empty selectors hit the structural fallback); `inner_text` fallback; 20k caps |
| `budget.py` | Per-run pages/actions/extracts/elapsed; every tool result carries a one-line budget |
| `manager.py` | Fresh context per run, `BROWSER_MAX_SESSIONS` semaphore (queue, don't fail), `fetch_html()` for stage 2, shutdown |
| `context.py` | `BrowserRun` + `browser_run_var` |
| `tools.py` | Shared `@tool`s: `chat_browser_tools()` (11) and `pipeline_browser_tools()` (+ `browser_collect_links`, `browser_finish`) |
| `app/sources/browser_any.py` | `BrowserAnySource` (`browser_any`) — normalize + `fetch_fulltext`; no `search()` |
| `app/pipeline/browser_discovery.py` | The pipeline's compact ReAct discovery loop |

## Security invariants (do not bypass)

1. **Every navigation** (`open` / `click` / `back` / popup destination / batch extraction) goes
   through `guard.assert_navigation()`, and the **final URL** is re-checked with
   `assert_final_url()`. Do not add a path that calls `page.goto` directly.
2. **Every context** installs `context.route("**/*", route_handler)`: subresources are checked
   too (`<img src="http://169.254.169.254/…">` must not load).
3. **Read-only**: only GET/HEAD; POST/PUT/… are aborted at the route layer. Password/file/hidden
   inputs are refused by the type action. `accept_downloads=False`; popups are recorded then
   closed; service workers and WebSocket are blocked.
4. **Fresh context per run** (`cookie/storage` never shared); closed on finish, cancel, or client
   disconnect — including the SSE cancel path, which uses `asyncio.shield(run.aclose())`.
5. **Page content is untrusted data.** Tool results wrap page text in
   `<untrusted_page_content>`; `prompts/chat_browser.md` / `prompts/browser_browse.md` tell the
   model not to follow page instructions, not to leak conversation content, and not to visit
   unrelated sites on the page's request.
6. **robots.txt is respected** (`SOURCE_RESPECT_ROBOTS`). The browser is **not** a robots
   bypass — gov.cn in-site search still goes through the MCP tools.
7. **SSRF**: protocols other than http/https rejected, private/loopback/link-local/metadata
   addresses blocked via `net.check_url` + a second DNS lookup on the async path. Optional
   `BROWSER_ALLOWED_DOMAINS` narrows further (empty = any public site). Chromium launches with
   `--no-proxy-server` so `HTTP_PROXY` cannot redirect an already-validated URL.
8. **No new telemetry.** Content browsed is sent to the configured **model gateway** (the same
   LLM path as extraction); nothing else leaves the machine.

Known residual risks: Chromium resolves DNS itself (TOCTOU between our check and its connect);
prompt-injection exposure is inherent to browsing arbitrary sites (mitigated by read-only +
budgets + untrusted wrapping); anti-bot sites may return an empty shell.

## Tools

Available to the chat model whenever the engine is available (no toggle); available to the pipeline discovery loop.

| Tool | Notes |
| --- | --- |
| `browser_open(url)` | Counts a page; validates + snapshots |
| `browser_snapshot()` | Re-reads the numbered snapshot (free — no action budget) |
| `browser_click(ref)` / `browser_type(ref,text,submit)` / `browser_select` / `browser_press` / `browser_scroll` / `browser_back` / `browser_wait` | One action each |
| `browser_extract_page()` | Current page → body/meta; counts an extract |
| `browser_extract_articles(refs)` | Up to 5 links in a separate page in the same context; counts one extract |
| `browser_collect_links(refs, note)` | **Pipeline only** — resolves refs against the latest snapshot; the model cannot submit a raw URL |
| `browser_finish(summary)` | **Pipeline only** — ends discovery, sets `stop_reason` |

Tools never raise: failures return `{"ok": false, "error": "…", "budget": "…"}` so the model can
change approach. Every result includes the latest snapshot (page text wrapped as untrusted) and
the budget line.

## Chat entry

- **No per-turn switch.** When `browser_runtime.available()` (`BROWSER_ENABLED=true` and
  playwright importable), every chat request creates a `BrowserRun` (SSE emitter for the stream,
  none for `/chat`), sets the contextvar in the same `isolated_context()` used for tracing, and
  closes the run in `finally`. The session is opened lazily on the first `browser_*` call, so an
  unused run costs one dataclass. When the engine is unavailable there is no run and no tools —
  no 400, no toggle; the model simply never sees `browser_*`.
- `agent/tools.py::all_tools()` always exposes the browser tools to `ToolNode`, but
  `call_model` filters the list before `bind_tools`: without an active run the model never sees
  the schemas (saves tokens; `ToolNode` can still execute any tool name).
- `agent/nodes.py::call_model` appends `prompts/chat_browser.md` (hot-read) while a run is
  active.
- `_chat_config()` raises LangGraph's `recursion_limit` to `max(25, 2*(BROWSER_MAX_ACTIONS+4))`
  when the engine is available (each agent↔tools roundtrip is two steps).
- **SSE `tool` events**: the stream now emits `{"type":"tool","name","detail","status","ok"}`
  (`status` = `start|done`) between `token` frames. `web/src/store/chat.ts` pairs `done` with
  the most recent matching `start` and renders the steps inside the assistant bubble. Unknown
  event types are ignored — do not fall back to "anything else means done".

## Pipeline entry

- `crawl_task.browse_url` / `browse_goal` (idempotent `ALTER TABLE` in `schema.sql`) — they must
  be persisted because stage 2 rebuilds its state from the row. `TaskCreate` validates the URL
  is complete http(s); schedule jobs reject the browser source (there is no per-run entry URL).
- `fetch_node` branches on `browse_url`:
  1. `_browse_policies()` calls `browser_discovery.discover()` (candidate cap
     `min(2 × pipeline_max_policies, 50)`), which opens the entry, drives the ReAct loop, and
     returns `run.collected` (or `run.extracted` as a fallback).
  2. Early date-window re-filter via `source.validate_item`; judge + body preflight run through
     `_judge_and_preflight()` — the **same** helper `_search_policies` uses, so both entries
     reach identical verdicts. The detail-page date filled by preflight triggers a second date
     re-filter.
  3. The existing three-way partition / insert / `refresh_task_counts()` / `await_confirm` path
     is unchanged.
- Stage 2 (`run_extraction_phase`) rehydrates `source` from the task row;
  `BrowserAnySource.fetch_fulltext()` calls `manager.fetch_html()` (fresh context, same guard,
  rendered HTML) and maps the parsed result to `FulltextResult`. Concurrent browser fetches are
  bounded by **both** the stage concurrency and `BROWSER_MAX_SESSIONS` — excess calls queue.
- The progress panel gets `浏览器：打开/点击/收集…` stage text and a summary log line
  (`opened N pages · M actions · K candidates (stop reason)`).
- `BrowserAnySource.filter_capabilities()` marks keyword/date/agency/doc_type as `client`:
  filtering happens in the discovery loop / after fetching, not server-side. `GET
  /api/filters/facets` returns empty facets for this source so the UI hides the filter row.

## Availability & configuration

`GET /api/config/features` → `{browser_available, browser_enabled}` (does **not** launch
Chromium). `browser_available` = `BROWSER_ENABLED=true` **and** playwright importable.
`/api/sources` and `available_sources()` gate on it; the browser source also disappears from
the source dropdown. The chat entry gates on `browser_runtime.available()` server-side (it no
longer reads a UI toggle).

`.env.features`:

```ini
BROWSER_ENABLED=false           # master switch (restart to change)
BROWSER_HEADLESS=true
```

`.env`:

```ini
BROWSER_MAX_SESSIONS=2          # contexts; also bounds browser fetch concurrency
BROWSER_MAX_ACTIONS=20
BROWSER_MAX_PAGES=10
BROWSER_MAX_EXTRACTS=10
BROWSER_TIMEOUT_SECONDS=300
BROWSER_NAVIGATION_TIMEOUT_MS=30000
BROWSER_MODEL=                  # empty = MODEL_NAME; must support tool calls
BROWSER_ALLOWED_DOMAINS=[]      # JSON array; empty = any public site
```

`SOURCE_USER_AGENT`, `SOURCE_RESPECT_ROBOTS`, `SOURCE_ALLOW_PRIVATE_HOSTS`, and
`SOURCE_REQUEST_INTERVAL_MS` are reused so HTTP-fetch and browser-fetch politeness are one
policy. `SOURCE_ALLOW_PRIVATE_HOSTS=true` also relaxes the browser (local debugging only).

Install: `cd server && uv sync --extra browser && uv run playwright install chromium`, then
set `BROWSER_ENABLED=true` in `.env.features`. `start.sh` syncs the extra when enabled and
prints a non-blocking hint if Playwright/Chromium is missing.

## Limits / deliberately not done

No login, cookie persistence, uploads/downloads, screenshots/vision, anti-bot evasion, robots
bypass, or scheduled browser jobs. POST search forms are rejected by design (read-only).
Browser state is **not** shared across chat turns (`thread_id` is client-supplied). The
unmerged `feat/js-render` branch stays isolated; a future merge has to converge on one browser
lifecycle.

## Smoke test

`server/scripts/test_browser.py` — offline guard/budget/snapshot/content/source cases (no
network; DNS is injected), plus a local HTTP server integration (`browser_open` → JS-rendered
list → `collect_links` → `extract_articles` → `BrowserAnySource.fetch_fulltext`). Chromium
missing = **skipped**, not passed.
