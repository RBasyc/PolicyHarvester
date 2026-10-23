# 判别总开关 + 配置拆分为 `.env` / `.env.features`（Phase 35）

> 定稿计划，2026-10-22 经用户批准后执行。状态：代码与文档已落地，**未构建未运行**，待实机验收（见 [TODO.md](../../TODO.md) Phase 35）。

## 背景

`.env` 里的 `JUDGE_ENABLED=false` 只关了 laya 标题判别；**正文预检**（`_preflight_bodies`）是另一条独立的打标通道，仍会把图片版/薄壳页面标成「不建议入库」，所以用户关了开关还是能看到徽标。同时，静态配置（模型、数据库、抓取参数）和功能开关（无头、判别、Langfuse）混在同一个 124 行的 `.env` 里，开关不显眼、容易误改。

本计划做两件事：

1. **判别总开关**：把 `JUDGE_ENABLED` 升级为总开关，`false` = 关闭一切「不建议入库」标注（laya + 正文预检）；新增 `JUDGE_LAYA_ENABLED`（默认 `true`）单独控制标题判别。
2. **配置文件拆分**：根 `.env` 只放静态配置；新建根 `.env.features` 放功能开关。两个文件都由 `config.py` / `start.sh` 读取，旧 `.env` 里残留的开关继续生效（后文件覆盖前文件），不强迫迁移。

用户已确认的取舍：

- 总开关关闭时，**正文预检仍抓全文并回填日期/机构/文号**，只是不写 `judge_tagged`——确认面板仍能看到元数据。
- 只影响**新任务**；已落库的历史待确认任务的旧标签不动。
- 开关文件名：`.env.features`（模板 `.env.features.example`）。

## 1. 配置加载：`server/app/config.py`

文件位置常量（顺序有意义：后者覆盖前者）：

```python
_ENV_FILES: tuple[Path, ...] = (_REPO_ROOT / ".env", _REPO_ROOT / ".env.features")
_REAL_ENV = dict(os.environ)          # 灌入之前快照
```

- `_env_file_values()` 用 `dotenv_values` 逐个读取并合并，缺失文件跳过。
- `_load_env_into_process()` 把合并结果写回 `os.environ`（OpenAI SDK 直接读环境变量），跳过真实 shell 变量。**不能**连调两次 `load_dotenv`：其默认 `override=False` 会变成「先加载者占位」，与 pydantic-settings「后文件覆盖」相反。
- `SettingsConfigDict.env_file` 改成序列 `tuple(str(p) for p in _ENV_FILES)`（pydantic-settings 2.15 支持序列，后文件覆盖）。
- `_live_env()` 改为显式成员判断：真实 shell > 新鲜文件值 > `os.environ`，保住 `PIPELINE_MODELS` 的热读语义。
- Settings 字段：`judge_enabled` 注释改为「判别总开关」；新增 `judge_laya_enabled: bool = True`。
- **不**把 `PIPELINE_MODELS` 或任何 `*_API_KEY` 迁出 `.env`。

## 2. 判别总开关：`server/app/pipeline/`

- `judge.py::judge_rows`：只看 `judge_laya_enabled`（它是「调用 laya 服务」的薄封装，校准脚本直接用它）；总开关属于流水线的**打标策略**，放在调用点。
- `crawl_graph.py::_judge_and_preflight`：读 `tagging = get_settings().judge_enabled`；关闭时不调 `judge_rows`、不写标签，`judge_stats["tagging_disabled"] = 1`；仍调 `_preflight_bodies(..., tagging_enabled=tagging)`。
- `_preflight_bodies(..., tagging_enabled=True)`：保留全部抓全文 / 元数据回填 / 模型兜底；只有「写标签与计数」被包住——抓取失败分支关闭时日志改为「……判别总开关已关闭，不标注」，指标不达标分支关闭时记「正文偏薄……不标注」且**不**增加 `body_rejected`；成功预检新增 `preflight_checked` 计数。
- `fetch_node`：`tagging_disabled` 时追加任务日志「入库判别总开关已关闭（JUDGE_ENABLED=false）：本轮不写标注；正文预检已抓取 N 篇用于回填日期/机关/文号」。
- **不改**：读取端（`_to_policy_row`、repositories、schema/视图、确认面板、scheduler、ingestion 服务、agent 工具）——标签为 `NULL` 时自然按「未判别」处理；`library_search.py` 的 `body_ok` 是对话侧提示、不归总开关管。

