"""页面内「导入网址」（Phase 15）的回归用例。

直接运行：uv run python scripts/test_source_import.py

**为什么单开一个脚本**：`app/services/source_import.py` 是页面入口的编排层，线上出过一次
「什么都取不到」的故障（2026-10-15），两个原因都在这个模块里，且**都不是网络问题**：

1. **候选只按分数取**——分析器给的是结构候选，「一堆同签名兄弟节点」的页脚／导航块分数
   往往比真列表还高；页面导入直接取最高分，于是拿页脚里的下载页当样本详情页，整次导入
   死在「样本详情页取回失败」。现在改成**逐个候选真抓一篇样本详情页**，能解析出正文容器的
   才算数，其余把原因写进 `rejected_reason`。
2. **选择器没收敛到引擎能执行的子集**——分析器会给带 `:nth-of-type(n)` 的选择器，而
   `validate_source_config` 会因此拒绝**整份配置**（一个选择器不可用 = 全盘失败，页面看到的
   就是「不管导入什么站都不通过」）。现在每个组装出来的选择器都过 `_usable_selector`：
   能去掉位置伪类就用去掉后的，去不掉就留空并记进日志。

本脚本不联网：假站点 + 真实编排（SSRF 校验、解析、选候选、试运行判据全是真实实现），
与 `test_source_onboarding.py` 共用同一套假站点设施（从它那里 import，避免两份 fixture 漂掉）。
落库用例需要数据库；不可用时相关断言明确**跳过**（跳过 ≠ 通过）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录的 test_source_onboarding
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import source_import as si  # noqa: E402
from app.sources import net  # noqa: E402
from test_source_onboarding import (  # noqa: E402
    FAILED,
    LIST_PAGE_1,
    PASSED,
    SEARCH_PROBE_KEYWORD,
    SKIPPED,
    build_column_portal_site,
    build_demo_site,
    build_next_only_site,
    build_search_site,
    build_tab_portal_site,
    check,
    install_fake_judge,
    install_fake_network,
    skip,
)

# 假站点的「诱饵」：一个长得像列表、实际是页脚友情链接／客户端下载的块。
# 它条目更多、文本更长，**按分数会排在真列表前面**——正是线上那次故障的形状。
DECOY_ITEMS = "".join(
    f'<li><a href="./download/index.html">国务院客户端与政务小程序下载安装说明（移动端第 {i} 版）</a></li>'
    for i in range(1, 9)
)
DECOY_BLOCK = f"""<div class="footer-links"><ul class="foot-list">{DECOY_ITEMS}</ul></div>"""

# 下载页：200 能取回、也有个像正文的容器，但正文短得不像一篇文件——
# 对应线上真实形状（教育部门户的「查阅全部专栏」入口、gov.cn 的客户端下载页）。
# 靠的是 `_SAMPLE_MIN_CJK` 那条门槛，不是「取回失败」。
DOWNLOAD_SHELL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>客户端下载</title></head>
<body>
<div class="header"><ul class="nav"><li><a href="/">首页</a></li></ul></div>
<div id="zoom"><p>扫描二维码下载客户端并在应用商店中搜索名称安装完成后即可查看政策推送与办件进度提醒本页面仅作下载与安装说明之用如需了解政策原文请访问门户网站的政策文件栏目并可使用站内检索查找</p></div>
</body></html>"""


def build_decoy_site():
    """在标准假站点上加一个「分更高的页脚块」，并把它的样本页做成无正文的下载壳。"""
    site = build_demo_site()
    site.add("/col/index.html", LIST_PAGE_1.replace("</body>", DECOY_BLOCK + "</body>"))
    site.add("/col/download/index.html", DOWNLOAD_SHELL)
    return site


# ---------- 1. 选择器收敛（无网络） ----------


def test_selector_subset() -> None:
    print("\n=== 1. 选择器收敛到引擎可执行的子集 ===")

    dropped: list[str] = []
    check(
        "支持的写法原样保留",
        si._usable_selector("div.detail-content", dropped, "f") == "div.detail-content" and not dropped,
    )

    dropped = []
    got = si._usable_selector("div.detail-title-des > h2:nth-of-type(1)", dropped, "detail.date_selectors")
    check(
        "位置伪类去掉后可用（这正是过去整份配置被拒的那条）",
        got == "div.detail-title-des > h2" and dropped,
        f"-> {got}",
    )

    dropped = []
    check(
        "去不掉的伪类 → 返回 None 并记录，不臆造替代写法",
        si._usable_selector("ul.list > li:first-child", dropped, "f") is None and dropped,
        str(dropped),
    )

    dropped = []
    check(
        "兄弟组合子同样不可用（引擎不支持）",
        si._usable_selector("h2 + p", dropped, "f") is None and dropped,
    )


# ---------- 2. 详情规则组装（无网络） ----------


def test_detail_assembly() -> None:
    print("\n=== 2. 样本详情页分析 → 正文规则 ===")

    analysis = {
        "detail_candidates": [
            {"selector": "div.detail-content", "score": 100.0},
            {"selector": "div.other:nth-of-type(1)", "score": 50.0},
        ],
        "meta_candidates": {
            "title": [{"selector": "title", "score": 1}, {"selector": "h1.article-title", "score": 9}],
            "date": [{"selector": "div.detail-title-des > h2:nth-of-type(1)", "score": 5}],
            "agency": [{"selector": "span.source:nth-of-type(2)", "score": 4}],
            "doc_no": [],
        },
    }
    detail, dropped = si._detail_from_analysis(analysis)

    check(
        "容器候选逐个收敛（含去掉位置伪类的那条）",
        detail["container_selectors"] == ["div.detail-content", "div.other"],
        str(detail["container_selectors"]),
    )
    check(
        "日期选择器不再带伪类（过去这里让整份配置不可执行）",
        detail["date_selectors"] == ["div.detail-title-des > h2"],
        str(detail["date_selectors"]),
    )
    check(
        "整页 <title> 不当正文标题（会被站名后缀污染）",
        detail["title_selectors"] == ["h1.article-title"],
        str(detail["title_selectors"]),
    )
    check("丢弃/改写过的选择器都写进了 dropped（不悄悄消失）", bool(dropped), str(dropped))


# ---------- 3. 地址安全（无网络） ----------


