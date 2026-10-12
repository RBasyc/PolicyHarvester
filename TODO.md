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
| 1 | 后端数据访问层（地基） | ⬜ 未开始 | — |
| 2 | 只读查询 API | ⬜ 未开始 | — |
| 3 | 写操作与动作 API | ⬜ 未开始 | — |
| 4 | 抓取 + 提取流水线（核心） | ⬜ 未开始 | — |
| 5 | 进度实时推送（SSE） | ⬜ 未开始 | — |
| 6 | 前端接入 | ⬜ 未开始 | — |
| 7 | 端到端验收与文档 | ⬜ 未开始 | — |

---

## Phase 1 — 后端数据访问层（地基）

**状态**：⬜ 未开始 ｜ **目标**：后端能连上 `agent_demo` 库并读写五张表

- [ ] 新增 `server/app/db/pool.py`（psycopg3 异步连接池）
- [ ] 新增 `server/app/db/repositories.py`（按表的读写函数）
- [ ] `app/config.py` 增加 `database_url`
- [ ] `pyproject.toml` 增加 `psycopg[binary,pool]`
- [ ] `server/.env.example` 增加 `DATABASE_URL`
- [ ] 新增 `server/scripts/test_db.py`（连通性 + 读出 seed 数据）

**验收**：`uv run python scripts/test_db.py` 打印出 `crawl_task` 与 `v_policy_results` 的 seed 数据。

---

## Phase 2 — 只读查询 API

**状态**：⬜ 未开始 ｜ **目标**：前端展示所需数据全部可从接口拿到（数据即 seed）

- [ ] 新增路由 `app/api/routes/tasks.py`
- [ ] 新增路由 `app/api/routes/policies.py`
- [ ] 新增 schema `app/schemas/task.py` / `app/schemas/policy.py`
- [ ] `GET /api/tasks/current`（无任务返回 `null`）
- [ ] `GET /api/tasks/{id}`
- [ ] `GET /api/tasks/{id}/logs`
- [ ] `GET /api/tasks/{id}/policies`（含 `summary` 四态计数）
- [ ] `GET /api/policies/{id}/extraction`（三栏）
- [ ] `progress` / `eta_seconds` 先按 `extracted_count / total_count` 计算

**验收**：Swagger 逐条调用，返回值与 `data.ts` 字段逐项对得上；空状态返回 `null`。

---

## Phase 3 — 写操作与动作 API

**状态**：⬜ 未开始 ｜ **目标**：按钮能用

- [ ] `POST /api/tasks`（校验表单 → 插 `crawl_task` + 首条 `task_log` → 返回 `task_id`）
- [ ] `POST /api/policies/{id}/retry`（`failed → pending`，`retry_count+1`，记日志）
- [ ] `POST /api/policies/{id}/reextract`（清空旧结果 → `extracting`）
- [ ] 后台执行留待 Phase 4，本阶段先落库 + 状态流转

**验收**：创建任务后 `GET /tasks/current` 能读到；retry 后状态与 `retry_count` 正确流转。

---

## Phase 4 — 抓取 + 提取流水线（核心）

**状态**：⬜ 未开始 ｜ **目标**：真实抓取 gov.cn 并结构化提取「事实/信息/知识」

- [ ] 新增 `server/app/pipeline/crawl_graph.py`（fetch → 落库 → 逐篇 extract → 汇总）
- [ ] 新增 `server/app/pipeline/extract.py`（结构化输出 schema，复用 `_build_llm()`）
- [ ] 单篇流程：`get_policy_fulltext` → LLM 提取 → 写 `extraction` + `fact_item` → 更新计数/stage → 写 `task_log`
- [ ] 容错：单篇失败标记 `failed` 并继续；MCP 不可用则整体 `failed` + 中文提示
- [ ] 由 `POST /tasks` 触发，异步执行，不阻塞请求

**验收**：`POST /api/tasks` 后结果表格出现真实政策，第 1 篇抽屉三栏有真实提取内容。

---

## Phase 5 — 进度实时推送（SSE）

**状态**：⬜ 未开始 ｜ **目标**：进度面板「活」起来

- [ ] `GET /api/tasks/{id}/stream`（基于 `task_log` 增量 + `crawl_task` 快照）
- [ ] 事件类型：`task` / `log` / `done`（+ `error`）
- [ ] `progress` 语义修正：抓取 `0→50`，提取 `50→100`
- [ ] `eta_seconds` 用已完成篇目均值外推

**验收**：订阅后日志逐条出现、进度条推进、结束时收到 `done`。

---

## Phase 6 — 前端接入

**状态**：⬜ 未开始 ｜ **目标**：`data.ts` 退场，组件由接口驱动

- [ ] 新增 `web/src/api/client.ts`（fetch 封装 + SSE）、`web/src/api/types.ts`
- [ ] `vite.config.ts` 加 `/api` → `http://localhost:8000` 代理
- [ ] `ConfigPanel` → 受控表单，关键词可增删，提交 `POST /tasks`
- [ ] `TopBar` → 「开始抓取」调 `POST /tasks`；状态标签读 `status`
- [ ] `ProgressPanel` → 订阅 SSE，实现未开始/进行中/完成/失败 四态
- [ ] `ResultTable` → 接口数据 + `summary` + 失败重试；空状态 + 骨架屏
- [ ] `DetailDrawer` → 按 id 拉详情；三栏骨架屏、单栏失败/空内容、重新提取

**验收**：点「开始抓取」→ 实时进度与日志 → 表格填充 → 抽屉三栏 → 重新提取可用；全程无硬编码数据。

---

## Phase 7 — 端到端验收与文档

**状态**：⬜ 未开始 ｜ **目标**：按设计稿状态清单全量验收 + 文档对齐

- [ ] 按 [PLAN.md](PLAN.md) 第二节关键状态清单逐条验收（含空状态、失败、超时文案）
- [ ] 更新 `CLAUDE.md`（补 db/pipeline/API 章节；修正 `source_catalog` 过时描述）
- [ ] 更新 `server/README.md`、`database/README.md`

**验收**：验收清单全绿；文档无过时描述。

---

## 进度日志

> 规则：每完成一个 Phase（或发生阻塞/范围变更）追加一行，倒序不必——按时间正序追加。

| 日期 | Phase | 事件 | 说明 |
|---|---|---|---|
| 2026-10-12 | — | 计划建立 | 产出 [PLAN.md](PLAN.md) 与本 Todo List；7 个决策点待用户确认 |
