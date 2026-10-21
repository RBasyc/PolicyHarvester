# 项目文档（wiki）

> 本目录是项目的文档库。仓库根目录的 [CLAUDE.md](../CLAUDE.md) 是**给 AI 编码助手的入口**——
> 只放索引与硬性规则，细节都在这里；[PLAN.md](../PLAN.md) / [TODO.md](../TODO.md) 仍在根目录。

## 语言约定

- **架构 / 指南类 = 英文**：沿用 `CLAUDE.md` 的既有语言，便于与代码、MCP 源码逐项对照。
- **进度类 = 中文**：`docs/progress/log.md` 与根目录 `TODO.md` 沿用中文。

## 文档地图

| 你要做的事 | 读这篇 |
| --- | --- |
| 了解项目全貌、仓库布局、双图结构、错误契约 | [architecture/overview.md](architecture/overview.md) |
| 改对话 Agent、加工具、MCP 接入、SSE | [architecture/chat-agent.md](architecture/chat-agent.md) |
| 改抓取流水线、检索/判别/预检、调度、配置 | [architecture/data-pipeline.md](architecture/data-pipeline.md) |
| 改无头浏览器抓取（任意网站 / JS 渲染页，对话与流水线双入口） | [architecture/browser.md](architecture/browser.md) |
| **改 gov.cn 检索逻辑前**（一手实测证据） | [architecture/gov-cn-search-quirks.md](architecture/gov-cn-search-quirks.md) |
| 接入一个新的网站来源（Skill / 9 个工具 / 页面入口） | [architecture/source-onboarding.md](architecture/source-onboarding.md) |
| 命令、环境变量、脚本 | [guides/commands.md](guides/commands.md) |
| ⛔「不要主动构建/运行」的约定 | [guides/building.md](guides/building.md) |
| 扩展点一览（加工具 / 加阶段 / 加字段 / 镜像清单） | [guides/extending.md](guides/extending.md) |
| **agent 线与流水线线的差距清单**（待排期，Phase 33） | [plans/2026-10-21-agent-alignment.md](plans/2026-10-21-agent-alignment.md) |
| 进度日志（2026-10-12 起，明细） | [progress/log.md](progress/log.md) |

## 目录结构

```
docs/
├── README.md                     # 本文件（wiki 首页 / 索引）
├── architecture/                 # 英文：系统怎么运作
│   ├── overview.md               #   仓库布局 · 双图结构 · 错误契约
│   ├── chat-agent.md             #   A. ReAct 对话 Agent
│   ├── data-pipeline.md          #   B. 数据层 + 抓取/提取流水线 + 调度 + 配置
│   ├── browser.md                #   D. 无头浏览器抓取（任意网站，双入口）
│   ├── gov-cn-search-quirks.md   #   中文：gov.cn 官方检索参数一手实测对照
│   └── source-onboarding.md      #   C. 网站来源接入
├── guides/                       # 英文：怎么做
│   ├── commands.md               #   命令 + 环境变量
│   ├── building.md               #   构建/运行约定（硬性规则）
│   └── extending.md              #   扩展指引 + 镜像清单
├── plans/                        # 中文：改造计划与待排期清单（定稿后落档）
│   └── 2026-10-21-agent-alignment.md   #   Agent 线与流水线线的对齐清单（Phase 33）
└── progress/
    └── log.md                    # 中文：进度日志明细（TODO.md 的下沉）
```

## 维护规则

- **每完成一个 Phase**：勾选根目录 [TODO.md](../TODO.md) 的任务项、改「总进度」表与各 Phase 的 `状态` 行，并在 [progress/log.md](progress/log.md) 追加一行。只把 Phase 标 `✅` 当且仅当其验收标准**确实**跑通并留下证据。
- **改了行为就改文档**：流水线/Agent 的行为描述在 `docs/architecture/*.md`；改阈值/判据/配置字段同时更新 `.claude/skills/website-source-onboarding/references/` 下的镜像；改 DTO 同时更新 `web/src/api/types.ts`。完整镜像清单见 [guides/extending.md](guides/extending.md)。
- **本目录是「单一事实来源」**：同一件事只写一处，别处放链接。
