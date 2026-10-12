# Agent Demo

一个「政策文件事实提取」Agent 演示项目：通过 **MCP（China-Central-Policy-MCP，国务院政策检索与解析）** 抓取政策原文，由 **LangGraph + Claude** 完成结构化提取（**事实 / 信息 / 知识**），前端展示任务进度、结果表格与提取详情。三端已打通：Vue 前端 → FastAPI 业务 API → PostgreSQL，进度经 SSE 实时推送。

## 项目结构

```
Agent-Demo/
├── design.pen            # pen.dev 设计稿（加密格式，仅通过 pen.dev MCP 读取）
├── PLAN.md / TODO.md     # 打通三端的实施计划与进度清单
├── database/             # PostgreSQL 脚本：建库 / 建表 / 种子数据（见 database/README.md）
├── server/               # 后端：FastAPI + LangGraph + PostgreSQL
│   ├── main.py           # 开发启动入口（python main.py，带热重载）
│   ├── pyproject.toml    # 依赖管理（uv）
│   ├── .env.example      # 环境变量模板
│   ├── scripts/
│   │   ├── test_mcp.py   # 单独测试 China-Central-Policy-MCP 连通性
│   │   └── test_db.py    # 数据库读写冒烟测试
│   └── app/
│       ├── main.py       # FastAPI 应用工厂、lifespan（开池 -> MCP -> 编译图）、路由注册
│       ├── config.py     # pydantic-settings 配置（读取 .env）
│       ├── agent/        # A. ReAct 对话 Agent
│       │   ├── state.py  #   图状态（messages + add_messages reducer）
│       │   ├── tools.py  #   本地 @tool 工具 + MCP_TOOLS 合并（all_tools）
│       │   ├── mcp.py    #   MCP 接入：node stdio 启动本地 China-Central-Policy-MCP
│       │   ├── nodes.py  #   agent 节点（LLM 调用，绑定工具）
│       │   └── graph.py  #   ReAct 循环：START -> agent <-> tools -> END
│       ├── pipeline/     # B. 抓取 + 提取流水线（确定性，非对话）
│       │   ├── crawl_graph.py  # prepare -> fetch -> extract -> summarize（失败走 fail）
│       │   └── extract.py      # 单篇结构化提取（事实 / 信息 / 知识）
│       ├── db/           # 数据访问层
│       │   ├── pool.py         # psycopg3 异步连接池
│       │   └── repositories.py # 全部 SQL（参数化；计数由聚合派生）
│       ├── api/routes/
│       │   ├── health.py   #   GET  /health
│       │   ├── agent.py    #   POST /api/chat、POST /api/chat/stream (SSE)
│       │   ├── tasks.py    #   任务：创建 / 查询 / 日志 / 结果 / SSE 进度
│       │   └── policies.py #   单篇：提取详情 / 重试 / 重新提取
│       └── schemas/        # 请求 / 响应模型（task.py、policy.py）
├── tools/                # 本地 MCP 服务（China-Central-Policy-MCP，node 构建产物）
└── web/                  # 前端：Vue 3 + TypeScript + Vite
    ├── design-export/    # 从 design.pen 导出的 HTML 设计稿（main / states 两页）
    └── src/
        ├── App.vue       # 页面骨架：TopBar + ConfigPanel + ProgressPanel + ResultTable + DetailDrawer
        ├── api/          # client.ts（fetch 封装 + SSE 订阅）、types.ts（后端契约镜像）
        ├── store.ts      # 模块级全局状态（任务 / 日志 / 结果 / 表单 / Toast）
        └── components/   # TopBar / ConfigPanel / ProgressPanel / ResultTable / DetailDrawer / AppIcon
```

## 后端（server/）

### 启动

```bash
cd server
uv sync                    # 需要 uv

# 配置认证：复制 .env.example 为 .env，二选一
#   - ANTHROPIC_API_KEY       官方 API Key
#   - ANTHROPIC_AUTH_TOKEN    Bearer Token（Kimi 等兼容网关，需配合 ANTHROPIC_BASE_URL）

python main.py             # 或 uvicorn app.main:app --reload
```

启动后访问 <http://localhost:8000/docs> 查看交互式 API 文档。

### API

对话（ReAct Agent）：

- `POST /api/chat` — 非流式对话：`{"message": "...", "thread_id": "user-1"}`。相同 `thread_id` 共享对话记忆（`InMemorySaver` checkpointer，重启丢失）。
- `POST /api/chat/stream` — SSE 流式对话，事件 `data: {"type": "token", "content": "..."}`，以 `{"type": "done"}` 结束。
- `GET /health` — 健康检查。

抓取任务（前端主流程）：

- `POST /api/tasks` — 创建任务并**后台启动**抓取流水线，返回 `201 {"task_id": n}`。
- `GET /api/tasks/current` — 最近一次任务（无任务返回 `null` → 前端空状态）。
- `GET /api/tasks/{id}` / `GET /api/tasks/{id}/logs` — 任务快照 / 日志流。
- `GET /api/tasks/{id}/policies` — 结果表格：`{items, summary:{total,extracted,extracting,pending,failed}}`。
- `GET /api/tasks/{id}/stream` — **SSE** 进度推送（事件 `task` / `log` / `done` / `error`）。
- `POST /api/tasks/{id}/cancel` — 终止运行中的任务（取消后台流水线 → 落 `failed` 终态）。
- `DELETE /api/tasks/{id}` / `DELETE /api/tasks` — 删除单个任务 / **清空全部**任务与结果。
- `GET /api/policies/{id}/extraction` — 抽屉三栏（事实 / 信息 / 知识）。
- `POST /api/policies/{id}/retry` / `POST /api/policies/{id}/reextract` — 失败重试 / 重新提取。

