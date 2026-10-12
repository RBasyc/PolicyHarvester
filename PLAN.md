# 打通三端（前端 · 后端 · 数据库）实施计划

> 目标：把 `web/` 的静态 mockup、`server/` 的 LangGraph Agent、`database/` 的 PostgreSQL schema 三者打通，
> 让设计稿 `design.pen` 描述的功能在真实数据链路上跑通。
>
> 状态：**待确认**（请审阅后再执行）

---

## 一、现状盘点

| 端 | 现状 | 缺口 |
|---|---|---|
| **前端** `web/` | Vue 3 静态稿，5 个组件全部读 `src/data.ts` 硬编码；无 HTTP 调用、无 loading/empty/error 态 | 需要 API client、类型对齐、状态驱动渲染、SSE 订阅 |
| **后端** `server/` | 只有通用 ReAct 对话（`/api/chat`、`/api/chat/stream`）+ 健康检查；**完全没连数据库**；MCP 已接（`get_latest_policies` / `get_policy_fulltext`） | 需要 DB 访问层、抓取/提取业务 API、抓取流水线、进度推送 |
| **数据库** `database/` | schema + seed 已就绪（5 表 1 视图），数据与 `data.ts` 同源 | 无人读写，需后端接入 |

**结论**：三端之间的「合同」已经设计好了（seed.sql 就是按前端字段造的），缺的是中间那一层**后端业务 API + 流水线**。

---

## 二、设计稿要实现的完整功能清单

来源：`design.pen` 的三个标注帧（`Y31pdk` 主流程标注 / `pTzyh` 关键状态示例 / `hJck9` 响应式说明）+ 5 个组件源码。

### 2.1 主流程（5 步）

```
1 配置抓取参数 → 2 开始抓取 → 3 进度与日志反馈 → 4 结果列表 → 5 详情抽屉 · 事实/信息/知识
```

### 2.2 关键状态清单（后端必须能表达这些状态）

| 区域 | 状态 | 设计稿文案 / 表现 | 后端要提供的字段 |
|---|---|---|---|
| 进度条 | 未开始 | 0% | `status=pending`, `progress=0` |
| 进度条 | 进行中 | `60% · 正在提取第 3/10 篇` | `progress`, `stage` |
| 进度条 | 已完成 | `100% · 已完成` | `status=completed` |
| 进度条 | 失败 | `失败 · 连接超时，请重试` | `status=failed`, `stage` 带原因 |
| 结果行 | ×4 | 未提取 / 提取中 / 已提取 / 提取失败 | `extraction.status` 四态 |
| 空状态 | 尚未抓取 | 「还没有抓取结果」+「配置抓取参数后，点击右上角「开始抓取」获取政策原文」 | `GET /tasks/current` 返回 `null` |
| 三栏 | 加载中 | 骨架屏 | `status=extracting` |
| 全局 | 抓取失败 + Toast | 「抓取失败：连接 policy-mcp 超时（10s），请确认本地服务已启动后重试」+ 重试 | 任务 `failed` + `error` 文案 |
| 单栏 | 提取失败 | 「模型返回超时（30s），请点击重试」+ 重试 | `extraction.error` |
| 单栏 | 空内容 | 「暂无提取内容」 | `extracted` 但三区皆空 |

> 注：设计稿里抓取源写作 `chuance-policy-mcp`，仓库实际为 `China-Central-Policy-MCP`；`seed.sql`/`data.ts` 用的是后者。**建议统一为 `China-Central-Policy-MCP`**。

### 2.3 前端元素 → 接口 → 表 映射

| 前端元素 | 需要的接口 | 数据库来源 |
|---|---|---|
| TopBar「开始抓取」按钮 + 运行中状态 | `POST /api/tasks` / `GET /api/tasks/current` | `crawl_task.status` |
| 抓取配置表单（源/类型/时间/周期/关键词/导入 JSON） | 同上（提交）；`GET /api/config/options`（下拉选项，可选） | `crawl_task.source/content_type/date_from/date_to/schedule/keywords` |
| 进度面板（进度条/阶段/统计/日志流） | `GET /api/tasks/{id}/stream`（SSE） | `crawl_task`（计数 + stage）+ `task_log` |
| 结果表格（含统计与重试） | `GET /api/tasks/{id}/policies` + `POST /api/policies/{id}/retry` | `v_policy_results` + `extraction.status` |
| 详情抽屉三栏 | `GET /api/policies/{id}/extraction` + `POST /api/policies/{id}/reextract` | `policy_document` + `extraction` + `fact_item` |

---

## 三、API 契约草案（`/api`）

