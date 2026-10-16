<div align="center">

# PolicyHarvester

**一个带人工确认闸门的政策提取 Agent——检索后先让你筛，再烧 token 提取。**

> 大多数政策爬取工具要么乱抓一气、要么黑箱烧钱。PolicyHarvester 把「人类判断」放回流程中间：先检索候选、标出建议入库的、等你勾选确认，再逐篇做结构化提取。**不确认，不提取，不浪费 token。**


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

## 它解决什么问题

政策研究者、合规团队、投资机构每周要处理几十份政策文件。现有的 AI 工具普遍有两个问题：

- **乱抓**：一次拉回几十篇，一半是无关的薄壳通知或图片版红头文件
- **不透明**：token 花在哪、为什么提取这几篇，全程黑箱

PolicyHarvester 的做法是：**把最贵的提取步骤放在人工确认之后**。检索阶段只做廉价判断（标题级入库判别 + 正文预检），你确认后再进入昂贵的结构化提取。

这也是它和一般「政策 RAG」或「爬虫 demo」的核心区别——**它把成本控制设计成了流程的一部分，而不是事后补救。**

---

## 一句话能力清单

| 能力             | 一句话说明                                                             |
| ---------------- | ---------------------------------------------------------------------- |
| **人工确认闸门** | 检索完先停住，前端标注「建议入库 / 不建议 / 已在库」，勾选后才开始提取 |
| **检索续抓**     | 在调用预算内翻页凑够新增篇数，已在库/不建议的条目不占配额              |
| **正文预检**     | 抓全文后清洗，挡下正文过短的薄壳通知与图片版红头文件                   |
| **三段式提取**   | 每篇产出 事实 / 信息 / 知识 三区，失败可单篇重试                       |
| **跨任务成果库** | 按 source_url 去重沉淀，支持机构/文种/年份筛选                         |
| **定时入库**     | 按分钟/小时/每日/每周自动抓取，跳过确认闸门直接入库                    |
| **成本可观测**   | 可用页数、正文长度、并发数、检索预算均可按任务覆盖                     |
| **网站来源接入** | 粘贴网址自动勘察 → 生成配置 → 试运行验收 → 注册启用，接入任意政策站点  |
| **站内检索优先** | 带站点自带检索入口时，关键词/日期推给站点检索页执行，而非取回后过滤    |
| **筛选能力声明** | 机读声明关键词/日期筛选在哪一层生效，前端如实标注召回边界              |

---

## 它长什么样

**抓取任务页** — 配置时间范围 / 关键词 / 机构 / 文种，下方是实时进度条与日志流（图中为定时任务触发：跨轮去重 → 检索候选 → 逐篇提取）

![抓取任务页](screenshot/crawl-task.png)

**结果抽屉** — 点击结果表任意一行，右侧抽屉即时展开该篇的「事实 / 信息 / 知识」三段式提取

![提取结果抽屉](screenshot/result-drawer.png)

**成果库** — 跨任务按 source_url 去重沉淀，支持机构 / 文种 / 年份筛选与分页

![政策成果库](screenshot/library.png)

**政策详情页** — 归档原文 + 三栏提取详情 + 单篇重新提取

![政策详情页](screenshot/policy-detail.png)

---

## 快速开始

**前置依赖**：uv ≥ 0.4、Node.js ≥ 18、pnpm ≥ 8、Docker

```bash
git clone https://github.com/RBasyc/policyharvester
cd policyharvester
cp .env.example .env   # 填入 LLM 认证
./start.sh
```

Windows 用户双击 `start.bat`。脚本会自动完成环境自检、依赖安装、数据库就绪检查、并发拉起前后端。

启动后：

| 服务          | 地址                       |
| ------------- | -------------------------- |
| 前端界面      | http://localhost:5173      |
| 后端 API 文档 | http://localhost:8000/docs |

**LLM 认证二选一，切勿同时配置：**

