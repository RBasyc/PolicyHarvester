<div align="center">

<img src="static/logo/policyharvester-logo.png" alt="PolicyHarvester" width="420" />

<p align="center">
  <b>English</b> | <a href="README.zh-CN.md">简体中文</a>
</p>

# 

**A policy-extraction agent with a human confirmation gate — filter first, then spend tokens.**

> Most policy crawlers either grab everything or burn money in a black box. PolicyHarvester puts human judgment back in the middle of the flow: retrieve candidates, flag the ones worth keeping, wait for you to confirm, and only then extract structure. **No confirmation, no extraction, no wasted tokens.**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](server/pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](server/app/main.py)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.6+-1C3C3C?logo=langchain&logoColor=white)](server/app/agent/graph.py)
[![Vue](https://img.shields.io/badge/Vue-3.5-4FC08D?logo=vuedotjs&logoColor=white)](web/package.json)
[![TypeScript](https://img.shields.io/badge/TypeScript-6.0-3178C6?logo=typescript&logoColor=white)](web)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](database/schema.sql)
[![uv](https://img.shields.io/badge/deps-uv-DE5FE9)](https://docs.astral.sh/uv/)
[![pnpm](https://img.shields.io/badge/deps-pnpm-F69220?logo=pnpm&logoColor=white)](https://pnpm.io/)

</div>

---

## The problem it solves

Policy researchers, compliance teams and investment firms process dozens of policy documents every week. Most AI tools fail in one of two ways:

- **Grabbing everything** — one pull returns fifty documents, half of them irrelevant thin-shell notices or image-only red-headed files.
- **Opaque costs** — where the tokens went, and why *these* documents were extracted, is a black box.

PolicyHarvester's answer: **put the most expensive step after human confirmation.** The retrieval stage only makes cheap judgements (title-level admission scoring plus a body preflight); once you confirm, the expensive structured extraction starts.

That is the real difference from a generic "policy RAG" or a crawler demo — **cost control is designed into the flow, not patched up afterwards.**

---

## Capabilities at a glance

| Capability              | One-line description                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------------------ |
| **Confirmation gate**   | Retrieval stops and waits; the UI flags "recommended / not recommended / already in library", and extraction starts only after you tick the ones you want |
| **Paged retrieval**     | Keeps paging within the call budget until enough new documents are found; already-stored or rejected entries do not consume the quota |
| **Body preflight**      | Cleans the fetched page and drops thin-shell notices and image-only documents with too little text  |
| **Three-part extraction** | Every document yields Facts / Information / Knowledge; a single failure can be retried alone        |
| **Cross-task library**  | Deduplicated by `source_url` across tasks, filterable by agency / document type / year              |
| **Scheduled ingestion** | Runs on a minute / hourly / daily / weekly schedule, skipping the confirmation gate and ingesting directly |
| **Observable cost**     | Page budget, body length, concurrency and retrieval budget can be overridden per task                |
| **Website onboarding**  | Paste a URL → reconnaissance → generated config → dry-run acceptance → registered and enabled      |
| **Headless-browser sources** | JS-rendered sites become browser-based sources automatically; the chat-side "browse the web" tool shares the same engine (optional) |
| **Native site search**  | When a site exposes its own search, keywords / dates are pushed to the site's search page instead of filtering after the fact |
| **Declared filter layers** | Machine-readable statement of where keyword / date filtering actually happens, so the UI can show the recall boundary honestly |

---

## What it looks like

**Policy assistant** — the ReAct chat agent entry point, wired to the State Council policy search toolchain and a "browse the web" tool, with streaming answers

![Policy assistant](static/screenshot/agent-chat.png)

**Crawl task page** — pick a source, configure the time range / keywords / agencies / document types; "Advanced settings" can override pipeline parameters and the extraction model per task

![Crawl task page](static/screenshot/crawl-task.png)

**Human confirmation gate** — retrieval stops: documents are flagged "recommended / not recommended", and per-document extraction starts only after you confirm your selection

![Human confirmation gate](static/screenshot/confirmation-gate.png)

**Result drawer** — click any row of the result table; the drawer on the right instantly shows that document's Facts / Information / Knowledge

![Extraction result drawer](static/screenshot/result-drawer.png)

**Library** — deduplicated by `source_url` across tasks, filterable by agency / document type / year, with pagination

![Policy library](static/screenshot/library.png)

**Source management** — the list of onboarded sources: import a URL → reconnaissance → dry-run acceptance → enabled and registered for crawling (headless-browser sources included)

![Source management](static/screenshot/sources.png)

---

## Quick start

**Prerequisites**: uv ≥ 0.4, Node.js ≥ 18, pnpm ≥ 8, Docker

```bash
git clone https://github.com/RBasyc/policyharvester
cd policyharvester
cp .env.example .env   # fill in your LLM credentials
./start.sh
```

Windows users can double-click `start.bat`. The script runs an environment self-check, installs dependencies, verifies the database is ready, and starts the front end and back end together.

Once it is up:

| Service          | Address                       |
| ---------------- | ----------------------------- |
| Web UI           | http://localhost:5173         |
| API docs         | http://localhost:8000/docs    |

**LLM gateway (OpenAI-compatible — any `/v1/chat/completions` endpoint works):**

```bash
OPENAI_API_KEY=sk-...                        # gateway API key (DeepSeek / Kimi / OpenAI…)
OPENAI_BASE_URL=https://api.deepseek.com     # OpenAI-compatible endpoint
```

Other commands:

```bash
./start.sh stop     # stop the services
./start.sh check    # environment self-check only
./start.sh --help   # full usage
```

---

## Architecture at a glance

```
Browser (Vue 3 + TS, :5173)
   │  /api ← Vite proxy + SSE
   ▼
FastAPI (:8000)
   ├─ A. Chat agent     agent ⇄ tools → END (ReAct loop)
   ├─ B. Crawl pipeline  prepare → fetch →「human confirmation gate」→ extract → summarize
   ├──► China-Central-Policy-MCP (State Council policy search, node stdio)
   ├──► PostgreSQL :5433 (docker container server-db-1)
   ├──► LLM gateway (OpenAI-compatible protocol)
   └──► Headless browser (optional Playwright stack, only assembled when BROWSER_ENABLED=true)
```

The two tracks are **fully independent**:

- **A · Chat agent** (`app/agent/`) — a classic ReAct loop whose tools come from local `@tool` definitions plus MCP tools launched over stdio at startup; when MCP is unavailable it degrades to local tools only. With the headless browser assembled, the model can call `browser_*` tools to "browse the web" under Guard constraints (click / scroll / extract body text).
- **B · Crawl pipeline** (`app/pipeline/`) — a **deterministic two-phase graph** with a human confirmation gate in the middle: phase one retrieves candidates, writes them to the database and stops (`awaiting_confirmation`); once the user has ticked / removed the selection, phase two extracts each document. Candidates can come from gov.cn (via MCP) or from any onboarded **config-driven website source** (`app/sources/`: column list pages or the site's own search page; JS-rendered sites go through the headless-browser engine).

---

## Configuration

All configuration lives in `.env` at the repository root; see `.env.example` for the annotated reference.

| Group        | Key items                                               | Notes                                                                                        |
| ------------ | ------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| LLM gateway  | `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `MODEL_NAME`     | OpenAI-compatible protocol; works with DeepSeek / Kimi / OpenAI / local vLLM                  |
| Models       | `MODEL_NAME` / `MAX_TOKENS` / `PIPELINE_MODELS`          | `PIPELINE_MODELS` feeds the "Advanced settings" dropdown and is hot-read                        |
| Database     | `DATABASE_URL`                                           | Default `postgresql://kos:kos@localhost:5433/agent_demo`                                       |
| Pipeline     | `PIPELINE_*`                                             | Max documents, items per page, retrieval budget, extraction concurrency, body truncation, preflight thresholds |
| Admission judge | `JUDGE_*`                                            | Judge service address and prompt; **fails open when unreachable** and never blocks a crawl      |
| Browser      | `BROWSER_ENABLED` + `BROWSER_*`                          | Master switch plus action / page / time budgets; browsing tools and rendered sources disappear when off |

The page's "Advanced settings" panel can override some of these — and the extraction model — per task.

---

## API at a glance

Full field lists and progress semantics are in `server/README.md`; the interactive docs are at http://localhost:8000/docs.

**Chat (ReAct agent)**

- `POST /api/chat` — non-streaming; the same `thread_id` shares memory (`InMemorySaver`, lost on restart)
- `POST /api/chat/stream` — SSE: `{"type":"token"|"tool"|"done"|"error", ...}` (tool calls emit start/done step events)

**Crawl tasks**

- `POST /api/tasks` — create and start in the background, returns `201 {"task_id": n}`
- `POST /api/tasks/{id}/confirm` — submit the confirmed selection, triggering phase-two extraction
- `GET /api/tasks/current` / `GET /api/tasks/{id}` / `GET /api/tasks/{id}/logs`
- `GET /api/tasks/{id}/policies` — result table `{items, summary}`
- `GET /api/tasks/{id}/stream` — SSE progress
- `POST /api/tasks/{id}/cancel` · `DELETE /api/tasks/{id}` · `DELETE /api/tasks`

**Policies and library**

- `GET /api/policies` / `/api/policies/meta` / `/api/policies/{id}`
- `GET|POST /api/policies/{id}/extraction|retry|reextract`

**Scheduled ingestion**

- `GET|POST /api/schedules` · `POST /api/schedules/{id}/toggle|run` · `DELETE /api/schedules/{id}`

**Source management**

- `GET /api/sources` — source list (including the built-in gov.cn and each source's declared fetch engine)
- `POST /api/sources/import` — paste a URL to start onboarding reconnaissance (async job, `201`); `POST /api/sources/import/batch` for bulk import
- `GET /api/sources/import/{job_id}` · `POST /api/sources/import/{job_id}/cancel` · `GET /api/sources/import/{job_id}/stream` (SSE)
- `POST /api/sources/browser` — turn a JS-rendered site into a headless-browser source (`201`)
- `GET /api/sources/{id}` (with onboarding records) · `GET /api/sources/runs/recent`
- `POST /api/sources/{id}/enable|disable|trial|health` · `DELETE /api/sources/{id}`

**Misc**

- `GET /health` · `GET /api/filters/facets` · `GET /api/config/pipeline`

> Errors are always `{"detail": "<string>"}`; the front end renders that field directly in its `ApiError`.

---

## FAQ

**401 / authentication failures**

`OPENAI_API_KEY` is not a real key (the `sk-...` placeholder is still there), or the gateway behind `OPENAI_BASE_URL` does not accept that key. Also note that `load_dotenv()` does **not** override existing environment variables: if `OPENAI_*` is already exported in your shell, `.env` is silently ignored. Check with `env | grep OPENAI`.

**The service starts, but crawl tasks fail immediately or return nothing**

First check the database — when it is unreachable the service still starts (it only warns in the log), the chat agent is unaffected, but the crawl pipeline fails:

```bash
docker ps | grep server-db-1     # is the container running?
./start.sh check                 # ports, .env, database and MCP artifacts in one go
```

Note that an empty retrieval result is sometimes **normal**: government sites often return the whole unfiltered listing when there is no strict match, and the pipeline re-validates client-side and honestly reports "no matching policy documents found".

**Extraction always fails / JSON parse errors**

The code already works around this: the default gateway (Kimi) often enables thinking mode, and "thinking on + a pinned `tool_choice`" gets rejected. So structured calls do not force tool use — the model calls tools on its own, and a failure falls back to lenient JSON text parsing. Keep the same strategy when you switch gateways or models. To debug a single document: `POST /api/policies/{id}/reextract`.

**I changed `tools/China-Central-Policy-MCP/src/` and nothing happened**

The server loads the build artifact `build/index.js`, so source changes must be built first:

```bash
cd tools/China-Central-Policy-MCP && npm install && npm run build
```

**Port already in use / services did not shut down cleanly**

```bash
./start.sh stop     # stops by the recorded PID first, then falls back to a port sweep
```

**Do the admission judge and scheduled ingestion work here?**

The judge service points at an internal address by default and is most likely unreachable from the outside. When unreachable it fails open (everything counts as "recommended"), so it never blocks a crawl. Scheduled ingestion needs a working local database.

**The chat forgets everything after a restart / a task is stuck in "running"**

Chat memory uses `InMemorySaver`, so it is lost on restart — switch to `PostgresSaver` to persist it. A stuck task is a leftover row from a killed process; the startup lifespan marks those as failed automatically. Tasks waiting at the confirmation gate are unaffected.

---

## Directory structure

```
policyharvester/
├── start.sh / start.bat     # one-shot start / self-check / stop
├── .env.example             # environment variable template
├── PLAN.md / TODO.md        # implementation plan / progress checklist
├── CLAUDE.md                # entry point for AI coding assistants (index + hard rules)
├── AGENTS.md                # repo-level hard rules and architecture quick reference
├── docs/                    # project documentation wiki (architecture / guides / progress)
├── static/                  # static assets (images referenced by this README)
│   ├── logo/                #   logo (banner / square mark)
│   └── screenshot/          #   UI screenshots
├── database/                # PostgreSQL schema / tables / seed data
├── server/                  # back end: FastAPI + LangGraph + psycopg3
│   └── app/
│       ├── agent/           #   A. ReAct chat agent (+ onboarding_tools)
│       ├── pipeline/        #   B. crawl / extract pipeline
│       ├── sources/         #   C. config-driven source layer (static / headless-browser engines)
│       ├── browser/         #   optional headless browser stack (Guard constraints + session pool, shared by chat browsing and rendered sources)
│       ├── services/        #   onboarding orchestration (onboarding / source_import)
│       ├── db/              #   connection pool + all SQL
│       ├── api/routes/      #   health / agent / tasks / policies / filters / config / schedules / sources
│       └── scheduler.py     #   scheduled ingestion polling loop
├── tools/                   # China-Central-Policy-MCP (vendored)
└── web/                     # front end: Vue 3 + TypeScript + Vite
```

---

## How to extend

| What you want to do   | Where to change it                                                                                        |
| -------------------- | --------------------------------------------------------------------------------------------------------- |
| Add a chat tool      | Define it with `@tool` in `server/app/agent/tools.py` and add it to `TOOLS`; the graph picks it up automatically |
| Change the chat graph | `server/app/agent/graph.py` (new nodes / edges, `interrupt` for human approval, conditional routing)       |
| Add a pipeline stage | Add a node and its edges in `server/app/pipeline/crawl_graph.py`                                            |
| **Add a stored field** | Change **all four** places: `database/schema.sql` → `repositories.py` → `schemas/*.py` → `web/src/api/types.ts` |
| Tune pipeline parameters | `PIPELINE_*` in the root `.env`; the page's "Advanced settings" can override them per task              |
| Swap model / gateway | `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `MODEL_NAME` in `.env`, or add an entry to the `PIPELINE_MODELS` registry |

> ⚠️ Any write that changes `policy_document` / `extraction` rows must be followed by `refresh_task_counts()`, otherwise the derived progress counters drift away from the result table.

---

## Documentation

The deep documentation is wiki-ed; the entry point is **[docs/README.md](docs/README.md)** (with the full document map).

| Document                                       | Contents                                                                        |
| ---------------------------------------------- | ------------------------------------------------------------------------------- |
| [docs/README.md](docs/README.md)             | Wiki home / index / maintenance rules                                           |
| [docs/architecture/](docs/architecture/)     | Architecture: overview, chat agent, pipeline, headless browser, gov.cn search quirks, source onboarding |
| [docs/guides/](docs/guides/)                 | Commands and configuration, build/run conventions, extension guide and mirror lists |
| [docs/progress/log.md](docs/progress/log.md) | Detailed progress log (every pitfall and acceptance evidence)                  |
| [PLAN.md](PLAN.md)                           | The overall plan for wiring up all three ends                                    |
| [TODO.md](TODO.md)                           | Per-phase task checklist and status (detailed log in docs/progress/log.md)      |
| [CLAUDE.md](CLAUDE.md)                       | Entry point for AI coding assistants (index + hard rules)                      |
| [AGENTS.md](AGENTS.md)                       | Repo-level hard rules and architecture quick reference for coding agents        |
| [server/README.md](server/README.md)         | Back-end API fields and progress semantics                                      |
| [database/README.md](database/README.md)     | Table design                                                                    |

---

- Author's statement: this project is provided for learning, teaching and academic research only. It is open-sourced in the hope of helping analysts and researchers carry out policy analysis and reproduce work more efficiently. Usage boundary: it is not intended for production or commercial deployment; the author does not endorse or guarantee any such use. If you need it in a production or commercial setting, please perform your own thorough compliance review and testing, and bear all risks and responsibilities yourself.
- Prohibited uses: this project must not be used for any illegal, infringing, non-compliant or unethical purpose, including but not limited to: bypassing a site's terms of service, access controls or anti-scraping mechanisms; large-scale scraping, storage or distribution of copyrighted or personal data; creating or spreading false or misleading content, or using it for opinion manipulation; anything that violates applicable law, data-compliance or privacy-protection requirements.
- Data sources and compliance: this project talks to public government website entry points. Users are responsible for checking the target site's terms of use, robots protocol, rate limits and copyright requirements, and for keeping the request rate reasonable so the site is not overloaded.
- Disclaimer: the project is provided "as is", without any express or implied warranty (including but not limited to merchantability, fitness for a particular purpose, or non-infringement). To the maximum extent permitted by law, the author is not liable for any direct or indirect loss arising from the use of this project.
- Copyright and license: this project is released under the Apache-2.0 license. The license terms are the only legally binding text; the "Legal notice and usage restrictions" section above states the author's attitude and compliance guidance and does not modify the license. If there is any conflict between the license terms and that notice, the license terms prevail.
- Compliance and takedown: if you believe something in this project may raise a compliance risk, or if you have a legitimate claim for removal or correction, please get in touch with the author.

---

## License

[Apache-2.0](LICENSE)
