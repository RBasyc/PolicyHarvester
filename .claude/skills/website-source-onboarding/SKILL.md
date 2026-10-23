---
name: website-source-onboarding
description: 把一个新的网站来源接进本项目的采集链路。当用户给出一个网站地址（URL）并希望「接入 / 采集 / 抓取 / 纳入」该站的内容（政策文件、通知公告、公开文件等）时使用本 Skill：勘察 → 取样 → 生成配置 → 试运行验收 → 注册启用，全程用工具真跑，不靠猜、不靠模拟。
---

# 网站来源接入

把「新增一个网站」做成一次**可配置、可验证、可追溯**的接入任务，而不是每次从零写爬虫。

**核心前提：接入是一个「用真实取数验证」的过程，不是「写一份爬虫建议」。**
每一步都要有真实执行结果；凡是没跑过的、没验证的，一律如实说「未验证 / 未测试」。

## 什么时候用

- 用户贴出一个网址，说「把 XX 网站的政策接进来」「采集这个站的通知公告」「新增一个来源」。
- 用户问「这个网站能不能抓」「抓不到怎么办」→ 用 `diagnose_source`。

**不适用**：站点在登录 / 验证码 / 付费墙之后。本 Skill 不绕过任何访问控制——遇到就报「受阻」并说明。

**另一条入口**：产品页面上也有「导入网址」（抓取配置 → 抓取来源 → 导入网址，或顶栏「来源管理」），
走的是**同一批服务函数**（勘察 / 生成配置 / 试运行 / 注册），差别只有两点：不经大模型
（候选由 `sources/analyze.py` 的打分排序，再**逐个真抓样本条目核实**才采用）与拿不到试运行内部的
「第 N/M 篇」粒度。
用户问「页面上那个导入和你说的是不是一回事」→ **是同一套校验与同一道启用闸**。

## 硬性规则（任何情况下都不得违反）

1. **不绕过访问控制**：不登录、不破验证码、不绕付费限制。遇到 401/403/维护页/需要登录 → 报 `blocked`，换公开入口或请用户与站点方确认。
2. **礼貌抓取**：限频、robots.txt、体积上限由 `sources/net.py` 统一执行，不要为了「跑通」去调低这些约束。
3. **目标地址必须过安全校验**：只允许公网 http/https，拒本机/内网/云元数据地址。用户给的地址一样要过（`inspect_source` 内部已做）。
4. **候选 ≠ 已确认**：自动识别出来的选择器在没有经过取样与试运行之前，只能称「候选」。
5. **没有验收通过就不算接入完成**：不得把「HTTP 200 取回成功」「单篇样本解析成功」当成接入成功；必须有小批量试运行 + 验收报告。
6. **未执行的、未验证的、未完成的，不得报告为成功**。`failed` / `blocked` / 未测试项都要原样转述给用户。
7. **注册与启用要有记录和确认**：默认注册为「草案（draft）」；直接启用（`approved=true`）必须先有指纹一致的通过验收。人工强推（`force=true`）必须由用户**明确同意**后才用，并写清变更说明。

## 默认路径：先一键，再手工

**用户给一个网址说「接进来」时，默认只调 `import_source(action="import")`**——
它跑的是页面「导入网址」同一套六阶段编排（勘察 → 内容发现 → 生成配置 → 试运行验收 →
注册），返回验收结论、失败归因与 `headless_eligible`。不要一上来就手工跑九步流程。

```
import_source(url=栏目页, target=采集目标)
  ├─ status=completed / report 通过 → 转述结论；注册出来是 draft，用户点头再启用
  ├─ 失败归因 = 选择器/入口/翻页  → 才进入下方「手工排查」
  └─ 失败归因 = JS 渲染空壳      → import_source(action="save_browser")
```

手工阶段工具（`inspect_source` → `discover_content` → `sample_content` → …）用于：
① 一键接入失败后按归因修规则；② 用户明确要精细控制（自定义选择器、检索型入口、
命名区域等）。**它们不是接入的主线**。

## 手工排查：七阶段流程

> 每步的工具都在 `server/app/agent/onboarding_tools.py`；参数、返回字段、边界见
> [references/tool-map.md](references/tool-map.md)。配置字段见 [references/config-schema.md](references/config-schema.md)；
> 验收标准见 [references/acceptance-criteria.md](references/acceptance-criteria.md)。

### 阶段 1–2 勘察（inspect_source）

用户给的地址先跑 `inspect_source`。