```bash
ANTHROPIC_API_KEY=sk-ant-...        # 官方 API Key
# 或
ANTHROPIC_AUTH_TOKEN=sk-kimi-...    # Kimi 等 Anthropic 兼容网关
ANTHROPIC_BASE_URL=https://api.kimi.com/coding/
```

其他命令：

```bash
./start.sh stop     # 停止服务
./start.sh check    # 只做环境自检
./start.sh --help   # 查看全部用法
```

---

## 架构总览

```
浏览器（Vue 3 + TS, :5173）
   │  /api ← Vite 代理 + SSE
   ▼
FastAPI（:8000）
   ├─ A. 对话 Agent   agent ⇄ tools → END（ReAct 循环）
   ├─ B. 抓取流水线   prepare → fetch →「人工确认闸门」→ extract → summarize
   ├──► China-Central-Policy-MCP（国务院政策检索，node stdio）
   ├──► PostgreSQL :5433（docker 容器 server-db-1）
   └──► LLM 网关（Anthropic 兼容协议）
```

两条链路**互相独立**：

- **A · 对话 Agent**（`app/agent/`）— 经典 ReAct 循环，工具来自本地 `@tool` + 启动时经 stdio 拉起的 MCP 工具；MCP 不可用时自动降级为仅本地工具。
- **B · 抓取流水线**（`app/pipeline/`）— **确定性的两段式图**，中间夹一道人工确认闸门：第一段检索候选并落库后停住（`awaiting_confirmation`），用户勾选/剔除后再跑第二段逐篇提取。检索候选可来自 gov.cn（MCP），也可来自任意已接入的**配置化网站来源**（`app/sources/`，栏目列表页或站点自带检索页）。

---

## 配置

所有配置集中在仓库根目录的 `.env`。完整注释见 `.env.example`。

| 分组     | 关键项                                                                   | 说明                                                       |
| -------- | ------------------------------------------------------------------------ | ---------------------------------------------------------- |
| LLM 认证 | `ANTHROPIC_API_KEY` **或** `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` | 二选一，同时配置会被网关拒绝                               |
| 模型     | `MODEL_NAME` / `MAX_TOKENS` / `PIPELINE_MODELS`                          | `PIPELINE_MODELS` 是高级设置下拉框的数据源，热读           |
| 数据库   | `DATABASE_URL`                                                           | 默认 `postgresql://kos:kos@localhost:5433/agent_demo`      |
| 流水线   | `PIPELINE_*`                                                             | 最大篇数、每页条数、检索预算、提取并发、正文截断、预检阈值 |
| 入库判别 | `JUDGE_*`                                                                | 判别服务地址与提示词；**不可达时 fail-open**，不阻断抓取   |

页面的「高级设置」可在创建任务时按任务覆盖其中几项与提取模型。

---

## API 速览

完整字段与进度语义见 `server/README.md`，交互式文档在 http://localhost:8000/docs。

**对话（ReAct Agent）**

- `POST /api/chat` — 非流式；相同 `thread_id` 共享记忆（`InMemorySaver`，重启丢失）
- `POST /api/chat/stream` — SSE：`{"type":"token"|"done"|"error", ...}`

**抓取任务**

- `POST /api/tasks` — 创建并后台启动，返回 `201 {"task_id": n}`
- `POST /api/tasks/{id}/confirm` — 提交确认勾选，触发第二段提取
- `GET /api/tasks/current` / `GET /api/tasks/{id}` / `GET /api/tasks/{id}/logs`
- `GET /api/tasks/{id}/policies` — 结果表格 `{items, summary}`
- `GET /api/tasks/{id}/stream` — SSE 进度
- `POST /api/tasks/{id}/cancel` · `DELETE /api/tasks/{id}` · `DELETE /api/tasks`

**政策与成果库**

- `GET /api/policies` / `/api/policies/meta` / `/api/policies/{id}`
- `GET|POST /api/policies/{id}/extraction|retry|reextract`

**定时入库**

- `GET|POST /api/schedules` · `POST /api/schedules/{id}/toggle|run` · `DELETE /api/schedules/{id}`

