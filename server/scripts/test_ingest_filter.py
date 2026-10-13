"""入库预检（正文长度/实质内容）校准脚本。

直接运行：uv run python scripts/test_ingest_filter.py

背景（2026-10-13 用户反馈的两类漏网，laya 只看标题拦不住）：
1. **图片版文件**（医保发〔2026〕19号 长期护理保险支付管理）——原文全是 <img>，
   抓回来的「正文」是页面壳（导航/脚本/元信息表）；
2. **薄壳转发通知**（工信部信发〔2026〕209号）——只有 3 行「现将《方案》印发给你们」，
   实质内容不在抓取结果里。
本脚本用真实链接校准「清洗后正文 CJK 字数 / 句末标点数」阈值（配合 laya 开/关），
给 crawl_graph._search_policies 的正文预检（第 ⑤ 步）默认值提供依据。

流程：
1. load_mcp_tools 起本地 MCP，get_latest_policies 按查询集搜真实链接
   （含两个已知坏例的定向查询 + 几个日常查询当好例）；
2. 每篇 get_policy_fulltext 抓正文 → 指标：原始长度 / 清洗后长度 / CJK 字数 /
   句末标点数（。！？；）/ image_count（未重建的旧 build 无此字段，按 0 计）；
3. laya 判别（judge_rows；服务不可达该轴自动降级为全「入库」，与线上 fail-open 一致）；
4. 组合矩阵 min_cjk × min_enders × laya，打印每组合的剔除清单（供肉眼复核误伤）+ 排名。

只读冒烟：起 stdio MCP 子进程、GET gov.cn 详情页、POST 内网判别服务；不起常驻服务、不写库。
"""

import asyncio
import re
import sys
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Windows 控制台默认 GBK，表格里的 ⏎ 等字符会 UnicodeEncodeError——统一切到 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.agent.mcp import load_mcp_tools, tool_json
from app.config import get_settings
from app.pipeline.fulltext_clean import split_fulltext
from app.pipeline.judge import judge_rows

# (标签, get_latest_policies 参数尝试列表——前一个搜不到坏例标题就试下一个, 期望剔除的标题子串)
QUERIES = [
    (
        "坏例定向·长期护理保险（图片版）",
        [{"keyword": "长期护理保险", "limit": 8}],
        ["长期护理保险支付管理"],
    ),
    (
        "坏例定向·人工智能+软件（薄壳通知）",
        [{"keyword": "人工智能+软件", "limit": 8}, {"keyword": "人工智能", "limit": 10}],
        ["人工智能+软件"],
    ),
    ("日常·以旧换新", [{"keyword": "以旧换新", "limit": 6}], []),
    ("日常·中小企业", [{"keyword": "中小企业", "limit": 6}], []),
    ("日常·数据要素", [{"keyword": "数据要素", "limit": 6}], []),
]

# 组合矩阵的阈值轴：min_cjk / min_enders（≤0 = 不启用该信号）
MIN_CJK_AXIS = [0, 200, 300, 500]
MIN_ENDERS_AXIS = [0, 5, 10]
LAYA_AXIS = [True, False]

FETCH_CONCURRENCY = 4  # 全文预检抓取并发（与计划的 PIPELINE_PREFLIGHT_CONCURRENCY 默认一致）

_CJK_RE = re.compile(r"[一-鿿]")
_ENDERS_RE = re.compile(r"[。！？；]")


