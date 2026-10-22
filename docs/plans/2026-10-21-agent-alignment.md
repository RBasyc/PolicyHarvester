# Agent 线与流水线线的对齐清单

> Phase 33 · 2026-10-21 · 状态：**四个批次全部落地**（A1–A4 / B1·C1–C3 / D1·E1·F1 / G1–G3），待实机验收

## Context（为什么做）

流水线这几个月连续换代：Phase 22（发布日期/成文日期分层、机构文号模型兜底）→ Phase 25
（JS 空壳失败归因）→ Phase 26（无头浏览器来源落库）→ Phase 27（来源页改版）。
对话 Agent 这条线**只有一部分**跟着走了，原因很清楚：

- **入库链路自动受益**：agent 的 `ingest_policies` → `app/services/ingestion.py` →
  `app/pipeline/crawl_graph.py`，与页面共用同一段代码。判别（laya）、正文预检、日期分层、
  机构文号兜底都在流水线里，agent 直接吃到。
- **但 agent 还有三条自己的路，不经过流水线**，于是口径开始漂：
  1. Phase 32 新增的**只读检索** `app/services/library_search.py`（自己的日期口径、自己的正文口径、自己的预算）；
  2. agent 自己的 **9 个接入工具**（阶段手工版），而页面走的是 `app/services/source_import.py`
     的六阶段编排（js_shell 归因、`headless_eligible`、批量、进度事件）+ `POST /api/sources/browser`
     的「保存为无头浏览器来源」；
  3. agent **完全没有**的能力：读成果库、终止任务、读任务日志、找回当前任务、定时任务、facets。

本文是**盘点 + 清单**，不是实施方案。每条给出「现状（带 `file:line`）→ 影响 → 建议做法 →
涉及文件 → 验收方式」，供后续按批次落地。清单本身不改变任何行为。

## 一、已经对齐的部分（不用动）

| 能力 | 依据 |
| --- | --- |
| 入库链路（检索 → 确认 → 提取） | agent 工具与页面共用 `app/services/ingestion.py`；确认序列只有一份实现（丢弃未选 → `refresh_task_counts()` → 落日志 → `set_task_running` → `start_extraction`） |
| 确认闸门 | agent 侧 `confirm` 必带 `user_approved=true`（`services/ingestion.py::confirm_ingestion`）；调度器的自动确认是另一条路，互不破坏 |
| 流水线换代自动生效 | 判别 / 正文预检 / 日期分层 / 机构文号模型兜底都在 `crawl_graph` 内，agent 的 `start` / `confirm` 直接吃到 |
| 工具层契约 | 三个工具模块共用 `app/agent/tool_utils.py`（`dump` / `error` / `parse_iso_date`）+「工具不抛异常」 |
| 遗留伪来源口径 | `library_search` 与 `routes/sources.list_sources` 同口径排除「任意网站（无头浏览器）」 |

## 二、P0：口径 bug —— 会给出**错误**信息，必须先修

> **状态：批次 1 已落地（2026-10-21）。** 改动集中在
> `app/services/library_search.py::fetch_source_fulltext` 与 `_item_view`，
> 口径逐条照抄流水线的 `crawl_graph.py::_preflight_bodies`；
> 回归断言在 `server/scripts/test_library_search.py` 第 4/4b/4c 节。

### A1 gov.cn 全文的**成文日期**被当成**发布日期**返回

- **现状**：`app/services/library_search.py:521`（`fetch_source_fulltext`）直接返回 `result.date`；
  而 `app/sources/gov_cn.py:30-34` 明确声明 `fulltext_date_is_publish = False`——MCP
  `get_policy_fulltext` 的 `date` 取的是详情页元信息表里的**成文日期**。
- **流水线怎么做**：`app/pipeline/crawl_graph.py:607` 只有 `source.fulltext_date_is_publish` 为真时
  才把 `result.date` 当发布日期；`:634-640` 另存 `signed_date`；`:982` 落库时同样
  `publish_date=result.date if source.fulltext_date_is_publish else None`。