- `ok=false` 且 `kind=blocked` → **停**。把原因原样告诉用户（robots 禁止 / 403 / 内网地址 / 不在白名单），给出替代途径（换栏目地址、用站方 RSS/检索接口、与站点方确认）。**不要试着绕过去。**
- `ok=false` 且 `kind=failed` → 地址多半写错了，让用户确认。
- `ok=true` → 看 `list_candidates`：
  - 有候选 → 进阶段 3。
  - 没有候选 → 这页是首页/正文页，或列表由 JS 渲染。用 `discover_content` 追一层栏目（`follow=1`）拿到真正列出条目的栏目页；仍然没有 → 看 `feeds` / `search_forms`：`search_forms` 里的 GET 检索表单可以做成**检索型入口**（见阶段 4，真取一页验证后写进 `entrypoints[].search`），`feeds` 目前没有对应引擎（RSS 源只能当普通列表页抓）；都没有 → 如实说「本引擎只能解析服务端渲染的静态 HTML，该站需要浏览器渲染能力（当前未实现）」。

如果用户给的是网站首页而不是栏目页，先 `discover_content(url, follow=1)` 找到栏目入口，再从候选里挑与目标主题最相关的栏目当 `entrypoints`。

### 阶段 3 取样与结构分析（sample_content / analyze_content_structure）

用 `sample_content(列表页URL, count=3, list_selector=候选选择器)` 取几篇真实详情页。

看 `samples` 每篇的 `body_ok` / `date` / `agency` / `doc_no`：

| 现象 | 处置 |
| --- | --- |
| 正文 `body_ok=true`、日期机构也能取到 | 规则可用，进阶段 4 |
| `body_cjk=0` 或正文为空 | 正文容器选择器不对。`analyze_content_structure(详情页URL)` 取新的 `detail_candidates`，挑次选重试 |
| 日期/机构全 0 | 先看 `analyze_content_structure` 返回的 `field_candidates` / `dom_outline`，按「标签 → 值」关系写 `detail.fields`（能表达 span 标签 + 父节点直接文本、`dt`/`dd` 兄弟、同节点三种形状）；仍取不到再换对应 `*_selectors`。该站确实没有这些字段 → 如实保留（不要编） |
| 列表条目数为 0 | `list_selector` 不对，回 `analyze_content_structure` 换候选 |

**不要**因为一页成功就宣布规则可用——至少看 3 篇；样本之间不一致要如实说。

### 阶段 4 生成配置（generate_source_config）

把选中的入口、选择器、翻页规则、正文规则组装成配置 JSON，跑 `generate_source_config`。
`ok=false` 时按 `problems` 逐条改（每条都写了怎么修）。

**翻页怎么写**：看 `inspect_source` 返回的 `pagination.kind`：
- `path_index` → 用 `path_template`（如 `index_{page}.html`），`start` 用报告里的值；
- `query_param` → 用 `param`；
- `unknown` → 报告说不出规律，**必须人工确认**（问用户或翻两页手工比对），不要瞎猜参数名；
- 站点只有一页 → `{"kind": "none"}`。

第 1 页就是入口地址本身（配置里那个 URL），翻页规则只作用于第 2 页起——所以 `entrypoints[].url` 必须是**真正列出条目的那一页**。

**关键词/日期能不能在服务端生效，取决于有没有检索型入口**：栏目列表页给不了检索能力，只配栏目入口时关键词只能拿翻到的几页逐条比对标题（召回上限 = 翻页深度 × 每页条数）。`inspect_source` / `discover_content` 报告里的 `search_forms` 就是站内检索入口候选——有 GET 表单（`method=get`、`action` 非空）时，**真取一页**试一下（用 `field` 拼关键词、带上 `params` 里的隐藏参数如 `channelid`），取回的页面上能按候选选择器解析出条目，就把它加成第二个入口：

```json
{"name":"站内检索","url":"<search_forms[].action>","list_selector":"<该检索结果页自己的列表选择器>",
 "search":{"keyword_param":"<field>","fixed_params":{/* params */}}}
```

三条纪律：① `url` 必须是实测取过、真出条目的那个地址，**不要猜 `/search`**；② `list_selector` 要用**检索结果页自己的**结构（它常常和栏目页不一样）；③ `method=post` 的表单、`action` 为 null（JS 拼地址）的检索框，当前引擎支持不了——照实说，别硬配。日期参数（`date_from_param` / `date_to_param`）只在站点的检索页**确实接受**日期参数时才填，别猜参数名。

### 阶段 5 试运行与验收（dry_run_ingestion）

跑 `dry_run_ingestion(config_json, limit=3, keywords=[...])`（关键词尽量按用户的采集目标给，如 `["人工智能","数字经济"]`；不确定就不给，直接取栏目列表）。

只看 `status`：

- **`passed`** → 进阶段 6。同时把 `untested` 与 `coverage_limits` **转述给用户**（这是「这次没验证什么」），不要把「没测」说成「没问题」。
- **`failed`** → 看 `criteria` 里 `passed=false` 的项（`note` 与 `actual` 是实测值）：
  - 「列表发现」失败 → 入口 URL 或 `list_selector` 错；
  - 「正文完整性」失败 → 正文容器/清洗规则不对，看 `details[].error` 是取回失败还是解析失败；
  - 「翻页可用性」失败 → 翻页规则在该站不成立，改规则或退化成 `none`（并接受覆盖范围收窄）；
  - 改完配置 → **必须重跑本工具**（配置一变指纹就变）。
