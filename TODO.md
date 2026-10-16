# 打通三端 · 实施 Todo List

> 配套计划：[PLAN.md](PLAN.md)（背景、API 契约草案、设计稿状态清单、待确认决策点）
>
> **维护规则**：每完成一个 Phase，必须同步更新两处——① 本文档：勾选该 Phase 的任务项、改「总进度」表与各 Phase 的 `状态` 行；② 进度日志：[docs/progress/log.md](docs/progress/log.md) 追加一行（日期 / Phase / 事件 / 说明）。
>
> 明细日志已从本文档抽出，见文末「进度日志」段。

## 状态图例

| 标记 | 含义 |
|---|---|
| `⬜ 未开始` | 尚未动工 |
| `🟨 进行中` | 正在做 |
| `✅ 已完成` | 已验证通过（附验收证据） |
| `⛔ 阻塞` | 卡住，需用户决策或外部条件 |

## 总进度

| # | Phase | 状态 | 完成时间 |
|---|---|---|---|
| 1 | 后端数据访问层（地基） | 🟨 进行中（代码完成，待实机验收） | — |
| 2 | 只读查询 API | 🟨 进行中（代码完成，待实机验收） | — |
| 3 | 写操作与动作 API | 🟨 进行中（代码完成，待实机验收） | — |
| 4 | 抓取 + 提取流水线（核心） | 🟨 进行中（代码完成，待实机验收；2026-10-15 明确 `publish_date` **只放发布日期**——成文日期不再顶掉它，见下方变更块） | — |
| 5 | 进度实时推送（SSE） | 🟨 进行中（代码完成，待实机验收） | — |
| 6 | 前端接入 | 🟨 进行中（代码完成，待实机验收） | — |
| 7 | 端到端验收与文档 | 🟨 进行中（文档已对齐，端到端验收待用户执行） | — |
| 8 | 终止 / 清空与重启收尾 | 🟨 进行中（代码完成，待实机验收） | — |
| 9 | 检索结果人工确认环节 | 🟨 进行中（代码完成，待实机验收） | — |
| 10 | 成果库两页改版（成果库 + 政策详情页） | 🟨 进行中（代码完成，待实机验收） | — |
| 11 | 定时自动入库（调度器 + 弹窗管理） | 🟨 进行中（代码完成，待实机验收） | — |
| 12 | 提取模型可选（高级设置下拉 + DeepSeek 接入） | 🟨 进行中（代码完成，待实机验收） | — |
| 13 | 检索全库去重/续抓 + 入库判别（judge）+ 定时防重入 | 🟨 进行中（代码完成，待实机验收） | — |
| 14 | 网站来源接入（配置化来源 + 接入 Skill 与工具） | 🟨 进行中（离线 + 落库用例已跑通；2026-10-15 修掉 `decode_html` 三处整页乱码缺陷、改进正文解析三项、新增检索型入口与能力声明、试运行的日期命中率判据补「值恒定 = 假命中」提示，见下方变更，待实机验收） | — |
| 15 | 页面内「导入网址」入口 + 来源下拉（确定性版） | 🟨 进行中（后端完成；2026-10-15 修复两个系统性缺陷后已在真实站点跑通验收，同日增量：自动探测站内检索 + 筛选能力声明 + 日期候选跨页核对 + 启用闸门凭据改按指纹全表查（「已停用 → 再启用」不再被误拒），见下方变更块；前端待 `pnpm build`） | — |
| 16 | vue-router 全站迁移 + 来源管理独立页面（含批量导入） | 🟨 进行中（代码完成，待实机验收；离线字段核对已过，前端待 `pnpm build`） | — |

> **整体状态说明**：Phase 1–7 的代码与文档均已落地，但**全部验收标准都需要实机运行**（连库、起服务、抓 gov.cn、调大模型）。按本仓库约定（见 [CLAUDE.md](CLAUDE.md)）由用户自行编译运行，因此所有 Phase 暂记 `🟨 进行中（代码完成，待实机验收）`，**未**标 `✅`。实机跑通后请对照各 Phase 的「验收」逐条勾选并在此更新。

---

## Phase 1 — 后端数据访问层（地基）

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：后端能连上 `agent_demo` 库并读写五张表

- [x] 新增 `server/app/db/pool.py`（psycopg3 异步连接池）
- [x] 新增 `server/app/db/repositories.py`（按表的读写函数）
- [x] `app/config.py` 增加 `database_url`
- [x] `pyproject.toml` 增加 `psycopg[binary,pool]`
- [x] `server/.env.example` 增加 `DATABASE_URL`
- [x] 新增 `server/scripts/test_db.py`（连通性 + 读出 seed 数据）

**验收**：`uv run python scripts/test_db.py` 打印出 `crawl_task` 与 `v_policy_results` 的 seed 数据。

**验收方法**（待用户执行）：

```bash
cd server && uv sync          # 首次需装 psycopg
uv run python scripts/test_db.py
```

预期输出：`crawl_task` 任务（id=1 / running / 进度 6/10 · 失败 1）+ `stage` 文案；7 行 `v_policy_results`（已提取 1 / 提取中 1 / 未提取 4 / 提取失败 1）；5 条 `task_log`；第 1 篇的事实 8 条 / 要点 4 条 / 概念 2 条；末行 `OK: 数据库读写测试通过`。

> 截止本次改动只做了语法与 import 静态检查（`ast.parse` 全部通过），未运行服务或连库——运行时结论以用户实机执行为准。

---

## Phase 2 — 只读查询 API

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：前端展示所需数据全部可从接口拿到

- [x] 新增路由 `app/api/routes/tasks.py`
- [x] 新增路由 `app/api/routes/policies.py`
- [x] 新增 schema `app/schemas/task.py` / `app/schemas/policy.py`
- [x] `GET /api/tasks/current`（无任务返回 `null`）
- [x] `GET /api/tasks/{id}`
- [x] `GET /api/tasks/{id}/logs`
- [x] `GET /api/tasks/{id}/policies`（含 `summary` 四态计数）
- [x] `GET /api/policies/{id}/extraction`（三栏）
- [x] `progress` / `eta_seconds` 计算（Phase 5 已改为最终语义，见下）

**验收**：`/docs` 逐条调用，返回值形状与契约一致；空状态返回 `null`。

**字段对照预期**（seed 任务 id=1；`progress`/`eta_seconds` 已按 Phase 5 语义修正）：

| 接口 | 预期 |
|---|---|
| `GET /api/tasks/current` | `status=running`、`progress=80`、`eta_seconds=null` |
| `GET /api/tasks/1/policies` | 7 行 + `summary={total:7, extracted:1, extracting:1, pending:4, failed:1}` |
| `GET /api/policies/1/extraction` | 三栏：事实 8 条 / 摘要 + 要点 4 条 / 概念 2 + 领域 3 + 延伸 1 |
| `GET /api/policies/5/extraction` | 未提取：`status=pending`，三区皆空 |
| `GET /api/policies/6/extraction` | 失败：`status=failed`、`error="模型调用超时（60s），已自动重试 2 次"` |

> ⚠️ **本表两处已作废的旧预期**（Phase 5 修改了语义，勿再按旧值验收）：
>
> 1. `progress` 原写 `60`（按 `extracted/total`）。Phase 5 改为「抓取 `0→50` + 提取 `50→100`」：seed 行 `total=10 / fetched=10 / extracted=6 / failed=0` → `50×10/10 + 50×6/10 = 80`。
> 2. `eta_seconds` 原写 `120`（占位常量「剩余篇数 × 30s」）。Phase 5 改为按 `started_at` 外推；seed 任务的 `started_at` 距今远超 6 小时（`_MAX_ELAPSED_FOR_ETA`），故**返回 `null`**，前端不显示「预计剩余」。
>
> 另注：seed 任务的 `progress`（读 `crawl_task` 计数）与表格 `summary`（读视图行）**在样本上仍然不一致**（10/6 的行 vs 7 行的表）——这是 seed 本身的不自洽，用户已裁定不修补。**真实运行不会出现**：计数由 `refresh_task_counts()` 从实际行数派生。

**已知差异（已裁定，不作为缺陷）**：`data.ts` 里第 2、4 篇标 `extracted`，而 `seed.sql` 只给第 1、3、6 篇建了 `extraction` 行（其余为 `pending`）——即表格将显示 **1 已提取 / 1 提取中 / 4 未提取 / 1 失败**；`crawl_task.extracted_count=6` 与表内 7 行同样不自洽（seed 只落了 10 篇中的 7 篇）。

> **裁定（2026-10-12，用户）**：seed 与前端的 mock 都只是**样本**，后续以 Phase 4 抓取的**真实内容**为准，因此**不要求接口返回值与 `data.ts` 逐项相等**，也不修补 `seed.sql`。相应地，验收标准改为：**真实数据必须自洽**——见 Phase 4 的计数一致性硬约束。

---

## Phase 3 — 写操作与动作 API

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：按钮能用

- [x] `POST /api/tasks`（校验表单 → 插 `crawl_task` + 首条 `task_log` → 返回 `task_id`）
- [x] `POST /api/policies/{id}/retry`（`failed → pending`，`retry_count+1`，记日志）
- [x] `POST /api/policies/{id}/reextract`（清空旧结果 → `extracting`）
- [x] 动作接口实际执行：Phase 4 补上 `start_single_extraction()`（见下）

**验收**：创建任务后 `GET /tasks/current` 能读到；retry 后状态与 `retry_count` 正确流转。

**落库细节**：

- `POST /api/tasks` 返回 `201 {"task_id": n}`；表单校验失败（日期倒置 / 关键词超 50 字 / 非法枚举）返回 `422 {"detail": "字段: 原因"}`（已把 FastAPI 默认的错误列表压成字符串，符合 PLAN 的 `{"detail": ...}` 约定）。
- `POST /api/policies/{id}/retry` 仅 `failed` 可重试，否则 `409`；成功后 `extraction.status=pending`、`error=NULL`、`retry_count+1`，并写一条 `task_log`。
- `POST /api/policies/{id}/reextract` 用 UPSERT 原地重置 `extraction`（清空摘要/要点/概念/领域/延伸/`extracted_at`）+ 删除该篇 `fact_item`，二者在同一事务内完成；`extracting` 中的篇目返回 `409`。**`retry_count` 保留**（重试历史），响应体如实回传。
- 两个动作接口都返回 `{"id":.., "status":.., "retry_count":..}`。

**Phase 4 补记（设计缺陷修复）**：Phase 3 时两个动作接口只改数据库状态，**没有任何东西去执行**——重试后会永远停在 `pending`。Phase 4 为其补上 `start_single_extraction(task_id, policy_id)`：在后台真正重跑该篇，并在处理完后决定任务终态（无 pending/extracting 才回 `completed`，否则保持 `running` 并在 `stage` 说明还剩几篇）。

---

## Phase 4 — 抓取 + 提取流水线（核心）

**状态**：🟨 进行中（代码完成，待实机验收；2026-10-15 明确 `publish_date` 的语义，见下方变更块） ｜ **目标**：真实抓取 gov.cn 并结构化提取「事实/信息/知识」

- [x] 新增 `server/app/pipeline/crawl_graph.py`（`prepare → fetch → extract → summarize`，失败走 `fail`）
- [x] 新增 `server/app/pipeline/extract.py`（结构化输出 schema，复用 `_build_llm()`）
- [x] 单篇流程：`get_policy_fulltext` → LLM 提取 → 写 `extraction` + `fact_item` → 更新计数/stage → 写 `task_log`
- [x] 容错：单篇失败标记 `failed` 并继续；MCP 不可用则整体 `failed` + 中文提示
- [x] 由 `POST /tasks` 触发，异步执行，不阻塞请求
- [x] **计数一致性（硬约束）**：见下，已由 `refresh_task_counts()` 实现

**实现要点**：

- `fetch` 节点按关键词逐个调 `get_latest_policies`（`asyncio.wait_for` + `pipeline_mcp_timeout`），按 URL 去重后 `insert_policies`（`ON CONFLICT (task_id, source_url)` 幂等），再 `refresh_task_counts()`；篇数按 `pipeline_max_policies` 截断。
- `extract` 节点用信号量限流（`pipeline_extract_concurrency`，**默认 1**）逐篇处理；并发=1 是为了让 `stage` 的「第 N/M 篇」与日志里单条 `active` 行保持真实——调大能显著缩短墙钟时间，代价是这几处文案不再精确。
- 单篇提取：`reset_extraction()`（干净起点、可重复执行）→ 写 stage/日志 → `get_policy_fulltext` → `extract_policy()`（正文按 `pipeline_max_body_chars` 截断；先试**不强制 `tool_choice`** 的工具调用，失败回退宽松 JSON 解析）→ `save_extraction()`（单事务写三栏 + 事实明细）→ 成功/失败日志 → `finally` 里 `refresh_task_counts()`。
- 失败文案（设计稿口径）：MCP 不可用/超时 → `抓取失败：连接 China-Central-Policy-MCP 超时或异常（…），请确认本地服务已启动后重试`；检索为空 → `未检索到匹配的政策原文，请调整关键词或时间范围后重试`。
- **知识区语义（用户裁定，2026-10-12）**：知识**不是**读懂这篇文件所需的背景知识（术语解释、概念科普、政策沿革），而是**从该文件里可沉淀、能指导下一步行动的知识**——政策划了哪些线、开了哪些口子、窗口期多长、钱和资源往哪投、什么主体在什么条件下能拿到什么。提示词与 Pydantic 字段描述已按此改写；抽屉三块标题相应改为「知识点 / 适用领域 / 行动建议」。**字段键名与 jsonb 列名不变**（仍是 `concepts[{term,desc}]` / `fields` / `extension` 与 `knowledge_concepts/…`），只是语义变成「标题 / 说明」，故无需改表、无需重跑 `init.sh`。

**2026-10-15 变更（用户问「现在的日期是模型提取之后生成的吗，怎么感觉不准」）**：

