# 工具与能力对照表

本文件回答三个问题：**Skill 每步用什么工具**、**系统原本有什么能力**、**缺什么**。

> 口径：只写**真实存在、已执行过**的东西。未实现的能力在「缺失能力」一节明说，
> 不把「计划做的」写成「已有的」。

## 1. Skill 阶段 → 工具

| 阶段 | 工具（`server/app/agent/onboarding_tools.py`） | 服务函数（`server/app/services/onboarding.py`） | 底层能力 |
| --- | --- | --- | --- |
| 1–2 勘察 | `inspect_source` | `inspect_source` | `sources/net.py`（SSRF/robots/限频）+ `sources/analyze.py` |
| 2 内容发现 | `discover_content` | `discover_content` | 同上（多层栏目页） |
| 3 结构分析 | `analyze_content_structure` | `analyze_structure` | `sources/htmlparse.py` + `analyze.py`（纯函数，可离线喂 HTML） |
| 3 样本核对 | `sample_content` | `sample_content` | `html_source.parse_detail_page` + `pipeline/fulltext_clean.body_metrics` |
| 4 生成配置 | `generate_source_config` | `generate_config` | `sources/config.py`（Pydantic + `validate_source_config` + 指纹） |
| 5 试运行验收 | `dry_run_ingestion` | `dry_run_ingestion` | **`pipeline/crawl_graph._search_policies`**（正式检索编排）+ `judge` + `_preflight_bodies` |
| 6 注册启用 | `register_source` | `register_source` | `db/repositories.save_content_source` + `sources/registry.register_configured` |
| 7 运维 | `manage_ingestion_job` | `manage_source` | 来源表 / 版本表 / `pipeline.start_pipeline` |
| 7 诊断 | `diagnose_source` | `diagnose` | 分层重试（地址→取回→列表→正文→元信息→附件） |

**同一批能力的 HTTP 入口（Phase 15，页面内「导入网址」）**：上面这张表不是唯一入口。
`server/app/services/source_import.py` 按同样的顺序调用**同样的服务函数**
（`inspect_source` → `discover_content` → `generate_config` → `dry_run_ingestion` →
`register_source`），由 `server/app/api/routes/sources.py` 暴露成：

| 方法 | 路径 | 对应能力 |
| --- | --- | --- |
| `POST` | `/api/sources/import` | 建一个导入作业（地址先过 `net.check_url`，不通过直接 400 且不建作业） |
| `GET` | `/api/sources/import/{job_id}/stream` | 作业进度 SSE（`phase` / `log` / `done` / `error`，与任务流同格式） |
| `GET` | `/api/sources/import/{job_id}` | 作业快照：配置 + 候选 + **完整验收报告** + 登记结果 |
| `POST` | `/api/sources/import/{job_id}/cancel` | 取消作业（证据保留） |
| `GET` | `/api/sources` | 来源清单（内置 + 各状态行，含 `verified`） |
| `GET` | `/api/sources/{id}` / `/{id}/runs` | 详情 / 接入记录（历次验收报告） |
| `POST` | `/api/sources/{id}/enable` \| `disable` | 与 `manage_ingestion_job` / `register_source` **同一段代码、同一道闸** |
| `DELETE` | `/api/sources/{id}` | 删除来源 |

两点与对话入口的**确定差异**（不是缺失，是范围决定）：

- **不经过大模型**：候选由 `sources/analyze.py` 的打分排序，但不是「取最高分就定」——打分只能说明
  「这里有一堆同签名的兄弟节点」，页脚／导航／下载块同样符合这个形状（2026-10-15 实测：页面导入
  被页脚块带偏过，用户看到的是「什么都取不到」）。所以编排层会**逐个候选真抓它自己的样本条目**
  （同主机优先，排序不是过滤），能解析出正文容器、且正文不是空壳（CJK ≥ 100 且句末 ≥ 2）的才采用；
  被否掉的候选带着原因列在作业结果里（「没试」与「试了不行」分得清），页面可点另一个重跑
  （`base_config` + `candidate`）。同理，组装出来的选择器会先收敛到引擎可执行的子集
  （去掉 `:nth-of-type` 这类位置伪类；去不掉就留空并记日志），因为**一个**不可用的选择器会让
  `validate_source_config` 拒绝整份配置。