## 3. `start.sh`

- `ENV_FILES=(...)`；`env_value()` 遍历两个文件，取最后一个有值的赋值（与 pydantic 一致）。
- `check_env_file()`：`.env.features` 缺失时，若 `.env` 仍残留功能键 → **不**自动复制模板（避免覆盖用户现有的 `false`），只提示迁移；否则复制 `.env.features.example`。同名键打印「以 `.env.features` 为准」。
- `check_ingest_judge()`：改读 `env_value JUDGE_ENABLED`；总开关 false 时打印「✓ 判别总开关已关闭」并返回；否则再看 `JUDGE_LAYA_ENABLED`。

## 4. 模板与迁移

- 新建 `.env.features.example`（全部注释）：`JUDGE_ENABLED`、`JUDGE_LAYA_ENABLED`、`MCP_POLICY_ENABLED`、`BROWSER_ENABLED`、`BROWSER_HEADLESS`、`LANGFUSE_ENABLED`；头部写优先级（shell > `.env.features` > `.env` > 默认）、重启要求（`PIPELINE_MODELS` 除外）、总开关关闭后预检仍抓详情页（连这次抓取也不要：`PIPELINE_MIN_BODY_CHARS=0` + `PIPELINE_MIN_SENT_ENDERS=0`），以及 Langfuse 自托管 / 数据出境提示。
- `.env.example`：移走六个开关，各段留指针；`JUDGE_URL`、`BROWSER_*` 预算、`LANGFUSE_*` 密钥与地址留在 `.env`；`SOURCE_RESPECT_ROBOTS` / `SOURCE_ALLOW_PRIVATE_HOSTS` **留在 `.env`**（安全约束，不是随手切换的功能）。
- `.gitignore` 加 `.env.features`；本机 `.env` 的三行开关移入 `.env.features`（值不变，密钥不碰）。

## 5. 文档镜像

`README.md` / `README.zh-CN.md`、`AGENTS.md`、`CLAUDE.md`、`docs/guides/{commands,extending}.md`、`docs/architecture/{data-pipeline,browser,chat-agent}.md`、`server/README.md`、`database/README.md`、`docs/验收-无头浏览器.md`、`server/scripts/test_db.py` 的过时提示、`TODO.md` + `docs/progress/log.md`。

## 6. 测试与验收（用户运行）

新增离线脚本 `server/scripts/test_feature_switches.py`：文件合并优先级 / `Settings.env_file` 顺序一致 / `PIPELINE_MODELS` 热读 / 总开关关闭（不调 laya、不写标、仍回填元数据）/ laya 子开关关闭（正文预检仍打标）/ 被 laya 标记者跳过预检。更新 `test_judge.py`、`test_ingest_filter.py` 的口径打印。

验收：① `uv run python scripts/test_feature_switches.py` 全部通过；② `./start.sh check` 显示总开关已关闭；③ 新任务无「不建议入库」徽标但日期/机构/文号仍在；④ 临时开总开关 + 关 laya 只剩正文预检标签；⑤ `test_judge.py` 仍能调 laya。

## 7. 风险与边界

- 两个文件里的开关都走 `Settings + lru_cache`，**改完需重启**；只有 `PIPELINE_MODELS` 热读。
- 四处读取口径必须一致：pydantic、`_env_file_values` / `_load_env_into_process`、`_live_env`、`start.sh::env_value`。
- `.env.features` 缺失时服务照常启动（向后兼容，老部署无需迁移）。
- 总开关关闭后没有标题预筛，所有候选都会抓全文做元数据回填——这是「保留元数据」的代价。
- 历史任务的旧标签不动，前端仍显示（明确取舍）。

## 8. 非目标

- 不 gate `library_search.py` 的 `body_ok`；不隐藏历史标签；不自动改写其他 clone 的 `.env`；不改变 `get_settings()` 缓存语义（不做运行期热更新）；不新增第三方依赖。