- **影响**：agent 会把成文日期说成发布日期。`prompts/chat.md` 还要求「涉及具体文件时必须给出
  发布日期」——错的不是「没给」，而是**给错了**，对研究人员是负资产。
- **建议**：`fetch_source_fulltext` 按 `fulltext_date_is_publish` 分流——为真才给 `publish_date`；
  否则 `publish_date=None`，成文日期单独走 `signed_date`（取 `result.metadata["signed_date"]`
  或 `split_fulltext` 的 meta）。**字段改名**：别再用含糊的 `date`。
- **涉及**：`app/services/library_search.py`、`prompts/chat.md`（日期口径一句）、`server/scripts/test_library_search.py`
- **验收**：agent 取一篇 gov.cn 政策全文 → 给出的日期是发布日期；成文日期另标「成文」。

### A2 正文未清洗：模型拿到的是归档原文，不是政策正文

- **现状**：`library_search.py:521+` 直接返回 `result.body` 原文——带元信息表、页头面包屑、零宽字符。
- **流水线怎么做**：`crawl_graph.py:591` `meta, clean = split_fulltext(result.body)`
  （`app/pipeline/fulltext_clean.py:92`：提元信息表、剥零宽字符、去面包屑、兜底补成文日期）。
- **影响**：模型引用条款时可能把元信息行当正文；零宽字符进了给用户的答案。
- **建议**：复用 `split_fulltext`，返回清洗后的 `body` + `meta`（机构 / 文号 / 成文日期，缺则 null），
  另给 `raw_chars` 便于对照。
- **涉及**：`app/services/library_search.py`
- **验收**：取一篇正文，返回的 body 不含元信息表与零宽字符。

### A3 没有正文预检指标：分不清「政策正文」与「页面壳」

- **现状**：只读检索不取全文时，模型无从判断拿到的是正文还是图片版/薄壳页面。
- **流水线怎么做**：`crawl_graph.py:592` `cjk, enders = body_metrics(clean)`，
  `_preflight_bodies` 用它判「图片版页面壳」与「薄壳通知」；阈值校准见
  `server/scripts/test_ingest_filter.py`（口径：CJK ≥ 100 且句末 ≥ 2）。
- **建议**：`fulltext` 动作返回 `body_metrics` 两个数 + `body_ok` 布尔，让模型能说
  「这页是图片版，取不到正文」而不是硬编。
- **涉及**：`app/services/library_search.py`
- **验收**：对一张图片版政策页取全文 → 结果标明 body 不达标。

### A4 附件只给个数，给不出附件名

- **现状**：`library_search.py` 只回 `attachments` 数量；`FulltextResult.attachments`
  （`app/sources/base.py`）本来就是 `{"url","name","ext"}` 清单。
- **影响**：用户问「附件有哪些」时 agent 答不出。
- **建议**：回前 N 条清单（name / ext / url）。
- **涉及**：`app/services/library_search.py`

## 三、P1：能力缺口 —— 页面有、agent 没有

> **状态：批次 2（B1 / C1 / C2 / C3）与批次 3（D1 / E1 / F1）已落地（2026-10-21）。**
> 批次 2：新增 `app/services/library_query.py` + 工具 `search_library`（查库内）；
> `app/services/ingestion.py` 增 `cancel_ingestion`、给 `inspect_ingestion` 加日志
> 与「最近一次任务」兜底；`POST /api/tasks/{id}/cancel` 转调服务层（一处实现两处用）。
> 批次 3：`app/services/facets.py`（`GET /api/facets` 下沉，工具加 `action="facets"`）、
> `app/services/schedule.py` + 工具 `manage_schedule`（`/api/schedules*` 下沉）、
> 工具 `import_source`（复用页面那套六阶段导入编排 + `save_browser_source`）。
> 回归断言在 `server/scripts/test_library_search.py`（离线已跑通，读库用例需 `--db`）。

