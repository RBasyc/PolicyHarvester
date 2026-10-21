"""网站来源接入工具（供对话 Agent 自主驱动「接一个新网站」的全流程）。

对应 Skill 的七个阶段，工具按**阶段边界**切分，每个工具负责「一次可验证的动作」：

    阶段 1–2 勘察      inspect_source            取回地址 → 结构候选 + robots 情况
    阶段 2 内容发现    discover_content          入口页（可追栏目）→ 内容入口清单
    阶段 3 结构分析    analyze_content_structure 单页候选识别（可离线喂 HTML）
    阶段 3 样本核对    sample_content            取样本详情页 → 逐字段命中情况
    阶段 4 生成配置    generate_source_config    组装 SourceConfig + 可执行性校验
    阶段 5 试运行验收  dry_run_ingestion         真实链路小批量试抓 → 验收报告
    阶段 6 注册启用    register_source           落库 + 装进注册表（需验收通过）
    阶段 7 运维诊断    manage_ingestion_job      启停 / 试抓 / 健康 / 列表
                      diagnose_source           失败归因（卡在哪一层）

**为什么与建议接口清单不完全一一对应**：`validate_ingestion` 没有单独成工具——
验收结论就是 `dry_run_ingestion` 的同一次执行产出的（先跑再判，分开会变成两次结果可能不一致
的独立动作），另补 `diagnose_source` 承担阶段 7 的故障归因。接口边界仍然是清楚的。

**这些工具的硬约束**（与 Skill 的行为规范一致，改了要同步改 skill）：

- 只做编排与结果压缩，**不绕过任何访问控制**：地址校验 / robots / 限频都在 `sources/net.py`，
  401/403/维护页一律报「受阻」，不尝试登录、验证码、付费墙或伪造身份；
- 抓到的东西在验收通过前只能是**候选**：报告里的结构结论一律标「候选（待试运行确认）」，
  `register_source` 没有通过验收的试运行记录时拒绝启用（除非调用方显式 `force`）；
- 未执行的采集、未验证的配置、未完成的验收**不得报成成功**——本模块把服务的
  `passed` / `failed` / `blocked` 原样透出，不做美化；
- 工具**不抛异常**：任何失败都返回 `{"ok": false, "error": "<中文原因>"}`，让模型能读着改。

`app/services/onboarding.py` 的导入写在函数体内：`app.agent.tools` → 本模块 → services →
`pipeline/crawl_graph` → `app.agent.mcp` → `app.agent.tools` 会成环，顶层导入在启动时就炸。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

# 共用小工具（结果压缩 / 统一失败返回 / 日期参数校验）只有一份，见 `tool_utils`；
# 这里起回原名，函数体一个字都不用改。
from app.agent.tool_utils import dump as _dump
from app.agent.tool_utils import error as _error
from app.agent.tool_utils import parse_iso_date as _parse_date


def _load_json(raw: str | dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    """解析 JSON 参数（允许直接传 dict）；返回 `(payload, error)`。"""
    if raw is None:
        return None, "缺少 JSON 参数"
    if isinstance(raw, dict):
        return raw, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}"
    if not isinstance(parsed, dict):
        return None, "JSON 顶层必须是对象（{...}）"
    return parsed, None


def _candidates(rows: list[dict[str, Any]], keep: int = 3) -> list[dict[str, Any]]:
    """只留候选的前 N 条（模型只需要首选与备选，不需要完整榜单）。"""
    out = []
    for row in rows[:keep]:
        slim = {k: v for k, v in row.items() if k not in ("text_ratio", "link_ratio", "paragraphs")}
        out.append(slim)
    return out


# ---------- 阶段 1–2：勘察 ----------


@tool
async def inspect_source(
    url: str,
    target: str | None = None,
    allowed_domains: list[str] | None = None,
) -> str:
    """接入第一步：勘察一个网站地址，判断「能不能抓、抓什么、用什么规则」。

    会先做目标地址安全校验（协议 / 本机与内网地址 / 域名白名单 / robots.txt），
    再取回该页并给出结构候选：列表容器、正文容器、日期/文号/机构字段、翻页规则、
    内容栏目入口、订阅源、站内检索表单。**返回的都是候选，未经试运行不算已确认。**

    Args:
        url: 要接入的页面地址（网站首页或某个栏目列表页都可以）。
        target: 这次接入要采集什么内容（自然语言，如「省级人工智能政策文件」），用于后续定关键词。
        allowed_domains: 允许访问的域名白名单；留空则自动收敛为 url 的主机名（推荐留空）。

    Returns:
        JSON 文本。`ok=false` 时 `kind` 说明是 `blocked`（站点/环境不允许，如 robots 禁止、
        403、内网地址——**按接入规范不绕过**）还是 `failed`（地址不可达等）；
        `ok=true` 时含 `list_candidates` / `detail_candidates` / `meta_candidates` /
        `pagination` / `columns` / `feeds` / `search_forms` / `other_hosts` 与下一步建议。
    """
    try:
        from app.services.onboarding import inspect_source as _inspect
    except Exception as exc:  # noqa: BLE001 - 依赖装不全时也要给模型一个可读原因
        return _error(f"接入服务不可用：{exc}")

    try:
        report = await _inspect(url, target=target, allowed_domains=allowed_domains)
    except Exception as exc:  # noqa: BLE001
        return _error(f"勘察失败：{exc}")

    if not report.get("ok"):
        return _dump(
            {
                "ok": False,
                "kind": report.get("kind"),
                "error": report.get("error"),
                "robots": report.get("robots"),
                "allowed_domains": report.get("allowed_domains"),
                "run_id": report.get("run_id"),
                "next": (
                    "地址被安全策略或站点规则拦下：不要尝试绕过（换栏目/换协议都不行就先与站点方确认），"
                    "改抓其它公开入口，或改用该站提供的 RSS / 检索接口。"
                    if report.get("kind") == "blocked"
                    else "地址不可达：核对地址是否写错（协议 / 域名 / 路径），再试一次。"
                ),
            }
        )

    meta = report.get("meta_candidates") or {}
    return _dump(
        {
            "ok": True,
            "run_id": report.get("run_id"),
            "url": report.get("url"),
            "final_url": report.get("final_url"),
            "allowed_domains": report.get("allowed_domains"),
            "allowed_domains_inferred": report.get("allowed_domains_inferred"),
            "robots": report.get("robots"),
            "http_status": report.get("http_status"),
            "bytes": report.get("bytes"),
            "page_title": report.get("page_title"),
            "text_len": report.get("text_len"),
            "list_candidates": _candidates(report.get("list_candidates") or [], 3),
            "detail_candidates": _candidates(report.get("detail_candidates") or [], 3),
            "meta_candidates": {k: v[:2] for k, v in meta.items()},
            "field_candidates": (report.get("field_candidates") or [])[:8],
            "pagination": report.get("pagination"),
            "columns": (report.get("columns") or [])[:8],
            "feeds": report.get("feeds"),
            "search_forms": report.get("search_forms"),
            "attachments_on_page": (report.get("attachments_on_page") or [])[:5],
            "other_hosts": report.get("other_hosts"),
            "note": "以上均为**候选**，未经 sample_content 取样与 dry_run_ingestion 试运行验证，不得当成已确认的接入配置。",
            "next": (
                "若 list_candidates 为空，说明这页不是列表页（首页/正文页）或列表由 JS 渲染："
                "用 discover_content 追一层栏目，或换栏目列表页地址。"
                "若 list_candidates 有结果，用 sample_content 取几篇详情页核对字段。"
            ),
        }
    )


@tool
async def discover_content(
    url: str,
    keywords: list[str] | None = None,
    follow: int = 0,
    allowed_domains: list[str] | None = None,
) -> str:
    """从入口页出发找「内容栏目」：把首页/栏目页里像内容入口的链接找出来并逐层分析。

    `follow=0` 只看入口页；`follow=1/2` 会沿内容栏目链接再往下取，深层栏目页才真正列出
    条目（首页通常只有导航）。每页都对同一批候选做结构分析，最后给出入口清单与建议栏目名。

    Args:
        url: 入口页地址（一般是网站首页或政务公开首页）。
        keywords: 这次要采集的主题词（用于给入口起名字与挑选相关栏目）。
        follow: 再往下追几层栏目页（0–2，默认 0）。
        allowed_domains: 域名白名单；留空自动用 url 的主机名。

    Returns:
        JSON 文本：`entrypoint_candidates`（每条含 page_url / name_suggestion / list_selector /
        item_count / sample_title / pagination）与 `errors`（取不回来的页面及原因）。
    """
    try:
        from app.services.onboarding import discover_content as _discover
    except Exception as exc:  # noqa: BLE001
        return _error(f"接入服务不可用：{exc}")

    try:
        report = await _discover(
            url, allowed_domains=allowed_domains, keywords=keywords or [], follow=follow
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f"内容发现失败：{exc}")

    entrypoints = [
        {
            "page_url": row.get("page_url"),
            "name": row.get("name_suggestion"),
            "list_selector": row.get("list_selector"),
            "item_count": row.get("item_count"),
            "sample_title": row.get("sample_title"),
            "pagination": row.get("pagination"),
        }
        for row in (report.get("entrypoint_candidates") or [])
    ]
    return _dump(
        {
            "ok": bool(entrypoints),
            "run_id": report.get("run_id"),
            "url": report.get("url"),
            "pages_analyzed": report.get("summary", {}).get("pages_analyzed"),
            "entrypoints": entrypoints,
            "errors": report.get("errors"),
            "error": None if entrypoints else report.get("error"),
            "next": (
                "把选中的入口（url + list_selector + pagination）交给 sample_content 取样核对。"
                if entrypoints
                else "没找到成组条目结构：确认该栏目是否需要 JS 渲染、是否需要翻到更深的栏目页，"
                "或改用站内检索 / RSS（inspect_source 的 feeds 与 search_forms）。"
            ),
        }
    )


# ---------- 阶段 3：结构分析与取样 ----------


@tool
async def analyze_content_structure(
    url: str | None = None,
    html: str | None = None,
    listed_as: str = "page",
    include_outline: bool = True,
) -> str:
    """分析**单个页面**的结构候选（正文容器 / 元信息 / 字段 / 附件 / 翻页 / 栏目）。

    `url` 与 `html` 二选一：给 `html` 时完全离线分析（可用来复核已经取回的页面）。

    **详情页补字段时重点看 `field_candidates` 与 `dom_outline`**：

    - `field_candidates` 是页面实测的「标签 → 值」关系，每条带 `selector`（标签节点）、
      `label`、`relation`（`label_child` / `label_sibling` / `inline`）、`value_from`
      （`parent_own_text` / `next_sibling_text` / `self_text` / `parent_text`）与
      `sample_value`。把 `field` + `label` + `selector` + `value_from` 直接写进
      `detail.fields` 的 `FieldRule` 即可。
    - `dom_outline` 是带**直接文本**的压缩 DOM：标签和值若分处不同节点，在这里能一眼看出
      该用哪种 `value_from`（`text_content` 会把两者糊在一起，看不出来）。

    Args:
        url: 页面地址（会先做地址安全校验再取回）。
        html: 直接给出的 HTML 源码。
        listed_as: 这页是什么角色（如 entry / detail / level-1），只影响报告里的自我标注。
        include_outline: 是否返回 DOM 提纲（默认 true；列表页可关掉省上下文）。

    Returns:
        JSON 文本：`list_candidates` / `detail_candidates` / `meta_candidates` /
        `field_candidates` / `dom_outline` / `pagination` / `columns` / `feeds` /
        `search_forms` / `attachments`，均为候选。
    """
    try:
        from app.services.onboarding import analyze_structure
    except Exception as exc:  # noqa: BLE001
        return _error(f"接入服务不可用：{exc}")

    if not url and html is None:
        return _error("需要提供 url 或 html 之一")

    try:
        report = await analyze_structure(
            url=url, html=html, listed_as=listed_as, include_outline=include_outline
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f"结构分析失败：{exc}")

    if not report.get("ok"):
        return _dump({"ok": False, "kind": report.get("kind"), "error": report.get("error")})

    meta = report.get("meta_candidates") or {}
    return _dump(
        {
            "ok": True,
            "url": report.get("url"),
            "page_title": report.get("page_title"),
            "text_len": report.get("text_len"),
            "list_candidates": _candidates(report.get("list_candidates") or [], 3),
            "detail_candidates": _candidates(report.get("detail_candidates") or [], 3),
            "meta_candidates": {k: v[:2] for k, v in meta.items()},
            "field_candidates": (report.get("field_candidates") or [])[:12],
            "dom_outline": report.get("dom_outline"),
            "pagination": report.get("pagination"),
            "columns": (report.get("columns") or [])[:6],
            "feeds": report.get("feeds"),
            "search_forms": report.get("search_forms"),
            "attachments": (report.get("attachments") or [])[:5],
            "note": "候选不是结论：采纳前必须取样（sample_content）与试运行（dry_run_ingestion）。",
        }
    )


@tool
async def sample_content(
    url: str,
    count: int = 3,
    selectors_json: str | None = None,
    list_selector: str | None = None,
    allowed_domains: list[str] | None = None,
) -> str:
    """阶段 3 取样核对：取几篇**真实详情页**，逐字段看解析规则命中得怎么样。

    这是把「候选规则」变成「已验证规则」的那一步——正文长度、日期、机构、文号、附件
    每项都给出**实测**命中情况；取不到就如实记 0，不猜。

    Args:
        url: 列表页地址（会先按 list_selector 取条目再逐篇取样），或直接给详情页地址。
        count: 取几篇样本（建议 3）。
        selectors_json: 解析规则 JSON（字段同配置里的 detail：container_selectors /
            title_selectors / date_selectors / agency_selectors / doc_no_selectors；
            以及语义化字段 `fields` —— 字段名 → FieldRule 列表，见 generate_source_config
            的说明与 analyze_content_structure 返回的 `field_candidates`）。
            不给就用自动候选里得分最高的那组，并在报告里标明「auto-candidate（待确认）」。
        list_selector: 列表页的条目选择器（不给则用自动候选首选）。
        allowed_domains: 域名白名单；留空用 url 的主机名。

    Returns:
        JSON 文本：`rules`（本次用的规则及其来源）、`samples`（逐篇逐字段实测）、
        `hits`（各字段命中数）、`rules_confirmed`（规则是显式给的还是自动候选）。
    """
    try:
        from app.services.onboarding import sample_content as _sample
    except Exception as exc:  # noqa: BLE001
        return _error(f"接入服务不可用：{exc}")

    selectors, err = (None, None)
    if selectors_json:
        selectors, err = _load_json(selectors_json)
        if err:
            return _error(f"selectors_json 解析失败：{err}")

    try:
        report = await _sample(
            url,
            count=count,
            selectors=selectors,
            allowed_domains=allowed_domains,
            list_selector=list_selector,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f"取样失败：{exc}")

    samples = [
        {
            "url": row.get("url"),
            "title": row.get("title") or row.get("list_title"),
            "date": row.get("date"),
            "agency": row.get("agency"),
            "doc_no": row.get("doc_no"),
            "fields": row.get("fields"),
            "body_cjk": row.get("body_cjk"),
            "sent_enders": row.get("sent_enders"),
            "body_ok": row.get("body_ok"),
            "container": row.get("container_selector"),
            "attachments": len(row.get("attachments") or []),
            "warnings": row.get("warnings"),
        }
        for row in (report.get("samples") or [])
    ]
    return _dump(
        {
            "ok": report.get("ok"),
            "run_id": report.get("run_id"),
            "rules": report.get("rules"),
            "rules_confirmed": report.get("rules_confirmed"),
            "hits": report.get("hits"),
            "samples": samples,
            "error": report.get("error"),
            "next": (
                "正文普遍 body_ok=true 且元信息能取到 → 用 generate_source_config 组装配置；"
                "正文为空/过短 → 正文容器选择器不对，回 analyze_content_structure 换候选；"
                "日期/机构/文号取不到、而页面上明明有 → 多半是标签和值分处 `<span>`/父节点/兄弟节点，"
                "按 analyze_content_structure 的 field_candidates 写 detail.fields（不要继续堆 "
                "*_selectors）；该站确实没有这些字段就如实留着。"
            ),
        }
    )


# ---------- 阶段 4：生成配置 ----------


@tool
async def generate_source_config(config_json: str) -> str:
    """阶段 4：把前面勘察/取样得到的规则组装成一份接入配置，并做**可执行性**校验。

    只校验不抓取：选择器语法是否支持、入口域名是否在白名单内、正文容器是否缺、
    入口名是否重复、翻页参数是否自相矛盾……校验通过才拿去做试运行。

    Args:
        config_json: 配置 JSON 文本。字段：
            key（英文唯一标识）、label（展示名，全局唯一）、level（national/province/city）、
            organization、source_type、
            engine（`html_list` = httpx 静态取数，默认；`browser_driven` = 无头浏览器驱动：
            没有 entrypoints/选择器，改配 browse.url 入口页——**勘察报告 js_render_suspected=true
            或试运行提示 JS 渲染空壳时必须走这条无头路径**）、
            scope{allowed_domains, start_urls, max_items_per_entry}、
            entrypoints[{name, url, list_selector, link_selector, title_selector,
                         date_selector, item_fields{字段名: [FieldRule]},
                         pagination{kind,param,path_template,start,step,max_pages},
                         search{keyword_param,date_from_param,date_to_param,date_format,fixed_params}}]、
            detail{container_selectors, title_selectors, date_selectors, agency_selectors,
                   doc_no_selectors, fields{字段名: [FieldRule]}, drop_selectors, min_body_chars}。

            **`fields` / `item_fields` 是「标签 → 值」的语义化规则**，解决旧选择器链表达不了的
            三种形状：标签在 `<span>`、值在父节点直接文本（`parent_own_text`）；标签与值分居
            兄弟节点（`next_sibling_text`）；标签与值同节点（`self_text` + `drop_label`）。
            每条规则：selector（通常指向标签节点）、label_equals / label_matches（标签比较前
            忽略所有空白与末尾冒号）、value_from（上面三种 + `self_text`/`own_text`/
            `parent_text`/`attr`）、可选 drop_label / pattern / group / reject / normalize
            （`whitespace`|`none`|`date`）/ match_index / scope。规则链按顺序 fallback，
            第一个取到非空值的获胜。字段名 `title`/`agency`/`doc_no`/`date`（= 发布日期）是
            保留名，其余字段原样进 `policy_document.metadata`（索引号 / 成文日期 / 主题分类…）。
            例（`<div><span class="fw-bold">文　　号：</span> 惠府〔2025〕13号</div>`）：
            `"fields": {"doc_no": [{"selector": "span.fw-bold", "label_equals": "文号",
             "value_from": "parent_own_text"}]}`。旧 `*_selectors` 可以继续保留作 fallback，
            `fields` 取到值时会覆盖它们。
            例：{"key":"zj_gov","label":"浙江省人民政府门户","level":"province",
                 "engine":"html_list","scope":{"allowed_domains":["zj.gov.cn"]},
                 "entrypoints":[{"name":"政策文件","url":"https://www.zj.gov.cn/col/col1/index.html",
                                 "list_selector":"ul.news-list > li","link_selector":"a",
                                 "date_selector":"span.date",
                                 "pagination":{"kind":"path_index","path_template":"index_{page}.html",
                                               "start":1,"step":1,"max_pages":3}},
                                {"name":"站内检索",
                                 "url":"https://www.zj.gov.cn/search.html",
                                 "list_selector":"ul.result-list > li",
                                 "search":{"keyword_param":"keyword","date_from_param":"startdate",
                                           "date_to_param":"enddate","date_format":"%Y-%m-%d",
                                           "fixed_params":{"channelid":"36021"}}}],
                 "detail":{"container_selectors":["div#zoom"],"drop_selectors":["div.attach"]}}

            **`entrypoints[].search` 是筛选能力的关键开关**：栏目列表页给不了检索能力，配它之前
            关键词与时间范围只能拿翻到的列表页逐条比对（召回上限 = 翻页深度）。填了它，这个入口
            就走站内检索页，关键词/日期由站点执行。它是可选字段——不加就是原来的栏目入口。
            两个硬性要求：① `url` 是**实测取过**的检索页地址（不是猜出来的 `/search`），
            ② 参数名照 `inspect_source` 报告的 `search_forms`（`field` / `params` / `method`）填，
            别猜；`method` 是 post 的表单当前引擎不支持，只能用 GET。无关键词的那一轮检索入口
            不取数（检索页没有关键词参数时返回空结果页），这是设计如此、不是缺陷。

    Returns:
        JSON 文本：`ok`（能否执行）、`problems`（不可执行的原因，逐条给出修复指引）、
        `fingerprint`（取数规则的指纹，配置改了它才变）、`summary`（配置摘要）。
    """
    try:
        from app.services.onboarding import generate_config
    except Exception as exc:  # noqa: BLE001
        return _error(f"接入服务不可用：{exc}")

    payload, err = _load_json(config_json)
    if err:
        return _error(f"config_json 解析失败：{err}")

    try:
        report = generate_config(payload)
    except Exception as exc:  # noqa: BLE001
        return _error(f"配置校验失败：{exc}")

    return _dump(
        {
            "ok": report.get("ok"),
            "problems": report.get("problems"),
            "fingerprint": report.get("fingerprint"),
            "summary": report.get("summary"),
            "config": report.get("config") if not report.get("ok") else None,
            "next": (
                "校验通过 → dry_run_ingestion 小批量试运行（这一步才真正验证站点结构）。"
                if report.get("ok")
                else "按 problems 逐条修正后重新校验（多数问题补一个选择器或改域名白名单即可）。"
            ),
        }
    )


# ---------- 阶段 5：试运行与验收 ----------


@tool
async def dry_run_ingestion(
    config_json: str,
    limit: int = 3,
    pages: int = 1,
    keywords: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """阶段 5：小批量试运行 + 验收报告（**走真实检索链路**，不是模拟）。

    会把配置临时装进来源注册表，用正式抓取同一套检索编排跑一遍小批量，再逐篇真取详情页，
    最后给出四类结论：**通过项 / 失败项 / 未测试项 / 覆盖范围限制**，以及最终 status：

    - `passed`  可进入注册；
    - `failed`  配置或站点结构与预期不符（改选择器/翻页规则后重跑）；
    - `blocked` 当前环境或站点不允许（robots 禁止 / 401 / 403 / 内网地址）——按接入规范不绕过。

    它**不建抓取任务、不写正式结果表**（那是注册后正式抓取的事）。

    Args:
        config_json: 与 generate_source_config 相同的配置 JSON。
        limit: 抽样试抓几篇（建议 3）。
        pages: 声明每个入口试抓前几页（影响报告里的覆盖范围说明）。
        keywords: 要试抓的关键词；留空 = 不按关键词过滤，直接取栏目列表。
        date_from / date_to: 时间窗口（YYYY-MM-DD），留空 = 不限。

    Returns:
        JSON 文本：`status`、`criteria`（每条含 标准/实测/是否通过/硬软性）、
        `items_found`、`details`（逐篇正文实测）`untested`、`coverage_limits`、`attachments_probed`。
        **只有 status=passed 才能进入注册**；failed/blocked 必须如实转述，不得说成成功。
    """
    try:
        from app.services.onboarding import dry_run_ingestion as _dry_run
    except Exception as exc:  # noqa: BLE001
        return _error(f"接入服务不可用：{exc}")

    payload, err = _load_json(config_json)
    if err:
        return _error(f"config_json 解析失败：{err}")
    start, err = _parse_date(date_from, "date_from")
    if err:
        return _error(err)
    end, err = _parse_date(date_to, "date_to")
    if err:
        return _error(err)

    try:
        report = await _dry_run(
            payload,
            limit=limit,
            pages=pages,
            keywords=keywords or [],
            date_from=start,
            date_to=end,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f"试运行失败：{exc}")

    criteria = [
        {
            "name": row.get("name"),
            "threshold": row.get("threshold"),
            "actual": row.get("actual"),
            "passed": row.get("passed"),
            "hard": row.get("hard"),
            "note": row.get("note"),
        }
        for row in (report.get("criteria") or [])
    ]
    details = [
        {
            "url": row.get("url"),
            "title": row.get("title") or row.get("list_title"),
            "body_cjk": row.get("body_cjk"),
            "sent_enders": row.get("sent_enders"),
            "body_ok": row.get("body_ok"),
            "date": row.get("date"),
            "agency": row.get("agency"),
            "doc_no": row.get("doc_no"),
            "fields": row.get("fields"),
            "error": row.get("error"),
        }
        for row in (report.get("details") or [])
    ]
    return _dump(
        {
            "ok": report.get("ok"),
            "status": report.get("status"),
            "run_id": report.get("run_id"),
            "error": report.get("error"),
            "search_error": report.get("search_error"),
            "fingerprint": report.get("fingerprint"),
            "items_found": report.get("items_found"),
            "entrypoints": report.get("entrypoints"),
            "criteria": criteria,
            "details": details,
            "attachments_probed": report.get("attachments_probed"),
            "untested": report.get("untested"),
            "coverage_limits": report.get("coverage_limits"),
            "next": (
                "passed → 可以 register_source 注册（默认草案，approved=true 直接启用）。"
                if report.get("status") == "passed"
                else "按 criteria 里没通过的判据改配置（改完必须重跑本工具），"
                "blocked 则改用其它公开入口或与站点方确认——不要尝试绕过访问控制。"
            ),
        }
    )


# ---------- 阶段 6：注册 ----------


@tool
async def register_source(
    config_json: str,
    approved: bool = False,
    force: bool = False,
    change_note: str | None = None,
) -> str:
    """阶段 6：注册来源（校验 → 落库留痕 → 装进来源注册表）。

    三道闸：① 配置必须可执行；② **默认注册为草案（draft）**，只有 `approved=true` 才置 `active`；
    ③ `approved=true` 时必须存在**指纹一致**的通过试运行记录，否则拒绝（除非 `force=true`
    由人明确拍板——那时会把「未经验收强制启用」写进变更说明）。

    Args:
        config_json: 与试运行完全相同的配置 JSON（改过任何一个取数字段指纹就会变，
            需要重新试运行）。
        approved: 是否直接启用（true 需先有通过验收的试运行）。
        force: 人工拍板放行（跳过验收要求，会在变更历史里留痕）。**只在用户明确要求时用。**
        change_note: 本次变更说明（会写进版本历史）。

    Returns:
        JSON 文本：`ok`、`source_id`、`status`、`version`、`version_bumped`、
        `fingerprint`、`verification`（验收凭据）、`next`。
    """
    try:
        from app.services.onboarding import register_source as _register
    except Exception as exc:  # noqa: BLE001
        return _error(f"接入服务不可用：{exc}")

    payload, err = _load_json(config_json)
    if err:
        return _error(f"config_json 解析失败：{err}")

    try:
        report = await _register(
            payload, approved=approved, force=force, change_note=change_note, changed_by="agent"
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f"注册失败：{exc}")

    if not report.get("ok"):
        return _dump(
            {
                "ok": False,
                "error": report.get("error"),
                "problems": report.get("problems"),
                "verification": report.get("verification"),
                "next": (
                    "先跑 dry_run_ingestion 拿到 status=passed（且配置未改动），再注册。"
                    "如确需人工放行，必须由用户明确同意后才用 force=true。"
                ),
            }
        )
    return _dump(
        {
            "ok": True,
            "source_id": report.get("source_id"),
            "key": report.get("key"),
            "label": report.get("label"),
            "status": report.get("status"),
            "version": report.get("version"),
            "created": report.get("created"),
            "version_bumped": report.get("version_bumped"),
            "fingerprint": report.get("fingerprint"),
            "verification": report.get("verification"),
            "next": report.get("next"),
        }
    )


# ---------- 阶段 7：运维与诊断 ----------


@tool
async def manage_ingestion_job(
    action: str,
    key: str | None = None,
    source_id: int | None = None,
    keywords: list[str] | None = None,
    max_policies: int = 3,
) -> str:
    """阶段 7：来源的日常运维（列出 / 看详情 / 启停 / 发起真实试抓 / 健康检查）。

    Args:
        action: 取值之一：
            - `list`      列出来源与状态（无需 key）
            - `detail`    单个来源：配置、版本历史、最近接入记录
            - `enable` / `disable`  启停（启用同样要求有通过验收的试运行）
            - `run_trial` 发起一次**真实的小批量抓取任务**（走完整流水线，会停在人工确认）
            - `delete`    删除来源配置
            - `health`    健康检查：逐个入口取第 1 页，看是否可达、能解析出几条
        key / source_id: 目标来源（二者之一）。
        keywords: `run_trial` 用。
        max_policies: `run_trial` 抓几篇（1–5）。

    Returns:
        JSON 文本，随 action 不同而不同（`sources` / `source`+`revisions`+`runs` /
        启停结果 / `task_id` / `entrypoints` 健康明细）。失败时 `ok=false` + `error`。
    """
    try:
        from app.services.onboarding import manage_source
    except Exception as exc:  # noqa: BLE001
        return _error(f"接入服务不可用：{exc}")

    try:
        report = await manage_source(
            action,
            key=key,
            source_id=source_id,
            keywords=keywords or [],
            max_policies=max_policies,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f"来源管理失败：{exc}")

    # 详情里带整份配置与版本历史，体积大；只留模型做判断需要的部分
    if action == "detail" and report.get("ok"):
        source = report.get("source") or {}
        return _dump(
            {
                "ok": True,
                "source": {
                    "id": source.get("id"),
                    "key": source.get("key"),
                    "label": source.get("label"),
                    "status": source.get("status"),
                    "version": source.get("version"),
                    "fingerprint": source.get("fingerprint"),
                    "loaded": source.get("loaded"),
                    "entrypoints": [
                        {"name": ep.get("name"), "url": ep.get("url")}
                        for ep in ((source.get("config") or {}).get("entrypoints") or [])
                    ],
                },
                "revisions": report.get("revisions"),
                "runs": report.get("runs"),
            }
        )
    return _dump(report)


@tool
async def diagnose_source(
    url: str | None = None,
    key: str | None = None,
    allowed_domains: list[str] | None = None,
) -> str:
    """阶段 7 故障归因：抓不到/抓不对时，指出**卡在哪一层**并给替代途径。

    分层逐级试：① 地址与访问权限 → ② 页面取回 → ③ 列表解析 → ④ 正文容器 →
    ⑤ 元信息 → ⑥ 附件。给 `key` 会按该来源配置的入口地址与白名单诊断（站点改版时用这个）。

    Args:
        url: 出问题的页面地址。
        key: 已注册来源的 key（与 url 二选一；优先用配置里的地址与白名单）。
        allowed_domains: 域名白名单覆盖项。

    Returns:
        JSON 文本：`layers`（每层是否通过 + 实测细节）、`suggestions`（可操作的下一步）。
    """
    try:
        from app.services.onboarding import diagnose
    except Exception as exc:  # noqa: BLE001
        return _error(f"接入服务不可用：{exc}")

    if not url and not key:
        return _error("需要 url 或 key 之一")

    try:
        report = await diagnose(url=url, key=key, allowed_domains=allowed_domains)
    except Exception as exc:  # noqa: BLE001
        return _error(f"诊断失败：{exc}")

    return _dump(
        {
            "ok": report.get("ok"),
            "error": report.get("error"),
            "layers": report.get("layers"),
            "suggestions": report.get("suggestions"),
            "final_url": report.get("final_url"),
            "pagination": report.get("pagination"),
            "list_candidates": _candidates(report.get("list_candidates") or [], 3),
            "detail_candidates": _candidates(report.get("detail_candidates") or [], 3),
        }
    )


# 注册表：`app/agent/tools.py` 把它并进 TOOLS（顶层导入，不含服务依赖）
ONBOARDING_TOOLS = [
    inspect_source,
    discover_content,
    analyze_content_structure,
    sample_content,
    generate_source_config,
    dry_run_ingestion,
    register_source,
    manage_ingestion_job,
    diagnose_source,
]