**其他**

- `GET /health` · `GET /api/filters/facets` · `GET /api/config/pipeline`

> 错误统一为 `{"detail": "<字符串>"}`，前端 `ApiError` 直接渲染该字段。

---

## 常见问题

**报 401 `invalid x-api-key`**

`ANTHROPIC_API_KEY` 和 `ANTHROPIC_AUTH_TOKEN` **同时存在**——走网关时 anthropic SDK 会同时发出两个头。只保留一个。另外 `load_dotenv()` **不覆盖**已存在的环境变量：如果 shell 里已经设了 `ANTHROPIC_*`，`.env` 会被静默忽略。用 `env | grep ANTHROPIC` 自查。

**服务起来了，但抓取任务直接失败 / 没有结果**

先看数据库是否可用——数据库连不上时服务照常启动（只在日志里告警），对话 Agent 不受影响，但抓取流水线会失败：

```bash
docker ps | grep server-db-1     # 容器在跑吗
./start.sh check                 # 端口、.env、数据库、MCP 产物一起体检
```

注意检索结果为「空」有时是**正常**的：政务站点在无严格匹配时会返回整张未过滤的浏览列表，流水线会做客户端二次校验后如实报「未检索到」。

**提取总是失败 / 提示 JSON 解析不了**

代码已规避：本仓库默认网关（Kimi）常开思考模式，而「思考开启 + 指定 tool_choice」会被拒。因此结构化调用不强绑工具，由模型自行调用，失败再退回宽松的 JSON 文本解析。换网关/模型时请沿用同一策略。调试单篇：`POST /api/policies/{id}/reextract`。

**改了 `tools/China-Central-Policy-MCP/src/` 却不生效**

服务端加载的是构建产物 `build/index.js`，源码改动必须先构建：

```bash
cd tools/China-Central-Policy-MCP && npm install && npm run build
```

**端口被占用 / 服务没退干净**

```bash
./start.sh stop     # 先按记录下来的 PID 停，再按端口兜底清理
```

**「入库判别」和「定时入库」能跑吗**

判别服务默认指向内网地址，外部环境大概率不通。不通时按 fail-open 处理（全部视为「建议入库」），不会阻断抓取。定时入库需要本机数据库可用。

**重启后对话失忆 / 任务卡在「运行中」**

对话记忆用 `InMemorySaver`，重启即丢，换 `PostgresSaver` 可持久化。任务卡住是上次进程被杀留下的行——启动时的 lifespan 会自动把它们标记为失败；停在确认闸门的任务不受影响。

---

## 目录结构

```
policyharvester/
├── start.sh / start.bat     # 一键启动 / 自检 / 停止
├── .env.example             # 环境变量模板
├── PLAN.md / TODO.md        # 实施计划 / 进度清单
├── CLAUDE.md                # AI 编码助手的入口（索引 + 硬性规则）
├── docs/                    # 项目文档 wiki（架构 / 指南 / 进度）
├── database/                # PostgreSQL 建库 / 建表 / 种子数据
├── server/                  # 后端：FastAPI + LangGraph + psycopg3
│   └── app/
│       ├── agent/           #   A. ReAct 对话 Agent（+ onboarding_tools）
│       ├── pipeline/        #   B. 抓取 / 提取流水线
│       ├── sources/         #   C. 配置化来源接入层
│       ├── services/        #   接入编排（onboarding / source_import）
│       ├── db/              #   连接池 + 全部 SQL
│       ├── api/routes/      #   health / agent / tasks / policies / filters / config / schedules / sources
│       └── scheduler.py     #   定时入库轮询循环
├── tools/                   # China-Central-Policy-MCP（已 vendored）
└── web/                     # 前端：Vue 3 + TypeScript + Vite
```

---

## 如何扩展