```jsonc
// 任务状态（轮询 & SSE 共用同一形状）
GET /api/tasks/current            → TaskDTO | null
GET /api/tasks/{id}               → TaskDTO
{
  "id": 1, "source": "国务院政策文件库（gov.cn）", "content_type": "policy",
  "date_from": "2025-06-01", "date_to": "2025-06-08", "schedule": "manual",
  "keywords": ["人工智能","中小企业","产业基金"],
  "status": "running",                 // pending|running|completed|failed
  "stage": "正在调用大模型提取：第 3/10 篇 ——《…》",
  "progress": 60, "eta_seconds": 120,
  "total_count": 10, "fetched_count": 10, "extracted_count": 6, "failed_count": 0,
  "started_at": "...", "finished_at": null
}

GET  /api/tasks/{id}/logs          → [{ "time":"[10:23:41]", "tone":"info|success|active", "message":"…" }]
GET  /api/tasks/{id}/stream        → SSE：{type: task|log|stage|progress|done|error}

GET  /api/tasks/{id}/policies
  → { "items":[{"id":1,"title":"…","publish_date":"2025-06-05","source_url":"…","status":"extracted"}],
      "summary":{"total":10,"extracted":6,"extracting":1,"pending":2,"failed":1} }

GET  /api/policies/{id}/extraction
  → { "policy":{"source_name":"…","title":"…","agency":"…","doc_no":"…","publish_date":"…","source_url":"…","fetched_by":"…"},
      "extraction":{ "status":"extracted","error":null,"model":"…","extracted_at":"…",
        "facts":[{"label":"时间","content":"…"}],
        "info":{"summary":"…","points":["…"]},
        "knowledge":{"concepts":[{"term":"…","desc":"…"}],"fields":["…"],"extension":"…"} } }

POST /api/tasks                    → { "task_id": 1 }          # 创建并后台启动抓取
POST /api/policies/{id}/retry      → { "status": "extracting" } # 失败重试
POST /api/policies/{id}/reextract  → { "status": "extracting" } # 重新提取
```

**约定**：错误统一 `{"detail": "..."}`；SSE 沿用 `/api/chat/stream` 的 `data: {...}\n\n` 格式（见 `app/api/routes/agent.py`）。

---

## 四、分 Phase 实施计划

### Phase 1 — 后端数据访问层（地基）

**目标**：后端能连上 `agent_demo` 库并读写五张表。

- 新增 `server/app/db/`：`pool.py`（psycopg3 异步连接池）、`repositories.py`（按表的读写函数）
- `app/config.py` 增加 `database_url`
- `pyproject.toml` 增加 `psycopg[binary,pool]`
- `server/.env.example` 增加 `DATABASE_URL=postgresql://kos:kos@localhost:5433/agent_demo`
- 新增 `server/scripts/test_db.py`（连通性 + 读出 seed 数据）

**验收**：`uv run python scripts/test_db.py` 打印出 `crawl_task` 与 `v_policy_results` 的 seed 数据。

---

### Phase 2 — 只读查询 API

**目标**：前端展示所需的数据全部可从接口拿到（数据就是 seed 数据）。

- 新增路由 `app/api/routes/tasks.py`、`app/api/routes/policies.py`
- 新增 `app/schemas/task.py`、`app/schemas/policy.py`
- 实现 `GET /tasks/current`、`GET /tasks/{id}`、`GET /tasks/{id}/logs`、`GET /tasks/{id}/policies`、`GET /policies/{id}/extraction`
- `progress` / `eta_seconds` 在此阶段先按 `extracted_count / total_count` 计算并写死逻辑（Phase 4 细化）

**验收**：Swagger (`/docs`) 逐条调用，返回值与 `data.ts` 中对应字段**逐项对得上**（含空状态：删掉任务后返回 `null`）。

---

### Phase 3 — 写操作与动作 API

**目标**：按钮能用。

- `POST /api/tasks`：校验表单 → 插入 `crawl_task`（`status=pending`）+ 首条 `task_log` → 返回 `task_id`
- `POST /api/policies/{id}/retry`：`failed → pending`，`retry_count+1`，记日志
- `POST /api/policies/{id}/reextract`：清空旧 `extraction`/`fact_item` → `extracting`
- 真正的后台执行留到 Phase 4（本阶段先落库 + 状态流转）

**验收**：创建任务后 `GET /tasks/current` 能读到；retry 后状态与 `retry_count` 正确流转。

---

### Phase 4 — 抓取 + 提取流水线（核心）

**目标**：任务真正从 gov.cn 抓取政策，并用大模型结构化提取成「事实/信息/知识」。

