# Extending the project

> Part of the [docs wiki](../README.md). **One canonical list** of "I want to X → change Y" —
> the architecture pages link here so these rules live in exactly one place.

## At a glance

| 想做的事           | 改哪里                                                                                                |
| ------------------ | ----------------------------------------------------------------------------------------------------- |
| 加一个对话工具     | `server/app/agent/tools.py` 用 `@tool` 定义并加入 `TOOLS`，图自动识别                                 |
| 改对话图结构       | `server/app/agent/graph.py`（新增节点/边、`interrupt` 人工审批、条件路由）                            |
| 加抓取流水线阶段   | `server/app/pipeline/crawl_graph.py` 加节点与边（分支还要动 `_route_after_fetch`）                    |
| **加一个落库字段** | 需**同时**改四处（见下）                                                                              |
| 调流水线参数       | 根目录 `.env` 的 `PIPELINE_*`；页面「高级设置」可再按任务覆盖                                         |
| 换模型 / 网关      | `.env` 的 `MODEL_NAME`、`ANTHROPIC_*`，或往 `PIPELINE_MODELS` 注册表加一项                            |
| 接入一个新网站     | 写一份 `SourceConfig`，**不要**写爬虫（见下）                                                         |
| 改无头浏览器       | `server/app/browser/` + 两个提示词；导航必须过 `guard`（见下）                                        |

## Agent

- New local tool: add a `@tool` function in `app/agent/tools.py` and append it to `TOOLS` — no graph changes needed.
- New graph behavior (interrupts, conditional routing): edit `app/agent/graph.py`.
- New state fields: `app/agent/state.py` (use reducers like `add_messages` for mergeable fields).

## Crawl pipeline

- **New website source: write a `SourceConfig`, don't write a crawler.** The flow is recon → sample → config → dry run → register (`html_source.HtmlListSource` executes any config). Only touch `app/sources/` when the site needs a genuinely new acquisition route (`engine`) or a parser capability the subset engine lacks. Adding a site this way is a **data** change, not a code change. See [source-onboarding.md](../architecture/source-onboarding.md).
- A new `engine` must state what its `fetch_fulltext` date **means**: set `fulltext_date_is_publish = False` if it reports something other than the 发布日期 (gov.cn reports the 成文日期). `publish_date` holds 发布日期 only — see [data-pipeline.md](../architecture/data-pipeline.md) → *gov.cn search quirks*. Date provenance lives in `policy_document.publish_date_source` (`NULL` = parsed by deterministic code, `'model'` = model fallback); sources never write it — only `fill_publish_date_from_model` sets `'model'`, and a structured parse resets it to `NULL`.
- New pipeline stage: add a node in `app/pipeline/crawl_graph.py` and wire the edges (and `_route_after_fetch` if it branches).
- New `SourceConfig` field: add it to `SourceConfig.config_fingerprint()`'s dump **only if it affects what gets fetched** (prose fields stay out), and to `.claude/skills/website-source-onboarding/references/config-schema.md` — that doc is a hand-kept mirror too.
- Any write that changes `policy_document`/`extraction` rows must be followed by `refresh_task_counts()` so the derived counters stay honest.
- **New per-site metadata does not get a column.** Add a `FieldRule` under `detail.fields` / `entrypoints[].item_fields`; non-reserved field names land in `policy_document.metadata` (JSONB) and are displayed as-is by the detail drawer. Only a field that *all* sources share and the SQL layer must filter/sort on belongs in `policy_document` columns — that's the four-place change below.
- **Headless browser**: optional Playwright extra (`uv sync --extra browser` + `uv run playwright install chromium`), plus a second persisted entry pair `browse_url` / `browse_goal` (schema → repository → DTO → `types.ts`). Any new navigation path **must** call `guard.assert_navigation()` before acting and `assert_final_url()` after; every context must keep `context.route("**/*", route_handler)`. Never add a `page.goto` shortcut and never reuse a context across runs. See [browser.md](../architecture/browser.md).

## Adding a persisted field — four places, no generator

1. `database/schema.sql`
2. `app/db/repositories.py`
3. the matching `app/schemas/*.py` DTO
4. `web/src/api/types.ts`

Nothing generates item 4 — it is a hand-kept mirror. Keep all four in step.

## Hand-kept mirrors (update both sides)

| Source of truth | Mirror |
| --- | --- |
| `app/schemas/*.py` DTOs | `web/src/api/types.ts` |
| source-onboarding behaviour (enable gate, refusal wording, troubleshooting) | `.claude/skills/website-source-onboarding/SKILL.md` + `references/{config-schema,acceptance-criteria,tool-map}.md` |
| filter capability (`sources/config.capability_summary()` / `PolicySource.filter_capabilities()`) | `app/api/routes/filters.py` + `app/schemas/task.FacetSupports` + `web/src/api/types.ts` + `web/src/store.ts` (`sourceFilterNote`) |
| pipeline & agent behaviour | this wiki (`docs/architecture/*.md`) + `CLAUDE.md` |
| prompt text in `prompts/*.md` | `prompts/README.md`'s file table + the consumer note in [chat-agent.md](../architecture/chat-agent.md) (which ones are hot-read vs. restart) |
