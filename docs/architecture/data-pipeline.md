# B. Data layer & crawl pipeline (`app/db/`, `app/pipeline/`)

> Part of the [docs wiki](../README.md). The deterministic, **two-stage** crawl/extract
> pipeline plus the psycopg3 data-access layer. Raw gov.cn search evidence lives in
> [gov-cn-search-quirks.md](gov-cn-search-quirks.md).

```
stage 1:  START -> prepare -> fetch -> (await_confirm | fail) -> END
stage 2:  (user confirms) -> discard unselected -> extract -> summarize -> END
            extract: per-policy fulltext -> LLM -> extraction/fact_item
```

## Data layer (`app/db/`)

### Pool (`app/db/pool.py`)

One process-wide psycopg3 `AsyncConnectionPool` (`dict_row`, `autocommit=True`). `open_pool()` warns rather than raising if the DB is unreachable, so the server still boots. `get_pool()` / `close_pool()` are wired into the lifespan.

### psycopg3 placeholder quirks (`app/db/repositories.py`)

Only `%s`/`%b`/`%t` placeholders — **`%(name)s` pyformat raises `ProgrammingError`**, and any literal `%` in the SQL text (e.g. `LIKE '国办发%'`) breaks client-side binding too (`got '%''`). Use positional `%s` + tuples, and prefer `starts_with()` over prefix-LIKE.

### Repositories (`app/db/repositories.py`)

*All* SQL lives here, parameterized. Two rules matter:

- **Counters are derived, never incremented.** `refresh_task_counts()` recomputes `crawl_task.total/fetched/extracted/failed_count` from an aggregate over `policy_document`/`extraction` (`UPDATE ... FROM (SELECT ...)`). The progress panel reads `crawl_task` while the result table reads the `v_policy_results` view — recomputation is what keeps those two views from drifting apart.
- Multi-step writes (e.g. reset extraction + delete facts) go in a single `conn.transaction()`.

## The pipeline (`app/pipeline/crawl_graph.py`)

The deterministic pipeline, split into **two stages by a human confirmation gate**. Stage 1 (graph A) runs `prepare` → `fetch` → `await_confirm` and **ends**; MCP failure or 0 results routes to `fail` with the design's Chinese error copy. `POST /api/tasks` persists the task then calls `start_pipeline(task_id)` (fire-and-forget asyncio task). The task then sits in `awaiting_confirmation` (non-terminal — SSE stays open, `progress` is 50) until `POST /api/tasks/{id}/confirm` drops the unchecked rows and calls `start_extraction(task_id)`, which runs stage 2: `extract` (semaphore-bounded `asyncio.gather` over `_extract_one`) → `summarize`.

**Why two stages**: the graph has no checkpointer (`builder.compile()` with no saver) so `PipelineState` cannot survive a pause — instead the fetched rows live in `policy_document` and `run_extraction_phase()` re-reads them and calls `extract_node`/`summarize_node` directly (they are plain `dict → dict` async functions). `pipeline_extract_concurrency` defaults to 1 so the "第 N/M 篇" stage text stays truthful. Stage 2 **must carry `crawl_task.source` into `extract_node`'s state**: `_extract_one` resolves the source adapter from it, and a missing value silently falls back to gov.cn — which skips a configured source's `detail.fields`/`date_selectors` and (because `fulltext_date_is_publish=False` there) never backfills `publish_date`. That exact omission was the 2026-10-19 "publish date never displays for an `html_list` source" bug.

## Search & filters (`_search_policies`)

Fans out over the `keyword × agency × doc_type` cartesian product under a **total call budget** of `pipeline_max_search_calls` (pagination rounds count too) because the API **silently ignores repeated filter params**. Filters persist as `crawl_task.agencies` / `doc_types` columns (the two-stage graph rebuilds `PipelineState` from the DB row, so filters must be persisted). `_search_policies` applies **client-side re-verification** — drop items whose title lacks the keyword, whose date falls outside `[date_from, date_to]`, and any `gongbao` row when a filter is active — because the API silently returns the full unfiltered browse list on a 0-match strict query. Results are deduped by `url` and capped by `pipeline_max_policies`.

**Pagination + rounds (Phase 13)**:

