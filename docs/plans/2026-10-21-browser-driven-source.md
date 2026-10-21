# 无头浏览器来源落库：从伪来源到一等来源

> Phase 26 · 2026-10-21 · 状态：待审批

## Context（为什么做）

用户用真实站点（fgk.hljrd.gov.cn 等）批量导入时，JS 渲染站被判「内容发现未通过：列表候选为空」。Phase 25 已加失败归因，但只给一段文案，用户没有下一步。本次改造：

1. **导入失败（JS 空壳归因）时可「保存为无头浏览器来源」**：轻量校验（无头浏览器真打开入口页一次，渲染后有可见内容即通过），通过即 `active`，出现在来源管理与抓取页下拉。
2. **抓取页移除「任意网站（无头浏览器）」伪来源**（`browse_url` 表单项）；统一从来源管理接入。选中无头来源时抓取配置只多一个「浏览目标」输入框（**goal 不落来源、抓取时填**——用户明确）；入口网址以来源 config 为准（服务端取用，忽略 payload）。
3. **「取值方式」即开关**：配置化来源可在 `html_list ↔ html_list_rendered` 间切换（fingerprint 含 engine，切换后 version+1、verified 自动失效、需重新试运行——机制现成）；无头来源显示「无头浏览器」标签、不可切换。
4. 无头来源支持启停/删除/试运行（trial = 发现循环小样本，`max_candidates=min(5×2,50)` 天然受限）。

用户已拍板：轻量校验（非完整试运行）／取值方式即开关（非独立 boolean）／goal 抓取时填。

## 关键设计决策（经代码验证）

- **第三引擎值** `engine="browser_driven"` 塞进现有 `content_source` 表（`engine TEXT` 无迁移）；config JSONB 仍是 SourceConfig 快照。
- **任务载体零 schema 变更**：建任务时把来源的 `browse.url` 拷进既有 `crawl_task.browse_url` 列、goal 拷进 `browse_goal`——`fetch_node` 现按 `state["browse_url"]` 分发（crawl_graph.py:114），流水线主体不动。
- **verified 口径复用**：保存时写一条 `kind='dry_run', status='passed', report={"fingerprint": fp, "light_check": true}` 的 run——现有 `_PASSING_DRY_RUN_SQL` 与部分索引原样命中，零迁移；不引入新 kind（老库 `CREATE INDEX IF NOT EXISTS` 不会重建谓词已变的索引）。
- **旧任务兼容（最高优先级风险）**：存量任务行 `source='任意网站（无头浏览器）'` 的 stage 2 靠 `get_by_label` 解析，`registry.py:152` 的 `BrowserAnySource` 单例**保留注册**（仅不再出现在任何清单）；`start_ingestion` 对 legacy key 的原校验路径保留一个版本。
- **label/key 撞名防护**：保存端点发现同 key 已存在且 `engine != browser_driven` 且 active → 409（不静默 upsert 覆盖已验收静态配置）。

## 实施步骤

### 后端（1→7 依序）

**1. `server/app/sources/config.py`**
- `SourceEngine` 加 `"browser_driven"`（:37）；新增 `BrowseConfig{url}`；`SourceConfig` 加 `browse: BrowseConfig | None = None`。
- `entrypoints` 改 `default_factory=list`（:324）；`_check_unique_entrypoints`（:350）加 `engine != "browser_driven" 时必须 ≥1`（字段级 min_length 在校验器之前抛错，必须移入 model_validator）。
- `validate_source_config`（:396-464）：browser_driven 跳过入口循环（:405-429）与 container_selectors 强制（:449）；浏览器可用性检查（:454）扩为 `in ("html_list_rendered","browser_driven")`；加 `browse.url` http(s)+域名白名单一致性。
- `capability_summary`（:486）加 browser_driven 显式分支（`agency/doc_type: "none"`，避免「翻页比对」误导文案）。

**2. 新文件 `server/app/sources/browser_driven.py`**
- `class BrowserDrivenSource(BrowserAnySource)`：`browser_driven = True` 类标记；`__init__(config)` 实例级覆盖 `key/label/config/entry_url`；`fetched_by = f"browser_driven:{key}"`。复用父类 `fetch_fulltext/filter_capabilities/validate_item/normalize_item/search`；重申 browser_any.py:23-26 的函数内局部导入纪律。

**3. `server/app/sources/registry.py`**
- `register_configured`（:96）按 `config.engine` 分支：`browser_driven → BrowserDrivenSource(config)`，否则 `HtmlListSource(config)`。
- :152 单例保留，注释改为「遗留伪来源，仅为存量任务解析保留」。

**4. `server/app/services/ingestion.py`**
- `start_ingestion`（:212-236）：is_browser 改 `getattr(source_obj,"browser_driven",False)`；browser_driven 的 `url_value` 取 `source_obj.entry_url`（payload browse_url 忽略并写进 docstring）；goal 仍取 payload；legacy `browser_any` key 路径保留。
- `available_sources`（:82-100）：browser_driven 持久化来源正常出现在清单。

**5. `server/app/services/onboarding.py`**
- `dry_run_ingestion`（:677）入口加 browser_driven 守卫（failed + 「验收在保存端点完成」）。
- `_run_trial`（:1786-1830）加 browser_driven 分支：task 带 `browse_url=config.browse.url` + goal（`SourceTrialRequest` 新增可选 `goal`）。
- `manage_source("health")`：browser_driven 返回「不支持，入口页校验在保存时已做」。
- `diagnose`（:1857）：`entrypoints[0]` 对空列表 IndexError → browser_driven 用 `config.browse.url`。

