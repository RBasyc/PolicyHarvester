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
- `web/` — Vue 3 + TypeScript + Vite frontend, managed with **pnpm**. API-driven (module-level stores under `web/src/store/`, `fetch` + SSE client in [web/src/api/](../../web/src/api/)); the old mock `web/src/data.ts` has been removed. `web/design-export/` contains the pen.dev design exports the UI was built from.
- `database/` — PostgreSQL schema/seed scripts (`schema.sql`, `seed.sql`, `init.sh`, `reset.sh`) for the docker container `server-db-1` (port 5433, db `agent_demo`).
- `prompts/` — the LLM prompts as `.md` files (`chat` / `chat_browser` / `browser_browse` / `extract` / `extract_json_fallback` / `extract_fields` / `judge`), loaded by `app/prompts.py::load_prompt`. One file per prompt, filename = key; a missing file raises at the call site instead of silently degrading. Whether an edit needs a restart depends on the consumer — see [chat-agent.md](chat-agent.md) → *Prompts*.
- `tools/China-Central-Policy-MCP/` — the MCP server, vendored into this repo as plain source (see [data-pipeline.md](data-pipeline.md) → MCP integration).
- `.claude/skills/website-source-onboarding/` — the **website source-onboarding Skill** (`SKILL.md` + `references/`): how the Agent drives 「接入一个新网站」 end to end. `.claude/` is gitignored **except** `.claude/skills/` (the Skill is a deliverable; `.claude/worktrees/` is session state). See [source-onboarding.md](source-onboarding.md).
- `docs/` — this wiki. `PLAN.md` / `TODO.md` stay at the repo root.

### Frontend shape (vue-router)

Five routes in `src/router.ts` — `/agent` (`views/AgentPage.vue`, the Agent entry page / 首页), `/crawl` (`views/CrawlPage.vue`), `/library` (`components/LibraryPage.vue`), `/library/:id(\d+)` (`components/LibraryDetailPage.vue`, id injected as a `policyId` prop), `/sources` (`views/SourcesPage.vue`) plus its detail route `/sources/:id(\d+)` (`views/SourceDetailPage.vue`, id injected as a `sourceId` prop — the per-source 试运行/健康检查/验收报告 page; card list lives at `/sources`); `/` redirects to `/agent` and everything unmatched falls back there too. History mode in dev (Vite's SPA fallback), **hash mode in prod** (the `dist/` may be served by anything, including `file://`).

The top bar (`TopBar.vue`) carries **nothing but the identity and the navigation**: brand on the left, one shared set of four tabs — 政策助手 → `/agent`, 采集任务 → `/crawl`, 政策库 → `/library` (+ `/library/:id`), 来源管理 → `/sources` — and an equal-width `.spacer` on the right. The brand and the spacer are both `flex: 1 1 0`, which is what pins the four tabs dead-centre: earlier the right side held each page's actions, so the tabs shifted as you navigated. **Page-level actions now live in each page's own header** — the crawl page's `.page-head` holds the keyword hint / 定时任务 / 终止 / 开始抓取 (its task-status pill is *not* repeated there; `ProgressPanel`'s card header already shows one), and the library's `.lib-head` holds the three stats plus 清空 (the old top-bar 已入库 N 篇 count duplicated the page's own 总篇数 and was dropped).

The shared `.content` page shell (padding / max-width) lives in `App.vue`. `/agent` additionally gets `.page--full` / `.content--flush`, but **only for the vertical axis**: the page is locked to one viewport height (`overflow: hidden`) with the scrolling delegated to the chat area, and the gap between groups is zeroed. The horizontal padding and `max-width: 1440px` are deliberately *kept*, so the Agent page lines up with the other four routes — it originally narrowed its content to the design frame's 720 px, which read as cramped. The chat input is the last child of `.agent`'s flex column, so it sits pinned at the bottom of the page while `.chat` scrolls above it. It is deliberately lighter than the design frame: everything account-related (header avatar), the left-hand history sidebar (session list + user block), the capability badge (redundant with the sub-heading) and the three shortcut cards (the same three pages are in the top nav) are all **not** built (user decisions, 2026-10-16). `AppIcon` therefore carries three currently-unreferenced icons (`user` / `message-square` / `settings`) for when the sidebar and account bits come back.

