# Agent Demo Server

基于 **FastAPI + LangGraph + PostgreSQL** 的后端 Demo：既提供 ReAct 对话 Agent，也提供「抓取 gov.cn 政策 → 大模型结构化提取」的确定性流水线与配套业务 API。

## 项目结构

```
server/
├── main.py                 # 开发启动入口（python main.py，带热重载）
├── pyproject.toml          # 依赖管理（uv）
├── .env.example            # 环境变量模板
├── scripts/
│   ├── test_mcp.py         # MCP 连通性冒烟测试
│   └── test_db.py          # 数据库读写冒烟测试
└── app/
    ├── main.py             # FastAPI 应用工厂、lifespan（开池 -> MCP -> 编译图）、路由注册
    ├── config.py           # pydantic-settings 配置（读取 .env）
    ├── agent/              # A. ReAct 对话 Agent
    │   ├── state.py        #   图状态定义（messages + add_messages reducer）
    │   ├── tools.py        #   工具定义（@tool），在 TOOLS 列表中注册
    │   ├── mcp.py          #   China-Central-Policy-MCP 接入（stdio/node）
    │   ├── nodes.py        #   agent 节点（LLM 调用，绑定工具）
    │   └── graph.py        #   图构建：START -> agent <-> tools -> END
    ├── pipeline/           # B. 抓取 + 提取流水线
    │   ├── crawl_graph.py  #   prepare -> fetch -> extract -> summarize（失败走 fail）
    │   └── extract.py      #   单篇结构化提取（事实 / 信息 / 知识）
    ├── db/                 # 数据访问层
    │   ├── pool.py         #   psycopg3 异步连接池（进程级单例）
    │   └── repositories.py #   全部 SQL（参数化；计数由聚合派生）
    ├── api/routes/
    │   ├── health.py       #   GET  /health
    │   ├── agent.py        #   POST /api/chat、POST /api/chat/stream (SSE)
    │   ├── tasks.py        #   任务：创建 / 查询 / 日志 / 结果 / SSE 进度
    │   └── policies.py     #   单篇：提取详情 / 重试 / 重新提取
    └── schemas/            # 请求 / 响应模型（task.py、policy.py）
```

## 快速开始

```bash
# 1. 安装依赖（需要 uv）
uv sync

# 2. 配置认证：复制 .env.example 为 .env，二选一
#    - ANTHROPIC_API_KEY       官方 API Key
#    - ANTHROPIC_AUTH_TOKEN    Bearer Token（Kimi 等兼容网关，需配合 ANTHROPIC_BASE_URL）
#    抓取流水线还需要 DATABASE_URL（默认 postgresql://kos:kos@localhost:5433/agent_demo）

# 3. 准备数据库（docker 容器 server-db-1 运行中）
bash database/init.sh        # 建库建表 + 灌入演示数据（幂等）

# 4. 启动
python main.py
# 或：uvicorn app.main:app --reload
```

启动后访问 <http://localhost:8000/docs> 查看交互式 API 文档。

> 两个冒烟脚本：`uv run python scripts/test_mcp.py`（MCP 工具列表 + `get_latest_policies` 调用）、`uv run python scripts/test_db.py`（读 `crawl_task` / `v_policy_results` / `extraction`+`fact_item`）。

## API

### `POST /api/chat` — 非流式对话

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "现在几点了？", "thread_id": "user-1"}'
```

`thread_id` 相同的请求共享同一段对话记忆（由 LangGraph 的 `InMemorySaver` checkpointer 提供，重启后丢失；生产环境可换成 `PostgresSaver` 等持久化实现）。

### `POST /api/chat/stream` — SSE 流式对话

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "介绍一下你自己", "thread_id": "user-1"}'
```

事件格式：`data: {"type": "token", "content": "..."}`，以 `{"type": "done"}` 结束。

### 抓取任务 API

