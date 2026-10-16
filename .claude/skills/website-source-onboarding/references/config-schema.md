# 来源接入配置（`SourceConfig`）字段说明

配置就是把「这个网站怎么取数」写成一份**可版本化的数据**：列表页在哪、条目怎么选、
翻页怎么翻、详情页正文在哪个容器里。代码只提供通用能力（取数 + 解析），差异全在配置里。

- **定义**：`server/app/sources/config.py`（Pydantic 模型）
- **落库**：`content_source.config`（JSONB），变更历史进 `content_source_revision`
- **校验**：`validate_source_config()` 检查**可执行性**（域名边界、选择器是否在解析引擎
  支持子集内、正文容器有没有给），Pydantic 负责字段级校验；两者都在落库/试运行前跑。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `schema_version` | int | 否 | `1` | 配置结构版本；结构变更时递增 |
| `key` | str | **是** | — | 机器标识。`^[a-z0-9][a-z0-9_\-]*$`，2–60 字符，全局唯一 |
| `label` | str | **是** | — | **展示名**。抓取任务的 `source` 字段就是它，也是流水线按名解析来源的键——**必须唯一**，撞名会在装载时被拒 |
| `level` | enum | 否 | `other` | `national` / `province` / `city` / `county` / `other` |
| `organization` | str | 否 | `null` | 发布机构（展示用） |
| `source_type` | enum | 否 | `government_portal` | `government_portal` / `open_api` / `rss` / `mixed`（当前引擎均为 HTML 取数，此项仅供归类与未来分流） |
| `engine` | enum | 否 | `html_list` | 取数引擎。**目前只有 `html_list`** |
| `scope` | object | **是** | — | 来源边界，见下 |
| `entrypoints` | array | **是** | — | ≥1 个内容入口，入口名不能重复 |
| `detail` | object | 否 | 见下 | 详情页解析规则 |
| `doc_type_patterns` | object | 否 | `{}` | 文号前缀 → 文种，如 `{"浙政发": "浙政发"}`；空 = 不做文种归类 |
| `keyword_match` | enum | 否 | `substring` | 关键词**复核**口径：`substring`（标题须含完整关键词）/ `any_char`（至少共享一个字）。检索型入口（`entrypoints[].search`）也要过这道复核——站内检索同样分词，不复核会漏进无关条目；没有检索入口时它就是唯一的过滤手段 |
| `drop_undated_when_windowed` | bool | 否 | `false` | 时间窗口内**缺日期**的条目是否丢弃。栏目列表页常不显示日期（日期在详情页），故默认保留 |
| `governance` | object | 否 | 见下 | 治理口径 |
| `notes` | str | 否 | `null` | 自由备注。**不进指纹**，不影响版本判定 |

## 2. `scope` —— 来源边界

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `allowed_domains` | array | **是** | — | 域名白名单（≥1）。既是来源边界，**也是 SSRF 白名单的一部分**——不在名单里的主机一律不取 |
| `start_urls` | array | 否 | `[]` | 起始地址（记录用；实际取数走 `entrypoints[].url`） |
| `request_interval_ms` | int | 否 | `null` | 本来源的抓取间隔（毫秒，0–60000）；`null` = 用全局 `SOURCE_REQUEST_INTERVAL_MS`。**只能调大不能调小**，别为了跑通去压这个值 |
| `max_items_per_entry` | int | 否 | `50` | 单个入口最多取多少条（1–500） |

## 3. `entrypoints[]` —— 内容入口

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `name` | str | **是** | — | 入口名（1–60 字符）。**同时是「发布机构」筛选的匹配值**：抓取时选了机构，只有同名的入口会被取数 |
| `url` | str | **是** | — | 入口地址。**必须是真正列出条目的那一页**（第 1 页就是它本身，翻页规则只作用于第 2 页起） |
| `list_selector` | str | **是** | — | 条目容器选择器，命中多个 = 多条 |
| `link_selector` | str | 否 | `a` | 相对条目容器的详情链接选择器 |
| `title_selector` | str | 否 | `null` | 标题选择器；不填 = 用链接文本 |
| `date_selector` | str | 否 | `null` | 列表页日期选择器；不填 = 列表不取日期（详情页再补） |
| `pagination` | object | 否 | `{"kind":"none"}` | 翻页规则，见下 |
| `search` | object | 否 | `null` | **站内检索入口**：填了它，这个入口的关键词/日期送进站点自己的检索页（见下）。不填 = 普通栏目列表页 |
| `notes` | str | 否 | `null` | 备注 |

### `search` —— 检索型入口（可选，但决定筛选能不能在服务端生效）