- 新增 `server/app/pipeline/`：
  - `crawl_graph.py` — LangGraph 图：`fetch（MCP get_latest_policies）→ 落库 policy_document → 逐篇 extract → 汇总`
  - `extract.py` — 定义提取用的结构化输出 schema：`facts[] / info{summary,points} / knowledge{concepts,fields,extension}`，复用 `nodes.py` 的 `_build_llm()`
- 单篇流程：`get_policy_fulltext(url)` 取全文 → LLM 结构化提取 → 写 `extraction` + `fact_item` → 更新 `crawl_task` 计数与 `stage` → 写 `task_log`
- 容错：单篇失败标记 `failed` 并继续；MCP 不可用则任务整体 `failed` 且 `stage` 给出设计稿要求的中文提示
- 由 `POST /tasks` 触发，在异步任务中执行（不阻塞请求）

**验收**：`POST /api/tasks` 后，结果表格出现**真实 gov.cn 政策**，第 1 篇抽屉里三栏有真实提取内容。

---

### Phase 5 — 进度实时推送（SSE）

**目标**：进度面板「活」起来。

- `GET /api/tasks/{id}/stream`：基于 `task_log` 增量 + `crawl_task` 快照推送
- 事件：`task`（全量状态）、`log`（单条新日志）、`done`（任务终态）
- `progress` 语义修正：抓取阶段 `0→50`（`fetched/total`），提取阶段 `50→100`（`extracted/total`）；`eta_seconds` 用已完成篇目均值外推

**验收**：前端（或 curl）订阅后能看到日志逐条出现、进度条推进、结束时收到 `done`。

---

### Phase 6 — 前端接入

**目标**：`data.ts` 退场，组件由接口驱动。

- 新增 `web/src/api/client.ts`（fetch 封装 + SSE）+ `web/src/api/types.ts`
- `vite.config.ts` 加 `/api` → `http://localhost:8000` 代理
- 组件改造：
  - `ConfigPanel` → 受控表单，关键词可增删，提交给 `POST /tasks`
  - `TopBar` → 「开始抓取」调 `POST /tasks`；状态标签读 `status`
  - `ProgressPanel` → 订阅 SSE；实现未开始/进行中/完成/失败 四态
  - `ResultTable` → 接口数据 + `summary` + 失败行重试；**空状态**与骨架屏
  - `DetailDrawer` → 按 `policy_id` 拉详情；三栏加载骨架屏、单栏失败/空内容、`重新提取`

**验收**：点「开始抓取」→ 进度条与日志实时更新 → 表格填充 → 点行进抽屉看到三栏 → 重新提取可用。全程无硬编码数据。

---

### Phase 7 — 端到端验收与文档

- 按第二节的**关键状态清单**逐条验收（含空状态、失败、超时文案）
- 更新 `CLAUDE.md`（补 db/pipeline/API 章节；**修正过时描述**：文中 `source_catalog` 工具已不存在，实际为 `get_latest_policies` / `get_policy_fulltext`）
- 更新 `server/README.md`、`database/README.md`（补「后端已接入」）

---

## 五、关键决策（请确认）

| # | 决策点 | 我的建议 |
|---|---|---|
| 1 | **抓取执行者**：独立 pipeline 图 vs 复用 ReAct Agent | **独立流水线图**。前端是确定性任务流，不需要对话式 Agent；Agent 图保持独立不干扰 |
| 2 | **数据来源**：真实 gov.cn vs 保留 mock | **真实抓取**，seed 数据保留作离线演示与兜底（加 `DEMO_MODE` 开关） |
| 3 | **DB 驱动** | **psycopg3 异步连接池**（与 FastAPI async 契合） |
| 4 | **进度推送**：SSE vs 轮询 | **SSE**，复用 `/chat/stream` 既有模式 |
| 5 | **提取模型** | 复用现有 `MODEL_NAME` 配置（不引入新模型/依赖） |
| 6 | **配置导入 JSON** | 纯前端解析并回填表单，不新增后端接口 |
| 7 | **抓取源命名** | 统一为 `China-Central-Policy-MCP`（设计稿的 `chuance-policy-mcp` 视为旧称） |

---

## 六、前置条件与风险

**前置**：① 本机 docker 容器 `server-db-1` 运行中且已执行 `database/init.sh`；② `node` 可用（MCP 走 stdio）；③ `server/.env` 已配好 LLM 认证。

**风险**：
- gov.cn 抓取为外部依赖，可能超时/改版 → 已设计降级与失败提示（Phase 4/5）
- 单篇提取耗时较长（设计稿「预计剩余约 2 分钟」）→ 流水线需并发或至少不阻塞请求
- 提取结果质量依赖模型 → 结构化输出用严格 schema + 失败重试，避免脏数据入库

---

**请确认第五节的 7 个决策点（或直接全部采纳建议），我再开始按 Phase 执行。**