### B1 agent 读不到成果库（**最大的洞**）

- **现状**：系统里有成果库——`policy_document` + 抽取出的事实/信息/知识三栏，
  页面有 `GET /api/policies`（关键词/机构/文种/年份筛选）、`/api/policies/{id}`（详情 + 归档正文）、
  `/api/policies/{id}/extraction`（提取结果）、`/api/policies/meta`（统计 + 下拉选项）
  （`app/api/routes/policies.py:47/53/85/108`）。**agent 一个工具都读不到。**
- **影响**：用户问「我们库里已经收了哪些浙江科技政策」→ agent 只能去网上重抓一遍；
  「浙政发〔2026〕15号提取出了哪些措施」→ 完全答不了。
- **数据都在**：`repositories.py:721 fetch_library_policies` / `:742 count_library_policies` /
  `:757 fetch_library_meta` / `:110 fetch_extraction_detail`。
- **建议**：加一个只读工具 `search_library`（`list` 查库：keyword/agency/doc_type/year + 分页；
  `detail` 读某篇的元信息 + 提取三栏 + 归档正文）。与 `search_policy_library` 分开——
  一个查「外面的」，一个查「我们已有的」，模型不该混。
- **涉及**：新文件 `app/services/library_search.py`（同文件另开一组函数）或新文件
  `app/services/library_query.py`；新工具模块或并入 `app/agent/library_tools.py`；
  `prompts/chat.md`（库内 vs 库外的口径一句）
- **验收**：问「库里有哪些浙江的科技政策」→ 列出库内篇目（含发布日期与来源链接），不再上网重抓。

### C1 agent 停不了任务

- **现状**：页面有 `POST /api/tasks/{id}/cancel`（`app/api/routes/tasks.py:132`，先取消后台流水线
  再落 `failed` 终态）；agent 的 `ingest_policies` 只有 `start` / `inspect` / `confirm`
  （`app/agent/ingestion_tools.py:38`）。
- **影响**：用户说「算了别抓了」，agent 只能干瞪眼（再 `start` 一个就是重复任务）。
- **建议**：`ingest_policies` 加 `action="cancel"`（走同一条 `cancel_pipeline` + `finish_task`，
  不另写一套）。
- **涉及**：`app/agent/ingestion_tools.py`、`app/services/ingestion.py`（加 `cancel_ingestion`）
- **验收**：对话里取消一个在跑的任务 → 任务落 `failed`，抓取页同步显示已终止。

### C2 agent 看不到任务日志

- **现状**：`GET /api/tasks/{id}/logs`（`routes/tasks.py:176`）有明细日志（正文预检失败原因、
  判别失败、召回归零等）；agent 的 `inspect` 只给 stage / 计数 / 候选。
- **影响**：检索 0 结果或大量篇目被判「不建议」时，agent 只能猜原因。
- **建议**：`inspect` 增加 `logs`（最近 N 条）或新增 `action="logs"`。
- **涉及**：`app/services/ingestion.py::inspect_ingestion`、`app/agent/ingestion_tools.py`

### C3 agent 找不回「刚才那个任务」

- **现状**：`GET /api/tasks/current`（`routes/tasks.py:65`）能拿到当前任务；
  agent 的 `inspect` 需要 `task_id`，跨会话时模型手上没有。
- **影响**：新会话里问「刚才那个抓取跑到哪了」→ agent 无法回答。
- **建议**：`inspect` 允许省略 `task_id` → 退化为「最近一次任务」。
- **涉及**：`app/services/ingestion.py`、`app/agent/ingestion_tools.py`

### D1 定时任务没有 agent 入口

- **现状**：`/api/schedules` 全套（list / create / toggle / run / delete，
  `app/api/routes/schedules.py`）+ `app/scheduler.py`（自动确认、自动入库）。