**为什么值得配**：栏目列表页给不了检索能力。没有 `search` 的入口，关键词只能拿已经翻到的
那些列表页**逐条比对标题**、时间范围只能**逐条比对列表页上那个日期**，召回上限 =
翻页深度 × 每页条数——更早的、更深处的文件永远碰不到。配了 `search`，关键词送进站点
自己的索引，召回由站点决定。口径的机读版本在 `sources/config.capability_summary()`。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `keyword_param` | str | `keyword` | 检索页接收关键词的参数名（`keyword` / `searchword` / `q` / `key`…）。**照 `inspect_source` 报告的 `search_forms[].field` 填，实测，不猜** |
| `date_from_param` | str | `null` | 起始日期参数名；**站点检索页确实接受日期参数时才填**。不填 = 日期仍由 `validate_item` 取回后复核 |
| `date_to_param` | str | `null` | 截止日期参数名，同上 |
| `date_format` | str | `%Y-%m-%d` | 日期参数的写法（strftime）。校验会拿 2000-01-02 渲染一遍，看不出年月日的写法直接拦下 |
| `fixed_params` | object | `{}` | 检索页的固定参数，如 TRS 系的 `{"channelid":"36021"}`（多半躺在表单的隐藏 input 里，报告里的 `search_forms[].params`） |

两条硬规矩：

- `url` 必须是**实测取过、确实出条目**的检索页地址。拼错的检索地址常常返回首页——
  解析出来的「条目」其实是导航链接，页面上看着像列表、内容全是错的。
- 无关键词的那一轮**检索入口不取数**（检索页没有关键词参数时返回空结果页，不是浏览路径）。
  关键词为空的任务只走栏目入口，这是设计如此。

页面「导入网址」会**自动**做这件事：发现页面里有 GET 检索表单 → 用探测关键词真取一页 →
解析出条目就自动加一个名为「站内检索」的入口（试不出来就不加，原因写进日志）。
Agent 侧则照 `search_forms` 报告自己填（`config-schema.md` 这里给的就是那个形状）。

### `pagination`

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `kind` | enum | `none` | `none`（只有一页）/ `query_param` / `path_index` |
| `param` | str | `page` | `query_param` 的参数名。**只在报告给出规律时才填**，别猜 |
| `path_template` | str | `null` | `path_index` 的模板，**必须含 `{page}`**（如 `index_{page}.html`、`list_{page}.htm`） |
| `start` | int | `1` | 站点对**第 1 页**的取值口径（`0` 起 / `1` 起）。第 2 页取 `start + (page-1)*step` |
| `step` | int | `1` | 步长 |
| `max_pages` | int | `20` | 硬上限，翻到这一页后不再翻（防止把站点掏空） |

`path_index` 缺 `path_template`、或模板里没有 `{page}`、或 `max_pages < 1` → 校验不通过。

## 4. `detail` —— 详情页解析

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `container_selectors` | array | `[]` | **候选链**：按序取第一个命中的容器；同类多个时取文本最长的（政务站常见「正文 + 打印版副本」）。全部落空时先**用结构启发式兜底**挑一块（会在该页 `warnings` 里标明「未经确认」），兜底也挑不出达标正文才判解析失败并记入失败项；两条路径都**不退回整页文本**（那会把导航/脚本当正文）。名单为空 = 配置不可执行，校验直接拦下 |
| `title_selectors` | array | `[]` | 标题候选链 |
| `date_selectors` | array | `[]` | 发布日期候选链。**只有「第一个取到非空文本」的候选算数**（`_text_of`），所以链内顺序有意义：取得到文本却 `parse_date` 不了的候选会挡住后面的好候选。页面导入组装这条链时会**跨 3 篇样本页核对**：每篇都取到同一个值 → 当页脚/模板元素丢弃（结果可能为空数组，日志里会说明是哪个选择器、值是什么）；能解析出真实日期的候选自动提到前面（见 `source_import._verify_date_selectors`） |
| `agency_selectors` | array | `[]` | 发布机构候选链 |
| `doc_no_selectors` | array | `[]` | 文号候选链 |
| `drop_selectors` | array | `[]` | 正文内要剔除的节点（分享条 / 相关阅读 / 打印提示）。**附件清单在剔除之前扫描**——把 `div.attach` 放这里不会导致附件丢失 |
| `attachment_selector` | str | `null` | 附件链接选择器；不填 = 内置扩展名启发式（pdf/doc/docx/xls/xlsx/zip/rar/ofd） |
| `min_body_chars` | int | `200` | 试运行时判定「正文过短」的 CJK 字数下限 |

## 5. `governance`

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `require_original_evidence` | bool | `true` | 要求留存原文证据（硬要求，别关） |
| `review_required` | bool | `true` | 需要人工复核（硬要求，别关） |
| `license_note` | str | `null` | 使用许可 / 转载说明，**人工填写**。不进指纹 |
| `contact` | str | `null` | 对接人（可选）。不进指纹 |

