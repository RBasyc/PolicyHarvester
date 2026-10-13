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
| 9 | 检索结果人工确认环节 | 🟨 进行中（代码完成，待实机验收） | — |
| 10 | 定时自动入库（调度器 + 弹窗管理） | 🟨 进行中（代码完成，待实机验收） | — |

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
- 单篇提取：`reset_extraction()`（干净起点、可重复执行）→ 写 stage/日志 → `get_policy_fulltext` → `extract_policy()`（正文按 `pipeline_max_body_chars` 截断；先试**不强制 `tool_choice`** 的工具调用，失败回退宽松 JSON 解析）→ `save_extraction()`（单事务写三栏 + 事实明细）→ 成功/失败日志 → `finally` 里 `refresh_task_counts()`。
- 失败文案（设计稿口径）：MCP 不可用/超时 → `抓取失败：连接 China-Central-Policy-MCP 超时或异常（…），请确认本地服务已启动后重试`；检索为空 → `未检索到匹配的政策原文，请调整关键词或时间范围后重试`。
- **知识区语义（用户裁定，2026-10-12）**：知识**不是**读懂这篇文件所需的背景知识（术语解释、概念科普、政策沿革），而是**从该文件里可沉淀、能指导下一步行动的知识**——政策划了哪些线、开了哪些口子、窗口期多长、钱和资源往哪投、什么主体在什么条件下能拿到什么。提示词与 Pydantic 字段描述已按此改写；抽屉三块标题相应改为「知识点 / 适用领域 / 行动建议」。**字段键名与 jsonb 列名不变**（仍是 `concepts[{term,desc}]` / `fields` / `extension` 与 `knowledge_concepts/…`），只是语义变成「标题 / 说明」，故无需改表、无需重跑 `init.sh`。

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

### 官方检索 API 参数对照（实测记录，`sousuo.www.gov.cn/search-gov/data`）

对应用户提出的「官网只提供几种筛选方式，MCP 能否对齐」。逐项实测结论（`t=zhengcelibrary_gw` 单通道才能看到真实 `totalCount`）：

| 官网筛选 | API 参数 | 实测 |
|---|---|---|
| 搜索位置＝标题 | `searchfield=title` | ✅ **唯一有效值**：`q=中小企业` 时 `totalCount` 1780 → **16** |
| 搜索位置＝全文 | `searchfield=content` / `title:content:` / `summary` / `titleOnly` … | ❌ **一律等于不过滤**（恒 1780）。MCP 现在写的 `title:content:summary` 是静默无效值 |
| 按相关度 | `sort=score&sortType=1` | ✅ |
| 按时间 | `sort=pubtime&sortType=1`（`sortType=0` 为升序） | ✅ |
| 日期＝**一周内** | `timetype=timeyz` | ✅ `q=批复` → 仅 2026.10.08 一篇 |
| 日期＝**一月内** | `timetype=timeyy` | ✅ → 2 篇（多出 2026.09.29） |
| 日期＝**一年内** | `timetype=timeyn` | ✅ → 22 篇 |
| 日期＝不限 | `timetype=timeqb`（或不传） | ✅ |
| 日期＝自定义区间 | `timetype=timezd` + `mintime`/`maxtime` | ✅ 精确（2024-01-01..2026-12-31 + title → 恰 3 条） |
| 主题标签 | `tsbq=<标签名>`（取值见 `searchVO.extendresult.facetMap.tsbq`） | ⚠️ **会顶替关键词**（`tsbq`+`q` 只按标签返回：`q=中小企业 + tsbq=营商环境` → 只剩 56 条公报）；在 `t=zhengcelibrary` 下完全无效 |
| **发布机构分类** | **`bmfl=<机构名>`**（取值即 `searchVO.extendresult.facetMap.bmfl` 的键——即用户抓包里「工业和信息化部:1411条 … count:12798」那个列表） | ✅ **有效，但仅在 MCP 现用通道 `t=zhengcelibrary_gw_bm_gb` 上**（在 `t=zhengcelibrary` 上无效，此前误判「参数名未确定」正是**测错了通道**）。`q=中小企业+title` 下 `bumenfile` 114 → 工信部 **76** / 财政部 **42** / 发改委 **19** / 教育部 **8**，**与 facet 计数逐一吻合**；浏览态（`q=''`）同样有效：`bumenfile` 12798 → 财政部 **2226** |
| 主题分类（树） | `childtype=<parentid>` | ✅ 同样**仅 MCP 通道有效**：`childtype=1081` → `bumenfile` 114→**42**（facet 1081:48）、`childtype=1084` → **50**（facet 51）；`nodeid=` ❌ 无效 |
| 发布日期（年） | `pubtimeyear=<年>` | ✅ 同样**仅 MCP 通道有效**：`pubtimeyear=2024` → `bumenfile` 114→**13**、`gongwen` 16→**1**；`2025` → 7/1；`2020` → 20/1 |
| 文种（公文） | `pcodeJiguan=<国办发…>` | ✅ 仅作用于 `gongwen`：16→**10**（与 `groupMap.国办发=10` 吻合）；`filetype` ❌ 反而清空 `gongwen` |
| — | `puborg` / `orpro` / `inpro` / `dup` / `nodeid` | ❌ 无效；`puborg=<机构名>` 甚至会**清空 `gongwen`**（16→0），勿用（机构筛选一律用 `bmfl`） |
| **政策解读** | 换通道 **`t=zhengcelibrary`**（其 `catMap` 多出 `otherfile`） | ✅ MCP 用的 `zhengcelibrary_gw_bm_gb` **不含 `otherfile`**，故政策解读一直被整个漏掉；切 `t=zhengcelibrary` 即可带回 |