前端「配置抓取参数 → 开始抓取 → 进度日志 → 结果列表 → 详情抽屉」这条主流程全部由下列接口驱动。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/tasks` | 创建任务并**异步启动**抓取流水线，返回 `201 {"task_id": n}` |
| `GET` | `/api/tasks/current` | 最近一个任务（`TaskDTO`）；无任务时返回 `null`（前端空状态） |
| `GET` | `/api/tasks/{id}` | 单个任务快照（轮询用） |
| `GET` | `/api/tasks/{id}/logs` | 日志流 `[{time, tone, message}]`（`time` 已是 `[HH:MM:SS]`） |
| `GET` | `/api/tasks/{id}/policies` | 结果表格：`{items:[…], summary:{total,extracted,extracting,pending,failed}}` |
| `GET` | `/api/tasks/{id}/stream` | **SSE** 进度推送（见下） |
| `POST` | `/api/tasks/{id}/cancel` | **终止**运行中的任务：取消后台流水线并落 `failed` 终态（`stage` 为「任务已手动终止」）；已是终态返回 `409` |
| `DELETE` | `/api/tasks/{id}` | 删除单个任务及其全部结果（政策 / 提取 / 事实 / 日志级联删除） |
| `DELETE` | `/api/tasks` | **清空**全部任务与结果，返回 `{"deleted": n}`（前端「清空」按钮） |
| `GET` | `/api/policies/{id}/extraction` | 抽屉三栏（`policy` 元信息 + `extraction` 事实/信息/知识） |
| `POST` | `/api/policies/{id}/retry` | 失败重试（仅 `failed` 可重试，否则 `409`），随后重跑该篇 |
| `POST` | `/api/policies/{id}/reextract` | 清空旧结果并重新提取（`extracting` 中返回 `409`） |

```bash
# 创建任务并观察进度（真实抓取需要 node + China-Central-Policy-MCP 本地构建 + 已配置的 LLM）
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"source":"国务院政策文件库（gov.cn）","content_type":"policy",
       "date_from":null,"date_to":null,"schedule":"manual","keywords":["人工智能"]}'

curl -N http://localhost:8000/api/tasks/1/stream     # SSE：task / log / done / error
```

**SSE 事件**：`{"type":"task","task":{…}}`（快照，计数变化时推送）、`{"type":"log","log":{…}}`、`{"type":"done","status":…,"task":{…}}`（终态，前端收到后应主动关闭连接）、`{"type":"error","message":…}`。订阅时会先补发全部历史日志，再增量推送；每 15 秒发一条 `: ping` 心跳。

**错误约定**：所有 HTTP 错误均为 `{"detail": "<字符串>"}`（含 422 校验错误，已被压成单条字符串）——前端 `ApiError.message` 可直接展示。

**终止 / 清空 / 重启收尾**：

- 后台流水线按 `task_id` 登记在 `crawl_graph._running` 里（`_track()`），因此可以精确定位「某个任务正在跑的后台任务」。
- `POST /api/tasks/{id}/cancel` 先 `cancel_pipeline()` 等后台任务真正退出，再落 `failed` 终态——顺序不能反：`run_pipeline` 的 `CancelledError` 分支写的是「服务重启导致任务中断」，由接口覆盖成「任务已手动终止」。**复用 `failed` 状态而非新增枚举值**，因此不需要改 `schema.sql`（前端把这条 stage 直接展示在进度面板上）。
- `DELETE /api/tasks`（清空）必须先 `cancel_all_pipelines()`：否则被取消前流水线还会继续往已删除的任务里写日志与计数。
- **重启收尾**：上个进程被强杀时残留的 `pending`/`running` 任务没有对应流水线，`lifespan` 启动时调用 `repo.fail_stale_tasks()` 统一落成 `failed`（`stage` 为「服务重启导致任务中断，请重新开始抓取」），否则页面会永远停在「运行中」。库不可用时只告警、不阻断启动。

### 进度与计数语义

- `progress`：抓取阶段 `0→50`（`fetched/total`），提取阶段 `50→100`（`(extracted+failed)/total`）；`completed` 时为 100。
- `eta_seconds`：按 `elapsed / 已完成篇数 × 剩余篇数` 外推；数据过旧（>6 小时）时返回 `null`，前端隐藏该行。
- `total/fetched/extracted/failed_count` 由 `repositories.refresh_task_counts()` 从 `policy_document` / `extraction` 的实际行数**聚合派生**，不累加——保证进度面板与结果表格 `summary` 永远同源一致。

## 如何扩展

- **加工具**：在 `app/agent/tools.py` 中用 `@tool` 定义函数，加入 `TOOLS` 列表即可，图会自动识别并执行。
- **改图结构**：在 `app/agent/graph.py` 中添加节点和边（如人工审批 `interrupt`、条件路由等）。
- **改状态**：在 `app/agent/state.py` 的 `AgentState` 中添加字段（配合自定义 reducer）。
- **改流水线**：在 `app/pipeline/crawl_graph.py` 加节点与边；新增落库字段需同时改 `database/schema.sql`、`app/db/repositories.py`、`app/schemas/*.py` 与 `web/src/api/types.ts`（前端类型是手工镜像，不会自动生成）。
- **换模型 / 网关**：修改 `.env` 中的 `MODEL_NAME`、`ANTHROPIC_BASE_URL`。
- **调流水线参数**：`.env` 中的 `PIPELINE_*`（每关键词条数、最大篇数、提取并发、正文截断长度、MCP 超时）。