- **`blocked`** → 站点/环境不允许（robots、401/403、安全策略）。停，按第七节第一条处理。

**硬性要求**：不得因为「试运行跑起来了」「取回了几篇」就说接入成功。只有 `status=passed` 才算通过验收。

### 阶段 6 注册与启用（register_source）

1. 先 `register_source(config_json)`（默认注册为**草案**）——落库留痕 + 装进来源注册表；
2. 需要马上用于正式抓取时才 `register_source(config_json, approved=true)`（要求已有指纹一致的通过验收）。

**只有用户明确要求时**才用 `force=true` 跳过验收要求，并把 `change_note` 写清楚（会进版本历史）。

注册后告诉用户：来源名（`label`）就是这个来源在抓取页的来源下拉里显示的名字，以及试抓方式（`manage_ingestion_job(action="run_trial")`）。

### 阶段 7 运维与诊断（manage_ingestion_job / diagnose_source）

- 站点改版导致抓不到了 → `diagnose_source(key=...)` 看卡在哪一层，再重新勘察生成新版本配置（指纹变了，要重新试运行）。
- 启停 / 看变更历史 → `manage_ingestion_job(action="enable|disable|detail")`。
- 想真跑一批看效果 → `manage_ingestion_job(action="run_trial", key=..., max_policies=3)`。**注意**：这会发起一次真实的抓取任务（走完整流水线，含判别与正文预检），并且**会停在人工确认**那一档——和手动抓取一样，不越过确认环节。发起前先跟用户说一声。

## 常见失败与处置（速查）

| 症状 | 多半是 | 怎么办 |
| --- | --- | --- |
| 勘察 `kind=blocked`，原因含 robots | 站点声明不许抓 | 换栏目地址或改用站方接口；不绕过 |
| 勘察 `kind=blocked`，原因含 403/401 | 有访问控制 | 不绕过；找其它公开入口 |
| 列表候选为空 | 首页没有条目列表 | `discover_content(follow=1)` 找栏目页；或换栏目地址 |
| 条目数对但正文空 | 正文容器选择器不对 | 在**详情页**上跑 `analyze_content_structure` 换容器 |
| 翻页第 2 页无新增 | 参数被站点忽略 | 改用真实的路径模板；或降级为单页 |
| 验收 `failed` 但每项都勉强过 | 抽样太少 | 提高 `limit` 重跑（≥3，正文完整性才不是初步结论） |
| 注册被拒「没有指纹一致的通过试运行」 | 配置改过（指纹变了）或没跑试运行 | 拒因会点出**上一次通过的指纹**：不一致=配置改过，**重跑** `dry_run_ingestion`；压根没有=**先跑**。确需放行才用 `force` |
| 已停用的来源点「启用」被拒 | 不该再发生 | 凭据按指纹在 `source_onboarding_run` **全表**查、不设条数窗口；若仍被拒，看拒因是「无法核对验收凭据」（库的问题）还是一条**真的**不一致（配置改过） |

## 已知能力边界（要主动告诉用户）

- 只能解析**服务端返回的静态 HTML**；JS 动态渲染的站点需要浏览器渲染能力（未实现）。
- 附件目前**只做可达性与归档验证**，不做 PDF/OFD 文字抽取（正文抽取是已知缺口）。
- 图片版红头文件（正文全是图片）会被正文指标判为「不达标」并标注，不会当成有效正文入库。
- 登录 / 验证码 / 付费墙之后的内容不采集（规范不允许绕过）；有站点还会对**非浏览器客户端**直接
  回 403（2026-10-15 实测：`www.gov.cn/zhengce/zhengceku/` 对 curl 默认 UA、我们的 UA、Chrome UA
  三种请求**都是 403**）——这类一律如实报 `blocked`，不换 UA 去绕。
- **已是内置来源的站点不需要重新接入**：gov.cn（国务院政策文件库）在来源下拉里本来就是
  `builtin=true` / `active`，用户问「导入 gov.cn 显示不通过怎么办」→ 直接告诉他不用导，
  报出来的 `blocked`（403）或「未通过」（栏目页列表由 JS 渲染）都是如实结论。
- 全站历史存量不在一次试运行覆盖范围内（每次只试抓少量样本）。

## 交付与留痕

每次勘察 / 发现 / 取样 / 试运行都会在 `source_onboarding_run` 留一条记录（含输入、结论、失败原因），
抓回来的原始附件落在 `SOURCE_ARTIFACT_DIR`（默认 `server/.artifacts/`）并在 `source_artifact` 登记
URL / 哈希 / 落盘路径 / 发现页。用户问「这次接入到底跑了什么」时，读这些记录，不要凭记忆回答。

原始文件是**证据**，AI 抽取出来的结果是**事实候选**——两者都不等于「已核验的知识」。