`AgentPage.vue` **is wired to the chat agent** (Phase 19). Two states share the bottom composer: with no messages it renders the centred welcome + suggestion cards (a card click sends its text), otherwise a top-aligned message list. Assistant bubbles go through `components/MarkdownText.vue` (`marked` + `DOMPurify`, config in `src/markdown.ts` — registered once at module scope, not per component instance); user bubbles are plain text with `white-space: pre-wrap`. While a reply has no content yet, the bubble shows 「正在检索政策…」 (「正在浏览网页…」 when the browser toggle is on); with browsing enabled the `tool` SSE events also render a live step list (打开/点击/抽取), so the silent tool window is visible. Without browsing the stream still carries tokens only. Scrolling is the page's own: a `watch` on `messages.length` + the last message's content length sets `.chat.scrollTop = scrollHeight`, but only when the user is already near the bottom (120 px), so reading back through history isn't yanked away. The composer is `Enter` to send / `Shift+Enter` for a newline (guarded by `isComposing` so an IME candidate isn't swallowed), auto-grows to ~160 px, and its button morphs into 停止 while streaming. The `.composer-tools` row shows the capability tags in the empty state and 「新对话」 (which rotates `thread_id`) once a conversation exists — no extra chrome row. When the backend reports the headless browser available (`GET /api/config/features`), a 「浏览网页」 toggle sits in the left slot; it only affects the **next** send and is disabled while streaming (see [browser.md](browser.md)).

State is module-level singletons — `store.ts` (task / library / sources / schedules), `store/sourceImports.ts` (import wizard + batch queue) and `store/chat.ts` (chat messages + the stream handle) — **no Pinia**. Pages load their own data on mount (`onMounted`), because the URL is now the source of truth for "which page". `store.ts` holds a `router` reference only to read the current route inside *actions* (`clearAll()`'s branch, the detail poll's guard) — a module-level store cannot use `useRoute()`.

The store's `task` is a **cache of the server's newest task**, not an authority: tasks are also created outside this page (a scheduled job firing, another tab). So `syncTask()` re-reads `GET /tasks/current` and is called explicitly (first paint, after a trial run), and `startTaskWatch()` polls it every 15 s while the tab is visible (plus an immediate check on `visibilitychange`) — when the id changed while a **non-terminal** task is on screen it only *offers* to switch (toast + `查看`), because the crawl page knows nothing but `/tasks/current` and there is no task list to navigate back from.

The 事实/信息/知识 three-column block is the shared `ExtractionColumns.vue`, used by both `DetailDrawer.vue` (crawl page) and the detail page. `ScheduleModal.vue` is still a store-driven overlay sibling of the page, copying its own overlay-close rule (`downOutsideCard` + check on both `mousedown` and `click`; **`@click.self` does not work** — see the comment there). The old `SourceModal.vue` is gone (Phase 16): 导入网址 and 来源管理 became the `/sources` page, split into `components/sources/{SourceListSection,ImportSection,RunsSection}.vue` behind three `?tab=`-synced tabs kept alive with `v-show` so a running import survives a tab switch. The list tab is card-based (per-design, 2026-10-21): each card shows name/tags/meta plus a one-line 试运行摘要 (`store/sourceImports.ts::recentDryRuns`, fed by `GET /sources/runs/recent?kind=dry_run`), with only three actions — an enable toggle (unverified sources fall into a force-enable confirm), delete, and a chevron into `/sources/:id` where 试运行 / 健康检查 / the full `SourceReport.vue` live. `SourceReport` itself renders the design's four blocks (结论卡 with stats → 判据 2-col grid with equal-height rows → 逐篇实测 → 未测试/覆盖范围双卡) and is shared by the wizard, batch expansion and the detail page.

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
- **D. 无头浏览器抓取（任意网站）** — [browser.md](browser.md)

## Error contract

All HTTP errors are `{"detail": "<string>"}` — a `RequestValidationError` handler flattens FastAPI's default 422 error list into one string, so clients can render `detail` directly (the frontend's `ApiError` does exactly this).
