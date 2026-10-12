# Agent Demo

一个「政策文件事实提取」Agent 演示项目：通过 **MCP（China-Central-Policy-MCP，国务院政策检索与解析）** 抓取政策原文，由 **LangGraph + Claude** 循环调用工具完成提取，前端展示任务进度、结果表格与提取详情。

## 项目结构

```
Agent-Demo/
├── design.pen            # pen.dev 设计稿（加密格式，仅通过 pen.dev MCP 读取）
├── database/             # PostgreSQL 脚本：建库 / 建表 / 种子数据（见 database/README.md）
├── server/               # 后端：FastAPI + LangGraph
│   ├── main.py           # 开发启动入口（python main.py，带热重载）
│   ├── pyproject.toml    # 依赖管理（uv）
│   ├── .env.example      # 环境变量模板
│   ├── scripts/
│   │   └── test_mcp.py   # 单独测试 China-Central-Policy-MCP 连通性
│   └── app/
│       ├── main.py       # FastAPI 应用工厂、中间件、路由注册
│       ├── config.py     # pydantic-settings 配置（读取 .env）
│       ├── agent/        # LangGraph 层
│       │   ├── state.py  #   图状态（messages + add_messages reducer）
│       │   ├── tools.py  #   本地 @tool 工具 + MCP_TOOLS 合并（all_tools）
│       │   ├── mcp.py    #   MCP 接入：node stdio 启动本地 China-Central-Policy-MCP
│       │   ├── nodes.py  #   agent 节点（LLM 调用，绑定工具）
│       │   └── graph.py  #   ReAct 循环：START -> agent <-> tools -> END
│       ├── api/routes/
│       │   ├── health.py #   GET  /health
│       │   └── agent.py  #   POST /api/chat、POST /api/chat/stream (SSE)
│       └── schemas/      # 请求 / 响应模型
├── tools/                # 本地 MCP 服务（China-Central-Policy-MCP，node 构建产物）
└── web/                  # 前端：Vue 3 + TypeScript + Vite
    ├── design-export/    # 从 design.pen 导出的 HTML 设计稿（main / states 两页）
    └── src/
        ├── App.vue       # 页面骨架：TopBar + ConfigPanel + ProgressPanel + ResultTable + DetailDrawer
        ├── data.ts       # 演示数据（当前前端为纯静态 UI，尚未对接后端 API）
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

- `POST /api/chat` — 非流式对话：`{"message": "...", "thread_id": "user-1"}`。相同 `thread_id` 共享对话记忆（`InMemorySaver` checkpointer，重启丢失）。
- `POST /api/chat/stream` — SSE 流式对话，事件 `data: {"type": "token", "content": "..."}`，以 `{"type": "done"}` 结束。
- `GET /health` — 健康检查。

### Agent 工作原理

经典 ReAct 循环（[graph.py](server/app/agent/graph.py)）：`agent` 节点调用 LLM 并绑定全部工具；若模型发起工具调用则路由到 `tools` 节点（`ToolNode`）执行，结果回灌给 `agent`，直到无工具调用为止。编译时挂载 `InMemorySaver`，按 `thread_id` 区分会话。

工具来源有两类（[tools.py](server/app/agent/tools.py)）：

- **本地工具**：`@tool` 定义的函数（如 `get_current_time`），加入 `TOOLS` 列表即生效。
- **MCP 工具**：[mcp.py](server/app/agent/mcp.py) 在应用启动时（lifespan）通过 `node` 以 stdio 方式拉起本地构建的 [China-Central-Policy-MCP](https://github.com/guangxiangdebizi/China-Central-Policy-MCP)（`tools/China-Central-Policy-MCP/build/index.js`，提供 `get_latest_policies` / `get_policy_fulltext` 两个工具），由 `langchain-mcp-adapters` 转成 LangChain 工具后填入 `MCP_TOOLS`。服务不可用时自动降级为仅本地工具，不影响启动。

可单独验证 MCP 连通性：

```bash
cd server
python scripts/test_mcp.py
```

## 前端（web/）

```bash
cd web
pnpm install
pnpm dev        # 开发服务器
pnpm build      # vue-tsc 类型检查 + 产物构建
```

**当前状态**：前端是依据 [design-export/](web/design-export/)（从 design.pen 导出的设计稿）实现的**静态 UI 演示**，数据全部来自 [data.ts](web/src/data.ts) 的 mock，**尚未对接后端** `POST /api/chat/stream` SSE 接口。

## 数据库（database/）

使用本机 docker 容器 `server-db-1`（pgvector/pgvector:pg16，端口 5433）中独立的 `agent_demo` 数据库，与容器内已有的 `kos` 库隔离：

```bash
bash database/init.sh    # 初始化（幂等）：建库 -> 建表 -> 灌入与前端 mock 同源的演示数据
bash database/reset.sh   # 删库重建
```

表结构按前端三个面板设计：`crawl_task`（抓取配置/进度）、`policy_document`（结果列表/抽屉元信息）、`extraction` + `fact_item`（事实/信息/知识三区）、`task_log`（日志流），另有 `v_policy_results` 视图直接作为「抓取结果」表格数据源。连接串：`postgresql://kos:kos@localhost:5433/agent_demo`。详见 [database/README.md](database/README.md)。

## 设计稿

[design.pen](design.pen) 是 pen.dev 加密设计文件，请勿用文本工具直接读取；通过 pen.dev 的 MCP 工具（`get_app_state` / `read_skill` / `execute` 等）访问。导出的可预览 HTML 在 [web/design-export/](web/design-export/)（含 `main` 主界面与 `states` 状态页，均有带/不带 SVG 两个版本）。

## 如何扩展

- **加工具**：在 `server/app/agent/tools.py` 中用 `@tool` 定义并加入 `TOOLS`，图会自动识别执行。
- **改图结构**：在 `server/app/agent/graph.py` 中添加节点和边（如 `interrupt` 人工审批、条件路由）。
- **换模型 / 网关**：修改 `.env` 中的 `MODEL_NAME`、`ANTHROPIC_BASE_URL`。
- **对接前端**：将 `web/src/data.ts` 的 mock 替换为对 `server` SSE 接口的订阅。
