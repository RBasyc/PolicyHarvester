<div align="center">

# Agent Demo

**政策文件事实提取 Agent**

从国务院政策库检索政策原文 → 由 LangGraph + Claude 完成「**事实 / 信息 / 知识**」三段式结构化提取 → 前端实时展示抓取进度、确认闸门、结果表格与提取详情。

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](server/pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](server/app/main.py)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.6+-1C3C3C?logo=langchain&logoColor=white)](server/app/agent/graph.py)
[![Vue](https://img.shields.io/badge/Vue-3.5-4FC08D?logo=vuedotjs&logoColor=white)](web/package.json)
[![TypeScript](https://img.shields.io/badge/TypeScript-6.0-3178C6?logo=typescript&logoColor=white)](web)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](database/schema.sql)
[![uv](https://img.shields.io/badge/deps-uv-DE5FE9)](https://docs.astral.sh/uv/)
[![pnpm](https://img.shields.io/badge/deps-pnpm-F69220?logo=pnpm&logoColor=white)](https://pnpm.io/)

</div>

---

## 快速开始

### 前置依赖

| 依赖 | 版本 | 用途 | 安装 |
| :--- | :--- | :--- | :--- |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.4 | 后端依赖管理与运行 | `curl -LsSf https://astral.sh/uv/install.sh \| sh`（Windows：[安装脚本](https://docs.astral.sh/uv/getting-started/installation/)） |
| [Node.js](https://nodejs.org/) | ≥ 18 | 前端构建 + MCP 政策检索服务 | 官网下载 LTS |
| [pnpm](https://pnpm.io/) | ≥ 8 | 前端包管理 | `corepack enable pnpm` |
| [Docker](https://www.docker.com/) | — | 跑 PostgreSQL 容器 `server-db-1` | 桌面版即可 |

> Python 本身不必预装（≥ 3.12）——`uv` 会自动准备。

### 一键启动

```bash
# macOS / Linux / WSL / Windows Git Bash
./start.sh
```

**Windows 用户直接双击 [start.bat](start.bat)** 即可（它会找到 Git Bash 并转调 `start.sh`；找不到时会给出手动步骤）。

脚本会自动完成：环境自检 → `uv sync` / `pnpm install` → 数据库就绪检查（缺库时自动执行 `database/init.sh`）→ 并发拉起前后端。启动后：

| 服务 | 地址 |
| :--- | :--- |
| 前端界面 | <http://localhost:5173> |
| 后端 API 文档 | <http://localhost:8000/docs> |

按 `Ctrl+C` 停止；或另开终端执行：

```bash
./start.sh stop     # 停止（先按 PID，必要时按端口兜底）
./start.sh check    # 只做环境自检，不启动任何服务
./start.sh --help   # 查看全部用法与环境变量
```

<details>
<summary><b>依赖没装齐 / 想手动分别启动</b></summary>

启动前 **必须** 复制根目录的 [.env.example](.env.example) 为 `.env`，并填写 LLM 认证（二选一，**切勿同时配置**）：

```dotenv
ANTHROPIC_API_KEY=sk-ant-...                        # 官方 API Key
# 或
ANTHROPIC_AUTH_TOKEN=sk-kimi-...                    # Kimi 等 Anthropic 兼容网关
ANTHROPIC_BASE_URL=https://api.kimi.com/coding/
```

```bash
# 终端 1 —— 后端
cd server && uv sync && python main.py              # :8000

# 终端 2 —— 前端
cd web && pnpm install && pnpm dev                  # :5173

# 终端 3 —— 数据库（首次或换机器时）
bash database/init.sh                               # 幂等；SEED=1 可灌演示数据
```

</details>

---

## 架构总览

```text
浏览器（web/ :5173，Vue 3 + TS）
   │  /api  ← Vite 代理 + SSE
   ▼
FastAPI（server/ :8000）
   ├─ A. 对话 Agent     agent ⇄ tools → END（ReAct 循环，InMemorySaver 记忆）
   ├─ B. 抓取流水线      prepare → fetch →「人工确认闸门」→ extract → summarize
   │
   ├──► tools/China-Central-Policy-MCP   国务院政策检索 / 原文解析（node stdio）
   ├──► PostgreSQL :5433 / agent_demo     docker 容器 server-db-1
   └──► LLM 网关                         Anthropic 兼容协议（Kimi / DeepSeek / 官方）
```

两条链路**互相独立**：

- **A · 对话 Agent**（`app/agent/`）— 经典 ReAct 循环，工具来自本地 `@tool` + 启动时经 stdio 拉起的 MCP 工具；MCP 不可用时自动降级为仅本地工具，不影响启动。
- **B · 抓取流水线**（`app/pipeline/`）— **确定性的两段式图**，中间夹一道人工确认闸门：第一段检索候选并落库后停住（`awaiting_confirmation`），用户在前端勾选/剔除后再跑第二段逐篇提取。抓取结果沉淀进跨任务的**成果库**（按 `source_url` 去重）。

---

## 功能一览

### 抓取与提取

| 能力 | 说明 |
| :--- | :--- |
| **人工确认闸门** | 检索完先停住，前端灰度标出「建议入库 / 不建议入库 / 已在库」，勾选后才开始烧 token 提取 |
| **检索续抓** | 在总调用预算内翻页凑够「新增篇数」，已在库/不建议的条目不再占配额 |
| **入库判别** | 检索阶段对候选**仅标题**做「建议/不建议入库」标注（内网 RL 服务，不可达时 fail-open 全部保留） |
| **正文预检** | 第二道筛：抓全文后清洗，正文过短的薄壳通知与图片版红头文件被挡下 |
| **结构化提取** | 每篇产出 **事实 / 信息 / 知识** 三区（facts / summary+points / concepts+fields+extension），失败可单篇重试或重新提取 |
| **多模型切换** | 页面「高级设置」可选提取模型，模型注册表热读 `.env`，改完**无需重启** |
| **成本可观测** | 可用页数、每篇正文长度、并发数、检索调用预算均可按任务覆盖 |

### 成果与任务

| 能力 | 说明 |
| :--- | :--- |
| **跨任务成果库** | 按 `source_url` 去重沉淀，支持关键词 / 发布机构 / 文种 / 年份筛选 + 分页 |
| **政策详情页** | 三栏提取结果 + 归档全文（提取时顺带落库，无需回源） |
| **实时进度** | SSE 推送任务状态与日志；进度面板的计数全部由实际行数**聚合派生**，与结果表格永远同源 |
| **任务管理** | 手动终止、删除单个、清空全部；进程被杀后启动时自动清理「运行中」僵尸任务 |
| **定时入库** | 按分钟 / 小时 / 每日 / 每周自动抓取并直接入库（跳过确认闸门），带跨任务去重与滑动时间游标 |

---

## 目录结构

```text
Agent-Demo/
├── start.sh / start.bat     # 一键启动 / 自检 / 停止
├── .env.example             # 环境变量模板 —— 复制为根目录 .env（前后端共用）
├── design.pen               # pen.dev 设计稿（加密，仅经 pen.dev MCP 读取）
├── PLAN.md / TODO.md        # 实施计划 / 逐阶段进度清单
├── database/                # PostgreSQL 建库 / 建表 / 种子数据（见 database/README.md）
├── server/                  # 后端：FastAPI + LangGraph + psycopg3（见 server/README.md）
│   ├── main.py              #   开发入口（python main.py，热重载）
│   ├── scripts/             #   冒烟脚本：test_mcp / test_db / test_judge / test_ingest_filter
│   └── app/
│       ├── main.py          #   应用工厂 + lifespan（开池 → 载 MCP → 编译图 → 起调度器）
│       ├── config.py        #   pydantic-settings 配置 + 模型注册表（热读 .env）
│       ├── agent/           #   A. ReAct 对话 Agent
│       ├── pipeline/        #   B. 抓取流水线（crawl_graph / extract / judge / 全文清洗）
│       ├── db/              #   连接池 + 全部 SQL（repositories.py）
│       ├── api/routes/      #   health / agent / tasks / policies / filters / config / schedules
│       ├── schemas/         #   请求响应模型
│       └── scheduler.py     #   定时入库轮询循环
├── tools/                   # China-Central-Policy-MCP（已 vendored，build/ 随仓库提交）
└── web/                     # 前端：Vue 3 + TypeScript + Vite
    ├── design-export/       #   从 design.pen 导出的可预览 HTML
    └── src/
        ├── api/             #   client.ts（fetch + SSE）、types.ts（后端契约手工镜像）
        ├── store.ts         #   模块级全局状态（页面切换也在这，无 vue-router）
        └── components/      #   TopBar / ConfigPanel / ProgressPanel / ResultTable /
                             #   ConfirmPanel / DetailDrawer / ExtractionColumns /
                             #   LibraryPage / LibraryDetailPage / ScheduleModal ...
```

---

## 配置

所有配置集中在**仓库根目录**的 `.env`（`server/app/config.py` 按 `__file__` 解析路径，所以从哪个目录启动都一样）。完整注释见 [.env.example](.env.example)。

| 分组 | 关键项 | 说明 |
| :--- | :--- | :--- |
| LLM 认证 | `ANTHROPIC_API_KEY` **或** `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` | 二选一，同时配置会被网关以 `invalid x-api-key` 拒绝 |
| 模型 | `MODEL_NAME` / `MAX_TOKENS` / `PIPELINE_MODELS` | `PIPELINE_MODELS` 是「高级设置」下拉框的数据源，热读 |
| 服务 | `HOST` / `PORT` | 后端监听地址，默认 `:8000` |
| 数据库 | `DATABASE_URL` | 默认 `postgresql://kos:kos@localhost:5433/agent_demo` |
| 流水线 | `PIPELINE_*` | 最大篇数、每页条数、检索调用预算、提取并发、正文截断、预检阈值…… |
| 入库判别 | `JUDGE_*` | 判别服务地址与提示词；**不可达时 fail-open**，不阻断抓取 |

页面的「高级设置」可在创建任务时**按任务覆盖**其中几项与提取模型（覆盖值随任务持久化，第二段提取会重新读取）。

---

## API 速览

完整字段与进度语义见 [server/README.md](server/README.md)，交互式文档在 <http://localhost:8000/docs>。

<details>
<summary><b>对话 / 任务 / 政策 / 调度</b></summary>

**对话（ReAct Agent）**

- `POST /api/chat` — 非流式；相同 `thread_id` 共享记忆（`InMemorySaver`，**重启丢失**）
- `POST /api/chat/stream` — SSE：`{"type":"token"|"done"|"error", ...}`

**抓取任务**

- `POST /api/tasks` — 创建并后台启动，返回 `201 {"task_id": n}`
- `POST /api/tasks/{id}/confirm` — 提交确认勾选，触发第二段提取
- `GET /api/tasks/current` / `GET /api/tasks/{id}` / `GET /api/tasks/{id}/logs`
- `GET /api/tasks/{id}/policies` — 结果表格 `{items, summary}`
- `GET /api/tasks/{id}/stream` — SSE 进度（`task` / `log` / `done` / `error`）
- `POST /api/tasks/{id}/cancel` · `DELETE /api/tasks/{id}` · `DELETE /api/tasks`

**政策与成果库**

- `GET /api/policies` / `/api/policies/meta` / `/api/policies/{id}` — 成果库列表、表头统计+筛选项、详情
- `GET|POST /api/policies/{id}/extraction|retry|reextract` — 提取详情 / 失败重试 / 重新提取

**定时入库**

- `GET|POST /api/schedules` · `POST /api/schedules/{id}/toggle|run` · `DELETE /api/schedules/{id}`

**其他**

- `GET /health` · `GET /api/filters/facets` · `GET /api/config/pipeline`

> 错误统一为 `{"detail": "<字符串>"}`，前端 `ApiError` 直接渲染该字段。

</details>

---

## 常见问题

<details>
<summary><b>报 401 <code>invalid x-api-key</code></b></summary>

`ANTHROPIC_API_KEY` 和 `ANTHROPIC_AUTH_TOKEN` **同时存在**——走网关时 anthropic SDK 会同时发出 `x-api-key` 与 `Authorization` 两个头。只保留一个。

另外 `load_dotenv()` **不覆盖**已存在的环境变量：如果 shell / 系统里已经设了 `ANTHROPIC_*`，`.env` 会被静默忽略。用 `env | grep ANTHROPIC` 自查。
</details>

<details>
<summary><b>服务起来了，但抓取任务直接失败 / 没有结果</b></summary>

先看数据库是否可用——**数据库连不上时服务照常启动**（只在日志里告警），对话 Agent 不受影响，但抓取流水线会失败：

```bash
docker ps | grep server-db-1     # 容器在跑吗
./start.sh check                 # 端口、.env、数据库、MCP 产物一起体检
```

注意检索结果为「空」有时是**正常**的：政务站点在无严格匹配时会返回整张未过滤的浏览列表，流水线会做客户端二次校验（标题、日期窗口、公报行）后如实报「未检索到」。
</details>

<details>
<summary><b>提取总是失败 / 提示 JSON 解析不了</b></summary>

代码已规避：本仓库默认的网关（Kimi）常开思考模式，而「思考开启 + 指定 `tool_choice`」会被拒（`tool_choice 'specified' is incompatible with thinking enabled`）。因此结构化调用**不强绑工具**，由模型自行调用，失败再退回宽松的 JSON 文本解析。换网关/模型时请沿用同一策略。

调试单篇：`POST /api/policies/{id}/reextract`。
</details>

<details>
<summary><b>改了 <code>tools/China-Central-Policy-MCP/src/</code> 却不生效</b></summary>

服务端加载的是**构建产物** `build/index.js`，源码改动必须先构建，否则是静默空操作：

```bash
cd tools/China-Central-Policy-MCP && npm install && npm run build   # = tsc
```

（`build/` 随仓库提交，`node_modules/` 不入库，因此冷克隆无需构建即可运行。）
</details>

<details>
<summary><b>端口被占用 / 服务没退干净</b></summary>

```bash
./start.sh stop     # 先按记录下来的 PID 停，再按端口兜底清理
```

Windows 上若仍有残留，关闭对应终端窗口最干脆。
</details>

<details>
<summary><b>「入库判别」和「定时入库」能跑吗</b></summary>

判别服务默认指向**内网地址**，外部环境大概率不通。不通时按 **fail-open** 处理（全部视为「建议入库」），只少一层筛选，**不会**阻断抓取。定时入库需要本机数据库可用。
</details>

<details>
<summary><b>重启后对话失忆 / 任务卡在「运行中」</b></summary>

对话记忆用 `InMemorySaver`，重启即丢，换 `PostgresSaver` 可持久化。任务卡住则是上次进程被杀留下的 `pending`/`running` 行——启动时的 lifespan 会自动把它们标记为失败；停在确认闸门的任务不受影响，仍可继续确认。
</details>

---

## 设计稿

[design.pen](design.pen) 是 pen.dev 的加密设计文件，**请勿**用文本工具直接读取，通过 pen.dev 的 MCP 工具访问。可预览的导出 HTML 在 [web/design-export/](web/design-export/)（主界面与状态页各有带/不带 SVG 两版）。

## 如何扩展

| 想做的事 | 改哪里 |
| :--- | :--- |
| 加一个对话工具 | 在 `server/app/agent/tools.py` 用 `@tool` 定义并加入 `TOOLS`，图自动识别 |
| 改对话图结构 | `server/app/agent/graph.py`（新增节点/边、`interrupt` 人工审批、条件路由） |
| 加抓取流水线阶段 | `server/app/pipeline/crawl_graph.py` 加节点与边（分支见 `_route_after_fetch`） |
| **加一个落库字段** | 需**同时**改四处：`database/schema.sql` → `server/app/db/repositories.py` → `server/app/schemas/*.py` → `web/src/api/types.ts`（前端类型是手工镜像） |
| 调流水线参数 | 根目录 `.env` 的 `PIPELINE_*`；页面「高级设置」可再按任务覆盖 |
| 换模型 / 网关 | `.env` 的 `MODEL_NAME`、`ANTHROPIC_*`，或往 `PIPELINE_MODELS` 注册表加一项 |

> ⚠️ 任何改变 `policy_document` / `extraction` 行的写操作，都要跟着调用 `refresh_task_counts()`，否则派生的进度计数会与结果表格漂移。

## 文档与进度

| 文档 | 内容 |
| :--- | :--- |
| [PLAN.md](PLAN.md) | 打通三端的整体实施计划 |
| [TODO.md](TODO.md) | 逐阶段任务清单与进度日志 |
| [CLAUDE.md](CLAUDE.md) | 面向 AI 编码助手的深度架构说明（踩坑记录最全） |
| [server/README.md](server/README.md) | 后端 API 字段、进度语义 |
| [database/README.md](database/README.md) | 表结构设计 |