- **拿不到试运行内部的粒度**：编排在服务函数**之外**，只能在阶段之间发进度事件，
  所以试运行阶段只有一句「正在试运行」，没有「第 N/M 篇」。

**与建议接口清单的差异**（有意为之，边界仍清楚）：

- `validate_ingestion` 未单独成工具 —— 验收结论与试运行是**同一次执行**的产物（先跑再判）。
  拆成两个工具会产生「两次结论可能不一致」的空档，反而不利于「未验证不得报成功」。
- 额外的 `diagnose_source` 承担阶段 7 的故障归因（清单里的第 9 项）。

## 2. 现有能力（本次接入直接复用的，全部为既有代码）

### 2.1 Agent 与对话

| 能力 | 位置 | 说明 |
| --- | --- | --- |
| LangGraph ReAct 图 | `server/app/agent/graph.py` | `START → agent ⇄ tools → END`，`InMemorySaver` 按 `thread_id` 记忆 |
| 工具注册表 | `server/app/agent/tools.py` | `TOOLS`（本地）+ `MCP_TOOLS`（启动加载），`all_tools()` 统一给 ToolNode / bind_tools |
| 对话 SSE | `server/app/api/routes/agent.py` | `POST /api/chat/stream`，`token` / `done` / `error` 事件 |
| 通用工具 | `server/app/agent/tools.py` | `get_current_time` |

### 2.2 MCP 工具（`tools/China-Central-Policy-MCP`，启动时经 stdio 加载）

| 工具 | 参数（真实 schema） | 用途 |
| --- | --- | --- |
| `get_latest_policies` | `date_from` / `date_to` / `limit`(≤100) / `keyword` / `agency` / `doc_type` / `page` | 国务院政策检索（gov.cn 检索接口） |
| `get_policy_fulltext` | `url`（必填）、`policy_id` | 取政策全文，返回 title / date / doc_no / body / issuer / image_count |
| `get_policy_facets` | `date_from` / `date_to` / `keyword` | 发布机构（bmfl）与文种（pcodeJiguan）可选值 + 计数 |

> 这三个是 **gov.cn 专用**的：抽正文时依赖 `div#UCAP-CONTENT` 等容器链，且 facet 过滤只在
> `t=zhengcelibrary_gw_bm_gb` 频道有效。**其它站点用不了它们**，这正是来源接入要解决的问题。

### 2.3 抓取与解析底座（本次新增 `server/app/sources/`，复用的既有约定）

| 模块 | 能力 |
| --- | --- |
| `sources/net.py` | 受控 HTTP：只允许公网 http/https、重定向逐跳复验、域名白名单、robots.txt、同域限频、全局并发上限、体积截断 |
| `sources/htmlparse.py` | 纯标准库的迷你 DOM + CSS 子集选择器（标签/类/id/属性/后代/子元素），编码嗅探（UTF-8 优先 → 声明/GB18030 → 取替换字符最少的一次） |
| `sources/base.py` | `PolicySource` 适配器契约（`search_plan` / `search` / `validate_item` / `fetch_fulltext` / `normalize_item`）+ `PolicyItem` / `FulltextResult` |
| `sources/gov_cn.py` | 既有内置源（国务院），实现同一套契约 |
| `sources/config.py` | `SourceConfig` 配置模型 + `validate_source_config()` + `config_fingerprint()` |
| `sources/html_source.py` | `HtmlListSource`：一份配置驱动的通用源（本次新增） |
| `sources/analyze.py` | 页面结构候选打分（列表/正文/元信息/翻页/栏目/订阅/检索） |
| `sources/registry.py` | 来源注册表：内置源 + 配置化源，按 label 解析，含冲突保护 |
| `sources/util.py` | `parse_date` 等小工具 |

### 2.4 采集流水线与数据层（完全复用）

