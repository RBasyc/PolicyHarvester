# gov.cn 官方检索 API 参数对照（实测记录）

> 接口：`sousuo.www.gov.cn/search-gov/data`。本文是从 `TODO.md` Phase 9 抽出的**原始实测证据**，
> 由 `[data-pipeline.md](data-pipeline.md)`（英文摘要）与 `CLAUDE.md` 的 gov.cn quirks 段引用。
> 结论性要点已收进那两处；**这里保留的是一手对照表、通道互斥表与踩坑经过**——改检索逻辑前先读这份。

对应用户提出的「官网只提供几种筛选方式，MCP 能否对齐」。逐项实测结论（`t=zhengcelibrary_gw` 单通道才能看到真实 `totalCount`）：

| 官网筛选 | API 参数 | 实测 |
|---|---|---|
| 搜索位置＝标题 | `searchfield=title` | ✅ **唯一有效值**：`q=中小企业` 时 `totalCount` 1780 → **16** |
| 搜索位置＝全文 | `searchfield=content` / `title:content:` / `summary` / `titleOnly` … | ❌ **一律等于不过滤**（恒 1780）。MCP 现在写的 `title:content:summary` 是静默无效值 |
| 按相关度 | `sort=score&sortType=1` | ✅ |
| 按时间 | `sort=pubtime&sortType=1`（`sortType=0` 为升序） | ✅ |
| 日期＝**一周内** | `timetype=timeyz` | ✅ `q=批复` → 仅 2026.10.08 一篇 |
| 日期＝**一月内** | `timetype=timeyy` | ✅ → 2 篇（多出 2026.09.29） |
| 日期＝**一年内** | `timetype=timeyn` | ✅ → 22 篇 |
| 日期＝不限 | `timetype=timeqb`（或不传） | ✅ |
| 日期＝自定义区间 | `timetype=timezd` + `mintime`/`maxtime` | ✅ 精确（2024-01-01..2026-12-31 + title → 恰 3 条） |
| 主题标签 | `tsbq=<标签名>`（取值见 `searchVO.extendresult.facetMap.tsbq`） | ⚠️ **会顶替关键词**（`tsbq`+`q` 只按标签返回：`q=中小企业 + tsbq=营商环境` → 只剩 56 条公报）；在 `t=zhengcelibrary` 下完全无效 |
| **发布机构分类** | **`bmfl=<机构名>`**（取值即 `searchVO.extendresult.facetMap.bmfl` 的键——即用户抓包里「工业和信息化部:1411条 … count:12798」那个列表） | ✅ **有效，但仅在 MCP 现用通道 `t=zhengcelibrary_gw_bm_gb` 上**（在 `t=zhengcelibrary` 上无效，此前误判「参数名未确定」正是**测错了通道**）。`q=中小企业+title` 下 `bumenfile` 114 → 工信部 **76** / 财政部 **42** / 发改委 **19** / 教育部 **8**，**与 facet 计数逐一吻合**；浏览态（`q=''`）同样有效：`bumenfile` 12798 → 财政部 **2226** |
| 主题分类（树） | `childtype=<parentid>` | ✅ 同样**仅 MCP 通道有效**：`childtype=1081` → `bumenfile` 114→**42**（facet 1081:48）、`childtype=1084` → **50**（facet 51）；`nodeid=` ❌ 无效 |
| 发布日期（年） | `pubtimeyear=<年>` | ✅ 同样**仅 MCP 通道有效**：`pubtimeyear=2024` → `bumenfile` 114→**13**、`gongwen` 16→**1**；`2025` → 7/1；`2020` → 20/1 |
| 文种（公文） | `pcodeJiguan=<国办发…>` | ✅ 仅作用于 `gongwen`：16→**10**（与 `groupMap.国办发=10` 吻合）；`filetype` ❌ 反而清空 `gongwen` |
| — | `puborg` / `orpro` / `inpro` / `dup` / `nodeid` | ❌ 无效；`puborg=<机构名>` 甚至会**清空 `gongwen`**（16→0），勿用（机构筛选一律用 `bmfl`） |
| **政策解读** | 换通道 **`t=zhengcelibrary`**（其 `catMap` 多出 `otherfile`） | ✅ MCP 用的 `zhengcelibrary_gw_bm_gb` **不含 `otherfile`**，故政策解读一直被整个漏掉；切 `t=zhengcelibrary` 即可带回 |