- **`page` param** on the MCP tool (gov.cn `p`; the request's `n` is set to `limit` — not the old fixed 100 — so consecutive pages advance exactly `limit` rows; the old fixed-`n` + post-slice combo silently skipped rows `limit+1..100` between pages; requires `npm run build` in the vendored repo).
- **Rounds loop** — keep paging each combo until `target_new` items **not in the library and not judge-tagged** are collected (judging runs **per page inside the loop**, so 不建议入库 rows never eat the quota) / budget runs out / every combo returns 0 items / a whole round contributes **nothing to the merged result at all** (all-seen repeats = broken pagination, or exhaustion; in-library items *do* count as contributions — a re-run whose first page is entirely in-library keeps paging for new items, which is the whole point of 续抓).
- `target_new=None` = exactly one round = legacy behaviour (judging still runs — the tags feed fetch_node/scheduler).

**`fetch_node` partitions results three ways**, each capped at cap (`repo.fetch_library_urls()`, global across tasks): 建议入库 new rows fill the quota; judge-tagged and in-library rows are **still inserted** so the confirm panel can gray them out — both groups sink to the bottom (rank order 建议入库 → 不建议入库 → 已在库, matching the stage text), unchecked by default but still manually checkable.

### Browser discovery (`browse_url` / `browse_goal`, Phase 24)

When the task row carries `browse_url` (source 「任意网站（无头浏览器）」), `fetch_node` takes a
different retrieval path — the rest of the stage is unchanged:

- `_browse_policies()` calls `pipeline/browser_discovery.discover()`: a compact ReAct loop
  (the chat model + `pipeline_browser_tools()`) opens the entry, reads numbered snapshots, and
  collects candidates **only** through `browser_collect_links` (refs are resolved against the
  real snapshot, so the model cannot invent URLs). It stops on `browser_finish`, no tool call,
  or the `BROWSER_MAX_*` budget. The target is `min(2 × pipeline_max_policies, 50)`.
- The collected items then go through the **same** `_judge_and_preflight()` helper used by
  `_search_policies` (laya title judge → body preflight), and the date window is re-checked
  after preflight filled detail-page dates. In-library rows are kept for grayed display.
- Stage 2 re-reads `source` from the task row; `BrowserAnySource.fetch_fulltext()` reopens the
  detail page with `manager.fetch_html()` (fresh context, same request guard) and parses the
  rendered HTML with `parse_detail_page(html, DetailConfig(), url)` — the structural fallback,
  no per-site selectors. Browser fetches queue behind `BROWSER_MAX_SESSIONS`.
- `browse_url` / `browse_goal` are persisted columns (the two-stage graph rebuilds state from
  the row); `TaskCreate` requires a complete http(s) URL for this source. Scheduled jobs reject
  this source — there is no per-run entry URL.
- Full design, security invariants and the tool table: [browser.md](browser.md).

### gov.cn search quirks (hard-won)

The condensed rules — full measured evidence, tables and channel matrix in [gov-cn-search-quirks.md](gov-cn-search-quirks.md):

- `searchfield` has exactly **one** effective value — `title` (anything else silently disables keyword filtering).
- Facet filters `bmfl`(发布机构) / `pcodeJiguan`(文种) / `childtype`(主题) / `pubtimeyear`(年份) **only work on channel `t=zhengcelibrary_gw_bm_gb`** — they're dead on `t=zhengcelibrary`.
- **Repeated filter params are silently ignored** (`bmfl=A&bmfl=B` = no filter), so multi-select must fan out client-side.
- The API **silently returns the full unfiltered browse list when a strict query matches 0** — client-side re-verification (title-shares-a-character-with-keyword + date-in-window) is mandatory for a truthful "无".
- The title check must **not** require the full keyword as a substring: `searchfield=title` is **tokenized** (`水域` → 水 + 域), so it legitimately returns titles like 「…流域防洪…」「…水污染防治…」 — requiring the exact substring killed every hit and produced "facet shows agencies, yet the run fails with 未检索到".
- `gongbao`(公报) rows **ignore all facet filters** — drop them when any filter is active (in `getLatestPolicies.ts` **before** the `limit` slice, so un-filterable 公报 rows can't crowd out real matches; the pipeline repeats the check as a safety net).
- The date window filters on the **displayed 发布日期** (`ptimeStr`/`pubtimeStr`) — `ptime` is an epoch that is often an *earlier* 成文日期, so `getLatestPolicies.ts` must report the **text date first, epoch only as fallback**; reporting the epoch made the pipeline's "date must fall inside the window" re-check discard rows the API had legitimately returned for the window (symptom: the facet shows 1 match, yet the run fails with 未检索到匹配的政策原文).
- `get_policy_facets` reads `searchVO.extendresult.facetMap`/`groupMap` for the agency/doc-type option lists.
- **`publish_date` means 发布日期 and nothing else.** The list round writes it (`ptimeStr`/`pubtimeStr`, see the bullet above); the fulltext round must **not** replace it, because `get_policy_fulltext`'s `date` is the detail page's 成文日期 (routinely weeks-to-months earlier). `GovCnSource.fulltext_date_is_publish = False` makes `_fetch_fulltext` pass `None`, so `update_policy_from_fulltext`'s `COALESCE` keeps the list-round value; `html_list` sources declare the default `True` (their detail-page date *is* the published one). Letting 成文日期 win misfiled New-Year-spanning documents — the year filter and the year histogram both run on `EXTRACT(YEAR FROM publish_date)` — and made a doc look "out of window" against the dates its run was configured with. 成文日期 is **not** discarded: it is re-parsed out of the archived fulltext into `FulltextMeta.signed_date` and shown as its own line on the library detail page.
- **Unlabelled dates are recovered by a layered resolver, not by the model.** `sources/date_signals.py` reads the page's own publish-time metadata (JSON-LD `datePublished`/`dateCreated`, `<meta name="pubdate|publishdate|article:published_time">`, `<time datetime>` with a date/pub hint, and the visible header label `日期：…来源：…` / `发布日期：…`) for `publish_date`, and reads `signed_date` from field rules plus the body tail (agency line + date, or a date-only line). Visible publish labels and publish metadata are **explicit**: they override a conflicting list date or legacy `date_selectors` date in preflight; a generic field/selector date only fills a missing list date. `parse_list_page` also falls back to a date inside the item text when no `date_selector`/item field produced one, skipping dates explicitly labelled 成文/印发/施行 so they cannot become `publish_date`. The landed `signed_date` is ISO in `policy_document.metadata` and is exposed as `signed_date` on `v_policy_results` / the library list; the UI shows it only when `publish_date` is empty (badge 「成文」). It never participates in the date window, year filter or ordering.
- **`publish_date` / `agency` / `doc_no` prefer deterministic parsing; the model is only a marked fallback.** `ExtractionPayload` (facts/info/knowledge) has no metadata fields (time-related content is free text inside `facts`, label `时间`). Each field is first written by deterministic code: the list round and, for sources that expose them, `_fetch_fulltext` (`result.date` / `result.issuer` / `result.doc_no`, plus the archived fulltext meta). Only fields that are **still empty** reach the model. It runs first in **body preflight** (`_preflight_bodies`, i.e. before the confirmation gate), fed `FulltextResult.page_text` (the whole detail page, because the published date and issuing agency often sit outside the body container), and writes `it["date"|"agency"|"doc_no"]` plus `date_source` / `agency_source` / `doc_no_source = 'model'`, which `insert_policies` persists — so the 发布日期 is visible in the confirm panel rather than only after extraction. If preflight is disabled or the model is unavailable, `_extract_one` retries the same fallback after extraction via `fill_fields_from_model`, which only fills NULL/empty columns and therefore can never overwrite a parsed value. Every row carries per-field provenance (`*_source = 'model'`), the UI renders a 「模型提取」 badge, and the task log records what the model found. `prompts/extract_fields.md` only accepts explicitly written values — never 成文/印发/施行 dates for 发布日期, never 来源/涉及机构 for 发文机关, never 索引号 for 文号, no guessing — and the results get a light shape/length check (dates must fall between 1990 and today). Model failure is fail-open: extraction still succeeds and the missing fields stay empty.

### Ingest judge (`app/pipeline/judge.py`, httpx)

Posts **仅标题** as `state` to the `/v1/systemone` choice API — 双轴矩阵实测 (2026-10-13, `scripts/test_judge.py`): 机关/文号进 state 会带偏该 RL 模型（文号含「函」直接被判不入库、长多机关标题全灭），「疑问句+枚举」提示词 × 仅标题 = 8/9 且唯一误判方向安全（函被判入库，面板里多一行而已）. Verdicts are stashed on the item (`judge_tagged`/`judge_prob`), flow into `policy_document` via `_to_policy_row`, and land in the `judge_stats` out-param for the fail-open log line. Scheduled flow auto-drops tagged rows straight from the stashed tags (its cursor advances only when `cap` kept rows were found); service down = **fail-open** (keep all, count as recommended, warn).

### Body preflight (`_preflight_bodies`, right after the judge)

Candidates the judge keeps get their fulltext fetched (concurrency `pipeline_preflight_concurrency`) and scored by `fulltext_clean.body_metrics` — cleaned-body CJK chars + sentence-enders (。！？；). Below `pipeline_min_body_chars`/`pipeline_min_sent_enders` (both ≤0 = off, restart to change) → same `judge_tagged` channel, `judge_reason` in-memory for logs (fetch_node splits the log line into laya/body/preflight-fail counts). This is the second screen the title-only judge cannot provide (2026-10-13 两类漏网：图片版文件 body 是整页 CSS/JS 壳——cjk 可达 555 但句末为 0，5% 清洗安全网反而保住垃圾；「现将《方案》印发给你们」式薄壳通知百余字 1 句). Thresholds calibrated by `scripts/test_ingest_filter.py` against real MCP links (好例 cjk≥1062/句末≥29, 坏例句末≤1 — the gap is huge); a preflight fetch failure counts as rejected (tagged, own counter), per «抓不到正文 = 不该入库» — manual tasks still show the row grayed & checkable, scheduled runs drop it via the existing path.

> The **decision lives at search time on purpose** — extraction adds no ingest judgment (user directive); its `len(body) < 50` check stays a mere fetch-failure guard.

## Confirmation state

`task_status` has a fifth value `awaiting_confirmation`; `schema.sql` carries an idempotent `ALTER TYPE task_status ADD VALUE IF NOT EXISTS ...` so an existing DB only needs `database/init.sh` re-run (no data reset). `fail_stale_tasks()` deliberately sweeps only `pending`/`running`, so a task parked at the gate survives a restart and can still be confirmed.

## Extraction (`app/pipeline/extract.py`)

`extract_policy()` truncates the body to `pipeline_max_body_chars`, tries a structured tool call (`_extract_via_tool`, deliberately **without** a forced `tool_choice` — see [chat-agent.md](chat-agent.md) → *No forced tool_choice*), and falls back to a lenient JSON parse. Output is 事实/信息/知识 (facts / info{summary,points} / knowledge{concepts,fields,extension}) — **no date field**; see *gov.cn search quirks* above for where the policy date actually comes from.

Tracing (Phase 21): `extract_policy()` takes an optional `config: RunnableConfig` and passes it straight to `llm.ainvoke` — the caller, `_extract_one()`, builds it with `trace_config(...)` so **each policy is its own trace** (tagged `pipeline`/`extract`, `session_id = task-<id>`, plus `task_id`/`policy_id` metadata an operator can join back to the DB rows). Because the config is threaded explicitly rather than inherited, an extraction that fell through to the JSON fallback shows **both** attempts as siblings in one trace — which is exactly the question these traces are meant to answer. `run_pipeline()` traces the retrieval segment separately; see [chat-agent.md](chat-agent.md) → *Observability* for the wiring and the degradation rules.

## Business API (`app/api/routes/tasks.py`, `policies.py`)

`POST /api/tasks`, `POST /api/tasks/{id}/confirm` (409 unless `awaiting_confirmation`; 400 if nothing is selected), `GET /api/tasks/current|{id}|{id}/logs|{id}/policies`, `GET|POST /api/policies/{id}/extraction|retry|reextract`, `GET /api/config/pipeline` (read-only view of the effective `PIPELINE_*` defaults), and `GET /api/tasks/{id}/stream` (SSE). The stream replays all logs from id 0 then tails; it emits `task` / `log` / `done` / `error` events, polls every 0.6 s, and sends `: ping` heartbeats every 15 s. `retry` (failed only, else 409) and `reextract` both re-enter `run_single_extraction`.

**Create and confirm live in `app/services/ingestion.py`, not in the route** (2026-10-16): the chat agent's `ingest_policies` tool drives the same two functions, so the confirm sequence (discard unselected → `refresh_task_counts()` → log → set `running` → `start_extraction`) has exactly one implementation. The routes kept only HTTP semantics — status codes, response models, and the error mapping `IngestionNotFound`→404 / `IngestionConflict`→409 / `IngestionRejected`→400 — and their user-visible text is unchanged. What the agent may *not* do is decide: `confirm_ingestion` refuses without `user_approved=True`, so the gate still belongs to a human, only the prompt has changed ([chat-agent.md](chat-agent.md) → *Ingestion tools*).

## 成果库 (library) (`GET /api/policies[/meta][/{id}]`, also in `policies.py`)

The cross-task results library — only `extracted` rows, **deduped by `source_url`** (`DISTINCT ON`, latest task's row wins) so re-running a task never duplicates/overwrites; list supports `keyword` (ILIKE) / `agency` / `doc_type` / `year` filters + pagination (default 6/page, fixed `publish_date DESC`); `meta` returns header stats (total / distinct agencies / this-month) + facet options; `/{id}` returns meta + three-column extraction + archived `fulltext`. The list also returns `source_name` so a card can show which source it came from — essential once the library mixes non-gov.cn sources, whose 机构/文种/日期 are frequently all null.

`doc_type` is the one derived column (`_LIBRARY_LATEST_CTE`, no such column on `policy_document`), and it has two legs: **① generic** — the text before the first bracket character in `doc_no` (`国发〔2026〕3号` → `国发`, `粤府〔2026〕1号` → `粤府`), accepted only when that prefix is 1–6 chars so prose like `关于…的通知〔2026〕3号` doesn't become a "文种"; **② fallback** — the gov.cn list (国办发/国办函 before 国发/国函, then 国令) for bracket-less forms like `国令第761号`. Leg ① is what makes the 文种 tag and the 文种 facet work at all for configured (non-gov.cn) sources, which carry their own local 文号. The **frontend mirrors this rule** in `LibraryDetailPage.vue` (`docType` computed) — change both together. **Route order matters**: `/meta` and the list route are registered **before** `/{policy_id}` or "meta" gets swallowed as an id. `policy_document.fulltext` is archived by `update_policy_from_fulltext` during extraction (empty body never overwrites the archive); rows predating the column are simply `null` and the UI shows a friendly empty state.

## Cancellation & clearing (route file + `app/pipeline/crawl_graph.py`)

Background tasks are registered per `task_id` in `crawl_graph._running` (`_track()`), so `cancel_pipeline()` / `cancel_all_pipelines()` can target one task. `POST /api/tasks/{id}/cancel` stops the pipeline and lands the task in `failed` with stage `任务已手动终止` (reusing the existing enum — no `schema.sql` change); `DELETE /api/tasks/{id}` deletes one task and `DELETE /api/tasks` clears all of them, both cancelling first because everything cascades via FKs. A manual cancel deliberately overwrites the `服务重启导致任务中断…` stage that `run_pipeline`'s `CancelledError` branch writes, so the cancel must *await* the cancellation before writing its own state. On startup the `lifespan` runs `repo.fail_stale_tasks()` to fail `pending`/`running` rows orphaned by a killed process (otherwise the UI shows "运行中" forever).

## Scheduled ingestion (`app/scheduler.py` + `app/api/routes/schedules.py`, Phase 11)

`schedule_job` rows (config snapshot + schedule params + a sliding `cursor_before`) are polled by a 15 s in-process loop (`start_scheduler()` in the `lifespan`, after `get_graph()`; `shutdown_scheduler()` before `shutdown_pipelines()`). A triggered run **skips the human-confirmation gate entirely** — it searches, keeps everything (minus judge-tagged rows, which are dropped with a log line), and calls `run_extraction_phase()` directly.

Two mechanics keep repeat runs honest:

- **Cross-run + global-library URL dedup** — `_search_policies(exclude_urls=…)` = this job's history ∪ `fetch_library_urls()` (all tasks' extracted URLs), so a doc ingested by another job/manual task is never re-extracted; empty-after-dedup records `last_status='noop'`, not a failure.
- **Sliding cursor** — a full run (hit `pipeline_max_policies`) advances `cursor_before` to *oldest fetched publish_date − 1 day* so the next run backfills older docs; a non-full run means the window is exhausted, so the cursor resets to NULL and the next run chases new docs over `[date_from, today]`.

Schedule kinds: `minutely`/`hourly` (`interval_minutes`) + `daily`/`weekly` (`run_time`, `run_weekday` 0=Mon per Python `weekday()`); daily/weekly times are server-local. `ScheduleCreate` rejects the browser source (`POST /api/schedules` → 400): a schedule row has no entry URL, so every run would fail. APIs: `GET|POST /api/schedules`, `POST /api/schedules/{id}/toggle|run`, `DELETE /api/schedules/{id}`. Triggered tasks carry `crawl_task.schedule_job_id` (`ON DELETE SET NULL`).

**Re-entrancy (Phase 13)**: the loop is claim-first + serially awaited, so scheduler-triggered rounds of the same job never overlap (a round slower than its interval just fires again immediately after finishing — catch-up without pile-up); the real gap was the manual 「立即运行」 spawning an independent task, now guarded by an in-process `_running_jobs` set (the route returns **409** while a round is in flight; the guard is the TOCTOU backstop inside `run_schedule_job`, which is split into `_run_schedule_job_locked` so early returns can't leak the guard entry).

## Config (`app/config.py`)

`pydantic-settings` reads the **repo-root** `.env` (path resolved from `__file__`, so CWD doesn't matter), and `load_dotenv()` is also called explicitly because the `openai` SDK reads `OPENAI_API_KEY` / `OPENAI_BASE_URL` straight from the environment. The headless browser adds `BROWSER_ENABLED/HEADLESS/MAX_*/TIMEOUT_SECONDS/NAVIGATION_TIMEOUT_MS/MODEL/ALLOWED_DOMAINS` (restart to change; see [browser.md](browser.md)). Pipeline knobs: `DATABASE_URL`, `DB_POOL_*`, `PIPELINE_MAX_POLICIES`, `PIPELINE_PER_KEYWORD_LIMIT`, `PIPELINE_MAX_SEARCH_CALLS`, `PIPELINE_EXTRACT_CONCURRENCY`, `PIPELINE_MAX_BODY_CHARS`, `PIPELINE_MCP_TIMEOUT`. Five of these plus the **extraction model** can be **overridden per task** from the page's collapsed 高级设置 section (values persist on `crawl_task.pipeline_*` columns; NULL = use the global default) — the second extraction stage re-reads them from the task row, so they must be persisted, not just passed in the create request.

The model list itself comes from `.env`'s `PIPELINE_MODELS` JSON registry (Phase 12), **hot-read on every access** — `get_model_registry()` → `_live_env()` resolves real-shell-env > current `.env` file > boot-loaded `os.environ` (a snapshot `_REAL_ENV` taken *before* `load_dotenv`, because `load_dotenv` pollutes `os.environ` with file values) — so editing `.env` takes effect on the next request/creation/extraction **without a restart** (keys via `resolve_model_api_key()` too); the frontend refetches `/api/config/pipeline` when the 高级设置 panel opens and snaps a selection that vanished from the registry back to `default_model`. All entries speak the **OpenAI-compatible protocol** (one `ChatOpenAI` path in `_build_llm`): an entry without `base_url`/`api_key_env` reuses the `OPENAI_BASE_URL` + `OPENAI_API_KEY` gateway (same gateway, just a different model name); a cross-gateway entry sets its own `base_url` (e.g. Kimi `https://api.moonshot.cn/v1`) + `api_key_env` naming a separate key variable defined in `.env`. Entries default to `thinking: false` — extraction passes no `reasoning_effort` (OpenAI protocol has no generic disable switch; server default applies); `thinking: true` sends `reasoning_effort="high"`. The chat agent is unaffected (it calls `_build_llm()` with no overrides). If the gateway returns 0 tool calls, the JSON-text fallback still covers it. `get_model_registry()` degrades to a single-model list on parse failure; `crawl_task.pipeline_model` stores the **id**, `extraction.model` records the **actual model name** used. A stale id at run time falls back to the first entry (default) with a warning instead of failing.

## Extending the crawl pipeline

See [../guides/extending.md](../guides/extending.md) → *Crawl pipeline* and *Adding a persisted field* (new stage, four-place schema mirror, `SourceConfig` fields, the `refresh_task_counts()` rule, and the hand-kept-mirror list).