- **答复：不是模型产的。** `ExtractionPayload`（facts / info / knowledge）里根本没有日期字段；`facts` 的「时间」是自由文本，留在提取结果里，不回写 `policy_document`。
- **根因：同一个 `publish_date` 被两个语义不同的日期先后写过一遍。** 列表/检索轮写**发布日期**（gov.cn 的 `ptimeStr`/`pubtimeStr`；`html_list` 的列表页 `date_selector`），抓全文那轮写 `result.date or parse_date(meta.signed_date)`——而 gov.cn 全文接口的 `date` 取的是详情页元信息表的**成文日期**（`getPolicyFulltext.ts`：成文日期 → `<time datetime>` → `.date` → 正文首个日期）。成文日期常比发布日期早几周到几个月，于是**顶掉**了列表轮的值：跨年文件被分错年份（年份筛选与年份直方图都跑在 `EXTRACT(YEAR FROM publish_date)` 上）、成果库日期在「抓完全文」前后**凭空变一天**（用户拿自己配置的日期窗口核对时看着像越界——抓取时窗口是按发布日期判的）、`ORDER BY publish_date DESC` 的排序也跟着动。
- **改法（用户裁定：`publish_date` 只放发布日期）**：`PolicySource` 新增类属性 `fulltext_date_is_publish`（默认 `True`）声明「`fetch_fulltext` 报的 `date` 算不算发布日期」，`GovCnSource` 覆写为 `False`；`_fetch_fulltext` 据此取 `result.date if source.fulltext_date_is_publish else None`，并去掉 `or parse_date(meta.signed_date)` 这个成文日期兜底。传 `None` 时 `update_policy_from_fulltext` 的 `COALESCE(%s, publish_date)` 保持原值——列表轮没给日期的行**诚实地留空**，不拿另一个语义的日期顶上。顺带删掉 `crawl_graph.py` 里因此不再使用的 `parse_date` 导入。`html_list` 来源不用改：它的详情页日期就是发布日期，默认 `True` 即对，「列表页没印日期、日期在详情页」的兜底原样保留。
- **成文日期没有丢**：仍由 `fulltext_clean` 从归档全文解析成 `FulltextMeta.signed_date`，详情页已单列展示（`LibraryDetailPage.vue`「成文日期」）。
- **前端文案同步**：`ConfirmPanel.vue` 页脚原写「（提取时会用原文回填）」——改后不再成立，改为「此处日期为检索结果给的**发布日期**，入库后保持不变；原文里的成文日期是另一个字段，在成果库详情页单独展示」。
- **镜像/文档**：`docs/architecture/data-pipeline.md`（gov.cn 段新增 `publish_date` 语义条 + 提取段点明「无日期字段」）、`docs/guides/extending.md`（新 `engine` 须声明 `fulltext_date_is_publish`）。
- **⛔ 历史数据不会自动订正**：受影响的行已把成文日期写进 `publish_date`，而**发布日期在库里没有第二份留档**（`policy_document` 不存它），无法用 SQL 反推；`insert_policies` 是 `ON CONFLICT (task_id, source_url) DO NOTHING`，重跑同一个任务也不会改写已存在的行。补救两条，均须用户执行：① 对关心的来源**建新任务重抓**（新行会拿到正确的发布日期）；② 接受旧行口径不一致，或人工订正。本次不改历史数据、不写订正脚本。
- **待实机验收**（我未运行，按仓库约定）：重启服务后跑一次 gov.cn 抓取 → 成果库详情页「发布于 X」应等于检索结果里的发布日期、不再被成文日期顶掉；同一页「成文日期」一行仍照常单独显示。

**计数一致性（硬约束，已实现）**：`refresh_task_counts()` 用 `UPDATE crawl_task ... FROM (SELECT COUNT(...) FILTER (...))` **从实际行数聚合派生**计数，不做自增：

- `total_count = fetched_count = COUNT(policy_document WHERE task_id)`；
- `extracted_count / failed_count = COUNT(extraction.status='extracted' / 'failed')`（无 extraction 行按 `pending` 计）；
- 因此同一时刻 `extracted + failed + extracting + pending = total`，且进度面板（读 `crawl_task`）与结果表格 `summary`（读视图行聚合）**必然同源一致**，seed 样本里的不自洽不会复现。
- 每处会改变 `policy_document`/`extraction` 行的写入后都紧跟一次 `refresh_task_counts()`（`fetch` / 每篇 `finally` / `summarize` / `fail` / `run_single_extraction`）。

**验收**（待用户执行）：`POST /api/tasks` 后结果表格出现**真实 gov.cn 政策**，第 1 篇抽屉三栏有真实提取内容；且进度面板计数与结果表格 `summary` 逐项相等（非近似）。

```bash
# 前置：node 可用 + China-Central-Policy-MCP 已构建 + .env 配好 LLM + server-db-1 在跑 + 已执行 init.sh
uv run python scripts/test_mcp.py     # 先确认 MCP 链路通
python main.py
# 另开终端：建任务并观察
curl -X POST http://localhost:8000/api/tasks -H "Content-Type: application/json" \
  -d '{"source":"国务院政策文件库（gov.cn）","content_type":"policy","schedule":"manual",
       "date_from":null,"date_to":null,"keywords":["人工智能","中小企业"]}'
curl -N http://localhost:8000/api/tasks/<task_id>/stream     # 或直接看前端进度面板
curl -s http://localhost:8000/api/tasks/<task_id>/policies | python -m json.tool
curl -s http://localhost:8000/api/tasks/<task_id> | python -m json.tool
# 比对：policies.summary.total == task.total_count；extracted/failed 两处相同
```

---

## Phase 5 — 进度实时推送（SSE）

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：进度面板「活」起来

- [x] `GET /api/tasks/{id}/stream`（基于 `task_log` 增量 + `crawl_task` 快照）
- [x] 事件类型：`task` / `log` / `done`（+ `error`）
- [x] `progress` 语义修正：抓取 `0→50`，提取 `50→100`
- [x] `eta_seconds` 用已完成篇目均值外推

**实现要点**：

- 订阅时**先补发全部历史日志**（从 id 最小开始）再推增量——前端可以只依赖本接口，不必先拉 `/logs`。
- 事件形状：`{"type":"task","task":{…}}`（快照变化才推）、`{"type":"log","log":{…}}`、`{"type":"done","status":…,"task":{…}}`（终态后关闭连接）、`{"type":"error","message":…}`；沿用 `/api/chat/stream` 的 `data: {...}\n\n` 格式，带 `X-Accel-Buffering: no`。
- 轮询 `0.6s`，每 `15s` 发一条 `: ping` 注释行保活；客户端断开按 `CancelledError` 处理，流内异常以 `error` 事件下发而非 HTTP 错误。
- **`progress` 修正**：`pending→0`；`completed→100`；运行中 `min(99, 50×fetched/total + 50×(extracted+failed)/total)`——提取阶段分子含 `failed`（失败篇目也算处理完，否则进度条永远差一截），封顶 99 避免「满了还在跑」。
- **`eta_seconds` 外推**：`elapsed / 已完成篇数 × 剩余篇数`；`done=0`、`done≥total` 或 `elapsed > 6h`（`_MAX_ELAPSED_FOR_ETA`）时返回 `null`，前端隐藏该段。

**验收**（待用户执行）：订阅后日志逐条出现、进度条推进、结束时收到 `done`。

```bash
curl -N http://localhost:8000/api/tasks/1/stream      # 直接看事件流；或在浏览器里点「开始抓取」看进度面板
```

---

## Phase 6 — 前端接入

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：`data.ts` 退场，组件由接口驱动

- [x] 新增 `web/src/api/client.ts`（fetch 封装 + SSE）、`web/src/api/types.ts`
- [x] `vite.config.ts` 加 `/api` → `http://localhost:8000` 代理
- [x] `ConfigPanel` → 受控表单，关键词可增删，JSON 导入回填，提交供 `POST /tasks` 使用
- [x] `TopBar` → 「开始抓取」调 `POST /tasks`；状态标签读 `status`
- [x] `ProgressPanel` → 订阅 SSE，实现未开始/进行中/完成/失败 四态
- [x] `ResultTable` → 接口数据 + `summary` + 失败重试；空状态 + 骨架屏
- [x] `DetailDrawer` → 按 id 拉详情；三栏骨架屏、单栏失败/空内容、重新提取
- [x] 新增 `web/src/store.ts`（模块级状态：任务/日志/结果/表单/Toast）
- [x] **删除 `web/src/data.ts`**（已无任何引用）

**实现要点**：

- 状态放模块级 `store.ts`（不引 Pinia）：`task/logs/policies/summary/form/toast` + `bootstrap()/startTask()/subscribe()/retryPolicy()/reextractPolicy()/loadExtraction()`。
- `bootstrap()` 在 `App.vue` 挂载时执行：拉 `/tasks/current`，有任务就并行拉日志与结果，若 `status=running` 自动订阅 SSE。
- SSE 订阅**先清空本地日志**（服务端会补发全部历史），只在计数字段变化时才重拉结果表格，避免每帧快照都打一次接口；收到 `done`/`error` 主动关闭 `EventSource`（防止其自动重连）。
- 错误统一走 `ApiError.message`（后端 `detail`）并弹全局 Toast，抓取失败/连接中断的 Toast 带「重试」按钮。
- 空状态三态：无任务 / 正在抓取（`fetched_count===0`）/ 抓完但 0 条——文案各不相同。
- 表单默认时间范围为 `null`（不限）而非设计稿里的固定 2025-06 区间，否则今天真实抓取会返回空。
- `progress` 直接读接口值；`eta_seconds=null` 时进度面板不显示「预计剩余」。

**有意偏差（记录备查）**：

1. 设计稿「三栏 · 加载中 = 骨架屏」。实现上：**接口拉取期间**显示骨架屏；而 `status=extracting`（大模型正在提取，可能持续数十秒）显示「正在提取…」状态块而非常驻骨架屏——常驻骨架屏会让人误以为页面一直在加载。
2. `TopBar` 把设计稿的「排队中」用于 `pending`（原稿只有四态标签，未区分 pending 文案）。

**验收**（待用户执行）：点「开始抓取」→ 进度条与日志实时更新 → 表格填充 → 点行进抽屉看到三栏 → 重新提取可用；全程无硬编码数据。

```bash
cd web && pnpm install && pnpm dev     # 需同时起后端（8000）
```

> 说明：本次未运行 `pnpm build` / `vue-tsc`（按仓库约定不主动构建），前端**尚未做过类型检查**。组件与 store 的 import/导出已逐一核对，但仍可能有 TS 类型细节需在 `pnpm build` 时修正。

---

## Phase 7 — 端到端验收与文档

**状态**：🟨 进行中（文档已对齐，端到端验收待用户执行） ｜ **目标**：按设计稿状态清单全量验收 + 文档对齐

- [x] 更新 `CLAUDE.md`（补 `db/` `pipeline/` 业务 API 章节；修正 `source_catalog` 过时描述）
- [x] 更新 `server/README.md`（补任务 API 表、SSE 事件、进度语义、目录结构）
- [x] 更新 `database/README.md`（改为「后端已接入」，说明 seed 与真实数据的差异）
- [x] 更新根 `README.md`（目录结构、API 章节、前端状态、扩展指引）
- [ ] 按 [PLAN.md](PLAN.md) 第二节关键状态清单逐条实机验收（下表，待用户执行）

### 关键状态清单 → 实现映射（对照 [PLAN.md](PLAN.md) §2.2）

| 区域 | 状态 | 设计稿文案 / 表现 | 实现位置 | 需实机确认 |
|---|---|---|---|---|
| 进度条 | 未开始 | `0%` | `schemas/task._progress`：`pending → 0` | 建任务后立即订阅 |
| 进度条 | 进行中 | `60% · 正在提取第 3/10 篇` | `_progress` + 流水线写入的 `stage`（`正在调用大模型提取：第 N/M 篇 ——《…》`） | 观察进度推进 |
| 进度条 | 已完成 | `100% · 已完成` | `_progress`：`completed → 100`；`summarize_node` 写 stage `已完成` | 任务跑完 |
| 进度条 | 失败 | `失败 · 连接超时，请重试` | `_progress` + `fail_node`/`run_pipeline` 写入的中文 `stage` | 停掉 MCP 或断网触发 |
| 结果行 | ×4 | 未提取 / 提取中 / 已提取 / 提取失败 | `v_policy_results.status` 四态 → `ResultTable` 四种 pill | 观察 |
| 空状态 | 尚未抓取 | 「还没有抓取结果」+ 引导文案 | `/tasks/current → null` → `ResultTable.emptyState` | 清库后开页面 |
| 三栏 | 加载中 | 骨架屏 | `DetailDrawer` 拉取期间 skeleton（`extracting` 态另见「有意偏差 1」） | 点开一行 |
| 全局 | 抓取失败 + Toast + 重试 | 「抓取失败：…请确认本地服务已启动后重试」 | `fail_node` stage → SSE `done(failed)` → `store.onDone` 弹 Toast + 重试 | 停 MCP 后抓取 |
| 单栏 | 提取失败 + 重试 | 「…请点击重试」 | `extraction.error` + `DetailDrawer` 失败态 → `POST /retry` | 造一篇 failed |
| 单栏 | 空内容 | 「暂无提取内容」 | `ExtractionOut.is_empty` 对应前端 `emptyContent` | 需构造 |

**验收**：上表逐条实机通过；文档无过时描述。

---

## Phase 8 — 终止 / 清空与重启收尾

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：不再被上一次未跑完的任务卡住，可终止、可清空、可重新开始

**背景**：上个进程被强杀（或 Ctrl-C 两次）后，`crawl_task` 里会残留一条 `running` 的任务——它的 asyncio 流水线已随进程消失，但 `GET /tasks/current` 仍返回它，前端于是**永远显示「运行中」**；页面上既没有终止按钮也没有清空入口，数据清不掉、也无法干净地重新开始。

- [x] `crawl_graph._running` 由 `set[Task]` 改为 `dict[task_id, set[Task]]`（`_track()` 登记，结束自动摘除）
- [x] 新增 `cancel_pipeline(task_id)` / `cancel_all_pipelines()`；`shutdown_pipelines()` 复用之
- [x] 新增仓储函数 `delete_task()` / `delete_all_tasks()`（走外键 `ON DELETE CASCADE`）、`fail_stale_tasks(reason)`
- [x] 新增 `POST /api/tasks/{id}/cancel`（终止）、`DELETE /api/tasks/{id}`（删单个）、`DELETE /api/tasks`（清空）
- [x] `lifespan` 启动时 `fail_stale_tasks()`：把上次遗留的 `pending`/`running` 任务落成 `failed`（库不可用只告警）
- [x] 前端：`TopBar` 加「终止」「清空」按钮（清空为二次确认）；`store` 加 `cancelTask()` / `clearAll()`、`canCancel`、`stopping` / `clearing`
- [x] 新增图标 `square`（终止）、`trash-2`（清空）；`api/client.ts` 加 `cancelTask` / `clearTasks`，`types.ts` 加 `TaskDeleted`
- [x] 文档：`server/README.md`（任务 API 表 + 「终止 / 清空 / 重启收尾」小节）、根 `README.md`、`CLAUDE.md`

**设计取舍**：

- **终止后复用 `failed` 状态**（`stage` = 「任务已手动终止」），**不新增 `task_status` 枚举值** —— 新增枚举要改 `schema.sql` 并让用户重跑脚本（`init.sh` 还会顺带重新灌 seed），而「清空」按钮本就能把数据清干净。代价是进度面板按「失败」色板显示终止态；如需中性色的「已终止」，再考虑加枚举（`cancelled`）。
- **取消必须先 `await` 后台任务退出再落终态**：`run_pipeline` 的 `CancelledError` 分支会写「服务重启导致任务中断」，由接口在其后覆盖成终止文案。
- **清空必须先 `cancel_all_pipelines()`**：否则被取消前流水线仍会往已删除的任务里写日志与计数。

**验收**（待用户执行）：

