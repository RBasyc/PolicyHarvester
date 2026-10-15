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
| `keyword_match` | enum | 否 | `substring` | 列表页关键词复核口径：`substring`（标题须含完整关键词）/ `any_char`（至少共享一个字） |
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
| `notes` | str | 否 | `null` | 备注 |

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
| `container_selectors` | array | `[]` | **候选链**：按序取第一个命中的容器；同类多个时取文本最长的（政务站常见「正文 + 打印版副本」）。全部落空 = 该页解析失败并记入失败项，**不退回整页文本**（那会把导航/脚本当正文）。名单为空 = 配置不可执行，校验直接拦下 |
| `title_selectors` | array | `[]` | 标题候选链 |
| `date_selectors` | array | `[]` | 发布日期候选链 |
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
- `disabled`：人工停用，可再启用；停用后不装载。

改配置 → 指纹变 → 回到「需重新验收」的状态（变更历史在
`manage_ingestion_job(action="detail")` 里按版本可查）。
