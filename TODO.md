# 打通三端 · 实施 Todo List

> 配套计划：[PLAN.md](PLAN.md)（背景、API 契约草案、设计稿状态清单、待确认决策点）
>
> **维护规则**：每完成一个 Phase，必须同步更新本文档——勾选该 Phase 的任务项、改状态表、并在文末「进度日志」追加一行。

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
| 4 | 抓取 + 提取流水线（核心） | 🟨 进行中（代码完成，待实机验收） | — |
| 5 | 进度实时推送（SSE） | 🟨 进行中（代码完成，待实机验收） | — |
| 6 | 前端接入 | 🟨 进行中（代码完成，待实机验收） | — |
| 7 | 端到端验收与文档 | 🟨 进行中（文档已对齐，端到端验收待用户执行） | — |
| 8 | 终止 / 清空与重启收尾 | 🟨 进行中（代码完成，待实机验收） | — |

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

**状态**：🟨 进行中（代码完成，待实机验收） ｜ **目标**：真实抓取 gov.cn 并结构化提取「事实/信息/知识」

- [x] 新增 `server/app/pipeline/crawl_graph.py`（`prepare → fetch → extract → summarize`，失败走 `fail`）
- [x] 新增 `server/app/pipeline/extract.py`（结构化输出 schema，复用 `_build_llm()`）
- [x] 单篇流程：`get_policy_fulltext` → LLM 提取 → 写 `extraction` + `fact_item` → 更新计数/stage → 写 `task_log`
- [x] 容错：单篇失败标记 `failed` 并继续；MCP 不可用则整体 `failed` + 中文提示
- [x] 由 `POST /tasks` 触发，异步执行，不阻塞请求
- [x] **计数一致性（硬约束）**：见下，已由 `refresh_task_counts()` 实现

**实现要点**：

- `fetch` 节点按关键词逐个调 `get_latest_policies`（`asyncio.wait_for` + `pipeline_mcp_timeout`），按 URL 去重后 `insert_policies`（`ON CONFLICT (task_id, source_url)` 幂等），再 `refresh_task_counts()`；篇数按 `pipeline_max_policies` 截断。
- `extract` 节点用信号量限流（`pipeline_extract_concurrency`，**默认 1**）逐篇处理；并发=1 是为了让 `stage` 的「第 N/M 篇」与日志里单条 `active` 行保持真实——调大能显著缩短墙钟时间，代价是这几处文案不再精确。
- 单篇提取：`reset_extraction()`（干净起点、可重复执行）→ 写 stage/日志 → `get_policy_fulltext` → `extract_policy()`（正文按 `pipeline_max_body_chars` 截断；先试 `with_structured_output`，失败回退宽松 JSON 解析）→ `save_extraction()`（单事务写三栏 + 事实明细）→ 成功/失败日志 → `finally` 里 `refresh_task_counts()`。
- 失败文案（设计稿口径）：MCP 不可用/超时 → `抓取失败：连接 China-Central-Policy-MCP 超时或异常（…），请确认本地服务已启动后重试`；检索为空 → `未检索到匹配的政策原文，请调整关键词或时间范围后重试`。

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

---

## 进度日志

> 规则：每完成一个 Phase（或发生阻塞/范围变更）追加一行，倒序不必——按时间正序追加。