```bash
# 1) 终止：起一个真实抓取，中途点「终止」→ 按钮消失、状态转「失败 · 任务已手动终止」、日志多一条终止记录
curl -X POST http://localhost:8000/api/tasks/<id>/cancel
curl -s http://localhost:8000/api/tasks/<id> | python -m json.tool   # status=failed / stage=任务已手动终止
curl -i -X POST http://localhost:8000/api/tasks/<id>/cancel          # 再次调用 → 409

# 2) 清空：应返回 {"deleted": n}，随后 /tasks/current 返回 null（前端回到「尚未抓取」空状态）
curl -X DELETE http://localhost:8000/api/tasks
curl -s http://localhost:8000/api/tasks/current                       # null

# 3) 重启收尾：抓取进行中直接 Ctrl-C（强杀）→ 重新起服务 → 该任务的 status 应为 failed、stage 为「服务重启导致任务中断，请重新开始抓取」
```

**2026-10-15 变更（用户报「成果库页清空按钮灰色点不动」）**：

- **症状**：成果库页顶栏「清空」按钮**永久置灰**（`TopBar.vue`）。
- **根因**：按钮写的是 `:disabled="!task || clearing"`。`task` 只是浏览器端 store 里的「**最近一次抓取任务**」，而成果库是**跨任务沉淀**的——清空一次、或由试运行 / 定时任务 / 另一个标签页起的任务（`SourceListSection.vue` 的 `trialSource()` 就只调接口 + Toast，从不写 store）都不会同步过来，于是 **store 里的 `task` 为 null/stale，库里却还有数据**，按钮就再也点不动了。服务端其实不存在这种状态：`policy_document.task_id` 非空且 `ON DELETE CASCADE`，库里非空就必有一条 `crawl_task`。
- **修法**：`:disabled="!task || clearing"` → `:disabled="clearing"` —— 只留 `clearing`（清空是**全库重置** `DELETE /api/tasks`，本就不该绑定任何单个任务）。空库点击也是安全的：后端返回 `deleted: 0`，前端提示「暂无可清空的数据」。
- **未跑构建**（按约定由用户运行）→ **待实机验收**。

---

## Phase 9 — 检索结果人工确认环节

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：检索完先摊开候选篇目，由用户确认要抓哪些，再开始提取

**背景**（用户提出）：关键词 + 时间范围检索出来的结果**相关度偏低、日期也常常不对**（MCP 的 `get_latest_policies` 并不严格按时间范围过滤），直接整批丢给大模型提取等于白烧 token。因此把流水线拆成「检索（自动）→ 人工确认 → 提取（自动）」两段；若近 N 天确实没有相关政策，检索段 0 结果即直接落 `failed`，`stage` 写「未检索到匹配的政策原文，请调整关键词或时间范围后重试」。

**已确认的设计选择**（2026-10-12，用户）：

| # | 决策点 | 采纳 |
|---|---|---|
| 1 | 确认粒度 | **逐篇勾选**，未勾选的**直接丢弃**（不保留「已跳过」记录） |
| 2 | 时间不符 | **标注提示**（超出所选范围的打「超出范围」标记、无日期的打「无日期」），由用户自行决定，不改检索逻辑 |
| 3 | 中间状态 | **新增 `awaiting_confirmation` 枚举值**（而非复用 `running` + stage 文案） |
| 4 | 界面位置 | **主区域新增面板** `ConfirmPanel`（不弹窗），确认期间取代结果表 |

- [x] `database/schema.sql`：`task_status` 加第 5 档 `awaiting_confirmation` + 幂等 `ALTER TYPE … ADD VALUE IF NOT EXISTS`
- [x] `schemas/task.py`：`TaskStatus` 加新值、新增 `TaskConfirm` 请求体、`_progress` 注释说明「待确认 = 50%」
- [x] `db/repositories.py`：新增 `await_task_confirmation()` / `discard_unselected_policies()`
- [x] `pipeline/crawl_graph.py`：第一段图改为 `prepare → fetch → (await_confirm | fail)`；新增 `await_confirm_node`、`start_extraction()` / `run_extraction_phase()`（第二段直接调用 `extract_node` / `summarize_node`）
- [x] `api/routes/tasks.py`：新增 `POST /api/tasks/{id}/confirm`
- [x] 前端：`types.ts`（`TaskStatus` + `TaskConfirmPayload`）、`client.ts`（`confirmTask`）、`store.ts`（`confirming` 阶段 / `confirming` 标志 / `confirmSelection()` / `canCancel` 含待确认态）
- [x] 前端：新增 `components/ConfirmPanel.vue`（勾选列表 + 全选/全不选 + 时间范围标记 + 确认抓取/放弃）
- [x] 前端：`App.vue`（确认期间用面板取代结果表）、`ProgressPanel`/`TopBar` 加「待确认」标签、`TopBar` 的按钮在待确认态改叫「放弃」
- [x] 文档：`CLAUDE.md`（两段流水线图 + 新接口 + 确认状态说明）、`server/README.md`（接口表 + 「检索结果确认」小节 + progress 语义）

**实现要点**：

- **为什么拆两段**：LangGraph 图**没有 checkpointer**，`PipelineState` 只活在单次 `ainvoke` 的内存里，暂停后无法恢复。好在检索结果此时已落在 `policy_document`，所以第一段跑完直接结束；用户确认后 `run_extraction_phase()` 从库里读回篇目再跑尾段。`extract_node` / `summarize_node` 是纯函数式的 `dict → dict`（内部只写库、不读图状态），因此直接调用而不必再包一层图。
- **确认接口先同步置 `running` 再丢后台**：否则响应体与 SSE 会先闪一下「待确认」再跳「运行中」。
- **丢弃 = 删行**：未勾选的 `policy_document` 行直接 `DELETE`（它们只是检索噪声），删完立刻 `refresh_task_counts()`，保证 `total_count` 与实际篇目数一致（Phase 4 的计数硬约束）。
- **重启收尾不碰这一档**：`fail_stale_tasks()` 只扫 `pending`/`running`；停在「待确认」的任务候选篇目都在库里，重启后照样能确认。
- **日期口径**：面板里显示的 `publish_date` 是**检索结果给的**日期，真实日期要等提取时 `get_policy_fulltext` 回填（`update_policy_from_fulltext`）——面板页脚已注明，避免用户误以为它是权威日期。
- **放弃本次抓取**：复用既有的取消链路（`POST /tasks/{id}/cancel` → `failed` + `stage`「任务已手动终止」），不新增枚举。

**验收**（待用户执行）：

```bash
# 前置：老库需先补枚举值（幂等，不重置数据）
database/init.sh          # 或单独执行：
# docker exec -i server-db-1 psql -U kos -d agent_demo -c \
#   "ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'awaiting_confirmation';"

curl -X POST http://localhost:8000/api/tasks -H "Content-Type: application/json" \
  -d '{"source":"国务院政策文件库（gov.cn）","content_type":"policy","schedule":"manual",
       "date_from":"2026-10-05","date_to":"2026-10-12","keywords":["人工智能"]}'
curl -s http://localhost:8000/api/tasks/current | python -m json.tool    # status=awaiting_confirmation, progress=50
curl -s http://localhost:8000/api/tasks/<id>/policies | python -m json.tool  # 候选篇目（含 publish_date）

# 只确认前两篇（其余应被丢弃）
curl -X POST http://localhost:8000/api/tasks/<id>/confirm -H "Content-Type: application/json" \
  -d '{"policy_ids":[<pid1>,<pid2>]}'
curl -s http://localhost:8000/api/tasks/<id> | python -m json.tool   # status=running / total_count=2
curl -X POST http://localhost:8000/api/tasks/<id>/confirm -H "Content-Type: application/json" -d '{"policy_ids":[]}'  # 非待确认态 → 409

# 前端：点「开始抓取」→ 进度停在 50%「待确认」→ 出现勾选面板（超出时间范围的篇目带红色标记）
#       → 取消勾选若干 → 点「确认抓取（N 篇）」→ 面板消失、结果表出现并逐篇提取
#       → 或点「放弃本次抓取」→ 任务转「失败 · 任务已手动终止」
```

预期：确认后结果表只剩勾选的篇目（`summary.total` == `task.total_count`），进度从 50% 继续推进到 100%；未勾选的篇目在库里被删除。

### 官方检索 API 参数对照（实测记录）

> 已抽出到 **[docs/architecture/gov-cn-search-quirks.md](docs/architecture/gov-cn-search-quirks.md)** —— 官网筛选 ↔ API 参数对照表、通道能力互斥表、`gongbao` 不受筛选、0 命中静默退回全量浏览等**一手实测证据**都在那里。改检索逻辑前先读它。

---

## Phase 10 — 成果库两页改版

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：产品改为两页结构——「抓取任务」页布局功能不变，新增「成果库」页（跨任务沉淀 + 筛选 + 分页）与「政策详情页」（三栏 + 政策正文存档）

**背景**（用户提出，2026-10-12）：整体设计见 `design.pen` 画板「成果库 展示页 / 政策详情页」及数据说明便签。核心语义：成果库**仅展示提取成功**的篇目、**跨抓取任务持久化**（重新执行任务不影响已入库数据）。

- [x] `database/schema.sql`：`policy_document` 幂等补 `fulltext TEXT` 列 + `publish_date` 索引（老库重跑 `init.sh` 即可）
- [x] 提取阶段把正文存档（`update_policy_from_fulltext` 顺带落 `fulltext`，空正文不覆盖旧存档）
- [x] `repositories.py`：`fetch_library_policies` / `count_library_policies` / `fetch_library_meta`——按 `source_url` `DISTINCT ON` 去重（最新任务行胜出），文种由文号前缀推导
- [x] `schemas/policy.py`：`LibraryPolicyItem` / `LibraryListResponse` / `LibraryMeta` / `LibraryPolicyDetail`
- [x] `routes/policies.py`：`GET /api/policies/meta`、`GET /api/policies`（keyword/agency/doc_type/year + 分页，默认每页 6）、`GET /api/policies/{id}`（注册顺序：meta/列表在 `/{id}` 之前）
- [x] 前端：`store.ts` 增加 `view`（crawl/library/library-detail）与成果库状态/动作；`TopBar` 加页面切换胶囊（抓取操作仅 crawl 页显示）；新增 `LibraryPage` / `LibraryDetailPage` / `SelectMenu`，三栏抽为共享组件 `ExtractionColumns`（`DetailDrawer` 复用，抽屉行为不变）
- [x] 「重新提取」提交后轮询详情直到终态；旧数据无正文时详情页显示友好空态

**验收**（待用户执行）：

1. `cd server && python main.py` + `cd web && pnpm dev`；抓取页完整跑一遍「新建任务 → 确认 → 提取」，行为与改版前一致（顶栏仅多切换器与「已入库 N 篇」）。
2. 成果库：只含提取成功篇目；机构/文种/年份/关键词筛选与分页联动；同一 URL 重复抓取任务后库内仍只有一条。
3. 详情页：三栏与抽屉一致；「查看原文」跳新标签；「重新提取」后计数自动刷新；「展开全文」显示存档正文（改版前的旧数据应显示空态文案而非报错）。
4. `pnpm build` 通过类型检查。

> 截止本次改动只做了 Python 侧 import 检查与语法检查（全部通过），未运行服务/前端构建——运行时结论以用户实机执行为准。

---

## Phase 11 — 定时自动入库（调度器 + 弹窗管理）

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：抓取配置可选「一次性入库 / 定时自动入库」；定时任务以弹窗卡片管理；触发时自动全流程入库

- [x] 用户裁定四点：① 触发时**自动全选、直接跑完**（跳过人工确认环节）；② 周期用**预设档位**（每 N 分钟 / 每 N 小时 / 每天 / 每周）；③ **落库 + 重启自动恢复**调度；④ 「时间范围」须解决**固定窗口 + 上限 N + 最新优先 = 永远只抓到最新 N 篇**的问题
- [x] 窗口方案：「**滑动游标 + 跨轮去重**」——`cursor_before` 为下轮窗口上界；本轮**抓满上限** → 游标推进到「本轮最旧一篇发布日期 − 1 天」向更早回填；**没抓满**（窗口掏空）→ 游标回 NULL，下轮回到 `[date_from, 今天]` 追新；每轮检索时把本任务历史轮次已入库的 `source_url` 从候选剔除，剔空记 `noop`（正常跳过，不留失败任务、不重复入库）
- [x] `schema.sql`：`schedule_job` 表（配置快照 + 调度参数 + 游标 + 运行记录）；`crawl_task.schedule_job_id` 外键（`ON DELETE SET NULL`，删定时任务保留历史抓取）；`schedule_type` 枚举补 `minutely`/`hourly`；全部幂等（老库重跑 `init.sh` 即可，不重置数据）
- [x] `app/scheduler.py`：进程内调度循环（15 s tick，先认领 `next_run_at` 再执行，防同 tick 重触发）；`compute_next_run`（本地时间口径，weekly 对齐 Python `weekday()` 0=周一）；`run_schedule_job`（检索 → 建任务 → 自动确认 → `run_extraction_phase` 直接提取 → 游标推进 / 结果落库）
- [x] `app/api/routes/schedules.py`：`GET/POST /api/schedules`、`POST /{id}/toggle`、`POST /{id}/run`（立即运行）、`DELETE /{id}`；`app/schemas/schedule.py` 校验（档位必填项、文种白名单等）
- [x] lifespan：启动时 `start_scheduler()`（补排缺失 `next_run_at` 的启用任务）→ 关停 `shutdown_scheduler()`
- [x] `_search_policies` 增 `exclude_urls` 跨轮去重参数
- [x] 前端：`ConfigPanel` 加「入库方式」（一次性 / 定时自动入库）；`TopBar` 加「定时任务」入口（启用数徽标），定时模式下主按钮变「定时入库」开弹窗；新增 `ScheduleModal.vue`（遮罩层 + 居中卡片：列表视图 启停/立即运行/删除/新建，新建视图 周期档位 + 条件输入 + 当前表单快照摘要）；`store` 增 `ingestMode` / 弹窗状态 / `loadSchedules` / `createSchedule` / `toggleScheduleJob` / `deleteScheduleJob` / `runScheduleNow`；`types.ts` / `client.ts` 同步
- [x] 静态检查：Python 6 文件 `py_compile` 全通过；`compute_next_run` 8 条断言通过（分钟/小时/每天未到点/每天已过点/每周未到/每周已过/当天已过）；`ScheduleCreate` 合法 + 4 组非法档位组合校验通过；前端未跑 `vue-tsc`（按约定不主动构建）

**验收**（待用户执行）：

```bash
cd database && bash init.sh     # 幂等补 schedule_job 表 / 新列 / 新枚举值（不重置数据）
# 重启 server 与 web
```

1. 抓取配置选「定时自动入库」→ 顶栏点「定时入库」→ 弹窗选「每 15 分钟」创建 → 列表出现该任务，「下次触发」≈ 15 分钟后
2. 列表点「立即运行」→ 抓取进度面板出现新任务并全流程跑完（无人工确认环节）；日志含「定时任务触发」「自动确认全部篇目」
3. 再次「立即运行」：若窗口内无新发文 → 任务列表该任务显示「无新增」，且不产生新任务/重复数据
4. 上限 3 + 长窗口回填验证：连续点「立即运行」若干次，应逐轮向更早日期推进（9 月抓满 3 篇后，下轮抓 8 月的），而不是反复抓同一批
5. 停用开关 → 「下次触发」变「已停用」，不再触发；再启用恢复排程；服务重启后调度自动恢复
6. `GET /api/schedules` 返回列表形状与 DTO 一致