> **`timetype` 取值是拼音缩写，不是英文**：`timey` + `z`(周)/`y`(月)/`n`(年)。这一点导致第一轮用 `timeweek`/`timemonth`/`timeyear` 测试时**全部误判为「无效」**，已更正。
> 用户提供的官网「政策解读」真实抓包（`t=zhengcelibrary&timetype=timeyy&searchfield=title&subchildtype=2229&type=gwyzcwjk`）是定位到以上结论的关键——`type`/`subchildtype` 经复测**均无实际作用**，真正起作用的是 `t` 与 `timetype`。
>
> **⚠️ 方法论教训：参数有效性是「按通道（`t`）区分」的。** 上表「仅 MCP 通道有效」的那几行，此前都是在 `t=zhengcelibrary` 上测的，因此得出「参数名仍未确定 / 8 种取值全部无效」的**错误结论**；用户指出抓包里 `facetMap.bmfl` 就是发布机构列表后，改在 `t=zhengcelibrary_gw_bm_gb`（即 MCP 现用通道）上复测才命中。**今后测参数必须用调用方实际使用的 `t`。**
>
> **筛选器之间是「交集」，可叠加**：`bmfl=财政部 + pubtimeyear=2024` → `bumenfile` **7**；`bmfl=工业和信息化部 + childtype=1081` → **14**；`timetype=timeyy + bmfl=工业和信息化部` → 合计 1。分页（`p`/`n`）在筛选集内正常工作（`bmfl=财政部&p=2&n=5` 仍报 `totalCount=42`）。
>
> **稳定性已验证（排除限流噪声）**：7 次交错 baseline 恒为 `186 = bumenfile 114 + gongbao 56 + gongwen 16`，各变体（`pubtimeyear=2024`→70、`childtype=1081`→104、`bmfl=工业和信息化部`→148）**逐一精确复现**，故上表数值可信，非 `code=1001` 抖动。
>
> **❗ 遗留数据正确性问题：`gongbao`（公报）不受 `bmfl` / `pubtimeyear` / `childtype` 过滤**——上述所有筛选下 `gongbao` 恒为 **56** 条纹丝不动（连 `pubtimeyear=2020` 时也仍是 56），只有 `timetype` 能把它压下去（`timetype=timeyy + bmfl=工信部` → `gongbao=1`）。**即：按机构/年份/主题筛选时，56 条公报仍会整批混进结果。** 实现时必须单独处置（按机构筛选时丢弃 `gongbao`，或对公报同样做客户端复核）。

**⚠️ 最大的坑（决定实现方式）**：严格条件命中 0 条时，接口**不会返回空集，而是静默退回「全量浏览列表」**——`q=数据要素 + searchfield=title`、`q=量子计算 + searchfield=title` 均返回 1780 条浏览噪声；`q=中小企业 + title + 2020 窗口` 甚至回了一篇 2025 年的文件。**因此「无相关政策」只能靠客户端二次校验得出，不能相信接口给的条数。** 另：突发连续请求会返回 `code=1001`（疑似限流）——同一个 `q=数据要素 + title` 前后两次结果分别 1001 / 200，故 1001 不可解读为「无结果」。

**❗ 通道二选一（本轮新增的关键约束）**：两个通道**各只有一半能力，且互斥**——

| 通道 `t` | 目录（`catMap`） | `bmfl`/`childtype`/`pubtimeyear` 筛选 |
|---|---|---|
| `zhengcelibrary_gw_bm_gb`（MCP 现用） | `gongwen` + `bumenfile` + `gongbao`（合计 186） | ✅ **有效** |
| `zhengcelibrary` | 上述三者 + **`otherfile`（政策解读）**（合计 285） | ❌ **全部失效** |

即：**要「发布机构/主题/年份筛选」就得放弃「政策解读」；要「政策解读」就得放弃全部筛选器。**（上一轮推荐的「切到 `t=zhengcelibrary` 带回政策解读」与本轮测到的筛选能力直接冲突，不能同时拿。）凡涉及此决策需先请用户拍板；若两者都要，只能并发打两个通道再并集去重。

**结论（推荐改法）**：① MCP 一行改 `searchfield: 'title:content:summary'` → `'title'`（顺带证实该字段此前一直无效）；② 新增 `bmfl` / `childtype` / `pubtimeyear` / `pcodeJiguan` 透传（**仅 `zhengcelibrary_gw_bm_gb` 通道**），即可对齐官网的发布机构分类、主题分类、发布日期、文种四档筛选；③ 本项目 `_search_policies()` 增加**客户端复核**——标题不含关键词、或日期不在 `date_from/date_to` 内的直接丢弃，只有这样才能得到可信的「无」；④ **按机构/年份/主题筛选时单独丢弃 `gongbao`**（它不参与这些筛选，恒 56 条）；⑤ `content_type`（policy/notice/all）**当前对抓取零影响**（仅校验/落库/回传/可选，流水线从不读它），该 API 也没有对应参数，建议从 UI 撤下或明确标注为仅记录。