| 日期 | Phase | 事件 | 说明 |
|---|---|---|---|
| 2026-10-12 | — | 计划建立 | 产出 [PLAN.md](PLAN.md) 与本 Todo List；7 个决策点待用户确认 |
| 2026-10-12 | 1 | 代码完成 | 新增 `app/db/{pool,repositories}.py`（psycopg3 异步池 + dict_row + autocommit）、`scripts/test_db.py`；`config.py` 加 `database_url`/池大小，`pyproject.toml` 加 `psycopg[binary,pool]`，`.env.example` 加 `DATABASE_URL`；`main.py` lifespan 开/关池（连不上只告警不阻断启动）。静态检查：`ast.parse` 全通过。**待验收**：`uv run python scripts/test_db.py` |
| 2026-10-12 | 2 | 代码完成 | 新增 `app/schemas/{task,policy}.py` + `app/api/routes/{tasks,policies}.py`，实现 5 个只读接口；`main.py` 注册路由并把 422 校验错误压成 `{"detail": "..."}`。**待验收**：`/docs` 逐条调用 |
| 2026-10-12 | 3 | 代码完成 | `POST /api/tasks`（201 + `task_id`，写入首条日志）、`POST /api/policies/{id}/retry`（仅 failed → pending，`retry_count+1` + 日志）、`POST /api/policies/{id}/reextract`（UPSERT 重置 + 删 `fact_item`，同事务）。**待验收**：建任务后 `/tasks/current` 可读；retry 后 `retry_count` 流转 |
| 2026-10-12 | 2 | 差异记录 | seed 与 `data.ts` 结果行状态不一致（`data.ts` 第 2、4 篇为 `extracted`，`seed.sql` 仅给第 1、3、6 篇建 `extraction` 行 → 接口返回 **1 已提取 / 1 提取中 / 4 未提取 / 1 失败**）；`crawl_task.extracted_count=6` 与表内 7 行同样不自洽。**不影响代码**，已提请用户裁定 |
| 2026-10-12 | 2 | 差异裁定 | 用户裁定：seed 与前端 mock **都只是样本**，后续以 Phase 4 抓取的**真实内容**为准 —— 不修补 `seed.sql`，Phase 2「与 `data.ts` 逐项对得上」的验收标准相应改为「**真实数据自洽**」。已在 Phase 4 增补「计数一致性（硬约束）」检查项（进度面板计数与表格 `summary` 必须逐项相等） |
| 2026-10-12 | 4 | 代码完成 | 新增 `app/pipeline/{crawl_graph,extract}.py`：`prepare → fetch → (extract \| fail) → summarize` 图；`fetch` 多关键词检索 + URL 去重 + `insert_policies`；`extract` 信号量限流（默认并发 1）逐篇 `get_policy_fulltext` → 结构化提取 → `save_extraction`；单篇失败只标 `failed` 继续；MCP 不可用/0 结果走 `fail` 并给设计稿中文文案。**硬约束已实现**：`refresh_task_counts()` 从实际行数聚合派生四个计数（非自增），进度面板与结果表格必然同源；每处写入后都重算。`config.py` 加 `PIPELINE_*` 参数。静态检查：`ast.parse` 通过。**待验收**：真实抓取 + 计数逐项相等 |
| 2026-10-12 | 4 | 缺陷修复 | Phase 3 的 retry/reextract 只落库、无人执行（重试后会永远停在 `pending`）→ 补 `start_single_extraction()` / `run_single_extraction()`：后台真正重跑该篇，处理完后按「是否还有 pending/extracting 篇目」决定任务回 `completed` 还是保持 `running` |
| 2026-10-12 | 4 | 一并修复 | `reset_extraction()` 由 `-> None` 改为 `-> int`（返回保留的 `retry_count`），修复 `reextract` 响应体把 `retry_count` 错报为 `0` 的问题 |
| 2026-10-12 | 5 | 代码完成 | `GET /api/tasks/{id}/stream`：订阅时补发全部历史日志再推增量，事件 `task/log/done/error`，轮询 0.6s、心跳 15s `: ping`、`X-Accel-Buffering: no`；`progress` 改为「抓取 0→50 + 提取 50→100（分子含 failed），运行中封顶 99」，`eta_seconds` 改为按 `started_at` 外推并加 6 小时陈旧上限。**待验收**：日志逐条出现 + 进度推进 + 结束收到 `done` |
| 2026-10-12 | 5 | 预期修正 | 上文 Phase 2 表中旧的 `progress=60` / `eta_seconds=120` 随之作废 → seed 任务现为 `progress=80` / `eta_seconds=null`（`started_at` 超 6 小时）。已在 Phase 2 段标注，勿再按旧值验收 |
| 2026-10-12 | 6 | 代码完成 | 新增 `web/src/api/{client,types}.ts`（fetch 封装 + `ApiError` + SSE 订阅，`done`/`error` 后主动 `close()` 防重连）、`web/src/store.ts`（模块级状态 + `bootstrap/startTask/subscribe/retryPolicy/reextractPolicy/loadExtraction`）、`vite.config.ts` 加 `/api` 代理；`TopBar/ConfigPanel/ProgressPanel/ResultTable/DetailDrawer/App.vue` 全部改为接口驱动（含四态标签、三态空状态、骨架屏、Toast + 重试、抽屉三栏 + 重新提取）。**删除 `web/src/data.ts`**（已无引用）。未运行 `pnpm build`/`vue-tsc`（按约定不主动构建）→ 前端尚未类型检查。**待验收**：点「开始抓取」全链路跑通 |
| 2026-10-12 | 6 | 有意偏差 | 设计稿「三栏加载中 = 骨架屏」：实现为「接口拉取期间显示骨架屏，`extracting` 态显示『正在提取…』状态块」，避免长时间提取被判读成页面卡住。已在 Phase 6 记录 |
| 2026-10-12 | 7 | 文档对齐 | `CLAUDE.md`：仓库布局补 `database/`、改写前端「静态 mockup」为接口驱动；命令补 `scripts/test_db.py`、修正 `test_mcp.py` 描述（`source_catalog` → `get_latest_policies`）；新增「数据层与抓取流水线」章节（双图结构、池、仓储、派生计数、SSE、错误契约）与「扩展流水线」指引。`server/README.md`：补目录结构、任务 API 表、SSE 事件与进度语义。`database/README.md`：改写「后端接入」为已完成，说明 seed 与真实数据的差异。根 `README.md`：项目结构补 `db/pipeline/api/store`、删除 `data.ts` 条目，API 章节补任务/政策接口与 SSE，前端状态由「静态 UI 未对接后端」改为「已对接」，扩展指引改指流水线 |
| 2026-10-12 | 7 | 验收清单就绪 | 已把 PLAN §2.2 的 10 项关键状态逐条映射到实现位置并列出实机确认方法（见 Phase 7 表）。**端到端验收待用户执行**——本仓库约定由用户自行编译运行，故 Phase 7 未标 `✅` |
| 2026-10-12 | 8 | 代码完成 | 需求：首次启动页面上残留上次未跑完的提取内容，且无终止 / 清空入口，数据清不掉也无法重新开始。后端：`_running` 改 `dict[task_id, set[Task]]` + `_track()`/`cancel_pipeline()`/`cancel_all_pipelines()`；仓储加 `delete_task()`/`delete_all_tasks()`/`fail_stale_tasks()`；新增 `POST /tasks/{id}/cancel`、`DELETE /tasks/{id}`、`DELETE /tasks`；`lifespan` 启动收尾僵尸任务。前端：`TopBar` 加「终止」（运行中才显示）+「清空」（二次确认）；`store` 加 `cancelTask()`/`clearAll()`/`canCancel`/`stopping`/`clearing` 及 `manualStop` 抑制误报「抓取失败」Toast；`api` 加 `cancelTask`/`clearTasks` + `TaskDeleted`；图标补 `square`/`trash-2`。静态检查：Python 5 个文件 `ast.parse` 全通过；前端未跑 `vue-tsc`（按约定不主动构建）。**待验收**：见 Phase 8 三条 curl |
| 2026-10-12 | 8 | 决策记录 | 用户裁定：「清空」= 删除**全部**任务与结果（彻底重置）；同时**加入终止按钮**。实现上**复用 `failed` 状态**表示终止（`stage`=「任务已手动终止」）而非新增 `cancelled` 枚举——避免改 `schema.sql`、避免让用户重跑 `init.sh`（其会重新灌 seed）。若日后需要中性色的「已终止」再引入枚举 |
| 2026-10-12 | 4 | 缺陷修复 | 实机报错 `AttributeError: 'str' object has no attribute 'get'`（`schemas/policy.py` 读 `info_points`）。根因：**写读形态不一致**——`info_points` 的契约是 `[{"point": "..."}]`（`schema.sql` 注释 + `seed.sql` + 读取端一致），但 `_extract_one` 传的是 `list(payload.info.points)` 裸字符串（同一次调用里的 `knowledge_concepts` 却是 `[{term,desc}]`，可见是笔误）。修：① `crawl_graph._extract_one` 改为 `[{"point": p} …]`，`save_extraction` 参数类型同步为 `list[dict[str, str]]`；② 读取端新增 `_info_points()`，同时兼容裸字符串与 `{"point":…}` 两种形态——**已被旧代码写坏的历史行无需迁移即可正常打开抽屉**。已用 `.venv` 跑断言验证 `_info_points()` 四种输入（含混合/空）与旧形态 detail 的端到端转换 |