---

## Phase 12 — 提取模型可选（高级设置下拉 + DeepSeek 接入）

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：抓取配置「高级设置」里按任务选择提取用大模型；模型在根目录 `.env` 注册（Kimi 网关 + DeepSeek），第一项为默认

- [x] 模型注册表：`.env` 新增 `PIPELINE_MODELS`（JSON 数组：`id`/`label`/`model`，可选 `base_url`/`api_key_env`/`max_tokens`/`thinking`）+ `DEEPSEEK_API_KEY`；`config.py` 解析为 `ModelRegistryEntry` 列表（`get_model_registry()`，解析失败/为空回退单模型并告警，不让服务起不来）
- [x] 统一 Anthropic 兼容协议（用户裁定，2026-10-13）：注册表全部走 `ChatAnthropic` 一条路径——不填 `base_url`/`api_key_env` 的项复用 `ANTHROPIC_*` 网关（Kimi Bearer，只换模型名，chat agent 不受影响）；DeepSeek 项填 `base_url=https://api.deepseek.com/anthropic` + `api_key_env=DEEPSEEK_API_KEY`（标准 x-api-key 认证）。**不再需要 `langchain-openai`**（已从 `pyproject.toml` 移除，无新依赖要装）
- [x] 思考默认关闭（用户要求「把思考默认都关上」）：注册表项 `thinking` 默认 `false` → `_build_llm(thinking=False)` 发 `thinking={"type": "disabled"}`（Anthropic 协议统一参数）；项里写 `"thinking": true` 才交给服务端默认。已知风险（Phase 4 探针记录）：kimi 网关在 thinking 关闭 + **强制** tool_choice 时返回 0 次工具调用——提取路径本就不强制 tool_choice，若仍遇 0 调用会自动落 JSON 文本回退，最坏是每篇多一次请求，不会失败
- [x] 热加载（用户要求「改 .env 无需重启」）：`config.py` 在 `load_dotenv` **之前**快照 `_REAL_ENV`，`_live_env()` 按「真实 shell 环境变量 > 当前 .env 文件 > 启动时灌入 os.environ 的旧值」解析——`get_model_registry()`（去掉 `@lru_cache`，每次调用热读）与 `resolve_model_api_key()`（换 key 也即时生效）都走它；前端「高级设置」**展开时重拉** `/api/config/pipeline`，选中项已被 .env 删掉时自动吸回默认
- [x] 落库与透传：`crawl_task` / `schedule_job` 各加 `pipeline_model TEXT`（幂等 ALTER，重跑 `init.sh` 补列）；TaskCreate/TaskDTO/ScheduleCreate/ScheduleJobDTO/两条 create 路由/`scheduler.run_schedule_job`/`run_extraction_phase`/`run_pipeline` 全链路透传；提取段从任务行重读（与其它 `pipeline_*` 同一套路）；单篇重试/重新提取也尊重任务行的模型
- [x] 校验：`pipeline_model` 必须在注册表 id 里（与文种白名单同一模式）；运行时 id 失效（.env 后来改过）→ 告警回退默认模型，不失败
- [x] `extraction.model` 现在记**实际使用**的模型名（如 `deepseek-flash`），任务级溯源真实可用
- [x] 配置接口：`GET /api/config/pipeline` 追加 `models: [{id,label}]` + `default_model`（不含密钥）
- [x] 前端：`PipelineConfig`/`TaskCreatePayload`/`TaskDTO`/`ScheduleCreatePayload`/`ScheduleJobDTO` 同步加字段；`store` 表单加 `pipeline_model`（bootstrap 回填默认、恢复默认重置、随任务/定时快照提交）；`ConfigPanel` 高级设置第一行加下拉框（选项来自 `/api/config/pipeline`）
- [x] 静态检查：Python 改动文件 `py_compile` 通过；`.venv` 实测 `get_model_registry()` 热读 `.env` 解析出三条目、`resolve_model_entry` 未知 id 回退默认、`thinking` 默认 False、`DEEPSEEK_API_KEY` 可热读；前端未跑 `vue-tsc`（按约定不主动构建）

**验收**（待用户执行）：

```bash
cd ../database && bash init.sh    # 幂等补 pipeline_model 列（不重置数据）；无新 Python 依赖，不用 uv sync
# 重启 server 与 web（首次部署 Phase 12 才需要重启；之后改 .env 注册表无需重启）
```

1. 高级设置展开 → 「提取用大模型」下拉显示「Kimi 网关（默认）/ DeepSeek Flash / DeepSeek V4 Pro」，默认选中第一项
2. 选 DeepSeek Flash 建任务跑完 → 结果抽屉里「模型」显示 `deepseek-flash`，提取内容正常
3. 选 Kimi（或不改）建任务 → 行为与改动前一致（`extraction.model` = `kimi-for-coding`）
4. **热加载**：服务运行中改 `.env` 的 `PIPELINE_MODELS`（如临时删掉一个条目）→ 重新展开高级设置，下拉即时反映（被删的选中项吸回默认），建任务直接可用，全程不重启
5. 定时任务弹窗创建时快照当前表单的模型选择，触发轮次用它提取
6. `GET /api/config/pipeline` 返回 `models` + `default_model`；故意把 `pipeline_model` 提交成不存在的 id → 422 `未知提取模型：…`

---

## Phase 13 — 检索全库去重/续抓 + 入库判别（judge）+ 定时防重入

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：① 抓取先查成果库——新增篇目正常展示，已在库的置灰沉底、默认不勾选，**续抓到 N 篇新增**为止；② 接入外部判别服务（/v1/systemone），批复等无用件打「不建议入库」标签（手动仅提示 / 定时自动剔除 / 服务不可达 fail-open）；③ 堵上「立即运行」与定时轮次并发的缺口

- [ ] MCP `get_latest_policies` 加 `page` 参数（透传 gov.cn `p`，此前固定第一页）；**需在 `tools/China-Central-Policy-MCP` 下 `npm run build`**（未构建 = 静默无效）；（2026-10-13 补修：请求 `n` 由固定 100 改为 = limit——gov.cn 的 `p` 按 `n` 步长翻页，固定 100 + 事后截前 limit 条会让 limit=10 时第 11–100 行整段跳过）
- [ ] `_search_policies` 翻页续查：`pipeline_max_search_calls` 改为**总调用预算**（旧版是对组合数截断，预算语义等价）；停止条件 = 凑够 `target_new` 篇**建议入库的新增**（判别在循环内逐页做，被标注的不占名额，见 2026-10-13 判别修正）/ 预算耗尽 / 全组合翻空 / **整轮零收获**（连 merged 都没进新链接=翻页失效或翻空；已在库的行算收获——重跑时第 1 页全在库**不会**停，继续翻页凑新增，见 2026-10-13 缺陷修复）；`target_new=None` 时只跑一轮，与历史行为逐字节一致；①~④复核过滤与 `exclude_urls` 语义原样保留
- [ ] 全库去重：`fetch_library_urls()`（全部任务提取成功的 DISTINCT source_url）；手动任务三分区各自封顶（建议入库凑满 cap / 判别不建议仍落库供面板提示 / 已在库**也落库**置灰展示默认不勾、不参与判别）；定时任务把全库 URL 并入跨轮去重集（修复「换任务重复提取同一篇」）
- [ ] 入库判别：`app/pipeline/judge.py`（httpx 移入主依赖、信号量并发 4、超时 5s、`trust_env=False` 防内网地址被代理劫持、**fail-open 不抛异常**）；契约 `questions.q1(choice) + state(仅标题 中文文本做语言路由，双轴矩阵实测机关/文号会带偏)`，取 `answers.q1.choice/probabilities/answer_confidence`；判别在 `_search_policies` 检索循环内**逐页**执行（结果写回 item → `_to_policy_row` 落库），被标注的不占 `target_new` 名额；`policy_document.judge_tagged/judge_prob` 列（幂等 ALTER）+ `v_policy_results` 追加判别列与 `in_library`（EXISTS 排除本任务自己的行，防提取中途全表变「已在库」）+ 单列索引 `idx_policy_document_url_only`；DTO/types.ts 镜像；手动=仅提示不影响勾选，定时=自动剔除（直接用带回的标注）并留日志、游标按「抓满 cap 篇**建议入库**」推进
- [ ] 定时防重入：`_running_jobs` 进程内闸（`run_schedule_job` 拆出 `_run_schedule_job_locked`，外层 try/finally 保证闸必释放——提前 return 路径不泄漏）；「立即运行」运行中 → 409「已有一轮在执行中」；慢轮次 + 短间隔 = 跑完立刻补一轮（`next_run_at` 锚定轮次开始时刻，连续追赶不堆积）已在 scheduler docstring 记录
- [ ] 前端 `ConfirmPanel`：已在库与判别「不建议入库」的行统一置灰（`.dim`）沉底（三档稳定排序：建议入库 → 不建议入库 → 已在库）默认不勾（`watch(policies)` 补种 `deselected`，`userToggled` 集合保证不覆盖用户手动切换，**新行默认选中的原约定不破坏**）；「已在库」「不建议入库」标记；头部计数「建议入库 X · 不建议入库 Y · 已在库 Z」（2026-10-13 需求调整：「不建议入库」从仅提示改为与已在库同款沉底置灰默认不勾）
- [ ] 配置：`JUDGE_*` Settings 字段（启动时读取，改 .env 需重启）+ `.env.example` 小节 + `PIPELINE_MAX_SEARCH_CALLS` 文档（续抓建议调大至 24-36）；`scripts/test_judge.py` 冒烟（**实测 state 契约**：若服务要求对象而非字符串会表现为全部失败，改 `_judge_one` 一行即可）
- [x] 静态检查：Python 改动 8 文件 `py_compile` 全通过；前端未跑 `vue-tsc`（按约定不主动构建）

**验收**（待用户执行）：

```bash
cd server && uv sync                                # httpx 移入主依赖（uv.lock 已含，只重新分组）
cd ../database && bash init.sh                      # 幂等补 judge 列 + 换视图，不重置数据
cd ../tools/China-Central-Policy-MCP && npm run build   # 必须！page 参数不构建 = 静默无效
cd ../../web && pnpm build                          # vue-tsc 校验新字段
cd ../server && uv run python scripts/test_judge.py     # 判别契约实测（含 fail-open：把 JUDGE_URL 指向不存在的端口重跑 → WARN + 全 tagged=False）
# 重启 python main.py（JUDGE_* 启动时读取）
```

1. **手动混合**：用已完成任务的同一关键词重跑 → 确认表格建议入库在上（默认勾选）、判别「不建议入库」与已在库置灰沉底（默认不勾，可手动勾选）；头部「共 N 篇（建议入库 X · 不建议入库 Y · 已在库 Z）· 已选 M 篇」；默认确认 → 只提取建议入库的新增篇目；勾选灰行 → 强制重提取
2. **续抓**：`.env` 设 `PIPELINE_PER_KEYWORD_LIMIT=3`、`PIPELINE_MAX_SEARCH_CALLS=30`、`PIPELINE_MAX_POLICIES=10`，宽关键词 → 日志显示多轮直到凑够 10 篇新增（或预算耗尽/整轮零新增停）
3. **定时**：分钟级任务运行中点「立即运行」→ toast 409；轮次耗时 > 间隔 → 跑完立刻补一轮（单轮串行）；其它任务已入库的不再重复提取（「跨轮/全库去重」日志）；批复类被剔除（「判别剔除 N 篇」日志）
4. **判别宕机**：错误 `JUDGE_URL` 重启 → 全部保留 + 告警日志 + 任务日志「按建议入库处理（fail-open）」

---

## Phase 14 — 网站来源接入（配置化来源 + 接入 Skill 与工具）

**状态**：🟨 进行中（离线 + 落库用例已跑通 91/0/0；2026-10-15 修掉 `decode_html` 三处整页乱码缺陷、改进正文解析三项（条目链接 / 容器兜底 / 表格），并新增 `entrypoints[].search` 检索型入口 + 能力声明，同日又给试运行的日期命中率判据补上「抽样值恒定 = 假命中」的提示，见下方变更，待实机验收） ｜ **目标**：新增一个网站来源变成**一次可配置、可验证、可追溯的接入任务**，而不是每次从零写爬虫——Agent 能自己勘察站点、取样、生成配置、试运行验收、注册启用

**交付物**（对应需求方列出的 6 项）：

- [x] **(1) Skill 定义 + 参考文档**：`.claude/skills/website-source-onboarding/SKILL.md`（七阶段流程 + 7 条硬性规则）+ `references/{tool-map,config-schema,acceptance-criteria}.md`
- [x] **(2) Skill ↔ 已有工具映射**：`references/tool-map.md` 第 1、2 节（按阶段列出调用链与复用到的既有代码）
- [x] **(3) 缺失能力清单 + 新增工具接口定义**：同文件第 3、4 节（缺失能力 8 项逐项说明；9 个新工具的参数/返回/错误/权限/幂等）
- [x] **(4) 配置 schema + 完整示例**：`app/sources/config.py`（Pydantic）+ `references/config-schema.md`（字段表 + 验收测试真正跑过的那份配置）
- [x] **(5) 可执行验收标准**：`references/acceptance-criteria.md`（5 条硬判据 + 3 条软判据 + 未测试项 + 覆盖范围限制，每条带阈值与实测值）
- [x] **(6) 自动化测试**（正常 / 部分失败 / 无法抓取 / 人工确认）：`server/scripts/test_source_onboarding.py`

**新增代码**：

- [x] `app/sources/`（9 个模块）：`net.py`（受控 HTTP：公网校验、重定向逐跳复验、域名白名单、robots、同域限频、并发闸、体积截断）、`htmlparse.py`（**纯标准库**迷你 DOM + CSS 子集选择器，拒绝伪类/兄弟组合子，编码嗅探：UTF-8 优先 → 声明/GB18030 → 取替换字符最少的一次）、`base.py`（`PolicySource` 契约）、`html_source.py`（配置驱动的通用源 `HtmlListSource`）、`config.py`（`SourceConfig` + 校验 + 指纹）、`analyze.py`（结构候选打分）、`registry.py`（内置 + 配置化来源注册表 + `load_configured_from_db`）、`gov_cn.py`、`util.py`
- [x] `app/services/onboarding.py`：勘察 / 发现 / 结构分析 / 取样 / 生成配置 / **试运行验收**（调**正式**的 `_search_policies`，不另写一套浅层检查）/ 注册 / 运维 / 分层诊断
- [x] `app/agent/onboarding_tools.py`：9 个 `@tool`（`inspect_source` / `discover_content` / `analyze_content_structure` / `sample_content` / `generate_source_config` / `dry_run_ingestion` / `register_source` / `manage_ingestion_job` / `diagnose_source`）；建议清单里的 `validate_ingestion` **有意并入** `dry_run_ingestion`（验收与试运行是同一次执行的产物，拆开会留下「两次结论不一致」的空档），另加 `diagnose_source` 承担第 7 阶段诊断
- [x] `app/agent/tools.py`：`ONBOARDING_TOOLS` 并入 `TOOLS`（顶层导入不成环——本地工具是静态注册表，与 MCP 的启动期加载无关）
- [x] `app/main.py`：lifespan 增加 `load_configured_from_db()`（库不可用/表未建只告警，服务照常启动）
- [x] `app/config.py`：11 个 `SOURCE_*` 设置（取数超时/体积/跳数/并发/限频/robots/UA/内网/列表缓存/附件目录/单次附件上限）
- [x] `database/schema.sql`：4 张表（`content_source` / `content_source_revision` / `source_onboarding_run` / `source_artifact`）+ 3 个枚举（`source_level` / `content_source_status` / `onboarding_status`）+ 1 个 `updated_at` 触发器；全部 `IF NOT EXISTS` / 幂等，老库重跑 `init.sh` 即可，**不重置数据**
- [x] `app/db/repositories.py`：来源 CRUD + 版本历史 + 接入留痕 + 附件登记 + `fetch_library_urls()`
- [x] `.gitignore`：`server/.artifacts/` 不入库；**`.claude/*` + `!.claude/skills/`**（Skill 是交付物必须入库，`worktrees/` 仍忽略——注意不能写成 `.claude/`，整目录排除会让 `!` 规则失效）
- [x] `.env.example`：`SOURCE_*` 小节

