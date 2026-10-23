# Database

Agent Demo 的 PostgreSQL 数据库脚本与表结构设计。按前端各面板（[web/src](../web/src)）与后端流水线所需的数据形态设计（早期与 `web/src/data.ts` mock 同源，该文件已随前端接入后端而移除）。

## 连接信息

使用本机 docker 容器 **`server-db-1`**（镜像 `pgvector/pgvector:pg16`，端口 `5433`）中独立的 **`agent_demo`** 数据库——与容器内已有的 `kos` 库完全隔离，互不影响。

```
postgresql://kos:kos@localhost:5433/agent_demo
```

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PG_CONTAINER` | `server-db-1` | docker 容器名 |
| `PGUSER` | `kos` | 数据库用户 |
| `PGDATABASE` | `agent_demo` | 数据库名 |

## 脚本

```bash
bash database/init.sh    # 初始化：创建数据库（幂等）-> 建表 -> 灌入演示数据
bash database/reset.sh   # 删库重建（只影响 agent_demo）
```

两个脚本都可重复执行；`schema.sql` / `seed.sql` 也可单独用 psql 执行。

## 表结构设计

五张表 + 一个视图，完全对齐前端三个面板的字段：

| 表 | 对应前端 | 关键字段 |
|---|---|---|
| `crawl_task` | 「抓取配置」+「抓取进度」 | `source`（抓取源）、`content_type`（政策文件/通知公告/全部）、`date_from`/`date_to`、`schedule`（手动/每天/每周，demo 不执行定时）、`keywords[]`、`status`、`total/fetched/extracted/failed_count`（进度统计）、`stage`（当前阶段文案） |
| `policy_document` | 「抓取结果」每行 + 抽屉头部元信息 | `title`、`publish_date`、`source_url`（原文链接）、`agency`（发文机关）、`doc_no`（文号）、`fetched_by` |
| `extraction` | 抽屉「事实 / 信息 / 知识」三区（与政策文件 1:1） | `status`（pending/extracting/extracted/failed）、`error`（失败原因，支持重试）、`model`、`info_summary`、`info_points`（JSONB）、`knowledge_concepts`（JSONB：`[{term, desc}]`）、`knowledge_fields[]`、`knowledge_extension` |
| `fact_item` | 抽屉「事实 Facts」明细行 | `label`（时间/发文机构/政策动作/数字指标…）、`content`、`position`（顺序）；计数用 `COUNT(*)` 得出 |
| `task_log` | 「抓取进度」日志流 | `tone`（info/success/active，对应前端三种颜色）、`message` |
| `v_policy_results` | 「抓取结果」表格数据源 | `policy_document LEFT JOIN extraction`：标题、发布日期、原文链接、提取状态 |

设计要点：

- **枚举约束状态**：`extract_status` / `task_status` / `content_type` / `schedule_type` / `log_tone`，与前端 TS 联合类型一一对应，防止脏数据。
- **JSONB vs 拆表**：`info_points`、`knowledge_concepts` 结构固定且只整体读写，用 JSONB 避免过度设计；`fact_item` 需要按维度计数/筛选，拆成明细表。
- **进度计数冗余在 `crawl_task` 上**：前端「已抓取 10/10 · 已提取 6/10 · 失败 0」直接读字段，无需实时聚合。
- **`UNIQUE (task_id, source_url)`**：同一任务内按原文链接去重；`extraction.policy_id UNIQUE` 保证一篇文件一份提取结果。
- **`ON DELETE CASCADE`**：删任务即清干净其政策文件、提取结果、事实与日志。

## 种子数据

[seed.sql](seed.sql) 与最初的前端 mock 同源（7 篇政策、1 个运行中任务、5 条日志、1 篇完整提取含 8 条事实/摘要/要点/概念）。

> 注意：seed 是**离线样本**，与前端 mock 同源但并非逐项自洽（例如 `crawl_task.extracted_count` 与表内实际行数不一致）。真实运行以 **Phase 4 抓取流水线**写入的 gov.cn 数据为准；届时 `crawl_task` 的计数由后端从实际行数**聚合派生**，因此进度面板与结果表格不会出现 seed 里的这种偏差。

## 后端接入（已完成）

后端已通过 `psycopg3` 异步连接池读写本库：

- 连接串在仓库根目录的 [.env](../.env)（静态配置；功能开关在 `.env.features`，数据库连接串**不**放那里）：`DATABASE_URL=postgresql://kos:kos@localhost:5433/agent_demo`
- 连接池：`server/app/db/pool.py`（进程级单例，`dict_row` + `autocommit`；库不可用时只告警不阻断服务启动）
- 全部 SQL 集中在 `server/app/db/repositories.py`；业务 API 与抓取流水线见 [server/README.md](../server/README.md)
- 冒烟测试：`cd server && uv run python scripts/test_db.py`

后续可把 LangGraph 的 `InMemorySaver` 换成 `PostgresSaver`（连接同一数据库）实现对话记忆持久化。