- **影响**：用户说「每天盯着浙江科技政策」→ agent 只能建议用户去页面建。
- **建议**：agent 侧加 `manage_schedule`（list / create / toggle / run / delete）。
  注意口径：调度器**绕过确认闸门**（`scheduler.py` 自动确认），与对话侧的
  `user_approved` 是两条路，工具说明必须写清「定时任务会自动入库」。
- **涉及**：新工具模块或并入 `app/agent/ingestion_tools.py`；`app/services/` 需抽一份
  「一处实现两个出入口」的调度服务（现在逻辑在 routes 里）
- **验收**：对话里建一个每天跑的定时任务 → 页面「定时任务」里能看到并启用。

### E1 各来源的筛选可选值没有 agent 入口

- **现状**：`GET /api/facets`（`app/api/routes/filters.py:95`）按来源给发布机构/文种可选值
  ——gov.cn 走 MCP `get_policy_facets`，配置化来源走 `_configured_facets`，无头来源返回
  「不支持」。agent 侧只有 gov.cn 的 MCP 版 `get_policy_facets`。
- **影响**：agent 问「浙江那个源有哪些机构可选值」答不出；`search_policy_library` 只能
  给出 `filters` 原始字典，不是可选值清单。
- **建议**：把 `/api/facets` 的分支逻辑下沉到 `app/services/`，两侧共用；agent 工具
  `search_policy_library` 加 `action="facets"`（`source` + `keyword` + 日期范围）。
- **涉及**：`app/api/routes/filters.py`（下沉）、`app/services/library_search.py`、
  `app/agent/library_tools.py`

### F1 接入链路的「新版本」在 agent 侧缺席

- **现状**：页面走 `app/services/source_import.py` 的六阶段编排（`inspect_source` →
  `discover_content` → `generate_config` → `dry_run_ingestion` → `register_source`，
  复用 `onboarding.py` 的能力层），外加：js_shell 归因、`headless_eligible` 判定、批量导入、
  进度事件，以及 `POST /api/sources/browser`（`routes/sources.py:356`）的
  「保存为无头浏览器来源」一键入口。agent 的 9 个工具是**阶段手工版**，
  `save_browser_source` **没有 agent 入口**。
- **影响**：agent 接手一个 JS 渲染站时，得手工串 6 个工具、还造不出无头来源配置；
  页面有的失败归因（哪个阶段卡住、能不能转无头）agent 看不到。
- **建议**：给 agent 一个「导入网址」动作，直接调 `services/source_import.py` 的编排
  （与页面同一条路），保留 9 个阶段工具给需要精细控制的场景；
  再补「保存为无头浏览器来源」的 agent 入口（复用 `save_browser_source`）。
- **涉及**：新工具模块或并入 `app/agent/onboarding_tools.py`；`app/services/source_import.py`
  （进程内作业快照与 SSE 事件对 agent 侧降级为一次性返回）
- **验收**：让 agent 接入一个 JS 站 → 失败归因与「转无头」建议与页面一致。

## 四、P2：两套并存导致的漂移

> **状态：批次 4（G1–G3）已落地（2026-10-22）。**
> G1：只读检索的每关键词条数取 `pipeline_per_keyword_limit`、总调用数共享
> `pipeline_max_search_calls` 预算、**支持翻页**（某页取不到新链接即停，与流水线
> 「整轮零收获即停」同款）；G2/G3：两条**恒定口径说明**随每次检索返回
> （`caveats`）：只读结果未经相关性判别、机构/文号空 = 列表页没解析到。

### G1 只读检索的预算/翻页是自己一套

- **现状**：流水线有 `pipeline_max_search_calls`（总调用预算）、`pipeline_per_keyword_limit`
  （`crawl_graph.py:291/295`）与翻页续抓（`:310-394`）；只读检索是自造的 `_MAX_COMBOS = 6`、
  `_MAX_ITEMS_PER_SOURCE = 20`（`library_search.py:55/57`）、**只取第 1 页**、不读 settings。