**关键设计决定**：

- **试运行跑的是正式链路**：`dry_run_ingestion` 临时装载配置后调用生产用的 `_search_policies`（扇出预算 / 翻页 / `validate_item` / judge / 正文预检全都在），并**留一条 `source_onboarding_run`**。刻意不为验收另写一套浅层检查——那会给出与正式抓取不一致的结论。
- **未验证不得报成功**：`register_source(approved=True)` 要求存在**指纹一致**的通过验收；`force=True` 必须由用户明确同意，并把「（未经验收强制启用，人工拍板）」写进变更历史。默认注册一律 `draft`。
- **`blocked` ≠ `failed`**：robots 禁止 / 401 / 403 / 需登录 / 内网地址 → `blocked`（站点或环境不允许，**不绕过**）；配置写错 → `failed`。把两者混起来会误导用户去改配置。
- **报告必须带「没验证什么」**：每次验收都返回 `untested[]`（样本 < 3 篇 → 正文结论标「初步」等）与 `coverage_limits[]`（未覆盖全站存量、未验证 JS 渲染/登录后内容、附件未做正文抽取等）。
- **附件探测范围与报告严格一致**：`attachments_probed` 列的就是**实际发起过探测**的那几个（上限 `min(3, SOURCE_MAX_ARTIFACTS_PER_RUN)`），不是「发现的前 3 个」。
- **配置指纹只认影响取数的字段**：改 `notes` / 许可说明 / 对接人不产生新版本，重复注册不刷版本记录。

**验收**：

```bash
cd server && uv sync
# 1) 离线用例（假站点 + 真实编排：SSRF 校验、解析、判别、验收判定全跑真的）
uv run python scripts/test_source_onboarding.py
# 2) 追加落库用例——需先建表（见下方「阻塞」）
uv run python scripts/test_source_onboarding.py --db-scratch   # 自动建临时库、跑完即删，不动 agent_demo
# 3) 追加真实站点勘察（联网）
uv run python scripts/test_source_onboarding.py --live
```

**已执行的验证**（2026-10-15，本机）：

- `--db-scratch` → **通过 91 项 / 失败 0 项 / 跳过 0 项**（离线段 87/0/4 跳过；落库段自动建 `agent_demo_onboarding_test` → 应用 `database/schema.sql` → 跑用例 → 用完即删）。
- 落库留痕实测：`source_onboarding_run` 写入 dry_run 通过 2 / 失败 1 / 受阻 1、inspect 受阻 1；`source_artifact` 5 行 / 3 个不同 sha256；跑完 `content_source` 已清空、`content_source_revision` 随外键级联删除。
- `database/schema.sql` 另在**真实库事务内**（`BEGIN … ROLLBACK`）验证可应用（PG 16.15），未改动 `agent_demo` 任何数据。
- 期间修掉两个生产缺陷：① `build_list_url(entry, 1)` 用翻页模板反推第 1 页（真实站点第 1 页常无页码 → 404），改为**第 1 页就是入口地址本身**；② `drop_selectors` 在附件扫描**之前**执行，把附件区一起摘掉 → 附件永远扫不到，改为先扫附件再剔除。

**阻塞**：

- ⛔ **`agent_demo` 库还没有 Phase 14 的 4 张表**（现有仅 `crawl_task` / `policy_document` / `extraction` / `fact_item` / `task_log` / `schedule_job`）。**需用户执行 `cd database && bash init.sh`**（幂等，不重置数据）后，服务端才能装载来源配置、落库用例才能对着 `agent_demo` 跑。按仓库约定（不主动跑 `docker exec ... psql`）我未代为执行。
- ⚠️ **文件冲突风险**：另一会话的工作树 `.claude/worktrees/phase14-agent-ingest-tools` 定义了另一组 Phase 14 工具（`search_policies` / `confirm_and_extract` / `get_task_status` / `create_schedule` / `list_schedules` / `manage_schedule`）。**工具名与我的 9 个不冲突**，但两边改的是**同一批文件**（`.env.example`、`TODO.md`、`database/schema.sql`、`app/db/repositories.py`、`app/agent/tools.py`、`app/config.py`、`app/schemas/task.py`）→ 合并时会撞车，需人工取舍。

**未实现（已知边界，已在 `references/tool-map.md` 第 3 节列明）**：JS 动态渲染（需无头浏览器）、PDF/OFD 文字抽取、OCR、RSS/官方 API 专用引擎、来源管理前端界面、健康巡检定时化、访问许可自动判定。

**2026-10-15 变更（用户报「有些文章爬回来正文会显示乱码」，来源为 `html_list` 配置化来源）**：

- **症状**：配置化来源抓回的文章正文整段乱码，形态是「阿拉伯字母 + 拉丁扩展字母 + `�`」混合（如 `ح�ƴ…`）；同站点**只有一部分**文章中招。
- **根因**（`app/sources/htmlparse.py` 的 `decode_html`，旧实现一句话概括就是「按候选顺序取第一个解不报错的」——**凡是无条件信任声明或兜底的做法，都会让整页（不是个别字）静默变成乱码**）：
  1. **末位兜底 `raw.decode("utf-8", errors="replace")`**：GBK 页面里只要有一个 `gb18030` 也解不了的字节（最典型是单字节 `0x80`「€」，其次 `0xFF` / `0xFE`）就会走到这里，整页变成 `�` 与拉丁扩展字母的混合体（实测正文 38 处 U+FFFD，且**不可还原**）——正是用户看到的那一类；
  2. **声明 gbk/gb2312、内容其实早已迁到 UTF-8 的老页面**（改了页面没改 meta）：`gb18030` 能否「成功」解码**取决于正文长度**（实测 35 种长度里 15 种落这条），于是同一站点只有一部分文章乱码，且是「涓枃」式汉字乱码，静默不报错；
  3. **反向的「UTF-8 页面混进个别脏字节」**：utf-8 严格解失败后照样落到 `gb18030`，若那段字节恰好合法就整页变汉字乱码。
- **修法**（`decode_html` 重写判定口径为「**谁能解出中文就用谁**」）：① `_utf8_ish()` 优先——坏字节占比 ≤ `max(2, len*0.005)` **且**解出了 CJK 才认 UTF-8（`gb18030` / `latin-1` 几乎能解任意字节，「解通了」本身不算证据，而真 GBK 按 UTF-8 解会有三成以上字节编不出来，阈值不敏感）；② 否则按声明（响应头 → meta）严格解、再退 `gb18030`；③ 全部解不通时取**替换字符最少**的那一次宽容解码（只坏那一个字节）。另把 `iso-8859-1` 别名改写到 `utf-8`（它**永远不会解码失败**，留着等于「声明 latin-1 的一律按它解」，而中文站误标 latin-1 很常见）。源码里用 `_REPLACEMENT = chr(0xFFFD)` 而非字面量，保持纯 ASCII。
- **回归用例**（`server/scripts/test_source_onboarding.py`，3 组 5 项检查）：① UTF-8 正文 + meta 标 gb2312；② GBK 页面在「关于加强」前插入单字节 `0x80`（附「插入确实发生」的自检，避免 `replace` 落空导致用例空过）；③ UTF-8 页面把一个字节改成 `0xFF` 并补足页面长度，使比例容差真正生效。
- **未跑构建 / 未连库**（按约定由用户运行）→ **待实机验收**：`cd server && python scripts/test_source_onboarding.py`（首段离线，其余段需 DB / 联网）；并重抓此前乱码的那篇 html_list 文章，正文应为正常中文。
- **已知未修**（与本次报修无关，仅记录）：node 侧 MCP `tools/China-Central-Policy-MCP/src/tools/getPolicyFulltext.ts` 的 axios 取数没有 charset 处理，只影响 gov.cn 路径；实测 gov.cn 全站（含 2008/2010/2014 年旧页与 `http://` 公报）均为 UTF-8、0 处 U+FFFD，故**不是**本次乱码的成因。如需补，改后须 `npm run build`（否则是静默空操作）。

**2026-10-15 变更（正文解析三项改进：条目链接 / 容器兜底 / 表格）**：

- **背景**：上一轮修的是「整页乱码」（正确性），本轮处理**内容不全 / 不齐**的三处（用户指定做这三项）。
- **① 列表条目不再取「第一个链接」**（`html_source._pick_item_link`）：一个 `<li>` 里常不止一个 `<a>`（标题 + 缩略图 +「更多」），旧逻辑取文档序第一个——缩略图链接没有文本、`title_selector` 也命中它 → 标题为空 → **整条被静默丢掉**。新口径按可靠性排序：先取「文本 == 已解析标题」的链接，否则取文本最长者（标题链接几乎总是最长的一段文字，缩略图链接无文本、「更多」只有两字）；同口径下按文档序取第一个，结果稳定可复现。
- **② 正文容器全落空时改用结构打分兜底**（`html_source._fallback_container`）：旧行为是直接判该页失败（`DetailResult.body` 为空 → `fetch_fulltext` 抛错、整页 0 结果）。改为复用 `analyze.detail_candidates` 打分挑一块——该候选链**已在候选阶段排除 `<html>` / `<body>`**，所以这仍**不是**「退化成整页文本」（那条铁律没破），只是把「零结果」变成「有结果但标注未经确认」。门槛 `_FALLBACK_MIN_CHARS = 200`（与 `DetailConfig.min_body_chars` 同口径），不达标仍旧判失败。**留痕**：`DetailResult.warnings` 写明「已用结构启发式兜底选中「X」，该段正文未经确认」；接入报告（`sample_content` 行的 `warnings`）**本来就带**该字段，另在 `HtmlListSource.fetch_fulltext` 补一条 `logger.warning`——否则「已降级」在正式抓取链路上完全不可见（成功路径此前不上报 warnings）。
- **③ 表格单元格不再各占一行**（`htmlparse.node_text`）：`<td>`/`<th>` 属块级，旧逻辑给每个单元格补 `\n`，`<tr><td>序号</td><td>项目</td></tr>` 出来是「序号\n项目」→ 政策附表的行列关系全丢。改为单元格之间补 `\t`、行之间才补 `\n`；单元格内部再出现的块级标签不再补换行（`<td><p>a</p></td>` 仍算一个单元格）。**实现细节**：不能直接塞 `\t`——`_SPACE_RE` 会把制表符并成空格，所以先用 US（`\x1f`）占位穿过空白收敛与 `str.strip()`，最后一步再换回 `\t`。
- **配套**：`analyze.Candidate` 增加 `node` 字段（`repr=False, compare=False`，**不进 `as_dict`**）——兜底需要拿到节点本身，因为 `describe_selector` 可能生成 `:nth-of-type`，而选择器引擎不支持伪类、拿选择器回头再查会落空。镜像同步：`config.py` 的 `DetailConfig` docstring、`.claude/skills/website-source-onboarding/references/config-schema.md`；`services/onboarding.py` 的空正文告警文案补上「启发式兜底也未找到达标正文」。
- **回归用例**（`server/scripts/test_source_onboarding.py`，新增 5 项 + 改写 1 项）：多条链接仍取到条目、且选中的是标题链接（2 项）；表格同行制表符 / 行间换行（1 项）；容器全落空但页面另有一块足够长的正文 → 兜底选中（1 项）且带「未经确认」告警（1 项）；原「容器全落空 → 正文为空」改写为「落空且兜底不达标 → 仍为空」，以贴合新语义。
- **未跑构建 / 未连库**（按约定由用户运行）→ **待实机验收**：`cd server && python scripts/test_source_onboarding.py`。

**已知边界（本轮未做）**：① **容器命中即定稿**——配置的选择器若命中一块短侧栏（非空但很短），仍会采信、不会继续试后面的候选；本轮只处理「全部选择器都落空」这一种情形；② `_clean_body` 的行式 junk 过滤会把「含 `(` 且无中文句末标点的表格行」整行丢弃，这是**既有**行为，本轮未动（改动会波及已校准的入库预检）；③ 附件（PDF / DOCX）仍只登记不抽正文，正文为空时不会回落到附件。

---

## Phase 15 — 页面内「导入网址」入口 + 来源下拉（确定性版）

**状态**：🟨 进行中（后端完成；**2026-10-15 修掉两个让入口「完全用不了」的系统性缺陷后，已在真实站点上跑通**；同日增量：导入时**自动探测站内检索**（GET 表单真出条目才加检索型入口）+ 筛选能力声明 + **日期候选跨页核对**（恒定值当页脚/模板元素丢弃）+ **启用闸门的验收凭据改为按指纹全表查**（原来只在「该来源最近 20 条记录」里找，「已停用 → 再启用」会被误拒，见下方变更块）；前端代码完成，`pnpm build` 待用户执行） ｜ **目标**：页面**不经过对话**就能把一个新网站接进来——输入栏目地址 → 后台跑「勘察 → 内容发现 → 生成配置 → 试运行验收」并流式推进度 → 摊开验收报告 → 人工点「启用」；启用后该来源出现在抓取页的来源下拉里，可当流水线的源

**范围决定**（用户已确认）：先做**确定性入口**（不经过大模型，取勘察报告里得分最高的候选组装配置），把手工流程跑通，Agent 驱动的接入面板留到后面接；候选可切换重跑；进度用后台作业 + SSE；本次一起做后端 API + 前端向导 + 来源下拉。

**新增代码**：