| 能力 | 位置 |
| --- | --- |
| 两阶段流水线（检索 → 人工确认 → 提取） | `pipeline/crawl_graph.py` |
| 检索编排（扇出预算 / 翻页续抓 / 去重 / 判别 / 正文预检） | `pipeline/crawl_graph._search_policies` |
| 入库判别（laya 标题判别，fail-open） | `pipeline/judge.py` |
| 正文清洗与指标（CJK / 句末标点） | `pipeline/fulltext_clean.py` |
| 事实/信息/知识抽取 | `pipeline/extract.py` |
| 任务与结果 API | `api/routes/tasks.py`、`api/routes/policies.py` |
| 成果库（按 `source_url` 去重） | `api/routes/policies.py` + `v_policy_results` |
| 定时入库 | `scheduler.py` + `api/routes/schedules.py` |
| 数据访问（全部 SQL 在这里） | `db/repositories.py` |
| 连接池 | `db/pool.py` |

## 3. 缺失能力（本次明确不做的，别当成已有）

| 缺口 | 影响 | 现状 |
| --- | --- | --- |
| **JS 动态渲染** | SPA / 前端渲染列表的站点解析不到条目 | **阶段 A 已实现（可选，默认关）**：来源配置 `render.enabled` + 可选依赖 `playwright`/Chromium + 服务端 `SOURCE_RENDER_ENABLED=true`（`sources/render.py`）。渲染只是取数方式，解析与字段规则不变。**未做**：勘察 / 取样（`inspect_source` / `sample_content`）仍取原始 HTML，不会自动识别 JS 空壳；滚动/点击等交互 DSL 未实现（只有 `scroll` 连续滚到底这一种） |
| **PDF / OFD 文字抽取** | 图片版红头文件、附件里的正文取不到 | 附件只做可达性 + 归档 + 哈希；正文抽取未实现 |
| **OCR** | 扫描件内容无法进入链路 | 未实现 |
| **登录 / 验证码 / 付费墙后的内容** | 这类站点接不进来 | **规范不允许绕过**，遇到即报「受阻」 |
| **RSS / 官方 API 专用引擎** | 有 `feeds` 的识别，但没有对应的取数引擎 | `engine` 目前只有 `html_list`；RSS 源可先当普通列表页抓 |
| **站内检索的日期参数自动识别** | 检索页支持按日期收窄但配置没填 `date_from_param` / `date_to_param` 时，日期只能取回后复核 | `search_forms` 只报关键词框与隐藏参数，**不认**日期输入框；要接就得人工实测后填进 `entrypoints[].search` |
| ~~**站内检索（search_forms）没有落点**~~ | 关键词/日期无处可推，只能客户端逐条比对（召回 = 翻页深度） | **已实现**：`entrypoints[].search`（`SearchConfig`）把入口声明成检索型；页面导入会自动探测并补一个「站内检索」入口，Agent 侧照 `search_forms` 报告自己填 |
| ~~**前端来源管理界面**~~ | — | **已实现**：Phase 15 起在 `web/src/components/SourceReport.vue`（验收报告）+ 抓取页的来源下拉；Phase 16 起来源管理独立成页 `/sources`（`web/src/views/SourcesPage.vue` + `web/src/components/sources/{SourceListSection,ImportSection,RunsSection}.vue`），含单条/批量导入、启停/回滚、试运行/健康检查与接入记录——旧的 `SourceModal.vue` 已删除 |
| **来源健康巡检的定时化** | 站点改版无法自动发现 | `manage_ingestion_job(action="health")` 是**手动**触发的 |
| **文件级访问许可自动判定** | 无法自动判断「这个站允不允许转载」 | `governance.license_note` 留人工填写 |

## 4. 本次新增的工具接口定义

工具名、参数、返回、错误语义、幂等性、权限要求如下（**均已实现并跑过测试**，
测试见 `server/scripts/test_source_onboarding.py` 第 5 节）。

公共约定：

- **返回**：JSON 文本。失败一律 `{"ok": false, "error": "<中文原因>"}`，**不抛异常**；
  长结果截断并明确标注（`_MAX_CHARS = 6000`）。
