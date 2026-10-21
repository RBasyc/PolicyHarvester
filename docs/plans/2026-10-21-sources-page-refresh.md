# 来源页体验修复与改版（2026-10-21）

参考 `design.pen` 的两个画板：**来源管理 · 入口卡片（优化版）**（`Cqr0C`）与
**来源详情 · 试运行结果（优化版）**（`yBqJk`）。设计稿不全盘复刻——已知的毛病
（验收判据各项高度不一致、随内容变化）在实现里修掉（grid 等行高）。

## 问题 1：单条导入报告页「完成后不更新」

复现：单条导入跑到报告页 → 点「启用该来源」或「保存为无头浏览器来源」→ 提示出现、
列表已刷新，但向导仍停在报告页、按钮原样（「启用该来源」还可再点，状态文案还写「草案」）。

- `store/sourceImports.ts::enableSourceById` 改为返回 `Promise<boolean>`（成功 true）。
- `enableRegistered()` / ImportSection `saveAsBrowserSource()` 成功后调 `resetWizard()`：
  报告页清空、回到表单页，地址栏保留（`resetWizard` 不动 draft），方便接着导下一条。
- 启用失败（未通过验收等）不回表单，停在报告页让用户换候选重跑。

## 问题 2：按设计稿优化两个页面

### 2.1 `SourceReport.vue` 重构（验收报告组件，向导/批量/详情三处共用）

- **验收结论卡**：绿底卡（`--green-soft`/`--green-border`），44px 圆形图标底 + 状态图标；
  结论（18px 绿）+ 一句说明；横向统计：命中候选 / 正文样本 / 判据通过（passed/total）/
  软判据警告，竖分隔线；右侧运行元信息（时间，可选 prop `finishedAt` 传入 + 指纹）。
- **验收判据卡**：白卡；卡头 = 标题 + 数量 chip + 右侧图例（通过 X · 警告 Y）；
  判据项 **2 列 grid（`grid-template-columns:1fr 1fr`，行内 `stretch`）——等高，修掉设计稿高度不齐的毛病**。
  判据卡内：状态 icon + 名称 + 实测值；「要求：…」；软判据 pill（琥珀）置顶行右侧；
  note 作琥珀色「建议」行。通过 = 灰底 `--divider` 左边条；警告 = `#FDF8F0` 底 + 3px 琥珀左边条；
  硬失败 = `--red-bg` + 红左边条。
- **正文实测卡**：白卡；卡头 = 标题 + 「逐篇 · N 篇」chip + 右侧一句说明；行间细分隔线；
  每篇：状态 icon + 标题（无标题则 PDF chip + URL）、指标 chip 行（正文/中文/句末/日期/机构/
  图片版/PDF 附件）、容器 chip（mono）、警告行（琥珀 triangle-alert，即原「兜底提示」）、
  右上原文外链 icon。
- **底部双卡**：`grid 1fr 2fr`——「本次未测试」窄卡 + 「覆盖范围限制」宽卡，条目 icon + 文本。
- 新增琥珀色变量到 `style.css`：`--amber:#b45309; --amber-bg:#fdf8f0; --amber-soft:#fbeee0`。

### 2.2 `SourceListSection.vue` 改成卡片列表（Cqr0C）

- 卡片 = 白底圆角 12px：`--border` 描边、padding 16/20。
- 名称行：来源名 15px/600 + 状态/验收 tag——已启用（绿底绿字）/已停用（灰）、
  已验收（`--blue-bg`）/待验收（琥珀）、内置、无头浏览器。
- 元信息行：代码 chip（mono 11px 灰底）· 入口：xxx · 取值方式（chip 或 ⇄ 开关）· 版本 v{n}。
- 摘要行：最近 dry_run 结论（绿勾 = 验收通过 / 时钟 = 尚未试运行），
  「命中 X 条候选 / 取回 Y 篇样本 · 试运行于 <date>」。数据来自 `GET /sources/runs/recent?kind=dry_run`
  按 `source_key` 映射（新增 `store/sourceImports.ts::refreshRecentRuns()`，与 `loadSources` 同节奏刷新）。
- 操作列（设计稿只留三个）：**启用 toggle**（绿/灰开关；未验收来源 toggle = 触发原「强制启用」
  二次确认，确认前开关不回弹）、**删除 icon**（沿用行内二次确认）、**chevron 进入详情** → `/sources/:id`。
- 试运行 / 健康检查 / 验收报告行内展开全部移到详情页，列表不再展开（设计稿口径）。

### 2.3 新增来源详情页 `SourceDetailPage.vue`（yBqJk）

- 路由 `/sources/:id(\d+)`（`router.ts`，数字约束同款 `/library/:id`）。
- 数据：`GET /sources/{id}`（后端已有，`SourceDetailDTO`）→ `api.getSource()`（client 补方法，
  `SourceDetail` 类型已存在）。