- [x] `app/schemas/source.py`：`SourceImportRequest`（地址 / 目标 / 关键词 / 试抓篇数与页数 / 候选重跑的 `base_config`+`candidate`）、`SourceImportJobDTO`（含 `report` / `stage_reports` / `registered` / `error_kind`）、`SourceDTO` / `SourceListResponse` / `SourceDetailDTO` / `SourceEnableRequest`（`SourceRollbackRequest` 已随回滚功能移除，见下方 2026-10-15 变更）
- [x] `app/services/source_import.py`：**确定性编排**（`inspect_source` → `discover_content` → `generate_config` → `dry_run_ingestion` → `register_source`）+ 进程内作业表（`JOBS`，`get_job` / `cancel_job`）+ 三条硬规则（见下）
- [x] `app/api/routes/sources.py`：10 条路由——`GET /api/sources`、`POST /api/sources/import`、`GET /api/sources/import/{job_id}`、`/cancel`、`/stream`（SSE，与任务流同格式）、`GET /api/sources/{id}`、`/runs`、`POST /{id}/enable|disable`、`DELETE /{id}`；`/import/*` 静态段注册在 `/{source_id}` **之前**（原第 11 条 `POST /{id}/rollback` 已随回滚功能移除，见下方 2026-10-15 变更）
- [x] `app/main.py`：`include_router(sources.router)`
- [x] `app/services/onboarding.py`：只加了一个公开函数 `verify_source_config()`（把内部的 `_latest_passing_run` 暴露出来给「验没验收过」的展示用，避免别处再抄一遍判定规则）——**其余函数签名一律未动**
- [x] `app/api/routes/filters.py` + `app/schemas/task.py`：`source` 查询参数；配置化来源走另一条口径（发布机构 = 入口/栏目名，无文种）；响应新增 `FacetSupports`（前端据此**隐藏**不支持的筛选项）与 `source_label`
- [x] `web/src/api/{types,client}.ts`：来源相关类型镜像 + 10 个 API 方法；把 SSE 传输层抽成内部 `subscribeSse()`（`subscribeTaskStream` 行为不变，新增 `subscribeImportStream`）
- [x] `web/src/store.ts`：`sources` / `selectableSources`（内置 + `active`）/ `currentSource` / `sourceSupportsDocTypes` / `loadSources()`（含**当前选中项失效时回退内置默认**）/ `setSource()`（切来源清空机构与文种并重拉名单）/ 弹窗开关与视图
- [x] `web/src/components/SourceModal.vue`：两视图（来源列表 + 导入向导）；向导三态（输入 → 进行中 SSE → 验收报告）
- [x] `web/src/components/SourceReport.vue`：验收报告渲染（状态徽章 / 判据表 / 逐篇正文实测 / **未测试项** / **覆盖范围限制**），列表页看历史验收与向导最后一屏共用
- [x] `web/src/components/ConfigPanel.vue`：新增「抓取来源」字段（复用 `SelectMenu`，`showCount=false` + `clearable=false`），配置化来源下**隐藏「文种」**并显示口径说明；`web/src/components/TopBar.vue` 加「来源管理」入口；`App.vue` 挂载弹窗与 Esc 处理
- [x] `SelectMenu.vue`：新增 `showCount` / `clearable` 两个可选 prop（默认值保持成果库筛选的语义不变）

**关键设计决定**：

- **编排单独一个模块，生产代码一行不改**：能力在 `services/onboarding.py`，编排在 `services/source_import.py`，Agent 工具（`onboarding_tools.py`）与 HTTP 路由调的是**同一段代码**——页面上跑出来的结论与对话里跑出来的结论一致。代价：拿不到试运行**内部**的「第 N/M 篇」粒度（只给一句「正在试运行」），好处是零回归风险。
- **确定性 = 不经过大模型**：列表容器按「同签名兄弟数 × 平均文本」打分、正文容器按「文本量 / 链接密度 / 常见 id-class」加权，直接取最高分组装配置，其余候选列进报告供人点选重跑。人只做最后那一下审批。
- **只有 `active` 才进进程内注册表**：试运行会临时装载新配置，失败时自己摘掉；若就此收手，一次失败的重复导入会**静默停掉一个生产中的来源**（到重启才恢复）。所以 `_register_after_dry_run` 在「验收不通过且该来源本是 active」时**把原配置放回注册表**，并在失败登记为草案后调 `_sync_registry` 摘掉草案——口径与重启后的 `load_configured_from_db()`（只装载 active）严格一致，避免「不重启能选、重启就没了」。
- **启用仍是一道人的闸**：`/enable` 复用 `register_source(approved=True, force=...)`，与 Agent 工具**同一道门**；没有指纹一致的通过验收一律 409；`force=true` 才放行，且把「未经验收强制启用」写进变更说明（前端是**二次确认**按钮）。
- **`blocked` 与 `failed` 全程分开**：作业的 `error_kind`、`phase_text`、日志级别、报告徽章文案四处都分开；`blocked` 明确写「站点或环境不允许，未绕过访问控制」。报告里的 `untested[]` / `coverage_limits[]` **必须可见**，不许折进「通过」。
- **白名单由服务端从地址推导**（`_host_domains`：只取主机本身与去掉 `www.` 的同一主机），**不接受页面传入**，也不放宽到兄弟子域；跨主机跳转直接报「不在来源白名单」。
- **地址在建作业之前先过 `net.check_url`**（公网 http/https、非内网、非云元数据）——不通过直接 400，**不会有作业被建出来**；另有并发上限 3 与 5 秒启动冷却（纯进程内，不引依赖）。
- **作业状态只活在进程内**：持久留痕在 `source_onboarding_run`（现有表，本次未改），服务重启丢的只是进度条，草案来源已落库。

**验收**（后端已执行，前端待用户）：

```bash
cd database && bash init.sh        # 若还没跑过：建 Phase 14 的 4 张表
cd ../server && python main.py     # 起服务（/docs 看 Swagger）
cd ../web && pnpm build            # vue-tsc 类型检查 + 构建
pnpm dev
```

**已执行的验证**（2026-10-15，本机）：

- `server/app/**` 全部改动文件 `py_compile` 通过。
- `app.openapi()` 列出 **11 条** `/api/sources*` 路径（`/import/*` 均在 `/{source_id}` 之前注册）；路由顺序的坑与 `policies.py` 的 `/meta` 同一处理。
- **安全用例**：`start_import` 对 7 个恶意/非法地址（`http://127.0.0.1:8000/x`、`http://169.254.169.254/`、`file:///etc/passwd`、`http://localhost/`、内网网段、非 http(s) 等）**7/7 全部拒绝**，且 `JOBS` 里**没有**作业被建出来。
- **回归基线**：`uv run python scripts/test_source_onboarding.py --db-scratch` → **通过 91 项 / 失败 0 项 / 跳过 0 项**（本次未改 `services/onboarding.py` 与 `sources/`，仅新增一个公开函数）。
- **前端未跑 `vue-tsc`**（按仓库约定不代跑构建）：类型对齐靠逐字段核对 `web/src/api/types.ts` 与 `app/schemas/source.py`。

**2026-10-15 缺陷修复：用户报「不管导入啥网站都取不到内容，连国务院政策文件库都显示不通过」**

用真实站点复现（不是推测）后定位到**两个都在 `services/source_import.py` 里的系统性缺陷**——都不是网络问题、也不是阈值问题：

1. **候选只按分数盲取**。分析器给的是**结构候选**（「一堆同签名的兄弟节点」），页脚／导航／友情链接块完全符合这个形状，在没有真列表的页面上往往是**唯一**候选。
   实测 `www.gov.cn/zhengce/zuixin/`：最高分候选是 `div.footer_r.fr > div.fl`（国务院客户端的下载块），样本条目是 `app.www.gov.cn/download/Chinese.html` → 整次导入死在「样本详情页取回失败」，页面看到的就是「什么都取不到」。
   **修**：`_pick_candidate` 改成**逐个候选真抓它自己的样本详情页**（`_rank_candidates` 仅把「样本与栏目页同主机」的排前面，是排序不是过滤），能定出正文容器且正文不像空壳的才算数；`_sample_detail_analysis` 用与 `sample_content` 同一口径的最低门槛（CJK ≥ 100 且句末 ≥ 2，`_SAMPLE_MIN_CJK`）挡掉页脚/下载页；每条被否掉的候选把原因写进 `rejected_reason`（**「没试」和「试了不行」分得清**），失败的作业也会把候选清单透出（过去 `job.candidates` 只在成功路径赋值，失败时页面只剩一句错误）。
2. **选择器没有收敛到引擎能执行的子集**——分析器会给带 `:nth-of-type(n)` 的选择器（`analyze.py` 写的是「降权但保留」），而 `validate_source_config` 会因**一个**不可用选择器拒绝**整份配置**。
   实测 `www.stats.gov.cn/sj/zxfb/`：日期候选只有 `div.detail-title-des > h2:nth-of-type(1)` 与 `… > p`，两条都不可用 → 每次导入都在「配置校验」整体失败，页面看到的就是「不管导什么站都不通过」。
   **修**：`_usable_selector` 把每个组装出来的选择器收敛一次——能去掉位置伪类就用去掉后的（与分析器对列表候选的做法一致），去不掉就**留空并在日志里说明**，绝不臆造替代写法；`_detail_from_analysis` / `_meta_selectors` 逐条过这道关。

**真实验证**（本机联网，2026-10-15）：

| 站点 | 结果 | 说明 |
| --- | --- | --- |
| `www.stats.gov.cn/sj/zxfb/` | ✅ 验收通过（3/3 正文，CJK 2284/2782/2730） | 修复前在这里整份配置被拒；现在日期选择器降级为去掉伪类的写法并生效 |
| `www.moe.gov.cn/jyb_xwfb/s5147/` | ✅ 验收通过 | 候选 #0（`#list > li`，含「查阅全部专栏」导航项）被**试过后否掉**（样本 37 字 0 句末），自动改用 #1 的 `li.moe_list_group_line` |
| `www.mof.gov.cn/zhengwuxinxi/zhengcefabu/` | ⚠️ 验收未通过（正文完整性 1/3） | 选中的入口混进了子栏目页 → 报告如实判失败，登记为草案；换 #2 候选（`div.xwfb_listerji > ul.xwfb_listbox`）重跑是下一步动作 |
| `www.gov.cn/zhengce/zuixin/` | ⚠️ 未通过（已说清原因） | 该页**静态 HTML 里没有条目链接**（11 个 `content_*.htm` 全是 2016/2023 年的页脚链接）→ 列表是 JS 渲染的，属已知能力边界；现在报的是逐条候选原因 + 「只解析静态 HTML」的说明，而不是一句「取回失败」 |
| `www.gov.cn/zhengce/zhengceku/` | ⛔ blocked（正确） | 该栏目页对非浏览器客户端**恒返回 403**（curl 默认 UA、我们的 UA、Chrome UA 三种都一样）→ 按规范不绕过，如实报「站点拒绝访问」 |

**复测证据（同一站点、修前修后各一遍；两次都不是本会话发起的抓取）**：`www.gd.gov.cn` 政策法规库在同一台机器上有两组 `source_onboarding_run` 记录——修复前 03:22–03:28 的三次试运行**全部 failed**（`列表发现`/`检索链路产出` 5 条都过，**`正文完整性` 0/3**、日期与机构命中率 0%，正是「候选按分数盲取 → 选中块里没有正文」的形状）；修复后（用户运行中的服务已随文件保存热重载）03:47:34、03:48:06、03:51:16 的三次试运行**全部 passed**（`正文完整性` 3/3、日期与机构命中率 100%）。同一站点、同一入口、同一个工具，唯一变量是那两处修复 → 这是本次故障与修复最直接的对照证据。

**claude 澄清（避免误判）**：gov.cn **不需要**走「导入网址」——它在来源下拉里已是内置来源（`builtin=true, status=active, verified=true`），导入路径是给**别的站**用的。另：`npm run build` 与 `database/init.sh` 两条既有阻塞不变。

**新增回归用例**：`server/scripts/test_source_import.py`（离线，**29 项**，与 `test_source_onboarding.py` 共用假站点设施）——覆盖「选择器收敛」「详情规则组装不被伪类整份否掉」「入队前地址校验」「分数更高的页脚块挡不住真列表」「**没有 `base_config` 的候选重跑**」五节；其中第 4 节就是这次故障的**形状复刻**（诱饵块 8 条 vs 真列表 4 条，诱饵分数更高），第 5 节守住「失败页上的候选清单点得动」。跑法：`cd server && uv run python scripts/test_source_import.py` → **通过 29 / 失败 0 / 跳过 0**。

**2026-10-15 增量：非 gov.cn 来源的筛选能力（用户问「除了 gov.cn 外的源不适配日期/机构/关键词筛选怎么办」→ 用户选定做 A + D）**

问题定位（不是猜）：栏目列表页**没有检索能力**。没有检索入口时，关键词只能拿**已经翻到的那些页**逐条比对标题、时间范围只能逐条比对列表页上印的日期 → 召回上限 = `max_pages × 每页条数`，更早/更深的文件永远碰不到。gov.cn 之所以「适配」，是因为它走的是内置的**服务端检索接口**（MCP），不是被筛选项特殊照顾。

- **A：`entrypoints[].search`（`SearchConfig`）= 把筛选推给站点自己的检索页**（`config.py` / `html_source.py`）
  - 字段：`keyword_param`（照实测的表单字段名填）、`date_from_param` / `date_to_param`（**站点检索页确实接受日期参数时才填**）、`date_format`、`fixed_params`（TRS 系的 `channelid` 等隐藏参数）。
  - `build_entry_url()` 是**唯一分流口**（检索型走检索页、栏目型走列表页），`HtmlListSource.search()` 与**所有接入探针**都用它——「试运行过了、正式抓取取不到」这类最难查的分歧从结构上消掉。
  - 三条纪律都是机械的：① `url` 必须是**实测取过、真出条目**的检索页（不猜 `/search`）；② `list_selector` 用**结果页自己的**结构（通常和栏目页不同）；③ `method=post` 的表单、`action` 为 null（JS 拼地址）的检索框，当前引擎支持不了 → **如实报出来，不硬配**。
  - 校验：`keyword_param` 全空格会拼出 `?%20%20=…`（地址看着正常、永远不命中）→ 入口上直接拦下；`date_format` 用「渲染 2000-01-02 必须同时出现 2000/01/02」判定（`strftime` 对写错的指令是**原样输出**而不是报错，认格式串不可靠）。
  - 两个边界：**无关键词时检索入口不取数**（空关键词的检索页是空结果页，不是浏览路径）；`pagination.kind="none"` 时第 2 页返回 `None`——否则会在站点不认 `page=2` 时把第 1 页原样重取一遍，全是重复 URL、组合永不进入「翻到底」，白白烧掉检索预算（本轮新发现并修掉）。**`validate_item` 的关键词/日期复核对两类入口都保留**：站内检索同样分词，服务端命中 ≠ 相关。
- **D：筛选能力声明（机读，不靠嘴说）**：`config.capability_summary()`（= `SourceConfig.capabilities()` / `summary()["filters"]`）对关键词 / 日期 / 机构 / 文种逐项回答「服务端 or 客户端」。它接受**裸 dict**（老库 `content_source.config` 可能没有新字段，来源列表不能因此 500）。
  - `base.py` 给 `PolicySource` 的默认值是「全部服务端」（对 gov.cn 正确）→ **新引擎必须覆写**。
  - `api/routes/filters.py` 改用 `source.filter_capabilities()` 组面板，并把检索型入口从「发布机构」名单里剔除（它们不是机构）。
  - 前端：`SourceDTO` 新增 `keyword_server_side` / `date_server_side` / `search_entrypoints`；抓取页 `ConfigPanel` 在有话要说时多渲染一行 `store.sourceFilterNote`，明确告诉用户筛选**在哪一层**生效（而不是让人以为结果已经是全量）。
