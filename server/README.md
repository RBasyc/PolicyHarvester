# Agent Demo Server

基于 **FastAPI + LangGraph** 的 Agent 后端 Demo。

## 项目结构

```
server/
├── main.py                 # 开发启动入口（python main.py，带热重载）
├── pyproject.toml          # 依赖管理（uv）
├── .env.example            # 环境变量模板
└── app/
    ├── main.py             # FastAPI 应用工厂、中间件、路由注册
    ├── config.py           # pydantic-settings 配置（读取 .env）
    ├── agent/              # LangGraph 层
    │   ├── state.py        #   图状态定义（messages + add_messages reducer）
    │   ├── tools.py        #   工具定义（@tool），在 TOOLS 列表中注册
    │   ├── nodes.py        #   agent 节点（LLM 调用，绑定工具）
    │   └── graph.py        #   图构建：START -> agent <-> tools -> END
    ├── api/routes/
    │   ├── health.py       #   GET  /health
    │   └── agent.py        #   POST /api/chat、POST /api/chat/stream (SSE)
    └── schemas/            # 请求 / 响应模型
```

## 快速开始

```bash
# 1. 安装依赖（需要 uv）
uv sync

# 2. 配置认证：复制 .env.example 为 .env，二选一
#    - ANTHROPIC_API_KEY       官方 API Key
#    - ANTHROPIC_AUTH_TOKEN    Bearer Token（Kimi 等兼容网关，需配合 ANTHROPIC_BASE_URL）

# 3. 启动
python main.py
# 或：uvicorn app.main:app --reload
```

启动后访问 <http://localhost:8000/docs> 查看交互式 API 文档。

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

## 如何扩展

- **加工具**：在 `app/agent/tools.py` 中用 `@tool` 定义函数，加入 `TOOLS` 列表即可，图会自动识别并执行。
- **改图结构**：在 `app/agent/graph.py` 中添加节点和边（如人工审批 `interrupt`、条件路由等）。
- **改状态**：在 `app/agent/state.py` 的 `AgentState` 中添加字段（配合自定义 reducer）。
- **换模型 / 网关**：修改 `.env` 中的 `MODEL_NAME`、`ANTHROPIC_BASE_URL`。