- 页头：「← 返回来源管理」+ 来源名 20px/600 + tag + 元信息行；
  右侧操作：试运行（红 primary，仅 active）/ 健康检查（ghost，非 browser_driven）/
  停用或启用（ghost，未验收启用走强制确认）/ 删除 icon（二次确认）。
- 主体：最近一次 `kind === 'dry_run'` 的 `SourceReport`（新样式，`finishedAt` 传 run.finished_at）；
  无试运行记录 → 空状态说明。健康检查结果沿用行内面板（检查按钮在页头，结果落在报告上方）。

## 触达文件

- `web/src/store/sourceImports.ts` — 问题 1 修复 + `refreshRecentRuns()`
- `web/src/components/sources/ImportSection.vue` — 保存无头来源后 reset
- `web/src/components/SourceReport.vue` — 重构
- `web/src/components/sources/SourceListSection.vue` — 卡片化
- `web/src/views/SourceDetailPage.vue` — 新增
- `web/src/router.ts`、`web/src/api/client.ts`、`web/src/style.css`
- 镜像同步：`docs/architecture/overview.md`（five routes 表述）；TODO.md + docs/progress/log.md 追加记录

## 验收（用户实机）

1. 单条导入 → 报告页点「启用该来源」→ 页面回到表单、toast 提示已启用、列表里该来源为已启用；
   失败时不回表单。
2. JS 渲染空壳失败的作业 → 「保存为无头浏览器来源」→ 同样回表单、列表出现无头来源（已验收·已启用）。
3. 来源列表呈卡片：名称/tag/元信息/摘要行齐全，toggle 启停即时生效，未验收 toggle 弹强制确认。
4. 点卡片 chevron 进详情：页头操作可用，报告为结论卡 + 判据 grid（等高）+ 正文实测 + 底部双卡。

## 修订（2026-10-21 用户裁定，替代上方「保存为无头浏览器来源」方案）

用户验收中直接裁定：「直接把这部分提示删掉就行了，都不需要这个提示，因为失败了就默认进列表开启无头了」。据此：

- **废弃**——向导报告页与批量展开行的「保存为无头浏览器来源」按钮、`isJsShellFailure` 判定、前端 `saveBrowserSource` API 方法及相关类型；discover 阶段 js-shell 长归因文案与两条「静态引擎永远抓不到」长提示。
- **改为服务端兜底**——`source_import.run_import_job` 收尾（finally）对 `status=error` 且 `error_kind=failed` 的作业自动调 `onboarding.save_browser_source` 落成无头浏览器来源（轻量校验通过即已验收·已启用）；试运行未通过（done）、取消、blocked（访问控制）均不兜底。结果经 `browser_source` 字段全链路下发（ImportJob → DTO → 单作业接口 + 批量聚合 SSE）。
- **前端只显示一行结果**——失败条目（向导 / 批量队列行 / 批量展开行）显示绿色提示「已自动保存为无头浏览器来源『label』（已验收 · 已启用）——可在抓取页直接选用」，无按钮无操作提示。
- 服务端 `POST /sources/browser` 端点保留（幂等；auto-save 走同一 service）。

## 修订二（2026-10-21 用户再次裁定：人工确认 + 按失败类型过滤）

用户在自动保存方案后追加两条裁定：「404、robots.txt 不让抓的、服务器错误的都不用存，只有配置无法抓取的、无法找到正文的、js 解析的才启用无头」+「进无头前还是要人工确认一下，失败的在右侧要有个按钮，尝试启用无头抓取模式」。据此**废弃自动保存**，最终形态：

- **服务端按失败阶段下发 `headless_eligible`**——只给「静态管线拿不到内容」的失败：内容发现为空/JS 渲染空壳、候选逐个试抓失败、配置不可执行、试运行找不到正文（含 status=done 的验收未通过）；**不给**勘察失败的 404/5xx/robots/DNS（站点本身拒绝或不可达）与未知异常。字段随 ImportJob → DTO → 单作业接口 + 批量聚合 SSE 全链路下发。
- **前端在失败条目右侧给「启用无头抓取」按钮**（单条向导报告页、批量队列行、批量展开行三处），点了才调 `POST /sources/browser`（请求体带 `job_id`）：服务端无头浏览器轻量校验通过即落成 browser_driven 来源（已验收 · 已启用），并把结果回写 `job.browser_source` 进程内快照——重拉作业（批量展开/路由对表）可见「已启用无头抓取模式」。
- 成功提示文案统一为「已启用无头抓取模式」；批量队列行另用组件级 `headlessSavedJobs` 记已确认行（SSE 已关，快照不再推送）。