| 想做的事           | 改哪里                                                                                                |
| ------------------ | ----------------------------------------------------------------------------------------------------- |
| 加一个对话工具     | `server/app/agent/tools.py` 用 `@tool` 定义并加入 `TOOLS`，图自动识别                                 |
| 改对话图结构       | `server/app/agent/graph.py`（新增节点/边、`interrupt` 人工审批、条件路由）                            |
| 加抓取流水线阶段   | `server/app/pipeline/crawl_graph.py` 加节点与边                                                       |
| **加一个落库字段** | 需**同时**改四处：`database/schema.sql` → `repositories.py` → `schemas/*.py` → `web/src/api/types.ts` |
| 调流水线参数       | 根目录 `.env` 的 `PIPELINE_*`；页面「高级设置」可再按任务覆盖                                         |
| 换模型 / 网关      | `.env` 的 `MODEL_NAME`、`ANTHROPIC_*`，或往 `PIPELINE_MODELS` 注册表加一项                            |

> ⚠️ 任何改变 `policy_document` / `extraction` 行的写操作，都要跟着调用 `refresh_task_counts()`，否则派生的进度计数会与结果表格漂移。

---

## 文档

深度文档已 wiki 化，入口是 **[docs/README.md](docs/README.md)**（含完整文档地图）。

| 文档                                         | 内容                                                          |
| -------------------------------------------- | ------------------------------------------------------------- |
| [docs/README.md](docs/README.md)             | 文档 wiki 首页 / 索引 / 维护规则                              |
| [docs/architecture/](docs/architecture/)     | 架构说明：总览、对话 Agent、流水线、gov.cn 检索踩坑、来源接入 |
| [docs/guides/](docs/guides/)                 | 命令与配置、构建/运行约定、扩展指引与镜像清单                 |
| [docs/progress/log.md](docs/progress/log.md) | 进度日志明细（逐条踩坑与验收证据）                            |
| [PLAN.md](PLAN.md)                           | 打通三端的整体实施计划                                        |
| [TODO.md](TODO.md)                           | 逐阶段任务清单与状态（明细日志见 docs/progress/log.md）       |
| [CLAUDE.md](CLAUDE.md)                       | 面向 AI 编码助手的入口（索引 + 硬性规则）                     |
| [server/README.md](server/README.md)         | 后端 API 字段、进度语义                                       |
| [database/README.md](database/README.md)     | 表结构设计                                                    |

---
- 作者声明：本项目仅供学习、教学与学术研究使用。 开源的目的在于帮助有需要的分析师与研究者更高效地开展政策信息分析与复现工作。
使用边界：本项目不面向生产环境或商业化部署，作者不对任何此类用途进行背书或提供保证。 如需用于生产/商业场景，请自行完成充分的合规审查与测试，并自行承担全部风险与责任。

- 禁止性用途：不得将本项目用于任何违法、侵权、违规或不道德的用途，包括但不限于：
绕过网站服务条款、访问控制或反爬机制；
大规模抓取、存储、传播受版权保护或包含个人信息的数据；
制造或传播虚假/误导性内容，或用于舆论操纵；
任何违反适用法律法规、数据合规与隐私保护要求的行为。

- 数据来源与合规：本项目对接公开的政府网站入口。 使用者应自行确认目标站点的使用条款、robots 协议、访问频率限制与版权要求，合理控制请求频率，避免对对方服务造成不当负载。

- 免责声明：项目按“现状”提供，不作任何明示或默示担保（包括但不限于适销性、特定用途适用性与非侵权）。 在法律允许的最大范围内，作者不对因使用本项目而导致的任何直接或间接损失承担责任。

- 版权与许可：本项目采用 Apache-2.0 许可证发布。 许可证条款是约束使用的唯一法律文本； 本节“法律声明与使用限制”表述的是作者的使用态度与合规提示，不构成对许可证的修改。 如对许可证与本声明存在理解冲突，以许可证条款为准。

- 合规与移除：如您认为本项目中的说明或工具可能引发合规风险，或出于正当权利主张提出移除/更正请求，请通过下述联系方式与作者取得联系。
---

## License

[Apache-2.0](LICENSE)
