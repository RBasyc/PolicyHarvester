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
    SKIPPED,
    build_demo_site,
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


async def main() -> int:
    # 与 test_source_onboarding 同一套做法：假站点不需要礼貌限频，产物落到测试目录
    from app.config import get_settings

    settings = get_settings()
    settings.source_request_interval_ms = 0
    settings.source_artifact_dir = "server/.artifacts/test-import"

    try:
        test_selector_subset()
        test_detail_assembly()
        test_url_guard()
        await test_pick_real_list()
        await test_rerun_without_base()
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