async def collect_items(search_tool) -> tuple[list[dict], dict[str, str]]:
    """跑查询集收集真实篇目（按 url 去重），并给期望剔除的打上 bad_substring 标记。"""
    items: list[dict] = []
    seen: set[str] = set()
    bad_substrings: dict[str, str] = {}  # url → 命中的坏例子串
    for label, args_list, bad_subs in QUERIES:
        got: list[dict] = []
        for args in args_list:
            raw = await search_tool.ainvoke(args)
            data = tool_json(raw) or {}
            got = (data.get("items") or [])[: args.get("limit", 8)]
            # 定向查询要能看到坏例标题才算搜到（gov.cn 分词检索，换词重试）
            if not bad_subs or any(
                any(sub in str(it.get("title") or "") for sub in bad_subs) for it in got
            ):
                break
        print(f"  [{label}] 返回 {len(got)} 条")
        for it in got:
            url = str(it.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            it["url"] = url
            items.append(it)
            for sub in bad_subs:
                if sub in str(it.get("title") or ""):
                    bad_substrings[url] = sub
    return items, bad_substrings


async def fetch_metrics(fulltext_tool, items: list[dict], mcp_timeout: int) -> None:
    """逐篇抓全文并计算指标，写回 item：raw_len / clean_len / cjk / enders / imgs / fetch_error。"""
    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def one(it: dict) -> None:
        async with semaphore:
            it.setdefault("url", "")
            try:
                raw = await asyncio.wait_for(
                    fulltext_tool.ainvoke({"url": it["url"]}), timeout=mcp_timeout
                )
                data = tool_json(raw)
                if not isinstance(data, dict):
                    raise ValueError("全文接口返回了非预期内容")
                body = str(data.get("body") or "")
                _, clean = split_fulltext(body)
                it["raw_len"] = len(body.strip())
                it["clean_len"] = len(clean)
                it["cjk"] = len(_CJK_RE.findall(clean))
                it["enders"] = len(_ENDERS_RE.findall(clean))
                it["imgs"] = int(data.get("image_count") or 0)
                it["preview"] = clean[:60].replace("\n", "⏎")
                it["fetch_error"] = ""
            except Exception as exc:  # noqa: BLE001 - 抓不到正文 = 任何阈值都会剔除，单篇继续
                it.update(raw_len=0, clean_len=0, cjk=0, enders=0, imgs=0, fetch_error=str(exc)[:80])

    await asyncio.gather(*(one(it) for it in items))


def rejected_by(it: dict, min_cjk: int, min_enders: int, laya_on: bool) -> str | None:
    """按一个组合判定单篇：返回剔除原因（None = 保留）。"""
    if laya_on and it.get("laya_tagged"):
        return "laya"
    if min_cjk > 0 and it.get("cjk", 0) < min_cjk:
        return f"cjk<{min_cjk}"
    if min_enders > 0 and it.get("enders", 0) < min_enders:
        return f"enders<{min_enders}"
    return None


async def main() -> int:
    settings = get_settings()
    print("=== 1. 加载 MCP 工具 ===")
    tools = await load_mcp_tools()
    if not tools:
        print("FAIL: 未加载到任何 MCP 工具（详见上方日志）")
        return 1
    search_tool = next(t for t in tools if t.name == "get_latest_policies")
    fulltext_tool = next(t for t in tools if t.name == "get_policy_fulltext")

    print("\n=== 2. 检索真实链接 ===")
    items, bad_substrings = await collect_items(search_tool)
    if not items:
        print("FAIL: 一个链接都没搜到（限流？稍后重试）")
        return 1
    n_bad = len(bad_substrings)
    print(f"  去重后共 {len(items)} 篇，其中已知坏例 {n_bad} 篇")

    print(f"\n=== 3. 抓全文算指标（并发 {FETCH_CONCURRENCY}） ===")
    await fetch_metrics(fulltext_tool, items, settings.pipeline_mcp_timeout)
    fetch_errors = sum(1 for it in items if it.get("fetch_error"))
    if fetch_errors:
        print(f"  WARN: {fetch_errors} 篇抓取失败（按 cjk=0 处理，任何阈值都会剔除）")

    print("\n=== 4. laya 判别（仅标题） ===")
    laya_available = settings.judge_enabled and bool(settings.judge_url)
    if laya_available:
        batch = await judge_rows(
            [{"source_url": it["url"], "title": it.get("title")} for it in items]
        )
        for it in items:
            verdict = batch.verdicts.get(it["url"])
            it["laya_tagged"] = bool(verdict and verdict.tagged)
        print(f"  判别 {len(batch.verdicts)} 篇，laya 标注不入库 "
              f"{sum(1 for it in items if it.get('laya_tagged'))} 篇，失败 {batch.failures} 篇（fail-open）")
    else:
        for it in items:
            it["laya_tagged"] = False
        print("  JUDGE_ENABLED=false 或未配 url，该轴全部按「入库」处理")

    print("\n=== 5. 逐篇指标 ===")
    print(f"  {'标题':<34} {'raw':>5} {'clean':>5} {'cjk':>5} {'enders':>6} {'imgs':>4}  laya  期望")
    for it in items:
        title = str(it.get("title") or "")[:32]
        bad = "坏例·剔除" if it["url"] in bad_substrings else "好例·保留"
        laya_mark = "不入库" if it.get("laya_tagged") else "入库"
        print(f"  {title:<34} {it.get('raw_len', 0):>5} {it.get('clean_len', 0):>5} "
              f"{it.get('cjk', 0):>5} {it.get('enders', 0):>6} {it.get('imgs', 0):>4}  "
              f"{laya_mark:<4}  {bad}")
    for url, sub in bad_substrings.items():
        it = next(i for i in items if i["url"] == url)
        print(f"    · 坏例《…{sub}…》清洗后正文开头：{it.get('preview') or '（空）'}")

    print(f"\n=== 6. 组合矩阵（{len(MIN_CJK_AXIS)} cjk × {len(MIN_ENDERS_AXIS)} enders × "
          f"{len(LAYA_AXIS)} laya） ===")
    scores: list[tuple[int, int, int, int, int, str]] = []  # (bad_hit, good_cut, min_cjk, min_enders, laya, ...)
    for min_cjk in MIN_CJK_AXIS:
        for min_enders in MIN_ENDERS_AXIS:
            for laya_on in LAYA_AXIS:
                label = f"cjk≥{min_cjk or '—'} · enders≥{min_enders or '—'} · laya={'开' if laya_on else '关'}"
                rejected = [
                    (it, rejected_by(it, min_cjk, min_enders, laya_on)) for it in items
                ]
                bad_hit = sum(
                    1 for it, why in rejected if why and it["url"] in bad_substrings
                )
                good_cut_titles = [
                    str(it.get("title") or "")[:22]
                    for it, why in rejected
                    if it["url"] not in bad_substrings and why
                ]
                scores.append((bad_hit, len(good_cut_titles), min_cjk, min_enders, int(laya_on), label))
                print(f"\n--- {label} → 剔除坏例 {bad_hit}/{n_bad}，"
                      f"另剔除非坏例 {len(good_cut_titles)} 篇（请复核） ---")
                for it, why in rejected:
                    if why:
                        mark = "坏例" if it["url"] in bad_substrings else "复核"
                        print(f"    [{mark}] {str(it.get('title') or '')[:26]}（{why}，"
                              f"cjk={it.get('cjk', 0)} enders={it.get('enders', 0)}）")

    print("\n=== 7. 结论 ===")
    # 排序：坏例剔除多 > 好例误伤少 > 阈值宽松（小的 min_cjk 优先，enders 其次，laya 开优先）
    ranked = sorted(scores, key=lambda s: (-s[0], s[1], s[2], s[3], -s[4]))
    for bad_hit, good_cut, _, _, _, label in ranked[:8]:
        print(f"  剔除坏例 {bad_hit}/{n_bad} · 非坏例剔除 {good_cut}（待复核）  {label}")
    full = [s for s in ranked if s[0] == n_bad and s[1] == 0]
    if full:
        print(f"\n  推荐组合：「{full[0][5]}」——写进 .env 的 PIPELINE_MIN_BODY_CHARS / "
              "PIPELINE_MIN_SENT_ENDERS（laya 开关即 JUDGE_ENABLED）。")
    else:
        print("\n  没有满分组合——看上面各组合的误伤清单权衡，或放宽坏例/好例样本后重跑。")

    print("\nOK: 入库预检校准结束")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