def test_url_guard() -> None:
    print("\n=== 3. 入队前的地址校验 ===")

    for bad in ("http://127.0.0.1:8000/x", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"):
        try:
            si.start_import(si.SourceImportRequest(url=bad))
            check(f"拒绝内部/非公网地址 {bad}", False, "居然建出了作业")
        except si.ImportUnavailable:
            check(f"拒绝内部/非公网地址 {bad}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"拒绝内部/非公网地址 {bad}", False, f"抛了别的异常：{exc}")


# ---------- 4. 端到端：诱饵候选在前，仍要选中真列表 ----------


async def test_pick_real_list() -> None:
    print("\n=== 4. 假站点端到端：分更高的页脚块挡不住真列表 ===")

    site = build_decoy_site()
    install_fake_network(site)
    install_fake_judge()

    url = site.url("/col/index.html")
    request = si.SourceImportRequest(url=url, limit=3, pages=1)
    job = si.ImportJob(job_id="import-test", url=url)
    si.JOBS[job.job_id] = job
    await si.run_import_job(job, request)

    names = [c["name"] for c in job.candidates]
    order = [(c["list_selector"], c.get("item_count")) for c in job.candidates]
    print(f"  候选顺序：{order}")

    check("作业跑完（不是内部异常）", job.status == "done", f"status={job.status} error={job.error}")
    check("配置通过可执行性校验", job.config is not None, str(job.config and job.config.get("entrypoints")))
    check("没有候选因选择器不可用而被整份拒绝", all(c["list_selector"] for c in job.candidates), str(names))

    selected = [c for c in job.candidates if c.get("selected")]
    check("刚好选中一个候选", len(selected) == 1, str(selected))
    if selected:
        chosen = selected[0]
        check(
            "选中的是真正列出条目的那个容器（不是页脚块）",
            "news-list" in chosen["list_selector"],
            chosen["list_selector"],
        )
        check(
            "选中的候选带上了正文规则（说明真抓过样本详情页）",
            bool(chosen.get("detail")),
            str((chosen.get("detail") or {}).get("container_selectors")),
        )

    decoys = [c for c in job.candidates if "foot-list" in str(c["list_selector"])]
    check("页脚块也在候选清单里（不悄悄丢掉）", bool(decoys), str(names))
    check(
        "页脚块带着「试过、为什么不行」的原因",
        bool(decoys) and all(c.get("rejected_reason") for c in decoys),
        str([c.get("rejected_reason") for c in decoys]),
    )
    reason = str(decoys[0].get("rejected_reason") or "") if decoys else ""
    check(
        "原因是「样本不像一篇正文」（短壳页也会被这条门槛挡住）",
        "正文" in reason and "取回失败" not in reason,
        reason,
    )

    report = job.report or {}
    check("试运行给出了验收报告", bool(report.get("criteria")), f"status={report.get('status')}")
    check(
        "真列表 → 验收通过（正文完整性不是靠运气过的）",
        report.get("status") == "passed",
        "；".join(
            f"{c['name']}={c['actual']}" for c in (report.get("criteria") or []) if not c["passed"]
        ),
    )
    check(
        "验收报告四要素齐（未测试项 / 覆盖范围限制也要在）",
        "untested" in report and "coverage_limits" in report and "details" in report,
    )

    if job.registered is None:
        # 未开连接池时 _register_after_dry_run 拿不到来源行 → 登记这一步没跑；如实跳过
        skip("登记结果可读", "数据库不可用（未开连接池），本次只验证到试运行")
    else:
        check(
            "登记结果可读（落库成功或给出可读原因，不抛异常）",
            isinstance(job.registered, dict)
            and (bool(job.registered.get("source_id")) or bool(job.registered.get("error"))),
            str(job.registered)[:160],
        )

    si.JOBS.pop(job.job_id, None)


async def test_rerun_without_base() -> None:
    """上一次在「选候选」阶段就失败（没留下配置骨架）→ 点候选仍要能重跑。

    失败页上的候选清单如果点不动，就等于只给人看、不给人试——这条用例守住的是那个动作。
    """
    print("\n=== 5. 候选重跑：没有 base_config 也要能跑 ===")

    site = build_decoy_site()
    install_fake_network(site)
    install_fake_judge()

    url = site.url("/col/index.html")
    candidate = {
        "index": 0,
        "page_url": url,
        "name": "政策文件",
        "list_selector": "ul.news-list > li",
        "item_count": 4,
        "sample_title": None,
        "sample_url": site.url("/col/art1.html"),
        "pagination": {"kind": "none"},
        "detail": None,  # 没带正文规则 → 服务端要自己抓样本条目补上
    }
    request = si.SourceImportRequest(url=url, limit=2, pages=1, candidate=candidate)
    job = si.ImportJob(job_id="import-rerun-test", url=url)
    si.JOBS[job.job_id] = job
    await si.run_import_job(job, request)

    report = job.report or {}
    check(
        "没有 base_config 也能重跑（骨架由服务端从地址重建）",
        job.status == "done" and report.get("status") == "passed",
        f"status={job.status} report={report.get('status')} error={job.error}",
    )
    check(
        "候选的正文规则是当场抓样本条目定出来的",
        bool((job.candidates[0].get("detail") or {}).get("container_selectors")) if job.candidates else False,
        str(job.candidates[0].get("detail") if job.candidates else None),
    )
    check(
        "重跑不重新勘察（阶段报告里没有 inspect）",
        "inspect" not in job.stage_reports,
        str(list(job.stage_reports)),
    )
    check(
        "清单里就是点的那一条，且标为当前使用",
        len(job.candidates) == 1 and bool(job.candidates[0].get("selected")),
        str([(c.get("index"), c.get("selected")) for c in job.candidates]),
    )
    check(
        "边界仍由服务端从地址推出（不接受页面传白名单）",
        (job.config or {}).get("scope", {}).get("allowed_domains") == ["demo.example.gov.cn"],
        str((job.config or {}).get("scope")),
    )

    si.JOBS.pop(job.job_id, None)


# ---------- 6. 站内检索入口：真出条目才加进配置 ----------


async def _run_import(site, job_id: str, *, limit: int = 3, pages: int = 1):
    install_fake_network(site)
    install_fake_judge()
    url = site.url("/col/index.html")
    request = si.SourceImportRequest(url=url, limit=limit, pages=pages)
    job = si.ImportJob(job_id=job_id, url=url)
    si.JOBS[job.job_id] = job
    await si.run_import_job(job, request)
    return job, url


async def test_search_entrypoint_added() -> None:
    print("\n=== 6. 站内检索入口：GET 表单真出条目才加进配置 ===")

    site = build_search_site()
    job, _ = await _run_import(site, "import-search-test")
    try:
        entrypoints = (job.config or {}).get("entrypoints") or []
        search_eps = [ep for ep in entrypoints if ep.get("search")]
        check(
            "作业跑完且配置通过校验",
            job.status == "done" and job.config is not None,
            f"status={job.status} error={job.error}",
        )
        check(
            "配置里多出一个检索型入口（原栏目入口保留）",
            len(entrypoints) == 2 and len(search_eps) == 1,
            str([(ep.get("name"), bool(ep.get("search"))) for ep in entrypoints]),
        )
        if search_eps:
            ep = search_eps[0]
            check("入口名是「站内检索」", ep.get("name") == "站内检索", str(ep.get("name")))
            check(
                "地址是**实测取过**的那个（不是猜的 /search）",
                ep.get("url") == site.url("/search.html"),
                str(ep.get("url")),
            )
            check(
                "关键词参数名照表单填（keyword）",
                (ep.get("search") or {}).get("keyword_param") == "keyword",
                str(ep.get("search")),
            )
            check(
                "表单里的隐藏参数一并带上（channelid）",
                (ep.get("search") or {}).get("fixed_params") == {"channelid": "36021"},
                str((ep.get("search") or {}).get("fixed_params")),
            )
            check(
                "用的是检索结果页自己的列表结构（栏目页的 ul.news-list 在这页取不到东西）",
                "result-list" in str(ep.get("list_selector")),
                str(ep.get("list_selector")),
            )
            check(
                "备注写明「探测后采纳」，参数名与条数可回溯",
                "探测" in str(ep.get("notes")) and "keyword" in str(ep.get("notes")),
                str(ep.get("notes"))[:80],
            )
        check(
            "日志写清关键词送去了哪个地址",
            any("站内检索入口" in row["message"] for row in job.logs),
            str([row["message"] for row in job.logs][-3:]),
        )

        # 能力声明必须与配置事实一致（前端「筛选在哪一层生效」读的就是它）
        from app.sources.config import SourceConfig

        caps = SourceConfig.model_validate(job.config).capabilities()
        check(
            "能力声明：关键词=服务端、日期=客户端（没配日期参数就不谎报）",
            caps["keyword"] == "server" and caps["date"] == "client",
            str(caps),
        )
        check(
            "能力声明：检索入口名单与配置一致",
            caps["search_entrypoints"] == ["站内检索"],
            str(caps["search_entrypoints"]),
        )

        report = job.report or {}
        names = [row["name"] for row in report.get("criteria") or []]
        check(
            "试运行通过了，且报告里有「站内检索入口」这条判据",
            report.get("status") == "passed" and "站内检索入口" in names,
            f"status={report.get('status')} criteria={names}",
        )
        check(
            "判据 1b 用的是探测词，并把它写进了实测值",
            SEARCH_PROBE_KEYWORD in str(
                next((r.get("actual") for r in report.get("criteria") or [] if r["name"] == "站内检索入口"), "")
            ),
            str(next((r.get("actual") for r in report.get("criteria") or [] if r["name"] == "站内检索入口"), "")),
        )
    finally:
        si.JOBS.pop(job.job_id, None)


# ---------- 7. 接不了的检索框：如实跳过，不硬配 ----------


async def test_search_entrypoint_skipped() -> None:
    print("\n=== 7. 接不了的检索框 / 0 命中的检索页：如实跳过 ===")

    from app.sources.config import SourceConfig

    cases = [
        ("post", "POST", "method='post' 的表单：当前引擎只支持 GET"),
        ("bare", "没有 form 提交地址", "只有输入框、地址由 JS 拼：不猜 /search"),
        ("empty", "都没取回条目", "检索页取回了但解析不出条目：不采纳"),
    ]
    for index, (variant, needle, why) in enumerate(cases):
        kwargs = {"result": "empty"} if variant == "empty" else {"method": variant}
        site = build_search_site(**kwargs)
        job, _ = await _run_import(site, f"import-search-skip-{variant}-{index}")
        try:
            entrypoints = (job.config or {}).get("entrypoints") or []
            check(
                f"[{variant}] 不加检索入口（{why}）",
                job.status == "done"
                and job.config is not None
                and not any(ep.get("search") for ep in entrypoints),
                str([(ep.get("name"), bool(ep.get("search"))) for ep in entrypoints]) or job.error,
            )
            check(
                f"[{variant}] 日志写清为什么没加（「没试」与「试了不行」分得清）",
                any(needle in row["message"] for row in job.logs),
                str([row["message"] for row in job.logs][-2:]),
            )
            if job.config is not None:
                caps = SourceConfig.model_validate(job.config).capabilities()
                check(
                    f"[{variant}] 能力声明如实说关键词只能在客户端比（召回受翻页深度限制）",
                    caps["keyword"] == "client_scan" and caps["search_entrypoints"] == [],
                    str(caps),
                )
        finally:
            si.JOBS.pop(job.job_id, None)


# ---------- 8. 命名区域：同页多列表 → 多入口草案 ----------


async def test_named_regions_import() -> None:
    """tab 门户页（三个同签名面板）→ 导入草案含三个命名区域入口，验收逐入口探第 1 页。

    这是「只抓政策解读 / 同时抓政府规章+市政府文件」的确定性通路：每个命名区域各成一个
    入口（名字 = 栏目名），抓取页现成的「发布机构 = 入口名」筛选直接可用；试运行判据 1
    逐入口真取第 1 页，任何一个区域解析不出条目都过不了验收。
    """
    print("\n=== 8. 命名区域：同页多列表 → 多入口 ===")

    site = build_tab_portal_site()
    install_fake_network(site)
    install_fake_judge()
    url = site.url("/zwgk/index.html")
    request = si.SourceImportRequest(url=url, limit=3, pages=1)
    job = si.ImportJob(job_id="import-regions-test", url=url)
    si.JOBS[job.job_id] = job
    await si.run_import_job(job, request)
    try:
        entrypoints = (job.config or {}).get("entrypoints") or []
        named = [ep for ep in entrypoints if ep.get("scope_selector")]
        check(
            "作业跑完且配置通过校验",
            job.status == "done" and job.config is not None,
            f"status={job.status} error={job.error}",
        )
        check(
            "三个命名区域各成一个入口（没有被合并成一个）",
            sorted(ep.get("name") for ep in named) == ["市政府文件", "政府规章", "政策解读"],
            str([(ep.get("name"), ep.get("scope_index")) for ep in entrypoints]),
        )
        zcjd = next((ep for ep in named if ep.get("name") == "政策解读"), {})
        check(
            "「政策解读」入口用 scope_index=2 定位到第 3 个面板",
            zcjd.get("scope_index") == 2 and zcjd.get("list_selector") == "li",
            str(zcjd),
        )
        check(
            "三个入口同一页地址、不同序号，互不合并",
            len({(ep.get("url"), ep.get("scope_index")) for ep in named}) == 3
            and len({ep.get("url") for ep in named}) == 1,
            str([(ep.get("url"), ep.get("scope_index")) for ep in named]),
        )

        region_candidates = [c for c in job.candidates if c.get("kind") == "region"]
        check(
            "候选清单里区域候选都带名字与样本（可切换重跑）",
            len(region_candidates) >= 3
            and all(c.get("name") and c.get("sample_url") for c in region_candidates),
            str([(c.get("name"), c.get("rejected_reason")) for c in region_candidates]),
        )
        check(
            "入选的区域都标了「当前使用」",
            sum(1 for c in region_candidates if c.get("selected")) == 3,
            str([(c.get("name"), c.get("selected")) for c in region_candidates]),
        )

        report = job.report or {}
        per_entry = {row.get("name"): row.get("items") for row in report.get("entrypoints") or []}
        check(
            "试运行通过",
            report.get("status") == "passed",
            "；".join(f"{c['name']}={c['actual']}" for c in (report.get("criteria") or []) if not c["passed"]),
        )
        check(
            "判据 1 逐入口探第 1 页：三个区域都真实取到条目（各 3 条）",
            all(per_entry.get(name) == 3 for name in ("政府规章", "市政府文件", "政策解读")),
            str(per_entry),
        )
    finally:
        si.JOBS.pop(job.job_id, None)


# ---------- 9. 重跑的替换范围：区域只换同位置那个，普通候选才整体重建 ----------


async def test_rerun_region_preserves_siblings() -> None:
    """点某个命名区域重跑 = 只纠错那一个入口，其它已接入栏目不许被静默丢掉。

    旧语义是无条件整份重建——在多入口（Phase 17）落地后，这意味着用户一次误点就会把
    已接入的其它命名区域全部弄丢。收窄后：区域候选只替换「同页 + 同 scope_selector」
    的入口；普通列表候选维持原语义（整份重建，保留站内检索）。
    """
    print("\n=== 9. 重跑替换范围：区域保兄弟，普通候选才重建 ===")

    site = build_tab_portal_site()
    install_fake_network(site)
    install_fake_judge()
    url = site.url("/zwgk/index.html")

    # 第一次导入：三个命名区域全部接入
    request = si.SourceImportRequest(url=url, limit=3, pages=1)
    job = si.ImportJob(job_id="import-rerun-scope-1", url=url)
    si.JOBS[job.job_id] = job
    await si.run_import_job(job, request)
    check("前置：首次导入接入 3 个命名区域", job.status == "done" and job.config is not None, job.error)
    base_config = job.config

    # 区域重跑：拿「政府规章」那个区域候选再跑一遍
    region_candidate = next(
        (dict(c) for c in (job.candidates or []) if c.get("kind") == "region" and c.get("name") == "政府规章"),
        None,
    )
    check("前置：清单里能找到政府规章区域候选", region_candidate is not None)
    rerun = si.SourceImportRequest(
        url=url, limit=2, pages=1, candidate=region_candidate, base_config=base_config
    )
    job2 = si.ImportJob(job_id="import-rerun-scope-2", url=url)
    si.JOBS[job2.job_id] = job2
    await si.run_import_job(job2, rerun)
    entrypoints = (job2.config or {}).get("entrypoints") or []
    names = sorted(ep.get("name") for ep in entrypoints if ep.get("scope_selector"))
    check(
        "区域重跑：其它命名区域全部保留（仍是 3 个栏目）",
        job2.status == "done" and names == ["市政府文件", "政府规章", "政策解读"],
        f"status={job2.status} names={names} error={job2.error}",
    )
    check(
        "区域重跑：日志写明替换几个、保留几个",
        any("保留其它命名区域" in row["message"] for row in job2.logs),
        str([row["message"] for row in job2.logs][-4:]),
    )

    # 普通列表候选重跑：维持原语义——整份以它为准重建
    plain_candidate = next(
        (dict(c) for c in (job.candidates or []) if c.get("kind") == "list" and "common_newslist" in str(c.get("list_selector"))),
        None,
    )
    check("前置：清单里能找到普通合并列表候选", plain_candidate is not None)
    rerun2 = si.SourceImportRequest(
        url=url, limit=2, pages=1, candidate=plain_candidate, base_config=base_config
    )
    job3 = si.ImportJob(job_id="import-rerun-scope-3", url=url)
    si.JOBS[job3.job_id] = job3
    await si.run_import_job(job3, rerun2)
    entrypoints3 = (job3.config or {}).get("entrypoints") or []
    scoped3 = [ep for ep in entrypoints3 if ep.get("scope_selector")]
    check(
        "普通候选重跑：整份重建为单个合并入口（旧语义，不带 scope）",
        job3.status == "done" and len(entrypoints3) == 1 and not scoped3,
        f"status={job3.status} entrypoints={[(ep.get('name'), ep.get('scope_selector')) for ep in entrypoints3]}",
    )

    for leftover in ("import-rerun-scope-1", "import-rerun-scope-2", "import-rerun-scope-3"):
        si.JOBS.pop(leftover, None)


# ---------- 10. 命名区域改接独立栏目页：召回不被页内预览块封顶 ----------


async def test_region_prefers_column_page() -> None:
    """tab 面板只是门户页上的预览块；tab 自己链接到的栏目页才是完整列表。

    线上形状（2026-10-16）：门户 tab 区域接入后「抓到 9 篇就不抓了」——页内预览块列几条
    就是几条，而且没有翻页。本用例考的是**通用**规则（不针对任何站点）：区域候选若自带
    独立栏目页，就真取一页验证后**就地**改接过去；栏目页取不回时保留页内区域并写明原因。

    这个 fixture 里被选中的是**页内区域**那个候选，改接的是**没被选中**的那个——展开同页
    命名区域时必须把改接过的区域照样接成入口，不能因为在门户页上找不到它的 scope 就丢掉。
    """
    print("\n=== 10. 命名区域改接独立栏目页（页内预览块只列几条） ===")

    site = build_column_portal_site()
    install_fake_network(site)
    install_fake_judge()
    url = site.url("/zwgk/index.html")
    request = si.SourceImportRequest(url=url, limit=3, pages=1)
    job = si.ImportJob(job_id="import-column-page-test", url=url)
    si.JOBS[job.job_id] = job
    await si.run_import_job(job, request)
    try:
        entrypoints = (job.config or {}).get("entrypoints") or []
        check(
            "作业跑完且配置通过校验",
            job.status == "done" and job.config is not None,
            f"status={job.status} error={job.error}",
        )
        by_name = {ep.get("name"): ep for ep in entrypoints}
        check(
            "三个命名区域各成一个入口（改接过栏目页的那个不许在展开时被丢掉）",
            set(by_name) == {"政府规章", "政策文件", "政策解读"},
            str([(ep.get("name"), ep.get("url"), ep.get("scope_selector")) for ep in entrypoints]),
        )

        column = by_name.get("政策文件") or {}
        check(
            "有独立栏目页的区域：入口地址改成栏目页（不再是门户页）",
            column.get("url") == site.url("/col/index.html"),
            str(column.get("url")),
        )
        check(
            "改接后不再需要 scope 定位（解析的是栏目页，不是门户页上的那一个模块）",
            not column.get("scope_selector") and column.get("scope_index") is None,
            f"scope={column.get('scope_selector')} index={column.get('scope_index')}",
        )
        check(
            "用的是**栏目页自己的**列表结构（页内预览块的选择器在栏目页上取不到东西）",
            "news-list" in str(column.get("list_selector")),
            str(column.get("list_selector")),
        )
        check(
            "栏目页自己的翻页规则一并带上（召回不再被预览块的长度封顶）",
            (column.get("pagination") or {}).get("kind") == "path_index"
            and (column.get("pagination") or {}).get("path_template") == "/col/index_{page}.html",
            str(column.get("pagination")),
        )
        check(
            "翻页上限与组装阶段同一口径（不是探测阶段自己拍一个）",
            (column.get("pagination") or {}).get("max_pages") == si._PAGINATION_MAX_PAGES,
            f"{column.get('pagination')} vs _PAGINATION_MAX_PAGES={si._PAGINATION_MAX_PAGES}",
        )

        others = [(by_name.get(n) or {}) for n in ("政府规章", "政策解读")]
        check(
            "栏目页取不回的两个：保留页内区域（地址仍是门户页 + 带 scope 定位）",
            all(ep.get("url") == url and ep.get("scope_selector") for ep in others),
            str([(ep.get("url"), ep.get("scope_selector")) for ep in others]),
        )
        check(
            "「试了不行」与「没试」分得清：日志写明栏目页取回失败",
            any("栏目页取回失败" in row["message"] for row in job.logs),
            str([row["message"] for row in job.logs if "栏目页" in row["message"]][:2]),
        )
        check(
            "改接这件事本身写进日志（人能看懂地址为什么变了）",
            any("改用其独立栏目页" in row["message"] for row in job.logs),
            str([row["message"] for row in job.logs if "栏目页" in row["message"]][:2]),
        )

        upgraded = [c for c in job.candidates if c.get("column_page")]
        check(
            "候选也一并改接（点它重跑时照栏目页跑，不会退回预览块）",
            len(upgraded) == 1
            and upgraded[0].get("page_url") == site.url("/col/index.html")
            and not upgraded[0].get("scope_selector"),
            str([(c.get("name"), c.get("page_url"), bool(c.get("column_page"))) for c in job.candidates if c.get("kind") == "region"]),
        )

        report = job.report or {}
        criteria = {row["name"]: row for row in report.get("criteria") or []}
        check(
            "试运行通过（含逐入口探第 1 页）",
            report.get("status") == "passed",
            "；".join(f"{c['name']}={c['actual']}" for c in (report.get("criteria") or []) if not c["passed"]),
        )
        check(
            "有入口声明了翻页 → 判据 5 真的跑了，且报出第 2 页的新增量",
            criteria.get("翻页可用性", {}).get("passed") is True
            and "政策文件" in str(criteria.get("翻页可用性", {}).get("actual")),
            str(criteria.get("翻页可用性", {}).get("actual")),
        )
    finally:
        si.JOBS.pop(job.job_id, None)


# ---------- 11. 单页声明不能被静默接受：推不出翻页规律就如实说截断 ----------


async def test_single_page_must_be_evidenced() -> None:
    """分页条只有「下一页」链接时，配置仍只能是单页——但**这件事必须被说出来**。

    这是「抓到 N 篇就不动了」的另一半：页面上明明写着还有下一页，只是地址规律反推不出来
    （页码由 JS 拼参数）。此时绝不能把「单页」当结论静默落库：入口 notes、日志、验收报告的
    覆盖范围三处都要看得见「召回被截断在第 1 页，请人工确认翻页规律」。
    """
    print("\n=== 11. 单页声明必须有页面证据，推不出规律就说清截断 ===")

    site = build_next_only_site()
    install_fake_network(site)
    install_fake_judge()
    url = site.url("/col/index.html")
    request = si.SourceImportRequest(url=url, limit=3, pages=1)
    job = si.ImportJob(job_id="import-next-only-test", url=url)
    si.JOBS[job.job_id] = job
    await si.run_import_job(job, request)
    try:
        check(
            "作业跑完（截断不等于失败：站点有内容，只是取不到第 2 页）",
            job.status == "done" and job.config is not None,
            f"status={job.status} error={job.error}",
        )
        entrypoints = (job.config or {}).get("entrypoints") or []
        check("只接一个栏目入口，仍是单页", len(entrypoints) == 1, str(len(entrypoints)))
        entry = entrypoints[0] if entrypoints else {}
        check(
            "翻页规则没有臆造（保持 kind=none，不塞一个会被站点忽略的 ?page=2）",
            (entry.get("pagination") or {}).get("kind") == "none",
            str(entry.get("pagination")),
        )
        check(
            "入口备注写清「召回被截断在第 1 页」以及人工要补什么",
            "截断" in str(entry.get("notes")) and "pagination" in str(entry.get("notes")),
            str(entry.get("notes")),
        )
        check(
            "日志也点了这个入口（不是只写进配置没人看得见）",
            any("截断" in row["message"] for row in job.logs),
            str([row["message"] for row in job.logs][-3:]),
        )

        report = job.report or {}
        limits = report.get("coverage_limits") or []
        check(
            "报告覆盖范围里带上该入口的备注（验收报告自己说清没覆盖到哪）",
            any("入口「" in row and "备注" in row and "截断" in row for row in limits),
            str([row for row in limits if "备注" in row]),
        )
        check(
            "报告明写「有 N 个入口声明为单页、只取第 1 页」（不再让用户猜是站点没内容还是没翻页）",
            any("个入口声明为单页" in row for row in limits),
            str([row for row in limits if "单页" in row]),
        )
        criteria = {row["name"]: row for row in report.get("criteria") or []}
        check(
            "没有入口声明翻页 → 判据 5 如实记为「未测试」，不冒充通过",
            "翻页可用性" not in criteria
            and any("翻页" in item for item in report.get("untested") or []),
            f"criteria={sorted(criteria)} untested={report.get('untested')}",
        )
        check(
            "截断不改判定：状态仍是 passed（站点可达、正文可取）",
            report.get("status") == "passed",
            str(report.get("status")),
        )
    finally:
        si.JOBS.pop(job.job_id, None)


# ---------- 12. 重跑是「替换」不是「清除」：跨页栏目不许被丢掉 ----------


async def test_rerun_keeps_other_page_entries() -> None:
    """「这几个栏目我都要」的通路：重跑只替换它自己那一个位置，别处的栏目一律留着。

    命名区域可能分布在**两张页面**上（门户首页的并列模块 + 它链接到的栏目页）。早先的保留
    条件多挂了一条「同页」，于是点另一页的区域候选会把这一页的栏目丢掉——跨页的栏目永远
    共处不了，点一个丢一个。这里直接调 `_rebuild_from_candidate`（要考的就是这一步的替换
    范围，不必跑整条作业与真实验收），断言「别处的栏目与检索入口都还在」。
    """
    print("\n=== 12. 重跑只替换同位置，跨页栏目保留 ===")

    site = build_tab_portal_site()
    install_fake_network(site)
    domains = ["demo.example.gov.cn"]
    detail = {
        "container_selectors": ["div#zoom"],
        "title_selectors": ["h1"],
        "date_selectors": ["span.pubdate"],
    }
    base = {
        "key": "demo_portal",
        "label": "演示门户",
        "engine": "html_list",
        "scope": {"allowed_domains": domains},
        "entrypoints": [
            {
                "name": "政策文件",
                "url": site.url("/col/index.html"),
                "list_selector": "ul.news-list > li",
                "link_selector": "a",
                "pagination": {"kind": "none"},
            },
            {
                "name": "政府规章",
                "url": site.url("/zwgk/index.html"),
                "list_selector": "li",
                "link_selector": "a",
                "scope_selector": "div.tab-panel",
                "scope_index": 0,
                "pagination": {"kind": "none"},
            },
            {
                "name": "站内检索",
                "url": site.url("/search.html"),
                "list_selector": "ul.result-list > li",
                "link_selector": "a",
                "pagination": {"kind": "none"},
                "search": {"keyword_param": "keyword"},
            },
        ],
        "detail": detail,
    }

    # (1) 另一张页面上的区域候选：只该换掉它自己，另外两个栏目都得留着
    cross_page = {
        "page_url": site.url("/zwgk/other/index.html"),
        "list_selector": "li",
        "scope_selector": "div.tab-panel",
        "scope_index": 1,
        "name": "其它栏目",
        "detail": detail,
    }
    job = si.ImportJob(job_id="import-rerun-cross-page", url=site.url("/zwgk/index.html"))
    request = si.SourceImportRequest(
        url=site.url("/zwgk/index.html"), candidate=cross_page, base_config=base
    )
    payload, _candidates, error = await si._rebuild_from_candidate(job, request, domains)
    names = sorted(ep.get("name") for ep in (payload or {}).get("entrypoints") or [])
    check(
        "跨页区域候选：换上去的是它自己，另一页的栏目与检索入口原样保留",
        error is None and set(names) == {"其它栏目", "政策文件", "政府规章", "站内检索"},
        f"error={error} names={names}",
    )

    # (2) 已改接独立栏目页的区域（scope 被清空、只剩 column_page）：同样是「一个栏目」
    upgraded = {
        "page_url": site.url("/zwgk/index.html"),
        "list_selector": "li",
        "scope_selector": None,
        "scope_index": None,
        "column_page": True,
        "region_page_url": site.url("/zwgk/index.html"),
        "name": "政府规章",
        "detail": detail,
    }
    job2 = si.ImportJob(job_id="import-rerun-column-page", url=site.url("/zwgk/index.html"))
    request2 = si.SourceImportRequest(
        url=site.url("/zwgk/index.html"), candidate=upgraded, base_config=base
    )
    payload2, _c2, error2 = await si._rebuild_from_candidate(job2, request2, domains)
    names2 = sorted(ep.get("name") for ep in (payload2 or {}).get("entrypoints") or [])
    check(
        "改接过栏目页的区域：只替换同位置那一个，别处的栏目不被整份重建清掉",
        error2 is None and set(names2) == {"政策文件", "政府规章", "站内检索"},
        f"error={error2} names={names2}",
    )

    for leftover in ("import-rerun-cross-page", "import-rerun-column-page"):
        si.JOBS.pop(leftover, None)


# ---------- 13. 候选名额只管普通列表：命名区域不许被静默挤掉 ----------


def test_region_candidates_not_capped() -> None:
    """名额（`_MAX_CANDIDATES = 5`）是给「给人挑」的普通列表候选准备的。

    命名区域是「同页全都要」：被名额挤掉的那一个**连候选都不出现**，于是永远不会被展开
    ——静默少一个栏目，页面上一点痕迹都没有。区域自己的上限由分析器管（每页 8 个）。
    """
    print("\n=== 13. 候选名额：区域全留，普通列表候选才截断 ===")

    page_url = "https://demo.example.gov.cn/zwgk/index.html"
    regions = [
        {
            "name": f"栏目{i + 1}",
            "scope_selector": "div.panel",
            "scope_index": i,
            "list_selector": "li",
            "item_count": 3,
            "sample_title": f"标题{i + 1}",
            "sample_url": f"/zwgk/art{i + 1}.html",
        }
        for i in range(7)
    ]
    rows = [
        {
            "name_suggestion": f"列表{i + 1}",
            "page_url": page_url,
            "list_selector": f"ul.list{i + 1} > li",
            "item_count": 5,
            "sample_title": "样本",
            "sample_url": f"/zwgk/x{i + 1}.html",
            "pagination": {"kind": "none"},
        }
        for i in range(7)
    ]
    out = si._build_candidates(
        {"pages": [{"url": page_url, "regions": regions}], "entrypoint_candidates": rows}
    )
    region_out = [c for c in out if c.get("kind") == "region"]
    list_out = [c for c in out if c.get("kind") == "list"]
    check(
        "7 个命名区域一个不少（不再被 5 个名额截断）",
        len(region_out) == 7 and [c.get("scope_index") for c in region_out] == list(range(7)),
        str(len(region_out)),
    )
    check(
        "普通列表候选仍按 _MAX_CANDIDATES 截断（名额是给它准备的）",
        len(list_out) == si._MAX_CANDIDATES,
        str(len(list_out)),
    )
    check(
        "候选 index 与清单顺序一致（前端与重跑都靠它定位）",
        [c.get("index") for c in out] == list(range(len(out))),
        str([c.get("index") for c in out]),
    )


# ---------- 14. 语义字段规则：三种「标签 → 值」形状 + 规则链 ----------


def _huizhou_like_detail(
    *,
    publish: str = "2026年九月八日",
    signed: str = "二〇二六年九月一日",
    footer: str = "2024-03-20",
) -> str:
    """模拟地方政务站详情页：三种标签/值形状各出现一次。

    - 标签在 `<span>`、值在父节点的直接文本里（`parent_own_text`）；
    - `<dt>`/`<dd>` 兄弟节点（`next_sibling_text`）；
    - 「标签：值」同节点（`self_text` + `drop_label`）。
    页脚再放一个固定日期——它是接入时最常见的误判来源，`_verify_field_rules` 要能识别。
    """
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>关于印发《惠州市既有住宅增设电梯管理办法》的通知_惠州市人民政府门户网站</title></head>
<body>
<div class="header"><a href="/">首页</a><a href="/zwgk/">政务公开</a></div>
<div class="detail-title-des"><h2>关于印发《惠州市既有住宅增设电梯管理办法》的通知</h2></div>
<div class="article-info">
  <div class="col-6"><span class="fw-bold">文　　号：</span> 惠府〔2025〕13号</div>
  <div class="col-6"><span class="fw-bold">发布机构：</span> 惠州市人民政府办公室</div>
  <div class="col-6"><span class="fw-bold">发布日期：</span> {publish}</div>
  <div class="col-6"><span class="fw-bold">责任处室：</span> 市司法局</div>
</div>
<dl class="meta">
  <dt>成文日期</dt><dd>{signed}</dd>
  <dt>索引号</dt><dd>007208-01-2026-000123</dd>
</dl>
<div class="label-line">责任处室：市司法局</div>
<div class="doccontent"><p>为规范既有住宅增设电梯工作，结合本市实际，制定本办法。</p></div>
<div class="footer">本站维护日期：{footer}</div>
</body></html>"""


def test_field_rules() -> None:
    print("\n=== 14. 语义字段规则：三种标签/值形状 + 规则链 + 日期归一 ===")

    from app.sources.config import DetailConfig, FieldRule
    from app.sources.field_rules import extract_field, extract_fields
    from app.sources.html_source import parse_detail_page
    from app.sources.htmlparse import parse_html

    root = parse_html(_huizhou_like_detail())

    doc_no = FieldRule(selector="span.fw-bold", label_equals="文 号", value_from="parent_own_text")
    agency = FieldRule(selector="span.fw-bold", label_equals="发布机构", value_from="parent_own_text")
    publish = FieldRule(
        selector="span.fw-bold",
        label_equals="发布日期",
        value_from="parent_own_text",
        normalize="date",
    )
    signed = FieldRule(
        selector="dt", label_equals="成文日期", value_from="next_sibling_text", normalize="date"
    )
    index_no = FieldRule(selector="dt", label_equals="索引号", value_from="next_sibling_text")
    owner = FieldRule(
        selector="div.label-line",
        label_equals="责任处室",
        value_from="self_text",
        drop_label=True,
    )

    check(
        "标签在 <span>、值在父节点直接文本（全角空格/冒号归一后仍能对上）",
        extract_field(root, [doc_no]) == "惠府〔2025〕13号",
        str(extract_field(root, [doc_no])),
    )
    check(
        "标签与值分居 dt/dd 兄弟节点",
        extract_field(root, [index_no]) == "007208-01-2026-000123",
        str(extract_field(root, [index_no])),
    )
    check(
        "同节点「标签：值」——drop_label 后只剩值",
        extract_field(root, [owner]) == "市司法局",
        str(extract_field(root, [owner])),
    )
    check(
        "中文数字日期归一成 ISO",
        extract_field(root, [signed]) == "2026-09-01",
        str(extract_field(root, [signed])),
    )
    check(
        "规则链：第一条命中就赢（发布日期优先于成文日期）",
        extract_field(root, [publish, signed]) == "2026-09-08",
        str(extract_field(root, [publish, signed])),
    )

    fallback_root = parse_html(_huizhou_like_detail(publish="2026.9.8").replace(
        '<div class="col-6"><span class="fw-bold">发布日期：</span> 2026.9.8</div>', ""
    ))
    check(
        "规则链 fallback：页面没有发布日期时落到成文日期",
        extract_field(fallback_root, [publish, signed]) == "2026-09-01",
        str(extract_field(fallback_root, [publish, signed])),
    )

    fields = {
        "title": [FieldRule(selector="div.detail-title-des > h2")],
        "agency": [agency],
        "doc_no": [doc_no],
        "date": [publish, signed],
        "索引号": [index_no],
        "责任处室": [owner],
    }
    parsed = parse_detail_page(
        _huizhou_like_detail(),
        DetailConfig(container_selectors=["div.doccontent"], fields=fields),
        "https://hz.example.gov.cn/art/2026/9/8/art_1.html",
    )
    check(
        "详情页：保留字段按规则写回（标题/机构/文号）",
        parsed.title == "关于印发《惠州市既有住宅增设电梯管理办法》的通知"
        and parsed.agency == "惠州市人民政府办公室"
        and parsed.doc_no == "惠府〔2025〕13号",
        f"title={parsed.title!r} agency={parsed.agency!r} doc_no={parsed.doc_no!r}",
    )
    check(
        "详情页：日期是规则归一后的 ISO（没有配 date_selectors 也照样取到）",
        parsed.date_text == "2026-09-08",
        str(parsed.date_text),
    )
    check(
        "详情页：非保留字段进 extra（索引号/责任处室），正文本身不受影响",
        parsed.extra == {"索引号": "007208-01-2026-000123", "责任处室": "市司法局"}
        and "既有住宅" in parsed.body,
        f"extra={parsed.extra} body={parsed.body[:40]!r}",
    )

    # 值里的正文句子不能被规则当成值：pattern 不命中就换下一条规则
    check(
        "pattern 不命中 → 该候选无效（返回 None 而不是把整段文本当值）",
        extract_field(root, [FieldRule(selector="div.doccontent", pattern=r"(\d{10})")]) is None,
    )


# ---------- 15. 分析器 → 规则草案：自动落地三种形状，恒定日期被识破 ----------


def test_field_rule_generation() -> None:
    print("\n=== 15. 结构分析 → 规则草案：形状自动识别 + 恒定日期核对 ===")

    from app.sources import analyze
    from app.sources.config import FieldRule
    from app.sources.field_rules import extract_fields, rules_from_field_candidates
    from app.sources.htmlparse import parse_html

    root = parse_html(_huizhou_like_detail())
    candidates = analyze.field_candidates(root)
    by_label = {c["label"]: c for c in candidates}
    relations: dict[str, set[str]] = {}
    for candidate in candidates:
        relations.setdefault(candidate["label"], set()).add(candidate["relation"])

    check(
        "分析器认出「标签在子节点」（文号/发布机构/发布日期 都在）",
        {"文号", "发布机构", "发布日期"} <= set(by_label),
        str(sorted(by_label)),
    )
    check(
        "文号的取值位置是 parent_own_text（值在父节点直接文本里）",
        by_label.get("文号", {}).get("value_from") == "parent_own_text",
        str(by_label.get("文号")),
    )
    check(
        "无冒号的 dt/dd 也能认出（成文日期/索引号）",
        by_label.get("成文日期", {}).get("value_from") == "next_sibling_text"
        and by_label.get("索引号", {}).get("value_from") == "next_sibling_text",
        str({k: by_label.get(k) for k in ("成文日期", "索引号")}),
    )
    check(
        "站点自定义字段在两种写法下都能识别（责任处室：label_child + inline）",
        {"label_child", "inline"} <= relations.get("责任处室", set()),
        str(relations.get("责任处室")),
    )

    fields = rules_from_field_candidates(candidates)
    check(
        "保留字段排在最前（date/agency/doc_no），且 publish_date 统一成 date",
        list(fields)[:3] == ["date", "agency", "doc_no"] and "publish_date" not in fields,
        str(list(fields)),
    )
    check(
        "日期规则带 normalize=date（2026年九月八日 → ISO）",
        fields["date"][0].normalize == "date",
        str(fields["date"][0].model_dump()),
    )
    values = extract_fields(root, fields)
    check(
        "自动生成的规则能原样跑出值（含 metadata 字段）",
        values.get("date") == "2026-09-08"
        and values.get("agency") == "惠州市人民政府办公室"
        and values.get("doc_no") == "惠府〔2025〕13号"
        and values.get("index_no") == "007208-01-2026-000123",
        str(values),
    )

    # 确定性的接入路径把同一批候选组装成 DetailConfig 草案
    analysis = {
        "detail_candidates": [{"selector": "div.doccontent", "score": 100.0}],
        "field_candidates": candidates,
    }
    detail, dropped = si._detail_from_analysis(analysis)
    fields_draft = detail.get("fields") or {}
    check(
        "接入草案里带上了 fields（不再是只有标题/日期选择器）",
        bool(fields_draft),
        str(list(fields_draft)),
    )
    check(
        "草案里每条规则都写成可序列化的 dict（能直接落进 config JSON）",
        all(isinstance(raw, dict) for rules in fields_draft.values() for raw in rules),
        str(fields_draft.get("date")),
    )

    # 跨样本核对：发布日期三篇各不相同 → 保留；页脚固定日期 → 丢弃
    pages = [
        _huizhou_like_detail(publish=value, footer="2024-03-20")
        for value in ("2026年九月八日", "2026年九月九日", "2026年九月十日")
    ]
    check(
        "前置：草案里的日期链同时有「发布日期」与页脚固定日期（这正是需要跨页核对的形状）",
        {raw.get("label_equals") for raw in fields_draft.get("date") or []}
        == {"发布日期", "本站维护日期"},
        str(fields_draft.get("date")),
    )
    kept, notes = si._verify_field_rules(pages, {"date": fields_draft["date"]})
    kept_labels = [raw.get("label_equals") for raw in kept.get("date") or []]
    check(
        "恒定日期（页脚维护日期）被丢弃，真实发布日期保留",
        kept_labels == ["发布日期"] and any("恒为" in note for note in notes),
        f"kept={kept_labels} notes={notes}",
    )

    # 丢弃后仍能执行：规则链里不能留下「恒为页脚日期」的第一条
    revalidated = {name: [FieldRule.model_validate(raw) for raw in rules] for name, rules in kept.items()}
    check(
        "核对后的规则仍取到各篇自己的日期（不是页脚的 2024-03-20）",
        extract_fields(parse_html(pages[1]), revalidated).get("date") == "2026-09-09",
        str(extract_fields(parse_html(pages[1]), revalidated)),
    )


async def test_render_config() -> None:
    print("\n=== 16. JS 渲染：配置合并 / 请求拦截口径 / 服务端开关 ===")

    from app.config import get_settings
    from app.sources import render
    from app.sources.config import RenderConfig, SourceConfig, validate_source_config

    base = {
        "key": "demo_render",
        "label": "渲染测试来源",
        "scope": {"allowed_domains": ["demo.example.gov.cn"]},
        "entrypoints": [
            {
                "name": "栏目",
                "url": "https://demo.example.gov.cn/col/index.html",
                "list_selector": "ul.list li",
                "render": {"enabled": True, "wait_for_selector": ".ready"},
            }
        ],
        "detail": {
            "container_selectors": ["div.content"],
            "render": {"scroll": True},
        },
        # 站点级默认值：入口/详情只覆盖各自写出的字段
        "render": {"enabled": False, "wait_until": "load", "timeout_ms": 30000},
    }
    config = SourceConfig.model_validate(base)

    ep_render = config.render_for_entrypoint(config.entrypoints[0])
    check(
        "入口覆盖只改写出的字段（enabled/select 来自入口，wait_until/timeout 继承站点）",
        ep_render.enabled
        and ep_render.wait_for_selector == ".ready"
        and ep_render.wait_until == "load"
        and ep_render.timeout_ms == 30000,
        str(ep_render.model_dump()),
    )
    detail_render = config.render_for_detail()
    check(
        "详情覆盖同理（scroll=true，enabled 继承站点默认 false）",
        detail_render.scroll and not detail_render.enabled,
        str(detail_render.model_dump()),
    )
    options = ep_render.to_options()
    check(
        "RenderOptions 与配置一一对应",
        options.enabled and options.wait_until == "load" and options.wait_for_selector == ".ready",
        str(options),
    )

    check(
        "渲染的 wait_for_selector 不受 mini-DOM 子集限制（Playwright 支持伪类）",
        RenderConfig.model_validate({"wait_for_selector": "li:nth-of-type(2)"}).wait_for_selector
        == "li:nth-of-type(2)",
        "",
    )

    def blocked(method: str, resource_type: str, url: str) -> str | None:
        return render.blocked_reason(
            method=method,
            resource_type=resource_type,
            url=url,
            allowed_domains=["demo.example.gov.cn"],
            block_resources=True,
        )

    check(
        "白名单内的 GET 文档放行，白名单外的子资源拦截（子请求也守来源边界）",
        blocked("GET", "document", "https://demo.example.gov.cn/a.html") is None
        and blocked("GET", "script", "https://cdn.other.com/app.js") is not None,
        "",
    )
    check(
        "图片/字体/媒体默认拦截（block_resources=true）",
        all(blocked("GET", kind, "https://demo.example.gov.cn/x") is not None
            for kind in ("image", "font", "media")),
        "",
    )
    check(
        "非只读请求拦截（渲染过程不产生写副作用）",
        blocked("POST", "fetch", "https://demo.example.gov.cn/api") is not None,
        "",
    )

    fingerprint_a = SourceConfig.model_validate(base).config_fingerprint()
    fingerprint_b = SourceConfig.model_validate(
        {**base, "render": {"enabled": False, "wait_until": "networkidle", "timeout_ms": 30000}}
    ).config_fingerprint()
    check(
        "渲染参数计入配置指纹（改了必须重新验收）",
        fingerprint_a != fingerprint_b,
        f"{fingerprint_a} / {fingerprint_b}",
    )

    settings = get_settings()
    was_enabled = settings.source_render_enabled
    try:
        settings.source_render_enabled = False
        problems = validate_source_config(config)
        check(
            "配置开了渲染但服务端开关没开 → 注册校验拒绝",
            any("SOURCE_RENDER_ENABLED" in item for item in problems),
            str(problems),
        )
        try:
            await net.fetch_text(
                "https://demo.example.gov.cn/a.html",
                allowed_domains=["demo.example.gov.cn"],
                render=ep_render.to_options(),
            )
        except render.RenderUnavailable:
            check(
                "开关关闭时 fetch_text 明确报 RenderUnavailable（不静默退回原始 HTML）",
                True,
                "",
            )
        else:
            check(
                "开关关闭时 fetch_text 明确报 RenderUnavailable（不静默退回原始 HTML）",
                False,
                "没有抛错",
            )

        settings.source_render_enabled = True
        problems = validate_source_config(config)
        check(
            "开关打开后不再报渲染环境问题",
            not any("SOURCE_RENDER_ENABLED" in item for item in problems),
            str(problems),
        )
    finally:
        settings.source_render_enabled = was_enabled


async def main() -> int:
    # 与 test_source_onboarding 同一套做法：假站点不需要礼貌限频，产物落到测试目录
    from app.config import get_settings

    settings = get_settings()
    settings.source_request_interval_ms = 0
    settings.source_artifact_dir = "server/.artifacts/test-import"

    try:
        test_selector_subset()
        test_detail_assembly()
        test_field_rules()
        test_field_rule_generation()
        await test_render_config()
        test_url_guard()
        await test_pick_real_list()
        await test_rerun_without_base()
        await test_search_entrypoint_added()
        await test_search_entrypoint_skipped()
        await test_named_regions_import()
        await test_rerun_region_preserves_siblings()
        await test_region_prefers_column_page()
        await test_single_page_must_be_evidenced()
        await test_rerun_keeps_other_page_entries()
        test_region_candidates_not_capped()
    finally:
        net.set_resolver(None)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASSED)} 项 / 失败 {len(FAILED)} 项 / 跳过 {len(SKIPPED)} 项")
    for name in FAILED:
        print(f"  失败：{name}")
    for name in SKIPPED:
        print(f"  跳过：{name}")
    if FAILED:
        print("\n结果：存在失败项")
        return 1
    if SKIPPED:
        print(f"\n结果：离线用例全部通过；另有 {len(SKIPPED)} 项未跑（见上，不计入通过）")
    else:
        print("\n结果：离线用例全部通过，无跳过项")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        # Windows 控制台默认 GBK，中文输出会炸；这里强制 UTF-8
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    sys.exit(asyncio.run(main()))