> **`timetype` 取值是拼音缩写，不是英文**：`timey` + `z`(周)/`y`(月)/`n`(年)。这一点导致第一轮用 `timeweek`/`timemonth`/`timeyear` 测试时**全部误判为「无效」**，已更正。
> 用户提供的官网「政策解读」真实抓包（`t=zhengcelibrary&timetype=timeyy&searchfield=title&subchildtype=2229&type=gwyzcwjk`）是定位到以上结论的关键——`type`/`subchildtype` 经复测**均无实际作用**，真正起作用的是 `t` 与 `timetype`。
>
> **⚠️ 方法论教训：参数有效性是「按通道（`t`）区分」的。** 上表「仅 MCP 通道有效」的那几行，此前都是在 `t=zhengcelibrary` 上测的，因此得出「参数名仍未确定 / 8 种取值全部无效」的**错误结论**；用户指出抓包里 `facetMap.bmfl` 就是发布机构列表后，改在 `t=zhengcelibrary_gw_bm_gb`（即 MCP 现用通道）上复测才命中。**今后测参数必须用调用方实际使用的 `t`。**
>
> **筛选器之间是「交集」，可叠加**：`bmfl=财政部 + pubtimeyear=2024` → `bumenfile` **7**；`bmfl=工业和信息化部 + childtype=1081` → **14**；`timetype=timeyy + bmfl=工业和信息化部` → 合计 1。分页（`p`/`n`）在筛选集内正常工作（`bmfl=财政部&p=2&n=5` 仍报 `totalCount=42`）。
>
> **稳定性已验证（排除限流噪声）**：7 次交错 baseline 恒为 `186 = bumenfile 114 + gongbao 56 + gongwen 16`，各变体（`pubtimeyear=2024`→70、`childtype=1081`→104、`bmfl=工业和信息化部`→148）**逐一精确复现**，故上表数值可信，非 `code=1001` 抖动。
>
> **❗ 遗留数据正确性问题：`gongbao`（公报）不受 `bmfl` / `pubtimeyear` / `childtype` 过滤**——上述所有筛选下 `gongbao` 恒为 **56** 条纹丝不动（连 `pubtimeyear=2020` 时也仍是 56），只有 `timetype` 能把它压下去（`timetype=timeyy + bmfl=工信部` → `gongbao=1`）。**即：按机构/年份/主题筛选时，56 条公报仍会整批混进结果。** 实现时必须单独处置（按机构筛选时丢弃 `gongbao`，或对公报同样做客户端复核）。

**⚠️ 最大的坑（决定实现方式）**：严格条件命中 0 条时，接口**不会返回空集，而是静默退回「全量浏览列表」**——`q=数据要素 + searchfield=title`、`q=量子计算 + searchfield=title` 均返回 1780 条浏览噪声；`q=中小企业 + title + 2020 窗口` 甚至回了一篇 2025 年的文件。**因此「无相关政策」只能靠客户端二次校验得出，不能相信接口给的条数。** 另：突发连续请求会返回 `code=1001`（疑似限流）——同一个 `q=数据要素 + title` 前后两次结果分别 1001 / 200，故 1001 不可解读为「无结果」。

**❗ 通道二选一（本轮新增的关键约束）**：两个通道**各只有一半能力，且互斥**——

| 通道 `t` | 目录（`catMap`） | `bmfl`/`childtype`/`pubtimeyear` 筛选 |
|---|---|---|
| `zhengcelibrary_gw_bm_gb`（MCP 现用） | `gongwen` + `bumenfile` + `gongbao`（合计 186） | ✅ **有效** |
| `zhengcelibrary` | 上述三者 + **`otherfile`（政策解读）**（合计 285） | ❌ **全部失效** |

即：**要「发布机构/主题/年份筛选」就得放弃「政策解读」；要「政策解读」就得放弃全部筛选器。**（上一轮推荐的「切到 `t=zhengcelibrary` 带回政策解读」与本轮测到的筛选能力直接冲突，不能同时拿。）凡涉及此决策需先请用户拍板；若两者都要，只能并发打两个通道再并集去重。

**结论（推荐改法）**：① MCP 一行改 `searchfield: 'title:content:summary'` → `'title'`（顺带证实该字段此前一直无效）；② 新增 `bmfl` / `childtype` / `pubtimeyear` / `pcodeJiguan` 透传（**仅 `zhengcelibrary_gw_bm_gb` 通道**），即可对齐官网的发布机构分类、主题分类、发布日期、文种四档筛选；③ 本项目 `_search_policies()` 增加**客户端复核**——标题不含关键词、或日期不在 `date_from/date_to` 内的直接丢弃，只有这样才能得到可信的「无」；④ **按机构/年份/主题筛选时单独丢弃 `gongbao`**（它不参与这些筛选，恒 56 条）；⑤ `content_type`（policy/notice/all）**当前对抓取零影响**（仅校验/落库/回传/可选，流水线从不读它），该 API 也没有对应参数，建议从 UI 撤下或明确标注为仅记录。

---

