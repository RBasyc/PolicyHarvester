# Architecture overview

> Wiki home: [docs/README.md](../README.md). This page is the map — what the product is,
> how the repo is laid out, and the two-graph shape of the backend.

## What this is

**PolicyHarvester / 打通三端** — a demo that connects a Vue front end, a FastAPI + LangGraph
back end and a PostgreSQL database. It harvests Chinese central-government policy documents
from gov.cn, extracts 事实/信息/知识 (facts / info / knowledge) from each one with an LLM, and
browses the results in a cross-task 成果库 (library).

The repo is built out phase-by-phase: [PLAN.md](../../PLAN.md) is the plan and
[TODO.md](../../TODO.md) is the live checklist. Comments and docs are primarily in Chinese;
these architecture pages are in English to match how `CLAUDE.md` was written — see
[docs/README.md](../README.md) for the language convention.

## Repository layout

- `server/` — Python backend: FastAPI + LangGraph, managed with **uv** (Python >= 3.12). Hosts **two independent graphs**: the ReAct chat agent and the deterministic crawl/extract pipeline, plus a psycopg3 data-access layer over the `agent_demo` database.
- `web/` — Vue 3 + TypeScript + Vite frontend, managed with **pnpm**. API-driven (module-level store in [web/src/store.ts](../../web/src/store.ts), `fetch` + SSE client in [web/src/api/](../../web/src/api/)); the old mock `web/src/data.ts` has been removed. `web/design-export/` contains the pen.dev design exports the UI was built from.
- `database/` — PostgreSQL schema/seed scripts (`schema.sql`, `seed.sql`, `init.sh`, `reset.sh`) for the docker container `server-db-1` (port 5433, db `agent_demo`).
- `tools/China-Central-Policy-MCP/` — the MCP server, vendored into this repo as plain source (see [data-pipeline.md](data-pipeline.md) → MCP integration).
- `.claude/skills/website-source-onboarding/` — the **website source-onboarding Skill** (`SKILL.md` + `references/`): how the Agent drives 「接入一个新网站」 end to end. `.claude/` is gitignored **except** `.claude/skills/` (the Skill is a deliverable; `.claude/worktrees/` is session state). See [source-onboarding.md](source-onboarding.md).
- `docs/` — this wiki. `PLAN.md` / `TODO.md` stay at the repo root.

### Frontend shape (vue-router)

Four routes in `src/router.ts` — `/crawl` (`views/CrawlPage.vue`), `/library` (`components/LibraryPage.vue`), `/library/:id(\d+)` (`components/LibraryDetailPage.vue`, id injected as a `policyId` prop), `/sources` (`views/SourcesPage.vue`); `/` redirects to `/crawl` and everything unmatched falls back there too. History mode in dev (Vite's SPA fallback), **hash mode in prod** (the `dist/` may be served by anything, including `file://`). The top bar's tab capsule is the navigation; the crawl page's `.content` page shell (padding / max-width) lives in `App.vue` and is shared by all four routes.

State is module-level singletons — `store.ts` (task / library / sources / schedules) and `store/sourceImports.ts` (import wizard + batch queue) — **no Pinia**. Pages load their own data on mount (`onMounted`), because the URL is now the source of truth for "which page". `store.ts` holds a `router` reference only to read the current route inside *actions* (`clearAll()`'s branch, the detail poll's guard) — a module-level store cannot use `useRoute()`.

The store's `task` is a **cache of the server's newest task**, not an authority: tasks are also created outside this page (a scheduled job firing, another tab). So `syncTask()` re-reads `GET /tasks/current` and is called explicitly (first paint, after a trial run), and `startTaskWatch()` polls it every 15 s while the tab is visible (plus an immediate check on `visibilitychange`) — when the id changed while a **non-terminal** task is on screen it only *offers* to switch (toast + `查看`), because the crawl page knows nothing but `/tasks/current` and there is no task list to navigate back from.

The 事实/信息/知识 three-column block is the shared `ExtractionColumns.vue`, used by both `DetailDrawer.vue` (crawl page) and the detail page. `ScheduleModal.vue` is still a store-driven overlay sibling of the page, copying its own overlay-close rule (`downOutsideCard` + check on both `mousedown` and `click`; **`@click.self` does not work** — see the comment there). The old `SourceModal.vue` is gone (Phase 16): 导入网址 and 来源管理 became the `/sources` page, split into `components/sources/{SourceListSection,ImportSection,RunsSection}.vue` behind three `?tab=`-synced tabs kept alive with `v-show` so a running import survives a tab switch.

## The two graphs

The backend exposes **two independent LangGraph graphs** over HTTP plus a database layer:

```
A. Chat (ReAct agent)                 B. Crawl pipeline (deterministic, two stages)
START -> agent --(tool calls)--> tools   stage 1: START -> prepare -> fetch -> (await_confirm | fail) -> END
          ^                    |           stage 2: (user confirms) -> discard unselected -> extract -> summarize -> END
          |------(done)--> END |                        extract: per-policy fulltext -> LLM -> extraction/fact_item
```

- **A. Chat agent** — [chat-agent.md](chat-agent.md)
- **B. Data layer & crawl pipeline** — [data-pipeline.md](data-pipeline.md)
- **C. 网站来源接入** — [source-onboarding.md](source-onboarding.md)

## Error contract

All HTTP errors are `{"detail": "<string>"}` — a `RequestValidationError` handler flattens FastAPI's default 422 error list into one string, so clients can render `detail` directly (the frontend's `ApiError` does exactly this).