**6. `server/app/api/routes/sources.py` 新端点**
- `POST /api/sources/browser` `{url,label?}`：`net.check_url` → `guard.ensure_navigable_sync` → 浏览器可用 → `manager.fetch_html` 轻量校验（`inner_text` ≥80 字且渲染后 `looks_like_js_shell` 为假，否则 422）→ 组装 SourceConfig（`_host_domains/_key_for/_label_for` 从 source_import 复用，label 用渲染页 `<title>`）→ validate → 撞名 409 防护 → `repo.save_content_source(status="active")` → `register_configured(replace=True)` → `_open_run/_close_run` 写 dry_run passed 记录（含 fingerprint + light_check）。**路由注册在 `/{source_id}` 之前的静态段区**（:14-16 的坑）。
- `POST /api/sources/{id}/engine` `{engine}`：仅 `html_list↔html_list_rendered`（browser_driven 行 422）；validate → save（status 原样传回）→ active 行重新 register、非 active 行 unregister（对齐 `_sync_registry` 口径）。
- `list_sources`（:408-426）：删 `_browser_dto()` 注入（:416），删除该函数。
- `trial_source`（:527）透传 goal。

**7. 收口**
- `schemas/source.py`：`SourceTrialRequest.goal`；新增 `SourceBrowserCreateRequest`/`SourceEngineSwitchRequest`。
- `schemas/schedule.py:89-101`：改解析后拒绝（`getattr(get_by_label(v),"browser_driven",False)`），保留 legacy label 拒——schedule 表不存 browse_url，无头来源永远不能进定时。
- `api/routes/filters.py:107` + `_configured_facets`：browser_driven 分支（supports 全 False + 浏览器口径说明）。
- `agent/ingestion_tools.py:111-112` 工具说明改写（browse_url 被忽略、goal 语义）；`source_import.py:102-105` `_STATIC_ONLY_HINT` 与 `onboarding.py:346-354` 空壳归因文案统一指向「保存为无头浏览器来源」。

### 前端（8→12）

**8. `types.ts` + `client.ts`**：`SourceDTO.engine` 联合类型加 `'browser_driven'`；新增 `saveBrowserSource()`、`switchSourceEngine()`；`trialSource` 带 goal。

**9. `store.ts`**：删 `BROWSER_SOURCE_KEY/LABEL`（:138-140）；`isBrowserSource` 改 `engine==='browser_driven'`；`startTask`（:579-614）删 browse_url 校验、payload browse_url 恒 null、goal 仅 browser_driven 时传；`setSource`/`clearBrowseFields`/`loadFacets` 特判同步改。

**10. `ConfigPanel.vue`**：browse 块（:262-289）改为「选中 browser_driven 来源时只显示浏览目标 textarea」，删 URL 输入；样式 :800-833 复用。

**11. `SourceListSection.vue`**：meta 行 engine 映射中文标签（静态/渲染/无头浏览器）（:231）；非无头来源加「切换取值方式」按钮（调新端点 + `loadSources()`）；无头来源隐藏健康检查按钮、显示「无头浏览器」tag。

**12. `ImportSection.vue`**：批量/单条失败行加「保存为无头来源」按钮——判定经 `api.sourceImportJob(jobId)` 取 `stage_reports.discover.summary.js_shell_pages > 0`（`SourceJobDTO` 不含 stage_reports，要单独拉）；成功后 `loadSources()` + toast 引导。

### 文档镜像与测试（13→14）

**13.** `config-schema.md`（engine 三值 + browse 字段 + entrypoints 条件必填）、`tool-map.md`、`source-onduction.md`（`docs/architecture/source-onboarding.md`）、`docs/progress/log.md`；**本计划审批后落档 `docs/plans/`**（用户偏好：定稿计划先落档 docs/）。
- 另需更新：`chat-agent.md`（ingest_policies 文案）、TODO.md 增 Phase 26 条目、根目录 PLAN.md。

**14. 测试**（offline）：SourceConfig 三引擎校验矩阵（browser_driven 空 entrypoints 通过 / 其余引擎拒绝）、`validate_source_config` 分支、`register_configured` 出对的类、fingerprint 随 engine 变化、capability_summary 三分支；接入 `server/scripts/test_source_onboarding.py` 风格的新段。

## 验证（用户侧，硬规则 1：我不构建不运行）

1. `uv run python scripts/test_source_onboarding.py`（+ 新增用例段）
2. 重启 server + `pnpm dev` 后：
   - 重跑 `fgk.hljrd.gov.cn` 导入 → 失败行出现「保存为无头来源」→ 点击 → 来源列表出现该来源（取值方式「无头浏览器」、已验收、已启用）
   - 抓取页来源下拉选它 → 只出现浏览目标框 → 填目标跑任务 → 确认面板有候选 → 走完抽取入库
   - 来源卡片切换 html_list↔html_list_rendered → verified 变未验收 → 重新试运行恢复
   - 旧任务（改造前的任意网站任务）详情页仍可打开、正文正常（回退兼容）
   - 定时任务选无头来源应被拒
3. 边界：`flk.npc.gov.cn`（robots 封禁）保存无头来源应被 guard/校验拒。

## 已知边界（不做）

- 交互式筛选（表单/会话态）仍接不了；无头来源进定时任务（现在入口 URL 已持久化，技术上可行，另行立项）；存量 `browse_url` API 字段保留兼容但 UI 不再发送。