## Phase 10 — 定时自动入库（调度器 + 弹窗管理）

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
# 重启 server（8001）与 web（5174）
```

1. 抓取配置选「定时自动入库」→ 顶栏点「定时入库」→ 弹窗选「每 15 分钟」创建 → 列表出现该任务，「下次触发」≈ 15 分钟后
2. 列表点「立即运行」→ 抓取进度面板出现新任务并全流程跑完（无人工确认环节）；日志含「定时任务触发」「自动确认全部篇目」
3. 再次「立即运行」：若窗口内无新发文 → 任务列表该任务显示「无新增」，且不产生新任务/重复数据
4. 上限 3 + 长窗口回填验证：连续点「立即运行」若干次，应逐轮向更早日期推进（9 月抓满 3 篇后，下轮抓 8 月的），而不是反复抓同一批
5. 停用开关 → 「下次触发」变「已停用」，不再触发；再启用恢复排程；服务重启后调度自动恢复
6. `GET /api/schedules` 返回列表形状与 DTO 一致

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
| 2026-10-12 | 4 | 缺陷修复 | 每篇提取都必然先打一次失败的请求：`with_structured_output` 强制 `tool_choice={"type":"tool",…}`，而 `kimi-for-coding` 默认开启 thinking → 400 `tool_choice 'specified' is incompatible with thinking enabled`，随后才走 JSON 文本回退。已改为 `_extract_via_tool()`：`bind_tools([ExtractionPayload])` **不指定** `tool_choice`。**.probe 实测**（经 `env -u ANTHROPIC_*` 让 `.env` 生效、打到 `api.kimi.com/coding`）：A 强制 → 必现该 400；B 不强制 → `stop_reason=tool_use`、参数校验通过；C `thinking={"type":"disabled"}`+强制 → 请求被接受但 0 次工具调用（此路不通）。**端到端复验**：`.probe/extract_policy_probe.py` 调真实 `extract_policy()`，走「工具调用（首选路径）」、无回退告警，产出 facts=10 / points=6 / concepts=4，内容与原文一致。日志文案相应改为「工具调用结构化输出失败（…）」 |
| 2026-10-12 | 4 | 缺陷修复 | 实机报错 `AttributeError: 'str' object has no attribute 'get'`（`schemas/policy.py` 读 `info_points`）。根因：**写读形态不一致**——`info_points` 的契约是 `[{"point": "..."}]`（`schema.sql` 注释 + `seed.sql` + 读取端一致），但 `_extract_one` 传的是 `list(payload.info.points)` 裸字符串（同一次调用里的 `knowledge_concepts` 却是 `[{term,desc}]`，可见是笔误）。修：① `crawl_graph._extract_one` 改为 `[{"point": p} …]`，`save_extraction` 参数类型同步为 `list[dict[str, str]]`；② 读取端新增 `_info_points()`，同时兼容裸字符串与 `{"point":…}` 两种形态——**已被旧代码写坏的历史行无需迁移即可正常打开抽屉**。已用 `.venv` 跑断言验证 `_info_points()` 四种输入（含混合/空）与旧形态 detail 的端到端转换 |
| 2026-10-12 | 9 | 代码完成 | 需求：检索（关键词 + 时间范围）出来的结果相关度低、日期常不符，需在提取前加人工确认。拆两段：**第一段**（`POST /api/tasks`）`prepare → fetch → await_confirm`，跑完停在**新增枚举** `awaiting_confirmation`（非终态、SSE 不结束、progress=50）；**第二段**（`POST /api/tasks/{id}/confirm`）丢弃未勾选篇目 → `extract → summarize`。后端：`schema.sql` 加枚举 + 幂等 `ALTER TYPE`；`repositories` 加 `await_task_confirmation()`/`discard_unselected_policies()`；`crawl_graph` 加 `await_confirm_node`/`start_extraction`/`run_extraction_phase`（**直接调用** `extract_node`/`summarize_node`——图无 checkpointer、无法暂停恢复，故尾段从库里重读篇目跑）；`tasks.py` 加 confirm 接口（409/400 分支）。前端：`TaskStatus` + `TaskConfirmPayload`、`api.confirmTask`、`store`（`confirming` 阶段 / `confirmSelection()` / `canCancel`）、新增 `ConfirmPanel.vue`（逐篇勾选 + 全选/全不选 + 「超出范围」/「无日期」标记 + 确认/放弃）、`App.vue` 确认期间用面板取代结果表、`ProgressPanel`/`TopBar` 加「待确认」标签、`TopBar` 按钮在待确认态改叫「放弃」。文档：`CLAUDE.md`（两段流水线图 + 接口 + 状态）、`server/README.md`（接口表 + 「检索结果确认」小节）。静态检查：Python 4 文件 `ast.parse` 全通过；前端未跑 `vue-tsc`（按约定不主动构建）。**待验收**：见 Phase 9 验收段 |
| 2026-10-12 | 9 | 决策记录 | 用户裁定四点：① **逐篇勾选**、未勾选的**直接删行**（不引入 `skipped` 状态）；② 时间不符**只标注不改检索**（超范围/无日期打标，用户自行取舍）；③ **新增 `awaiting_confirmation` 枚举**（`ALTER TYPE … ADD VALUE IF NOT EXISTS` 幂等、不重置数据，跑一次 `database/init.sh` 即补上）；④ 界面用**主区域面板**而非弹窗。另记录两处口径：面板里的 `publish_date` 是**检索结果给的**日期（真实日期要等提取时 `get_policy_fulltext` 回填，面板页脚已注明）；「放弃本次抓取」复用既有取消链路（`failed` + 「任务已手动终止」），不新增枚举 |
| 2026-10-12 | 9 | ⛔ 阻塞 | 用户反馈「检索『中小企业』，拿到的标题与正文都没有这四个字」。实机复现并定位到根因，**问题在 MCP 侧（`tools/China-Central-Policy-MCP`，vendored 第三方仓库），非本项目代码**：① MCP 用的是**合并通道** `t=zhengcelibrary_gw_bm_gb`（`src/tools/getLatestPolicies.ts:97`），该通道恒返回 `searchVO.totalCount=0` / `searchVO.listVO=null`，结果全被塞进 `searchVO.catMap.{gongwen,bumenfile,gongbao}.listVO`；换成单目录通道 `t=zhengcelibrary_gw` 才会填 `searchVO.listVO`（`totalCount=884`）—— 即**合并通道根本不返回搜索结果**，导致解析器的 Case 1（`data.results`）恒为死代码；② `catMap.*.listVO` 是**按目录浏览**列表而非命中集：`q=量子计算` 三目录各 50 条、标题命中 **0** 条；`q=人工智能` 的 gongwen 命中落在第 **41** 条（不是第一条）；③ 解析器（`getLatestPolicies.ts:164-195`）把三个目录**全量拼接**再 `items.slice(0, limit)`，于是落库的绝大多数篇目与关键词无关 —— **这正是用户看到的现象**。另两点澄清：**日期窗口过滤本身是有效的**（`timetype=timezd` + `mintime/maxtime`；实测 2020-01 窗口只回 2020.01.xx，窗内无政策的 `2026-09-01..09-05` 直接回空结构 → 0 条），「时间也不对」是问题 ③ 的连带现象（返回的本来就是跨年份的无关文档），**不是独立的日期过滤 bug**；`build/` 与 `src/` 逻辑一致（`npm run build` = `tsc`），跑的确实是这段代码。**待用户决策**：(A) 改本项目 server 侧 `_search_policies()` 加关键词/日期客户端过滤（推荐，不动第三方仓库、无需重建）；(B) 改 MCP 源码换单目录通道 + 客户端过滤并 `npm run build` 重建（实测仅换通道也修不好相关度） |
| 2026-10-12 | 9 | 根因追补测 | 用户追问「官网只提供相关度/时间排序、标题/全文、一周/一月/一年/不限、发布机构、主题分类等几种筛选，MCP 能否对齐」，遂逐项实测官方 `search-gov/data` 并补出**决定性修法**：**`searchfield=title` 是唯一有效取值**（`q=中小企业` → `totalCount` 1780→16，命中全为真政策），而 `title:content:summary`/`content`/`summary`/`titleOnly` 等**一律等于不过滤** —— 即 MCP 当前那个字段一直是**静默无效值**，这才是相关度崩掉的第一因（此前只归因于 catMap 拼接）。关键组合 `t=zhengcelibrary_gw_bm_gb` + `searchfield=title` + `timetype=timezd`&`mintime/maxtime`（**完全落在 MCP 现有代码路径上**）实测返回 31 篇全相关文件（优质中小企业梯度培育管理办法 / 支持专精特新中小企业高质量发展通知 …），**故 MCP 侧只需改一行**。另测出最大陷阱：**严格条件命中 0 条时接口静默退回全量浏览列表**（`q=数据要素+title`、`q=量子计算+title` 均回 1780 条噪声；`q=中小企业+title+2020窗口` 甚至回一篇 2025 年文件）→ **「无相关政策」必须靠客户端二次校验得出**；突发连续请求返回 `code=1001`（疑似限流，同请求两次结果 1001/200 不一致）。同时确认 **`content_type` 对抓取零影响**（仅校验/落库/回传/UI 可选，流水线从不读，API 亦无对应参数）。详细参数对照表已写入 Phase 9「官方检索 API 参数对照（实测记录）」。**仍待用户决策**：是否按①②③（改 MCP 一行 + server 侧客户端复核 + 处理 `content_type`）实施 |
| 2026-10-12 | 9 | 参数测绘补完 | 用户提供官网**「政策解读」标签页的真实抓包**（`t=zhengcelibrary&q=&timetype=timeyy&sort=score&sortType=1&searchfield=title&subchildtype=2229&puborg=&pcodeYear=&pcodeNum=&filetype=&p=1&n=5&inpro=&dup=&orpro=&type=gwyzcwjk`），据此补测并**推翻/补完上一条的两处结论**：① **相对时间窗确实存在，只是取值是拼音缩写**——`timetype=timeyz`(一周内)/`timeyy`(一月内)/`timeyn`(一年内)/`timeqb`(不限)，实测 `q=批复` 分别返回 1 / 2 / 22 篇（一周内恰好只剩 2026.10.08 那篇），且**在 MCP 现有通道 `zhengcelibrary_gw_bm_gb` 上同样生效**；首轮用 `timeweek/timemonth/timeyear` 测试**全部误判为「无效」**，已在对照表中更正。② **政策解读不是独立通道，而是 `t=zhengcelibrary` 的 `otherfile` 目录**——MCP 现用的 `zhengcelibrary_gw_bm_gb` 的 `catMap` **不含 `otherfile`**，故政策解读一直被整个漏掉；`q=中小企业+title` 在 `t=zhengcelibrary` 下 `otherfile` 返回 3 条（「推动中小企业回款难问题整体好转」「治理中小企业回款难 这场吹风会深入解读」等）。③ 抓包里的 `type=gwyzcwjk`、`subchildtype=2229` 经逐项复测**均无实际作用**（带上 `q` 后 8 种 `subchildtype`/机构类取值结果完全一致），**发布机构分类的真实参数名仍未确定**。④ 0 命中时接口行为**不一致**：`q=数据要素+title` 静默退回全量浏览（1780 条），而 `q=中小企业+timeyz`（一周内确无该关键词的标题命中）返回 `code=1001` 空集 → **进一步坐实「客户端二次复核不可省」**。对照表已在 Phase 9「官方检索 API 参数对照（实测记录）」中整体更正 |
| 2026-10-12 | 9 | ⛔→✅ 阻塞解除（发布机构分类破案） | 用户指出上一轮抓包的 `facetMap.bmfl` **就是发布机构分类的内容**（工业和信息化部:1411条 … `count:12798`），并更正上一条「参数名仍未确定」的判断。据此分 6 轮实测（先后约 50 次请求，均已写入 Phase 9 对照表）：**根因是「测错了通道」**——`bmfl` / `childtype` / `pubtimeyear` / `pcodeJiguan` **只在 MCP 现用通道 `t=zhengcelibrary_gw_bm_gb` 上生效**，此前全在 `t=zhengcelibrary` 上测，故得出「8 种取值全部无效」的错误结论。**`bmfl=<机构名>` 为发布机构筛选参数**，取值即 `facetMap.bmfl` 的键，实测 `q=中小企业+title` 下 `bumenfile` 114 → 工信部 **76** / 财政部 **42** / 发改委 **19** / 教育部 **8**，**与 facet 计数逐一吻合**；浏览态 `bumenfile` 12798 → 财政部 **2226**。另测出：`childtype`=主题分类（114→42/50，与 `parentid` facet 吻合）、`pubtimeyear`=发布年份（114→13/7/20）、`pcodeJiguan`=文种（`gongwen` 16→10，与 `groupMap.国办发=10` 吻合）；**筛选器可叠加求交集**（`bmfl=财政部+pubtimeyear=2024`→7）。**已排除限流噪声**：7 次交错 baseline 恒为 186，各变体精确复现。**两个新问题**：① **`gongbao` 不受 `bmfl`/`pubtimeyear`/`childtype` 过滤**（恒 56 条，只有 `timetype` 能压下去）→ 按机构筛选时公报会整批混入，实现须单独丢弃；② **通道能力互斥**——`zhengcelibrary_gw_bm_gb` 有筛选器但无 `otherfile`（政策解读），`zhengcelibrary` 反之，**上一轮「切通道带回政策解读」的建议与本轮筛选能力冲突**，需用户拍板二选一或双通道并集。`puborg`/`filetype` 会清空 `gongwen`、`orpro`/`inpro`/`dup`/`nodeid` 无效，均已在表中标 ❌。**仍待用户决策**：是否按最新①~⑤ 实施（改 `searchfield=title` + 新增 `bmfl`/`childtype`/`pubtimeyear` 透传 + 客户端复核 + 公报单独处置 + `content_type` 标注） |
| 2026-10-12 | 9 | 代码完成（筛选器落地） | 用户裁定：**保持现通道 `zhengcelibrary_gw_bm_gb`、接筛选器、同步前端、去掉无用控件、优化体验**。据此落地「发布机构 + 文种」两档筛选 + 相关度修复，端到端贯通。**MCP**（`tools/China-Central-Policy-MCP`）：① `searchfield: 'title:content:summary'` → **`'title'`**（第一因修复）；② `get_latest_policies` 增可选 `agency`/`doc_type` 标量参数并透传 `bmfl`/`pcodeJiguan`；③ `run()` 分派改为「关键词**或**任一筛选存在」都走 API（否则筛选+无关键词会静默落空到 HTML 抓取器）；④ item 增 `catalog_key`；⑤ **新增 `get_policy_facets` 工具**（读 `facetMap.bmfl`/`groupMap`，返回机构/文种名单+条数）并在 `index.ts` 注册。**DB**：`crawl_task` 增 `agencies`/`doc_types TEXT[]` + 幂等 `ALTER TABLE ADD COLUMN IF NOT EXISTS`；`policy_document` 增 `catalog`；`v_policy_results` 补 `catalog`。**服务端**：`TaskCreate`/`TaskDTO` 移除 `content_type`/`schedule`、新增 `agencies`/`doc_types`（`from_row` 新键用 `.get` 防御未迁移老库）；`create_task`/`insert_policies` 同步；**新增 `GET /api/filters/facets`**（实时 facet 优先、`app/data/agencies.py` 静态回退）；`_search_policies` 改**扇出**（`keyword×agency×doc_type` 笛卡尔积，`pipeline_max_search_calls=12` 封顶）+ **客户端复核**（标题含关键词 + 日期在窗口内 + 筛选生效时丢弃 `gongbao`）；`fetch_node` 0 结果文案扩成「调整关键词、时间范围或筛选条件」。**前端**：`ConfigPanel` **删除 `内容类型`/`抓取源`/`抓取周期` 三个死控件**，新增 `发布机构`（多选 chip 带条数，实时/内置标记）与 `文种`，时间预设对齐官网（增「近一年」），折叠态显示已选筛选摘要；`TopBar` 无关键词时拦截并提示；`ConfirmPanel` 增「来源」列（公文/部门文件/公报）；`store` 增 `facets`/`loadFacets`。**关键实测约束**：API **静默忽略重复筛选参数**（`bmfl=A&bmfl=B` 等于不过滤）→ 多选只能服务端逐值扇出再按 url 去重。文档：`CLAUDE.md`（MCP quirks + 检索筛选段）、`server/README.md`（facets 接口 + curl 示例）。**仍待验收**：`npm run build` 重建 MCP + `database/init.sh` 迁移 + 端到端（详见 Phase 9 验收段） |
| 2026-10-12 | 9 | 缺陷修复 | 实机 `GET /api/filters/facets` 报 **404**。根因：`app/api/routes/filters.py` 的 `APIRouter(prefix="/filters")` **漏了 `/api`**（同目录 agent/tasks/policies 均为 `/api`…），实际注册成 `/filters/facets`。修：前缀改 `/api/filters`。仅此一行 |
| 2026-10-12 | 9 | 缺陷修复 | 用户报「点某些文种报错 `未知文种：国函（可选：国令、国发、国办发、国办函）`」。根因：`get_policy_facets` 把 gov.cn 的 `groupMap` **原样透传**（实时名单含 **国函**，官方公文文种共五个），而 `schemas/task.py` 的 `DOC_TYPES` 只硬编码了四个 → 实时模式下点了就 422（回退静态名单恰好等于白名单四值，故只在实时态偶发）。修：`DOC_TYPES` 与 `data/agencies.py::FALLBACK_DOC_TYPES` 双双补「国函」；`schema.sql` 注释同步（注释变更，无需重跑 `init.sh`）。校验器**必须保留**——gov.cn「严格 0 命中静默退回全量」的怪癖下，放行垃圾文种会让筛选完全失效且不报错 |
| 2026-10-12 | 9 | 缺陷修复 | 用户指出「关键词为空时走了另一条抓取路线，拿的内容不对」，要求弃用。确认根因：`getLatestPolicies.ts::run()` 原为 `if (keyword \|\| 筛选) → searchPolicyAPI ... else → fetchFromGovCnLatest`，**后者抓的是 `www.gov.cn/zhengce/zuixin/` 的「最新政策」HTML 列表**——另一个更小的源，且**静默忽略全部筛选**（故「不输关键词只看某机构发文」也会退化成全量最新列表）。修：**删掉 else 分支与 `fetchFromGovCnLatest`/`fetchGovCnPage` 两个死函数，一律走官方检索 API**（空 `q` 即政策库浏览态，Phase 9 已实测有效：`bmfl` 在 `q=''` 下 `bumenfile` 12798→财政部 2226）。顺带 `doc_type` 描述补「国函」。**⚠️ 必须 `npm run build`**——服务端加载 `build/index.js`，不重建则本次改动是静默空操作 |
| 2026-10-12 | 4 | 需求变更 | 用户裁定「知识」区语义：**不是**读懂这篇政策所需的背景知识（术语解释 / 概念科普 / 政策沿革），而是**从这份文件里可沉淀、能指导我们行动的知识**——政策划了哪些线、开了哪些口子、窗口期多长、钱和资源往哪投、什么主体在什么条件下能拿到什么。改：`extract.py` 的 `SYSTEM_PROMPT` 知识条目重写 + 新增「不要写名词解释/概念科普」负向约束、`ConceptPayload`/`KnowledgePayload` 字段描述改为「标题（一句可执行的判断）/ 依据与做法 / 适用领域 / 行动建议」；`schemas/policy.py` 的两个 docstring 同步；`DetailDrawer.vue` 三块标题改为「知识点 / 适用领域 / 行动建议」（空态文案、复制文本同步）；`schema.sql` 三列注释同步。**字段键名（`term`/`desc`）与 jsonb 列名（`knowledge_concepts` 等）保持不变**，故不改表、不需重跑 `init.sh`、历史行照常渲染。静态检查：Python 两文件 `ast.parse` 通过；前端未跑 `vue-tsc`（按约定不主动构建）。**待验收**：重新提取任一篇，知识区应给出可执行结论而非术语解释 |
| 2026-10-12 | 9 | 需求变更（筛选栏改版） | 用户裁定交互：**不要预填关键词**；用户在搜索框输入内容后，发布机构与文种以**下拉框**筛选；无匹配时提示「没有相关内容」；**不输关键词也要能用**（如只看某机构近期发文）。设计稿 `design.pen` 的「筛选行」(`rD50Q`) 已改为两个下拉选择 + 一行提示。**前端**：`store.ts` 去掉预填的 `['人工智能','中小企业','产业基金']`；`TopBar.onStart` **删除「必须先有关键词」的拦截**（关键词改为可选范围，留空即按筛选条件浏览）；`ConfigPanel.vue` 把「多选 chip 行」换成**多选下拉**（沿用设计稿样式：图标 + `全部发布机构`/`全部文种` 占位 + chevron，展开面板内勾选并显示条数，已选项以 `tag accent` 展示，点击外部收起）；`AppIcon` 补 `building-2`/`file-text`/`info`/`check`；关键词标签与占位文案对齐设计稿。**下拉语义**：用户选定**多选**（保留 `keyword×agency×doc_type` 扇出能力），关闭态显示「已选 N 项」。**后端**：`_search_policies` 早已支持空关键词（`kw_list=[None]` → 不带 `q` 的浏览态），本次仅前端放开。静态检查：Python `ast.parse` 通过；前端未跑 `vue-tsc`（按约定不主动构建）。**待验收**：不填关键词 + 选一个机构 → 能出结果；关键词选到 0 命中 → 下拉显示「没有相关内容」 |
| 2026-10-12 | 9 | 根因修正（facet「没有相关内容」永远不出现） | 用户要求「无匹配时提示没有相关内容」，实测发现实现不了。分 4 轮探针（`.probe/facets{,2,3,4}.py`）查明两件事：① **`code=1001` 不是限流**（TODO 早前记为「疑似限流」**是错的**）——它的 `msg` 就是「抱歉，没有找到相关结果」，且**完全跟随查询**：`中小企业` 每次 `200`、`量子计算`/`火星移民` 每次 `1001`，与请求时机无关。即 gov.cn 用**非 200 状态码明确表达「0 命中」**，而不是返回空列表。② facet 确实随关键词收窄（浏览态 77 个机构 / `bumenfile` 12798 → `中小企业` 27 个 / 114），**且 `bmfl_len=0` 时也可能有真实结果**（`中小企业`+2015 窗口：`gongwen=1`+`gongbao=2` 但机构数为 0）。**修**：`filters.py` 原先 `if agencies or doc_types` 才返回 `live`，否则掉进内置名单——而 0 命中时两个名单皆空，**于是永远显示内置名单，「没有相关内容」永不出现**。改为「MCP **调用成功**就如实返回（可为空列表，`source="live"`），只有异常/超时/非 JSON 才回退内置名单」；`get_policy_facets` 返回的是合法 JSON 空数组（`tool_json` 不抛错），故能可靠区分「成功但 0 命中」与「调用失败」。另把 `searchPolicyAPI` 的 `code!=200` 显式带进 `debug`，避免真正的接口错误被当成「无结果」。**推论**：Phase 9 记录的「严格 0 命中静默退回全量浏览列表」应主要由当时的 `searchfield` 无效值（`title:content:summary`）造成，改 `title` 后 0 命中会显式报 `1001`——`_search_policies` 的标题复核仍是廉价保险。**待验收**：关键词填 `量子计算` → 两个下拉均显示「没有相关内容」 |
| 2026-10-12 | 9 | 缺陷修复（facet 有 1 条却报「未检索到匹配的政策原文」） | 用户报：「筛选完时间后单独勾选发布机构、文种默认全部，发布机构明明显示有 1 条，却提示抓取失败：未检索到匹配的政策原文」。3 个探针（`.probe/agency_mismatch.py`（facet 计数 vs 实拉条目）、`slice_probe.py`（照 MCP 原样复现 catMap 拼接 + `slice(0,limit)`）、`date_field_probe.py`（换窗口验证接口按哪个字段过滤））查明根因**在 MCP 的日期取值**：`getLatestPolicies.ts` 原先 `formatDateFromEpoch(result.ptime) \|\| formatDate(result.ptimeStr)`——**优先取 `ptime`（epoch，实测常是更早的「成文日期」）**，而 gov.cn 做时间窗过滤用的是**站点显示的那个发布日期**（`ptimeStr`/`pubtimeStr`）。实测（窗口 2026-10-05..10-12 + `bmfl=工业和信息化部`，即用户场景）：唯一真命中 `ptimeStr=2026.10.08` 在窗内、`ptime` 解码为 **2026-10-03 出窗**；同批 `bumenfile` 行 `ptimeStr=2026.10.10` / `ptime=2026-09-11` 同样出窗；12 条公报行 `ptimeStr` 全为 `2026.10.10`（该期公报出版日）而 `ptime` 散在 2026-05..09。**于是流水线规则③「日期必须落在窗口内」把接口按窗口返回的行整批误判越界 → 0 条**，而 facet 计数（1）是接口自己的口径，二者必然矛盾。第二个（潜在同类）缺陷：MCP 先按 catMap 顺序全量拼接再 `items.slice(0, ten)`（`pipeline_per_keyword_limit=10`），公报行**恒不受 `bmfl`/`pcodeJiguan` 过滤**（恒 12–56 条），一旦非公报行不足十条，`limit` 会被随后被调用方整批丢弃的公报占满。**修**：① MCP 日期优先级反转为**文本日期优先、epoch 兜底**（`formatDate(ptimeStr\|pubtimeStr\|publishtime\|time) \|\| formatDateFromEpoch(ptime\|pubtime)`，两者都覆盖：另一个探针里接口对整批记录**不返回** `ptimeStr`，此时只能落 epoch，而该批 `ptime` 8/9、20/25 在窗内）；② MCP 在 **`slice` 之前**丢弃公报（`(agency \|\| docType) && catKey === "gongbao"` → `continue`），空筛选的浏览态仍保留公报；③ 流水线侧规则①保留为兜底并注明「MCP 已先丢，防线上跑旧 `build/index.js`」、规则③补注「其前提是 MCP 报的 date 与接口窗口同源」。**未动** `_parse_date`（本就兼容 `-/.` 分隔，非根因）。静态检查：`npx tsc --noEmit` 通过（`TYPECHECK DONE`，无输出）；**⚠️ 必须 `npm run build`** 重建 `build/index.js`，否则本次 MCP 改动与上一轮一样是静默空操作。**待验收**：复现原场景（选时间窗 + 单个发布机构）应能检索到与 facet 计数一致的篇目 |
| 2026-10-12 | 9 | 需求变更（配置面板精简 + 高级设置） | 用户四点要求：① 发布机构 / 文种**删掉条数显示**；② 删除「导入 JSON 配置」；③ `.env` 移到**仓库根目录**；④ `.env` 里的五个 `PIPELINE_*` 参数显示在页面「高级设置」（默认收起），且**用户可改了用**（后续消息确认：展示 .env 默认值、按任务覆盖）。实现：**前端** `ConfigPanel.vue` 删菜单 `.menu-count` 与已选 chip 的条数后缀、简化 `selectedAgencies/selectedDocTypes`（不再按 count 排序）；删 `pickFile/onFileChange/fileInput/config-foot` 与导入按钮；新增默认收起的「高级设置」区（五个数字输入框，预填 `GET /api/config/pipeline` 的 .env 默认值，`setPipelineValue` 把空/非法输入记为 null，附「恢复默认」）。**后端**：`app/config.py` 改为按 `__file__` 定位根目录 `.env`（`load_dotenv(_REPO_ROOT/'.env')` + `env_file` 同步，工作目录无关）；新增 `app/schemas/config.py::PipelineConfig` 与 `GET /api/config/pipeline`（只读透出生效默认值）；`crawl_task` 增 5 个可空 `pipeline_*` 列（`schema.sql` 建表 + 幂等 `ALTER TABLE ADD COLUMN IF NOT EXISTS`，老库重跑 `init.sh` 即可，不重置数据）；`create_task` 落库覆盖值；`TaskCreate`/`TaskDTO` 增字段（`from_row` 用 `.get` 防御老库）；`crawl_graph`：`PipelineState` 增 5 个覆盖键 + `_effective()`（任务值优先、空则回退 .env），`fetch_node`（max_policies）、`_search_policies`（per_keyword_limit/mcp_timeout）、`extract_node`（extract_concurrency）、`_fetch_fulltext`（mcp_timeout）、`extract_policy`（max_body_chars）全部改为「任务覆盖 → 全局默认」两段取值；`run_extraction_phase`/`run_single_extraction` **从任务行重读覆盖值**（两段式流水线第二段是确认后重建的，创建请求早已结束，只有库里的值还在）。**覆盖值必须落库的原因**：第二段提取由 `POST /{id}/confirm` 触发，`run_extraction_phase` 只拿 task_id 从库里重建，不传请求参数。前端 `types.ts`/`store.ts`（form 增 5 字段、`loadPipelineConfig` 回填空位、`resetPipelineOverrides`、`startTask` 空串转 null）、`client.ts` 加 `api.pipelineConfig`。**文档**：`.gitignore`（根，忽略 `.env`）、`.env.example` 头注、`CLAUDE.md`（config 段 + Business API 补 `/api/config/pipeline`）、根/server/database 三个 `README.md` 的 .env 位置与路由清单。静态检查：Python 9 文件 `ast.parse` 全通过；前端未跑 `vue-tsc`（按约定不主动构建）。**待验收**：重跑 `database/init.sh`（幂等补列）+ 重启 server；页面上发布机构/文种下拉不再显示条数、无导入按钮；「高级设置」展开显示 .env 默认值，改成 5/并发 2 后发任务，日志应体现（如第 N/M 篇节奏变化）；单篇重试沿用创建任务时的覆盖值 |
| 2026-10-12 | 9 | 缺陷修复（关键词复核误杀分词命中） | 用户报「输入『水域』，发布机构有 6 项、文种为空，抓取报未检索到匹配的政策原文」。实探（`get_latest_policies q=水域`）：接口正常返回 gongwen 17 / bumenfile 35 条候选、items 标题全是「…流域防洪建设…」「…水污染防治…」——**gov.cn `searchfield=title` 是分词检索**（水域 → 水 + 域），不是精确子串；而流水线复核规则②「标题必须包含完整关键词」把这批正常命中整批误杀。修：规则②放宽为「标题与关键词**至少共享一个字**」（`keyword not in title and not any(ch in title)` 才丢）——仍能拦住「0 命中静默退回全量浏览列表」的整页噪声（与关键词毫无交集），不再误杀分词命中。实测：`水域` 3 篇通过（= .env 的 PER_KEYWORD_LIMIT=3，全 gongwen）、`量子计算` 0 篇（真无命中，依旧如实失败）。文档：CLAUDE.md quirks 段同步。**待验收**：重启 server 后搜「水域」应能出候选篇目 |
| 2026-10-13 | 10 | 代码完成 | 需求：定时自动运行抓取工作流。用户裁定：① 触发时**自动全选直接跑完**（跳过人工确认）；② 周期用**预设档位**（每 N 分钟/每 N 小时/每天/每周）；③ **落库 + 重启自动恢复**；④ 窗口须解决「固定窗口+上限 N+最新优先=永远只抓最新 N 篇」（用户举例：9 月 3 篇上限 3，永远抓这 3 篇、8 月永远拿不到）。窗口方案：**滑动游标 + 跨轮去重**——`cursor_before` 为下轮窗口上界（NULL=追 `[date_from, 今天]`；非 NULL=回填 `[date_from, cursor-1天]`）；本轮**抓满上限**→游标推进到「本轮最旧一篇发布日期−1 天」；**没抓满**→窗口掏空、游标回 NULL 追新；每轮把历史轮次已入库 `source_url` 从候选剔除，剔空记 `noop`（正常跳过，不留失败任务、不重复入库）。**后端**：`schema.sql` 加 `schedule_job` 表（配置快照+调度参数+游标+运行记录，幂等）+ `crawl_task.schedule_job_id`（`ON DELETE SET NULL`）+ `schedule_type` 枚举补 `minutely`/`hourly`；`app/scheduler.py`（15 s tick 调度循环、先认领 `next_run_at` 再执行、`compute_next_run` 本地时间口径 weekly 对齐 Python weekday()、`run_schedule_job` 检索→建任务→自动确认→直接 `run_extraction_phase`→游标推进）；`app/api/routes/schedules.py`（GET/POST `/api/schedules`、`/{id}/toggle`、`/{id}/run`、`DELETE /{id}`）；`app/schemas/schedule.py`（档位必填项/文种白名单校验）；`_search_policies` 加 `exclude_urls`；lifespan 启动 `start_scheduler()`（补排缺失 `next_run_at`）→关停 `shutdown_scheduler()`。**前端**：`ConfigPanel` 加「入库方式」（一次性/定时自动入库）；`TopBar` 加「定时任务」入口（启用数徽标）+ 定时模式主按钮变「定时入库」开弹窗；新增 `ScheduleModal.vue`（遮罩层+居中卡片，列表视图 启停/立即运行/两步删除/新建，新建视图 档位选择+条件输入+表单快照摘要+游标行为提示）；`store` 加 `ingestMode`/弹窗状态/CRUD 动作；`types.ts`/`client.ts` 同步。静态检查：Python 6 文件 `py_compile` 全通过；`compute_next_run` 8 条断言（分钟/小时/每天未到点/每天已过点/每周三种情形）通过；`ScheduleCreate` 合法 3 组 + 非法 4 组校验通过；前端未跑 `vue-tsc`（按约定不主动构建）。**待验收**：见 Phase 10 验收段（重跑 `database/init.sh` → 建任务 → 立即运行 → 连续触发验证游标回填 → 停用/启停 → 重启恢复） |