- **权限**：这些工具会**发起真实网络请求**（只读 GET，遵守 robots 与限频），
  并可能**写数据库**（`register_source`、`manage_ingestion_job` 的启停）。
  不写 `policy_document` / `extraction`，不建抓取任务（`run_trial` 除外，它会显式建任务）。
- **幂等性**：`inspect_source` / `discover_content` / `analyze_content_structure` /
  `sample_content` / `diagnose_source` 只读，可重复调用；`dry_run_ingestion` 可重复
  （每次留一条接入记录，不产生正式数据）；`register_source` 幂等（配置未变 = 指纹未变 =
  不新增版本）；`manage_ingestion_job(enable/disable)` 状态收敛，可重复调用。

| 工具 | 参数 | 返回要点 | 错误类型 |
| --- | --- | --- | --- |
| `inspect_source` | `url`(必), `target`, `allowed_domains` | 结构候选、robots 情况、`other_hosts` | `kind=blocked`（robots/403/内网/白名单）或 `failed`（不可达） |
| `discover_content` | `url`(必), `keywords`, `follow`(0–2), `allowed_domains` | `entrypoints[]`（含 list_selector 与 pagination）、`errors[]` | 无入口 → `ok=false`，附每个失败页面的原因 |
| `analyze_content_structure` | `url` 或 `html`（二选一）, `listed_as` | 各类候选 + 附件 | 取数失败 → `blocked`/`failed` |
| `sample_content` | `url`(必), `count`, `selectors_json`, `list_selector`, `allowed_domains` | `rules` / `rules_confirmed` / `samples[]` / `hits` | 正文全空 → `ok=false`（规则待调整） |
| `generate_source_config` | `config_json`(必) | `ok` / `problems[]` / `fingerprint` / `summary` | JSON 语法错误、配置不可执行（逐条给修复指引） |
| `dry_run_ingestion` | `config_json`(必), `limit`, `pages`, `keywords`, `date_from`, `date_to` | `status`(passed/failed/blocked) + `criteria[]` + `details[]` + `untested[]` + `coverage_limits[]` + `attachments_probed[]` | 配置不可执行 / 装载冲突 / 检索链路异常 |
| `register_source` | `config_json`(必), `approved`, `force`, `change_note` | `source_id` / `status` / `version` / `verification` | 无通过验收 → 拒绝（须 `force` 且留痕）；落库失败 |
| `manage_ingestion_job` | `action`(必，list/detail/enable/disable/run_trial/delete/health), `key`/`source_id`, `keywords`, `max_policies` | 随 action 不同 | 来源不存在 / 未启用不能试抓 / 数据库不可用 |
| `diagnose_source` | `url` 或 `key`（二选一）, `allowed_domains` | `layers[]`（逐层 ok + 细节）、`suggestions[]` | 参数缺失 / 来源不存在 |

## 5. 测试覆盖

`server/scripts/test_source_onboarding.py`（离线，假站点 + 真实编排）：

- **正常接入**：勘察 → 取样 → 配置 → 试运行 `passed` → 注册（`--db` 时含落库）；
- **部分失败**：部分详情页 500 → 验收 `failed`，报告如实写出「取回失败」；
- **无法抓取**：403 / robots 禁止 → `blocked`，原因点明访问控制且说明不绕过；
- **人工确认**：无验收记录时拒绝启用；`force` 必须显式传；默认注册为 `draft`；
  凭据按指纹**全表**查（不设条数窗口）——来源反复导入后凭据被更晚的记录埋住仍要能启用
  （2026-10-15「手动停用的项目无法再次启用」的回归用例）；
- **工具层**：9 个工具都返回可解析 JSON、坏输入不抛异常、展示名撞车被拒。

跑法：

```bash
cd server
uv run python scripts/test_source_onboarding.py             # 纯离线
uv run python scripts/test_source_onboarding.py --db        # 追加落库用例（需已建表）
uv run python scripts/test_source_onboarding.py --db-scratch  # 自动建临时库跑落库用例，结束即删
uv run python scripts/test_source_onboarding.py --live      # 追加真实站点勘察（联网）
```