- **A 的自动落点（页面导入）**：`source_import._maybe_add_search_entry()` 在选候选之后运行，用勘察报告里**已经有**的 `search_forms`（`analyze.search_forms()` 早就在报，只是此前没人用）→ 用**运行时同一个** `build_search_url` 拼地址**真取一页** → 用结果页自己的结构解析，**解析出条目才补一个名为「站内检索」的入口**，否则不加、并写清原因。日志把「没发现表单」「是 POST」「地址由 JS 拼」「主机不在白名单」「取回了但解析不出条目」「试过都没条目」逐条分开（「没试」与「试了不行」分得清）。`_rebuild_from_candidate` 在候选重跑时保留已声明的检索型入口。
- **验收口径同步分开**：判据 1「列表发现」**只算栏目入口**（一个 0 命中的检索入口不得把「每个栏目都取到条目」判死，那会让人去改一份其实是对的配置）；检索型入口单独成**判据 1b**，**只有用了用户自己的关键词时才是硬判据**，用兜底探测词（`SEARCH_PROBE_KEYWORD`，「通知」）时是软的并写进 `untested[]`；`entrypoints[].probe_keyword` 记录实际用的词；没有可用关键词的检索入口记为**未探测**（而不是「0 条」——拿没带关键词的检索页去探测会得出「这个入口坏了」的错误结论）；健康检查同样报 `reachable: None` + 原因，不伪造可达。
- **镜像同步**：`.claude/skills/website-source-onboarding/`（`SKILL.md` 阶段 2/4 的检索入口写法与三条纪律、`references/config-schema.md` 的 `### search` 字段表 + 示例第二入口、`references/acceptance-criteria.md` 的判据 1b 与探测词规则、`references/tool-map.md` 里那条已解决的缺口）与 `docs/architecture/source-onboarding.md`。
- **回归用例**：`test_source_onboarding.py` 新增（离线）`SearchConfig` 校验（`date_format` 两例 + 空 `keyword_param`）、`capability_summary`（含旧配置/结构不全的裸 dict 不炸）、检索地址生成（固定参数与关键词、无关键词 → None、单页第 2 页 → None、声明翻页时保留参数、日期按 `date_format` 渲染、`build_entry_url` 分流），以及**假站点 + 假检索接口**上的试运行 4b 一节（判据 1b 硬/软两种、0 命中不连累判据 1）；`test_source_import.py` 新增两节（GET 表单被采纳为「站内检索」且用结果页自己的选择器；POST / JS 拼地址 / 0 命中的检索页分别如实跳过）。假站点设施新增 `query_pages`（`add_query`，检索接口按**路径**分派、查询串作参数——与真实检索页同形）与 `build_search_site(method=…, result=…)`。
- **待实机验收**（我未运行，按仓库约定）：`cd server && uv run python scripts/test_source_onboarding.py` 与 `uv run python scripts/test_source_import.py`（预期全绿；新增用例数待实跑确认，**不预填数字**）。

**2026-10-15 缺陷修复：用户报「通过其他来源进来的日期就是不对，一直都是 3 月 20 日」**：

- **根因：日期候选链是「在一页上」扫出来的，而它的取值口径是「第一个取到非空文本的候选」。** 配置化来源的列表轮**不给日期**（`source_import` 从不填 `entrypoints[].date_selector`），`publish_date` 于是完全由详情页的 `detail.date_selectors` 决定：`date_text = _strip_label(_text_of(root, selectors))` → `parse_date`，而 `_text_of` 只认**第一个取到非空文本**的选择器。这条链出自 `analyze.meta_candidates`——**文档顺序、不打分、只扫一页**：页脚/模板里那个固定日期与文章自己的日期，在那一页上长得一模一样，于是它排在前面就赢了，跟着栏目翻下去就把**同一个日期**写进该来源的每一篇（年份筛选、时间窗口核对、成果库排序全部跟着错）。
- **实测证据（chinanews 2026-10-15 那篇）**：`#pubtime_baidu` → `2026-10-15`、`div.content_left_time` → `2026-10-15`（页本身没问题）；而 `div.left`（侧栏，被全文档解析到 8 个节点）→ `时政` → `parse_date` = `None`。**同一个 `_text_of` 缺陷的第二种形态**：候选在前挡住真候选时，日期直接丢掉。
- **改法（跨页核对 + 诚实丢弃）**：`html_source.date_texts_by_selector()`（与运行时**同一条**取值链，逐个候选分别量值）→ `source_import._verify_date_selectors()`：在所比 `_DATE_CHECK_SAMPLES = 3` 篇样本上**恒定不变**的候选当页脚/模板元素**丢弃**；能 `parse_date` 的候选自动提到链首（解掉上面的遮挡）；「丢」与「重排」都写进作业日志与 `sample_metrics.dropped_selectors`，**不静默**。样本由 `_sibling_detail_pages()` 回**候选自己的列表页**取（复用 `page_url` + `list_selector`，不新增任何字段——挑到的就是这份配置将来会抓的那些条目），取不到就少比几篇并在日志里说明。**门槛故意保守**（3 篇全中才算恒定）：同栏目同一天发两条通知很常见，拿它当模板元素会把**正确**的日期也丢掉；被否为非正文的候选**不做**这一步（不多花抓取）。
- **日期宁可缺失也不写错**：`date_selectors` 可能被清成 `[]` —— 这是本项目一贯口径（「取不到就是 None，绝不猜」）。日志会写明是哪个选择器、值是什么，并提示「若确认它就是该站的发布日期，请手工写回 `detail.date_selectors`」。
- **验收侧同步堵漏（Phase 14 的 `dry_run_ingestion`）**：判据 6「详情页日期命中率」是**软**判据，恒定值让它一路 100% 通过——正是这个 bug 过验收的方式。现改为：抽样各篇的日期值**只有一个**时，判据 6 的 `note` 与 `coverage_limits[]` 双双写明「⚠ 抽样 N 篇的发布日期全部相同（X）：疑似取到了页脚/模板元素，命中率高不代表取对了」。
- **镜像同步**：`docs/architecture/source-onboarding.md`（新条目）、`.claude/skills/website-source-onboarding/references/config-schema.md`（`date_selectors` 行补顺序语义 + 跨页核对）、`references/acceptance-criteria.md`（判据 6 补「值恒定 = 假命中」）。
- **⛔ 历史数据不会自动订正**：已落库的错日期**改不了自动修正**——`publish_date` 在插入时写定，`update_policy_from_fulltext` 用 `COALESCE` 不覆盖非空值，`insert_policies` 又是 `ON CONFLICT … DO NOTHING`，重跑同一个任务也不会改写旧行。补救须用户执行：对该来源**建新任务重抓**（新行按改正后的配置拿到正确日期），旧行按需人工订正（本次不写订正脚本、不碰历史数据）。
- **待实机验收**（我未运行，按仓库约定）：重新导入那个报错的来源，看作业日志里「日期候选跨页核对：比了 N 篇样本」与「有 K 条候选被丢弃或重排（原因见「正文规则」那条）」，确认 `detail.date_selectors` 里留下的是**会随文章变化**的那条；再建任务抓一遍，成果库日期应逐篇不同。

**2026-10-15 缺陷修复：用户报「手动停用的项目无法再次启用」（启用闸门查验收凭据时带了条数窗口）**：

- **根因：同一个问题，两处各写了一份判定。** 列表页/详情页的「已验收」徽标走 `repo.list_passing_dry_run_fingerprints()`——**全表**按 `report->>'fingerprint'` 找通过试运行；启用闸门 `onboarding._latest_passing_run()` 却走 `repo.list_onboarding_runs(source_key=…, limit=20)`——只在**该来源最近 20 条记录**里找。同一来源反复导入 / 候选重跑之后（每次导入至少写 inspect、discover、dry_run 三条记录），那份凭据被后来的记录挤出窗口，于是「徽标说已验收、按钮说能启用，点下去却被拒」——且**不会自愈**（再点多少次都一样），除非 `force=true`。
- **凭据是既成事实，不该有窗口。** 指纹是配置内容的哈希（`config_fingerprint()`），「指纹一致」就等于「这份配置真被跑过一次并通过了」；同一来源之后勘察多少回、失败多少回都与其无关。新增 `repo.fetch_latest_passing_run(fingerprint=…)`：按指纹在**全表**取最近一条通过试运行（`ORDER BY started_at DESC LIMIT 1`，无窗口），闸门改用它。
- **判定口径收成一处**：`repositories._PASSING_DRY_RUN_SQL`（`kind='dry_run' AND status='passed' AND report IS NOT NULL`），单条闸门与列表页批量查询**都拼这一条**——原来两份手抄的判定，漂出来的分歧正是这个 bug。`schema.sql` 新增部分索引 `idx_onboarding_run_passing_fp`（`(report->>'fingerprint') WHERE kind='dry_run' AND status='passed'`）服务这次查询（幂等；老库重跑 `database/init.sh` 即生效，不加也能跑、只是慢）。
- **拒绝文案要说清是「哪一种没有」**：闸门未命中时再查一次「该来源有没有**别的**指纹通过过」，如有则在拒绝文案里点出「该来源最近一次通过的试运行用的是指纹 X（当前配置 Y）——说明配置在验收之后改过」。两种情况用户要做的事不同（**重跑** vs **先跑**），不该都只丢一句「没有指纹一致的」。
- **回归用例**（`test_source_onboarding.py`，落库用例，我未运行）：先跑出一次通过试运行，再**故意灌 20 条更晚的 inspect 记录**，然后 `register_source(approved=True)`——必须仍然 `ok`。按旧实现的窗口语义这些记录必然把凭据挤出前 20 条，所以这个形状在旧实现下必挂、在新实现下必过。用例收尾连它写进去的接入记录一起清（新增 `repo.delete_onboarding_runs()`，**只给测试用**——生产的验收凭据就住在同一张表里）。
- **镜像同步**：`docs/architecture/source-onboarding.md`（诚实规则那条下补「凭据按指纹全表查、不设窗口」）、`references/acceptance-criteria.md`（第 6 节验收与注册的绑定）、`references/config-schema.md`（状态机 `disabled` 行）、`references/tool-map.md`（测试覆盖那条）、`SKILL.md` 速查表（拒因现在会点出上一次通过的指纹；新增一行「已停用来源点启用被拒=不该再发生」）、`docs/guides/extending.md` 的 *Hand-kept mirrors* 表（把 `SKILL.md` 本身也列进去——它的速查表同样在镜像行为）。
- **待实机验收**（我未运行，按仓库约定）：`cd server && uv run python scripts/test_source_onboarding.py --db-scratch`（预期新增那条通过；`--db-scratch` 用一次性库，不留脏数据）；页面上把那个被停用的来源点「启用」，应直接成功、不再出现「没有指纹一致的通过试运行」。

**待用户验收（页面手工路径）**：

1. 顶栏「来源管理」→ 列出内置 gov.cn；「导入网址」→ 粘一个政务栏目页 → 阶段与日志**逐条**出现（不是一次刷完）。
2. 报告页：状态徽章 / 判据表 / **未测试项与覆盖范围限制可见**；点另一个候选 → 重跑，报告结构一致。
3. 「启用」→ 列表变「已启用」；**刷新页面**后仍在（验证 `load_configured_from_db` 装载）。
4. 抓取页来源下拉出现该来源 → 选它 → **「文种」被隐藏**、发布机构变成该来源的入口名 → 发起抓取，任务日志的来源标识是 `html_list:<key>`。
5. 切回 gov.cn → 「文种」恢复、机构选项回到 gov.cn 的 facet 名单。
6. 弹窗手势：卡片内按住拖选文字、移到遮罩上松手 → **不得**关闭；遮罩上单击 → 关闭。
7. 导入一个 `blocked` 的站点（robots 禁止）→ 报告写明「未绕过访问控制」，且「启用」不可用。
8. 「停用」一个已启用来源 → 再点「启用」→ **必须能重新启用**（不得再出现「拒绝启用：没有指纹一致的通过试运行」）；徽标「已验收」与实际闸门不得互相打架。

**阻塞**：

- ⛔ **`agent_demo` 库仍需 Phase 14 的 4 张表**（`cd database && bash init.sh`，幂等不重置）——没有它，来源列表只剩内置项、导入作业在登记那一步会失败。与 Phase 14 是同一条阻塞，未重复执行。
- ⚠️ 前端 `pnpm build` 未执行（仓库约定由用户运行）：`vue-tsc` 若报类型错，最可能在 `SourceModal.vue` 的 `job.registered`（`Record<string, unknown>` 需要窄化后才能索引）与 `SourceReport.vue` 的可选字段上。

**未实现（本次有意不做）**：Agent 驱动的接入面板（用户确认后面再接）、JS 动态渲染站点、PDF/OFD 正文抽取、来源健康巡检定时化、验收报告里的附件探测结果展示（字段已透传，页面暂未渲染）。

---

## Phase 16 — vue-router 全站迁移 + 来源管理独立页面（含批量导入）

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：全站搬上 vue-router（刷新/直达/后退都不丢页），来源管理从 1491 行的弹窗独立成一页 `/sources`，并补上「批量导入多个网址」与「接入记录」两个缺口

**范围决定**（用户已确认）：① 全站迁到 vue-router，不做「只给新页加路由」；② 新页含三个模块——导入入口（单条 + 批量）、来源列表 + 详情 + 启停、接入记录/验收报告；③ **不做**「按来源的抓取任务记录」（用户明确未选）；④ **不做**回滚（2026-10-15 用户指示移除，见下方变更）。

**2026-10-15 变更（用户指示）**：

- **移除回滚功能**（用户：「不需要回滚的功能」）。删掉的面：前端 `SourceListSection.vue` 的「回滚」按钮与行内版本面板 + `rollbackSource` / `sourceDetail` 两个 client 方法 + `SourceRollbackPayload` / `SourceDetail` 类型引用；后端 `POST /api/sources/{id}/rollback` 路由 + `SourceRollbackRequest` DTO + `manage_source` 的 `rollback` 分支（连带其独有的 `version` / `change_note` 形参）+ `manage_ingestion_job` 的同名参数 + 只被回滚用到的 `repo.fetch_source_revision()`；Skill 与 wiki 同步（`SKILL.md` / `references/tool-map.md` / `references/config-schema.md` / `docs/guides/commands.md`）。
- **保留**：`content_source_revision` 表与其写入路径（每次配置变更仍留一条，属「可追溯」审计痕迹，不是回滚专用），`GET /api/sources/{id}` 与 `manage_source(action="detail")` 仍返回 `revisions`。
- **顺带修复**：成果库页顶栏「清空」按钮的 `:disabled="!task || clearing"` 去掉 `!task`——见 Phase 8 的同日变更。

**2026-10-15 变更（第二轮，用户报修）**：