## 6. 选择器支持范围

解析引擎是纯标准库实现（`server/app/sources/htmlparse.py`），**只支持 CSS 子集**：

- ✅ 标签（`div`）、类（`.news`）、id（`#zoom`）、属性（`[data-role="title"]`）
- ✅ 后代（`div#zoom p`）、子元素（`ul > li`）、分组（`h1, h2`）
- ❌ 伪类 / 伪元素（`:first-child`、`nth-of-type`、`::before`）——校验会直接拦下
- ❌ 兄弟组合子（`+`、`~`）

属性值的写法（如 `a[href$=".html"]`）不受伪类限制的影响。写不支持的写法时，
`generate_source_config` 会在 `problems` 里逐条说明原因。

## 7. 完整示例

下面这份配置是**验收测试真正跑过的那一份**（`server/scripts/test_source_onboarding.py`
里的假站点 `demo.example.gov.cn`，试运行 `status=passed`）。抄它的**结构**，
把地址、选择器换成目标站点的实测值：

```json
{
  "schema_version": 1,
  "key": "zj_demo",
  "label": "浙江省人民政府门户（演示）",
  "level": "province",
  "organization": "浙江省人民政府",
  "source_type": "government_portal",
  "engine": "html_list",
  "scope": {
    "allowed_domains": ["demo.example.gov.cn"],
    "start_urls": ["https://demo.example.gov.cn/col/index.html"],
    "max_items_per_entry": 20
  },
  "entrypoints": [
    {
      "name": "政策文件",
      "url": "https://demo.example.gov.cn/col/index.html",
      "list_selector": "ul.news-list > li",
      "link_selector": "a",
      "title_selector": "a",
      "date_selector": "span.date",
      "pagination": {
        "kind": "path_index",
        "path_template": "index_{page}.html",
        "start": 1,
        "step": 1,
        "max_pages": 3
      }
    },
    {
      "name": "站内检索",
      "url": "https://demo.example.gov.cn/search.html",
      "list_selector": "ul.result-list > li",
      "link_selector": "a",
      "pagination": { "kind": "query_param", "param": "page", "max_pages": 3 },
      "search": {
        "keyword_param": "keyword",
        "date_from_param": "startdate",
        "date_to_param": "enddate",
        "date_format": "%Y-%m-%d",
        "fixed_params": { "channelid": "36021" }
      }
    }
  ],
  "detail": {
    "container_selectors": ["div#zoom", "div.article"],
    "title_selectors": ["h1"],
    "date_selectors": ["span.pubdate"],
    "agency_selectors": ["span.source"],
    "doc_no_selectors": ["span.docno"],
    "drop_selectors": ["div.attach"],
    "min_body_chars": 150
  },
  "doc_type_patterns": {},
  "keyword_match": "substring",
  "drop_undated_when_windowed": false,
  "governance": {
    "require_original_evidence": true,
    "review_required": true,
    "license_note": null,
    "contact": null
  },
  "notes": null
}
```

> 示例里的地址与选择器**只对演示站点成立**，不是真实站点的可用配置——真实站点必须
> 用 `inspect_source` / `sample_content` 实测出来的值。

## 8. 内容指纹与版本

`config_fingerprint()` = 对 `model_dump(exclude={"notes", "governance.license_note", "governance.contact"})`
做规范化 JSON 后取 sha256 前 16 位。

- **只认影响取数的字段**：改 `notes`、改许可说明、改对接人**不会**产生新版本（它们是给人看的，不改变抓什么）。
- **注册是幂等的**：指纹没变 → 不新增版本；指纹变了 → 版本 +1 并写一条变更历史。
- **验收绑定指纹**：`register_source(approved=true)` 要求存在**指纹一致**的通过验收。改完配置必须重跑 `dry_run_ingestion`，否则会被拒（除非用户明确同意 `force=true`）。

## 9. 状态机

```text
draft ──试运行通过──▶ validated ──启用──▶ active
  │                                   │
  └──────── 人工停用 ────▶ disabled ◀──┘
```

- `draft`：刚注册，仅供试运行。不带 `approved=true` 的 `register_source` 默认落这里。
- `validated`：试运行通过、尚未启用。
- `active`：**服务启动时会自动装进来源注册表**（`load_configured_from_db`），抓取页的来源下拉与流水线才认得它。
- `disabled`：人工停用，**可再启用**（凭据按指纹全表查，不因停用/时间/后续勘察次数失效，
  见 [acceptance-criteria.md](acceptance-criteria.md) 第 6 节）；停用后不装载。

改配置 → 指纹变 → 回到「需重新验收」的状态（变更历史在
`manage_ingestion_job(action="detail")` 里按版本可查）。