错误统一为 `{"detail": "<字符串>"}`。完整字段与进度语义见 [server/README.md](server/README.md)。

### 抓取流水线

与对话 Agent 相互独立：`POST /api/tasks` 落库后触发一个**确定性的 LangGraph 图**，`fetch`（按关键词调 `get_latest_policies`，去重落库）→ `extract`（逐篇 `get_policy_fulltext` → 大模型结构化提取 → 写 `extraction` + `fact_item`）→ `summarize`。单篇失败只标记该篇 `failed` 并继续；MCP 不可用则任务整体 `failed` 并给出中文提示。

`crawl_task` 的 `total/fetched/extracted/failed_count` 一律由 `refresh_task_counts()` 从 `policy_document` / `extraction` 的**实际行数聚合派生**（不做自增），因此进度面板与结果表格的统计永远同源一致。

### Agent 工作原理

经典 ReAct 循环（[graph.py](server/app/agent/graph.py)）：`agent` 节点调用 LLM 并绑定全部工具；若模型发起工具调用则路由到 `tools` 节点（`ToolNode`）执行，结果回灌给 `agent`，直到无工具调用为止。编译时挂载 `InMemorySaver`，按 `thread_id` 区分会话。

工具来源有两类（[tools.py](server/app/agent/tools.py)）：

- **本地工具**：`@tool` 定义的函数（如 `get_current_time`），加入 `TOOLS` 列表即生效。
- **MCP 工具**：[mcp.py](server/app/agent/mcp.py) 在应用启动时（lifespan）通过 `node` 以 stdio 方式拉起本地构建的 [China-Central-Policy-MCP](https://github.com/guangxiangdebizi/China-Central-Policy-MCP)（`tools/China-Central-Policy-MCP/build/index.js`，提供 `get_latest_policies` / `get_policy_fulltext` 两个工具），由 `langchain-mcp-adapters` 转成 LangChain 工具后填入 `MCP_TOOLS`。服务不可用时自动降级为仅本地工具，不影响启动。

可单独验证 MCP 连通性与数据库读写：

```bash
cd server
uv run python scripts/test_mcp.py    # MCP：列出工具 + 调 get_latest_policies
uv run python scripts/test_db.py     # 数据库：读 crawl_task / v_policy_results / extraction
```

## 前端（web/）

```bash
cd web
pnpm install
pnpm dev        # 开发服务器
pnpm build      # vue-tsc 类型检查 + 产物构建
```

**当前状态**：前端已**对接后端业务 API**——界面依据 [design-export/](web/design-export/)（从 design.pen 导出的设计稿）实现，数据全部来自 `server` 的 `/api/tasks`、`/api/policies` 接口与 `/api/tasks/{id}/stream` SSE；原先的 `src/data.ts` mock 已删除。开发时需同时起后端（默认 8000），`vite.config.ts` 已配 `/api` 代理。

## 数据库（database/）

使用本机 docker 容器 `server-db-1`（pgvector/pgvector:pg16，端口 5433）中独立的 `agent_demo` 数据库，与容器内已有的 `kos` 库隔离：

```bash
bash database/init.sh    # 初始化（幂等）：建库 -> 建表 -> 灌入演示数据（仅作离线样本）
bash database/reset.sh   # 删库重建
```

表结构按前端三个面板设计：`crawl_task`（抓取配置/进度）、`policy_document`（结果列表/抽屉元信息）、`extraction` + `fact_item`（事实/信息/知识三区）、`task_log`（日志流），另有 `v_policy_results` 视图直接作为「抓取结果」表格数据源。后端已通过 psycopg3 异步连接池接入（`server/app/db/`），连接串 `postgresql://kos:kos@localhost:5433/agent_demo`。**种子数据只是离线样本**，真实内容以流水线抓取的 gov.cn 政策为准。详见 [database/README.md](database/README.md)。

## 设计稿

[design.pen](design.pen) 是 pen.dev 加密设计文件，请勿用文本工具直接读取；通过 pen.dev 的 MCP 工具（`get_app_state` / `read_skill` / `execute` 等）访问。导出的可预览 HTML 在 [web/design-export/](web/design-export/)（含 `main` 主界面与 `states` 状态页，均有带/不带 SVG 两个版本）。

## 如何扩展

- **加工具**：在 `server/app/agent/tools.py` 中用 `@tool` 定义并加入 `TOOLS`，图会自动识别执行。
- **改图结构**：在 `server/app/agent/graph.py` 中添加节点和边（如 `interrupt` 人工审批、条件路由）。
- **改抓取流程**：在 `server/app/pipeline/crawl_graph.py` 加节点与边；新增落库字段需同时改 `database/schema.sql`、`server/app/db/repositories.py`、`server/app/schemas/*.py` 与 `web/src/api/types.ts`（前端类型是手工镜像）。
- **调流水线参数**：`.env` 中的 `PIPELINE_*`（每关键词条数、最大篇数、提取并发、正文截断长度、MCP 超时）。
- **换模型 / 网关**：修改 `.env` 中的 `MODEL_NAME`、`ANTHROPIC_BASE_URL`。