- **试抓不回写「当前任务」**（用户确认要修）：`SourceListSection.vue` 的 `trialSource()` 只调接口 + 弹 Toast，store 里的 `task` 仍是旧的/空的，于是**抓取页一直显示「尚未抓取」，而那条试抓任务其实正在跑（或正停在人工确认）**——同一个成因也解释了「清空」按钮的置灰。**修**：`store.ts` 把 bootstrap 里那段「读 `/tasks/current` → 拉日志/结果 → 非终态订阅」抽成 `export async function syncTask()`（bootstrap 改调它），试抓成功后 `await syncTask()` 对齐；顺带把订阅条件从 `running|awaiting_confirmation` 扩到**非终态一律订阅**（`pending` 也要——任务落库时就是 pending，不订阅进度面板会停在那一刻）。`runScheduleNow()` 走的 `bootstrap()` 现在也复用了这段逻辑。
- **「高级设置」收起后，发布机构 / 文种的下拉被卡片底边切掉一半**：`.config-body` 常驻 `overflow: hidden`（为 `grid-rows 0fr↔1fr` 折叠动画挡溢出），而这两个下拉是**绝对定位浮层**——高级设置一收起卡片就变矮，浮层越过卡片底边即被裁。**修**：裁剪只在**已收起**与**折叠动画进行中**开（`.collapse-wrap.closed` / `.collapse-wrap.animating`），动画结束由 `collapseAnimating`（`toggleCollapse` 里 300ms 定时器，与 CSS 时长一致）摘掉，浮层恢复可见。附带修好同族问题：抓取来源的 `SelectMenu` 浮层同样不再被裁。
- **让 store 自己发现「不是这张页面发起」的任务**（用户确认要做，补齐上一个缺口）：定时任务**到点自动触发**、另一个标签页、另一台设备起的任务，此前都要刷新一次页面才看得到。**修**：`store.ts` 新增 `startTaskWatch()` / `stopTaskWatch()`（`App.vue` 挂载时起、卸载时停）——每 15s 轮询一次 `GET /tasks/current`（只在页面**可见**时查，`visibilitychange` 切回本标签页**立刻**查一次），发现 id 变了：① 本地没在看的任务 / 看的那条已终态 → 直接 `syncTask()` 对齐；② 本地正在看一条**非终态**任务（跑着 / 等下确认）→ **不抢视图**，只弹一条带「查看」按钮的提示（抓取页只认 `/current`，一换就再也回不到原来那条，**页面还没有任务列表**，所以切换必须由用户点；本地那条跑完后下一次轮询会自动对齐）。同一条新任务只提示一次（`notifiedTaskId`，`syncTask()` 时重新武装），轮询有重入保护。**配套**：`toast` 加可配的动作按钮文案（默认仍「重试」，`App.vue` 渲染 `toast.actionLabel || '重试'`），`notify()` 可带动作。文档：`docs/architecture/overview.md` 的 Frontend shape 补一句（`task` 是「服务端最新任务的缓存」而非权威）。

**后端**：

- [x] `app/services/source_import.py`：`ImportJob` 加 `url` / `batch_id` / `draft`；批量入队（`BATCHES` 聚合表 + `asyncio.Queue` + lazy worker，批内不设全局冷却）；`JOBS` 惰性驱逐最旧**终态**作业（保留 30min / 上限 50 / running 永不驱逐）；新增 `list_jobs()` / `batch_snapshot()`
- [x] `app/schemas/source.py`：`SourceBatchImportItem` / `SourceBatchImportRequest` / `SourceBatchImportCreated` / `SourceJobDTO` / `SourceBatchChildDTO` / `SourceBatchSnapshot` / `SourceTrialRequest`；`ImportJobStatus` 加 `queued`
- [x] `app/api/routes/sources.py`：`POST /import/batch`、`GET /import`、`GET /import/batch/{batch_id}/stream`（**GET**，EventSource 只支持 GET）、`GET /runs/recent`、`POST /{id}/trial`、`POST /{id}/health`——静态段全部在 `/{source_id}` 之前；`verified` 的 N+1 改为一次取 fingerprint 集合
- [x] `app/db/repositories.py`：`list_onboarding_runs(kinds=...)` 过滤；新增 `list_passing_dry_run_fingerprints()`

**前端**：

- [x] `web/src/router.ts`（新）：四条路由 + `/` 与兜底重定向；dev 用 history、prod 用 hash；`/library/:id(\d+)` 用 props 注入 `policyId`
- [x] `web/src/views/CrawlPage.vue`（新，从 App.vue 拆出）、`web/src/views/SourcesPage.vue`（新，`?tab=` 同步 + `v-show` 常驻三模块）
- [x] `web/src/components/sources/{SourceListSection,ImportSection,RunsSection}.vue`（新，分别承载来源列表/导入/接入记录）
- [x] `web/src/store/sourceImports.ts`（新）：单条向导 + 批量队列 + 作业列表 + `syncImportsOnMount()` 路由返回自愈
- [x] `web/src/api/{types,client}.ts`：新 DTO 镜像 + `startBatchImport` / `listImportJobs` / `subscribeBatchStream` / `sourceRunsRecent` / `trialSource` / `healthSource`（`rollbackSource` 已随回滚功能移除）
- [x] `store.ts`：删掉 `view` / `AppView` / `libraryPolicyId` / `goCrawl` / `goLibrary` / `openLibraryPolicy` / `backToLibrary` / `sourceModal*`；`clearAll()` 与详情轮询守卫改读 `router.currentRoute.value`
- [x] `App.vue` / `TopBar.vue` / `ConfigPanel.vue` / `LibraryPage.vue` / `LibraryDetailPage.vue`：全部改路由驱动（`LibraryDetailPage` 接 `policyId` prop，`onMounted` + `watch(props.policyId)` 拉详情）
- [x] 删除 `web/src/components/SourceModal.vue`；`App.vue` 恢复全站共用的 `.content` 页面外壳（原先被 CrawlPage 独占，三个页面会贴边）

**关键设计决定**：

- **一个 `batch_id` 关联 N 个独立作业**，不做父子作业：导入是 IO 受限的串行活，复用现成的单作业流水线/取消/SSE，模型不膨胀。
- **批量导入只落草案**（作业级 `draft=True` → 跑完把 `job.registered` 清成 `None`）：Phase 15 的 DEV 自动注册后门在单条路径保留，批量下会把 N 个 URL 全灌进生产来源表，故注册动作交还前端。**单条导入行为不变**（`POST /import` 不带 `draft`）。
- **批量进度用一个 batch-wide SSE**（轮询队列位置）+ 按需连的单作业 SSE（点开「详情」才连），避免 N 条长连接。
- **路由模式代码级回退**：dev = history（Vite 自带 SPA fallback），prod = hash（dist 可能被任意静态服务器甚至 `file://` 托管），不引入 env 变量。

**验证证据（离线，本会话执行）**：

- 4 个后端改动文件 `ast.parse` 全通过（未构建未运行，按仓库约定）。
- 前后端逐字段核对：`types.ts` ↔ `app/schemas/source.py` 的全部新 DTO 字段名/可选性一致；SSE 事件形状（`phase`/`log`/`done`/`error`）两侧一致。
- **核对中发现并修掉一处真实漂移**：`SourceBatchChildDTO` 漏了 `gone`，而 `batch_snapshot()` 会下发它（Pydantic v2 默认丢弃模型未声明的键）。已补字段，并把 batch SSE 改为下发前过一遍 `SourceBatchSnapshot.model_validate()`，让 schema 真正约束线上字段。

**验收反馈修复（2026-10-15，用户实机 `pnpm dev` 后提出）**：

- [x] **启动即失败**：`Failed to resolve import "./store" from "src/store/sourceImports.ts"`。store 本体是单文件 `web/src/store.ts`，`store/` 只是子目录，新文件按「同目录」写成了 `./store` → 解析成不存在的 `web/src/store/store.ts`。改为 `'../store'`；并用静态脚本扫全 `web/src` 的 80 条相对导入逐个查目标存在性，修后 **80/80 全部可解析**（`components/sources/*` 的 `../../store`、`views/*` 的 `../store` 均正确）。
- [x] **循环依赖核对（新引入，安全）**：修好路径后 `store.ts → router.ts → SourcesPage.vue → store/sourceImports.ts → store.ts` 这条环才真正生效。安全——`store.ts` 从不反向 import `sourceImports.ts`，且三个模块顶层语句全是 `ref`/`reactive`/`computed` 声明，无一处解引用对方绑定。
- [x] **问题 1｜批量导入状态胶囊裸奔**：`components/sources/ImportSection.vue` 只写了 `.tag.run` / `.tag.fail` 两条 tone，**丢了基类 `.tag`**（padding/圆角/字号都在基类里）→「未通过 / 运行中」只剩高亮底色、方角贴字，「排队中 / 完成 / 已取消」三条 tone 未定义而完全无样式。补齐基类 + 五种 tone，配色与尺寸对齐同页 `RunsSection` / `SourceListSection`：运行中=蓝（同「事实」栏）、完成=绿（同「信息」栏）、未通过=红（全局 `--accent`）、排队中/已取消=中性灰。
- [x] **问题 2｜非 gov.cn 来源的成果库卡片显示异常**：卡片标签行对这类来源**全空**——`agency`/`publish_date` 常为空，`doc_type` 因推导规则只认 `国XXX` 前缀而**恒为 null**，且列表契约根本没有来源字段，卡片只剩标题+摘要，看不出是哪家站的。三处修复：
  - `repositories.py` `_LIBRARY_LATEST_CTE`：`doc_type` 推导改为两条腿——① 通用：取文号里**第一个括号符之前**的部分（`国发〔2026〕3号`→`国发`，`粤府〔2026〕1号`→`粤府`），前缀限 1–6 字挡住长句；② 兜底：无括号的旧形态沿用 gov.cn 名单（`国令第761号`→`国令`）。**gov.cn 行为逐字不变**，同时修好 文种 facet 下拉为空 的问题。CTE 加选 `p.source_name`。
  - `schemas/policy.py` + `web/src/api/types.ts`：`LibraryPolicyItem` 加 `source_name`（**已存在列**，非新增持久字段）。
  - `LibraryPage.vue`：卡片首位渲染来源胶囊（限宽 45% + 省略号 + `title` 悬浮全名），并同步 `LibraryDetailPage.vue` 里那份 `docType` 手抄镜像。
- [x] **问题 3｜成果库卡片摘要「有时有一行、有时没有、有时换行了看不见」**（`LibraryPage.vue`）：
  - **根因**：`.card-summary` 写的是 `flex: 1 1 0`，`flex-basis: 0` 让摘要**不参与卡片的固有高度计算**——一排卡片的高度由标题/标签/底栏决定，摘要只能分「剩下的空间」，而剩下多少取决于同一排里别人的标题占几行。于是标题短的卡片摘要显示得下、标题满两行的卡片摘要被挤到 0 行（看着就是没摘要）、中间态则一行被截。表现正是用户描述的三态。
  - **修法**：① `.card-summary` 改 `flex: 0 0 auto` + `min-height: 40px`（两行固定槽位，按自身高度参与计算，最多两行）；② `.card-title` 加 `min-height: 44px`（一行标题也占满两行高度，同排卡片的标题/摘要/底栏才会逐行对齐）；③ `.card-foot` 加 `margin-top: auto`（剩余高度落在摘要与底栏之间，而不是接到摘要尾巴上）。
  - **另修一个独立诱因**：摘要是大模型写的，常带换行/多段；在 `-webkit-line-clamp: 2` 下一个换行就占掉两行配额里的一行 → 「换行了、正文看不见」。加 `summaryOf()` 先把文本压成一段再渲染，顺带修掉空串落到「暂无摘要」（`??` 只挡 null/undefined，挡不住 `''`）。
  - 注：问题 2 新加的来源胶囊让标签行变高，**放大了**这个既有布局问题（症状更早出现），不是它的起因。

**验证证据（离线，本会话执行）**：

- 2 个后端改动文件 `ast.parse` 通过；用 `ast.literal_eval` 取出 CTE 常量**原样打印**，确认 raw string 让正则里的 `\[` 完整到达 SQL（PostgreSQL 括号表达式需要它当字面量）。
- 文种推导**对拍**：同一组 9 个文号在「Python 复刻 ↔ 前端 TS 逻辑（node 实跑）」上逐例一致——`国发/国办发/国令` 与旧行为完全相同；`粤府`/`穗府办`（半角括号）/`浙政办发`（6 字上限）正确推出；`〔2026〕3号`（无前缀）与 `关于…通知〔2026〕3号`（超长前缀）正确判空。

**阻塞 / 待验收**：

- ⚠️ 前端 `pnpm build`（`vue-tsc`）与 `pnpm dev` 未执行（仓库约定由用户运行）。实机请按顺序过：① 四个 URL 直达且刷新恢复（`/crawl`、`/library`、`/library/1`、`/sources`）；② 成果库卡片 → 详情 → 浏览器后退，列表状态保留；③ 抓取页「导入网址」跳 `/sources?tab=import`；④ 来源列表/启停/回滚与旧弹窗行为一致，试运行与健康检查出结果；⑤ **单条导入回归**（dev 下仍自动注册）；⑥ 批量导入 3–5 个真实栏目 URL，队列逐个跑、可取消单个、失败给原因；⑦ 接入记录的 kind 过滤与报告展开；⑧ **批量导入状态胶囊**五色（排队中灰/运行中蓝/完成绿/未通过红/已取消灰）都是圆角胶囊，不再是方角裸文字；⑨ **非 gov.cn 来源的成果库卡片**能显示来源胶囊与文种（用一条 `粤府〔2026〕N号` 这类地方文号的记录验），文种筛选下拉不再是空的；⑩ **成果库卡片摘要整齐**——同一排卡片的标题/摘要/底栏逐行对齐，摘要恒占两行槽位（长摘要截两行、短摘要不塌陷、无摘要显示「暂无摘要」），不再出现「有时有一行、有时没有、有时换行了看不见」；⑪ **试抓会同步到抓取页**——在来源列表点「试运行」，切到抓取页应看到那条任务（不再显示「尚未抓取」），进度面板可跟进、可终止，并停在「等确认」；⑫ **「高级设置」收起时下拉不再被裁**——收起高级设置后点「发布机构」「文种」，浮层能完整展开（越过卡片底边也不被切）；⑬ **外部发起的任务能自己出现**——在 `/sources` 或另一个标签页建一个任务（或等定时任务到点），回来不刷新页面也能看到：本地没有在看的任务时**直接对齐**，本地正看着一条非终态任务时**不抢视图**、只弹一条带「查看」的提示。
- ⛔ **`agent_demo` 库仍需 Phase 14 的 4 张表**（`cd database && bash init.sh`，幂等不重置）——与 Phase 14/15 同一条阻塞，未重复执行。

**未实现（本次有意不做）**：按来源的抓取任务记录（用户明确未选）、批量导入的进度持久化（进程内 30 分钟即为上限，持久留痕仍在 `source_onboarding_run`）、路由级代码分割（产物体量不值当）。

---

## 进度日志

> 明细已抽到 **[docs/progress/log.md](docs/progress/log.md)**（2026-10-12 起，按时间正序，只增不改）。
> 每完成一个 Phase（或发生阻塞 / 范围变更）在该文件追加一行；本文档只保留上面的清单与状态。