- **影响**：同一站点，agent 看到的条数与流水线抓到的条数不一样；配置化来源有站内检索入口时
  （可翻页），agent 永远只看第一页。两条口径还会各自漂。
- **建议**：只读检索读同一套 settings（预算与每关键词条数），并支持翻页（至少到
  `pipeline_per_keyword_limit` 允许的深度）；组合数上限与流水线一致。
- **涉及**：`app/services/library_search.py`、`app/config.py`（若需新字段）

### G2 只读检索不判别（取舍，但要写进提示词）

- **现状**：流水线的检索段逐页跑判别（`crawl_graph.py:397 _judge_and_preflight`，laya 标题判别 +
  正文预检），结果标「不建议入库」；只读检索完全不判别。
- **判断**：只读不花钱是合理的取舍，**不必**在只读路里加判别。
- **建议**：在 `prompts/chat.md` 与工具返回里说明「只读结果**未经相关性判别**，是候选」，
  避免模型把它当定论；需要入库时用 `ingest_policies`（那一步会判别）。
- **涉及**：`prompts/chat.md`、`app/agent/library_tools.py`（返回值加一句 note）

### G3 机构/文号在只读结果里可能为空，表述要诚实

- **现状**：配置化来源的列表页常解析不到机构/文号；流水线靠 `extract_fields` +
  模型兜底补齐（Phase 22），只读检索补不了。
- **影响**：agent 可能说「发文机关：无」——实际是「列表页没解析到」。
- **建议**：只读结果的 `agency` / `doc_no` 为空时，字段含义写明「列表页未解析到，
  详情页可能有」；用户要准确机构/文号时，走 `fulltext`（详情页解析有结构化字段）。
- **涉及**：`app/services/library_search.py`、`prompts/chat.md`

## 五、建议批次

| 批次 | 内容 | 理由 |
| --- | --- | --- |
| 批次 1（P0）✅ | A1–A4 | 现在会给出**错误日期**与脏正文；改动集中在一个文件 + 提示词一句，风险最低 |
| 批次 2（P1 读侧）✅ | B1、C1、C2、C3 | 「聊得起也停得下」：能读库、能取消、能看日志、能找回任务 |
| 批次 3（P1 写侧）✅ | D1、E1、F1 | 让 agent 能建定时任务、拿 facets、走页面同款接入编排 |
| 批次 4（P2）✅ | G1–G3 | 消除两套预算/口径；G2/G3 主要是提示词与返回值的措辞 |

## 六、验证（用户侧，硬规则 1：不构建不运行）

1. 批次 1：`cd server && uv run python scripts/test_library_search.py`（新增断言：日期分流、
   正文清洗、预检指标、附件清单）→ 已通过；重启 server → agent 取一篇 gov.cn 全文，日期口径正确。
2. 批次 2：`cd server && uv run python scripts/test_library_search.py --db`（读库用例；
   离线部分不加 `--db` 也能跑）→ 重启 server → 问「库里有哪些浙江科技政策」
   「刚才那个任务怎么样了」「别抓了」，分别命中读库 / 找回任务 / 取消三个动作。
3. 批次 3：重启 server → 问「每天盯着浙江科技政策」能建定时任务；给一个 JS 站地址让 agent
   接入，失败归因与页面一致。
4. 批次 4：同一站点，agent 只读检索的条数与页面抓取任务的条数口径一致（每关键词上限、
   总调用预算、翻页深度三项都对齐 `pipeline_*`）。

## 七、已知边界（不做 / 另议）

- **不做**：在只读检索里加 laya 判别（花钱且慢；只读定位是「看看有没有」）。见 G2。
- **不做**：让 agent 绕过确认闸门自动入库——`user_approved` 仍是硬约束。
- **另议**：无头浏览器来源进定时任务（`schedule` 表不存 `browse_url`，技术上要改表），
  沿用 Phase 26 计划里的「另行立项」。
- **另议**：agent 直接读写 `web/` 前端状态（跨进程共享 store），保持现状。
