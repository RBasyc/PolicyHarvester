"""抓取 + 提取流水线（Phase 4；Phase 9 在中间插入人工确认）。

分两段执行，中间由用户在页面上确认「要抓哪些篇目」：

    第一段（图 A，由 `POST /api/tasks` 触发）
    START → prepare → fetch ─┬─(有政策)→ await_confirm → END
                             └─(出错/0 结果)→ fail → END

    第二段（图 B，由 `POST /api/tasks/{id}/confirm` 触发，见 `start_extraction`）
    （丢弃未勾选篇目）→ extract → summarize → END

- `prepare`       任务置 running、写首条日志
- `fetch`         调用 MCP `get_latest_policies`（多关键词逐个检索后按 URL 去重，逐页入库判别）→ 落库 → 重算计数
- `await_confirm` 任务置 `awaiting_confirmation` 并停住——**图到此结束**，等用户确认
- `extract`       逐篇：`get_policy_fulltext` 取正文 → 大模型结构化提取 → 写 extraction + fact_item
- `summarize`     任务置 completed、汇总日志
- `fail`          任务置 failed，stage 里带出设计稿要求的中文提示

为什么拆成两段：图**没有 checkpointer**（`builder.compile()` 无 saver），`PipelineState` 只活在
一次 `ainvoke` 的内存里，没法「暂停后再恢复」。所以第一段跑完就结束，检索结果已落库
（`policy_document`）；用户确认后由 `run_extraction_phase()` 从库里重新读出篇目，**直接调用**
`extract_node` / `summarize_node` 跑完尾段——两个节点是纯函数式的 `dict → dict`，不依赖图的状态机。

设计要点：
- 由 `POST /api/tasks` / `POST /api/tasks/{id}/confirm` 以**后台 asyncio 任务**触发，不阻塞请求；
- 单篇失败只标记该篇 `failed` 并继续，不影响其他篇；
- 计数一律走 `refresh_task_counts()`（按实际行数派生），保证进度面板与结果表格同源一致；
- 工具按名字在运行时查找（`find_tool`），因此本图不依赖「先加载 MCP 再编译」的顺序。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.db import repositories as repo
from app.db.repositories import PolicyRow
from app.pipeline import judge
from app.pipeline.extract import extract_policy
from app.pipeline.fulltext_clean import body_metrics, split_fulltext
from app.sources import SourceError, get_by_label
from app.sources.base import PolicySource
from app.tracing import isolated_context, trace_config

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """流水线整体性失败（MCP 不可用、检索异常等）——stage 直接展示其文案。"""


class PipelineState(TypedDict, total=False):
    task_id: int
    source: str
    keywords: list[str]
    date_from: date | None
    date_to: date | None
    agencies: list[str]  # 发布机构分类（bmfl）
    doc_types: list[str]  # 文种（pcodeJiguan）
    # 本任务覆盖的流水线参数（页面「高级设置」；缺失/None = 用 .env 全局默认，见 _effective）
    pipeline_max_policies: int | None
    pipeline_per_keyword_limit: int | None
    pipeline_extract_concurrency: int | None
    pipeline_max_body_chars: int | None
    pipeline_mcp_timeout: int | None
    pipeline_model: str | None  # 提取模型（PIPELINE_MODELS 注册表 id；None = 默认模型，即第一项）
    policies: list[dict[str, Any]]  # 待提取篇目（id / title / source_url）
    error: str | None


def _effective(source: dict[str, Any] | None, key: str) -> int:
    """读本任务覆盖的流水线参数；任务行 / 状态里没有（或为空）时回退 .env 全局默认。"""
    value = (source or {}).get(key)
    return int(value) if value else getattr(get_settings(), key)


# ---------- prepare ----------


async def prepare_node(state: PipelineState) -> dict:
    task_id = state["task_id"]
    stage = (
        f"正在连接 {get_by_label(state.get('source')).fetched_by}"
        f"（抓取源：{state.get('source', '')}）"
    )
    await repo.set_task_running(task_id, stage)
    await repo.add_task_log(task_id, stage, tone="info")
    return {}


# ---------- fetch ----------


async def fetch_node(state: PipelineState) -> dict:
    task_id = state["task_id"]
    cap = _effective(state, "pipeline_max_policies")
    # 先查成果库（Phase 13）：检索阶段就区分「新增 / 已在库」，并续抓直到凑够 cap 篇
    # **建议入库的新增**——判别在检索循环内逐页完成（见 _search_policies），被判「不建议」
    # 的不占名额，结果已写回 item 上的 judge_tagged / judge_prob。
    library_urls = await repo.fetch_library_urls()
    judge_stats: dict[str, int] = {}
    try:
        items = await _search_policies(
            state.get("keywords") or [],
            state.get("date_from"),
            state.get("date_to"),
            state.get("agencies") or [],
            state.get("doc_types") or [],
            overrides=state,
            library_urls=library_urls,
            target_new=cap,
            judge_stats=judge_stats,
        )
    except PipelineError as exc:
        return {"error": str(exc)}

    rows = [row for item in items if (row := _to_policy_row(item, state.get("source")))]
    fresh = [r for r in rows if r["source_url"] not in library_urls]
    # 三分区各自封顶：建议入库的新增凑满 cap（检索循环已按此口径停）；被判「不建议」的
    # 仍落库供确认面板提示（同样封顶，防病态关键词刷几十行批复淹没面板）；已在库的置灰展示
    keep_rows = [r for r in fresh if not r.get("judge_tagged")][:cap]
    tagged_rows = [r for r in fresh if r.get("judge_tagged")][:cap]
    lib_rows = [r for r in rows if r["source_url"] in library_urls][:cap]

    if not keep_rows and not tagged_rows and not lib_rows:
        return {"error": _empty_result_message(state)}

    await repo.insert_policies(task_id, keep_rows + tagged_rows + lib_rows)
    await repo.refresh_task_counts(task_id)

    # 以落库结果为准（去重/截断后真实入库的篇目），三分计数也从库里读（单一事实来源）
    stored = await repo.fetch_task_policies(task_id)
    n_keep = sum(1 for p in stored if not p["in_library"] and not p["judge_tagged"])
    n_tagged = sum(1 for p in stored if not p["in_library"] and p["judge_tagged"])
    n_lib = sum(1 for p in stored if p["in_library"])
    await repo.set_task_stage(
        task_id,
        f"已检索到 {len(stored)} 篇（建议入库 {n_keep} · 判别不建议 {n_tagged} · 已在库 {n_lib}），等待确认抓取",
    )
    await repo.add_task_log(
        task_id,
        f"检索完成：建议入库 {n_keep} 篇，另有 {n_tagged} 篇被判别标注不建议入库、"
        f"{n_lib} 篇已在成果库（置灰展示、默认不勾选）",
        tone="info",
    )
    if not n_keep:
        await repo.add_task_log(
            task_id,
            "本轮未凑够建议入库的新篇目；可勾选带标记/已在库的行强制提取，或放弃本次抓取",
            tone="info",
        )
    if n_tagged:
        # 拆开计数让来源可读：laya 标题判别 / 正文预检剔除 / 预检抓取失败（都落 judge_tagged 一列）
        n_body = judge_stats.get("body_rejected", 0)
        n_preflight_fail = min(judge_stats.get("preflight_failed", 0), n_tagged)
        n_laya = max(n_tagged - n_body - n_preflight_fail, 0)
        breakdown = f"laya 标题判别 {n_laya} · 正文预检 {n_body} · 预检抓取失败 {n_preflight_fail}"
        await repo.add_task_log(
            task_id,
            f"标注 {n_tagged} 篇不建议入库（{breakdown}；仅提示，是否抓取由你决定）",
            tone="info",
        )
    if judge_stats.get("preflight_failed"):
        await repo.add_task_log(
            task_id,
            f"正文预检 {judge_stats['preflight_failed']} 篇抓取失败，按不建议入库处理",
            tone="info",
        )
    if judge_stats.get("failures"):
        await repo.add_task_log(
            task_id,
            f"判别服务本轮 {judge_stats['failures']} 篇调用失败，按建议入库处理（fail-open）",
            tone="info",
        )
    return {
        "policies": [
            {"id": p["id"], "title": p["title"], "source_url": p["source_url"]}
            for p in stored
        ],
        "error": None,
    }


# ---------- await_confirm ----------


async def await_confirm_node(state: PipelineState) -> dict:
    """停在「等用户确认」这一档：图到此结束，提取由确认接口另行触发。

    检索到的篇目此刻已经落在 `policy_document`（`fetch_node` 里写好的），
    因此即使用户不马上确认、甚至服务重启，候选列表也不会丢。
    """
    task_id = state["task_id"]
    count = len(state.get("policies") or [])
    stage = f"已检索到 {count} 篇，等待确认后开始提取"
    await repo.await_task_confirmation(task_id, stage)
    await repo.add_task_log(
        task_id,
        f"已检索到 {count} 篇，请在「检索结果确认」中勾选要抓取的篇目（不勾选的将被丢弃）",
        tone="active",
    )
    return {}


def _empty_result_message(state: dict[str, Any]) -> str:
    """0 结果时的用户文案。**来源不同，话不一样**：

    配了站内检索入口的来源，关键词为空时那个入口根本不取数（检索页没有关键词参数时
    返回的是空结果页，见 `build_search_url`）——只说「调整关键词」会让人以为是自己筛得太严。
    """
    message = "未检索到匹配的政策原文，请调整关键词、时间范围或筛选条件后重试"
    try:
        source = get_by_label(state.get("source"))
        search_names = source.filter_capabilities().get("search_entrypoints") or []
    except Exception:  # noqa: BLE001 - 文案补充失败不该让任务报错
        return message
    if search_names and not [k for k in (state.get("keywords") or []) if str(k or "").strip()]:
        return (
            message
            + f"（提示：来源「{source.label}」的关键词由站内检索入口执行"
            f"（{'、'.join(search_names)}），本轮没有填关键词，该入口没有取数）"
        )
    return message


async def _search_policies(
    keywords: list[str],
    date_from: date | None,
    date_to: date | None,
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
    exclude_urls: set[str] | None = None,
    library_urls: set[str] | None = None,
    target_new: int | None = None,
    judge_stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """按「关键词 × 发布机构 × 文种」组合扇出检索并合并去重；可**翻页续抓**直到凑够新增篇数。

    组合扇出与各站点过滤规则在抓取源适配器里（`app/sources`，gov.cn 因检索 API
    会静默忽略重复的筛选参数，多选只能由服务端逐值扇出）；本函数只做编排：
    调用预算（`pipeline_max_search_calls` 是**总调用数**预算，翻页续抓也计入，
    防组合爆炸）、翻页、入库判别与正文预检。每关键词条数 / 超时可用 `overrides`
    （任务行上的高级设置）按任务覆盖。

    Phase 13 续抓语义：一轮（round）= 对每个未翻空的组合各取「下一页」；某组合返回 0 条
    即视为翻空（gov.cn 越界页回空列表 / 非 200 code）。停止条件（任一满足）：
    ① 凑够 `target_new` 篇**建议入库的新增**（不在 `library_urls` 里、且未被「laya 标题
    判别 + 正文预检」两级筛选标注；两级都在循环内**逐页**执行——被标「不建议」的仍进
    `merged` 供确认面板提示，但不占名额，翻页继续补齐；`exclude_urls` 命中的根本不进结果）；
    ② 总调用预算耗尽；③ 全部组合翻空；④ 续抓模式下**整轮零收获**——一轮下来连 `merged`
    都没进任何新链接（全是 `seen` 里的重复 = 翻页失效，或组合翻空）。注意④故意**不看**
    新增数：重跑场景第 1 页常整页都是已在库的篇目（它们进 `merged` 供置灰展示），这正是
    要继续翻页凑新增的时候，不能停。`target_new=None` 时只跑一轮（判别照做，标签供
    fetch_node / scheduler 展示与剔除用）。

    `exclude_urls` 供定时任务做跨轮去重：历史轮次已入库的链接直接跳过（见 app/scheduler.py）。
    `library_urls` 是成果库已有链接：**不剔除**（手动任务要置灰展示），不计入新增数、也不判别。
    `judge_stats` 是出参：`failures` 累计 laya 判别调用失败篇数（fail-open 按建议入库计数）、
    `body_rejected` / `preflight_failed` 累计正文预检剔除 / 抓取失败篇数，调用方据此写任务日志。
    """
    source = get_by_label((overrides or {}).get("source"))
    settings = get_settings()
    per_keyword = _effective(overrides, "pipeline_per_keyword_limit")
    mcp_timeout = _effective(overrides, "pipeline_mcp_timeout")

    combos = source.search_plan(keywords or [], agencies or [], doc_types or [])
    budget = settings.pipeline_max_search_calls

    facet_active = bool(agencies or doc_types)
    library = library_urls or set()
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    calls = 0  # 已消耗的总调用预算（等价于旧版对 combos 的 [:max_search_calls] 截断）
    new_count = 0  # **建议入库**的新增篇数（不在成果库且未被判别标注；判别按页在循环内做）
    page_of: dict[tuple[str | None, str | None, str | None], int] = {c: 1 for c in combos}
    exhausted: set[tuple[str | None, str | None, str | None]] = set()
    rounds_done = 0

    def _enough() -> bool:
        return target_new is not None and new_count >= target_new

    while True:
        if calls >= budget or _enough() or len(exhausted) >= len(combos):
            break
        # 只跑一轮（target_new=None，未启用续抓）时不进入第二轮
        if rounds_done and target_new is None:
            break
        round_added = 0  # 本轮进入 merged 的行数（含已在库）；为 0 = 翻页失效或翻空
        for combo in combos:
            if calls >= budget or _enough():
                break
            if combo in exhausted:
                continue
            keyword, agency, doc_type = combo
            try:
                raw_items = await source.search(
                    keyword=keyword,
                    agency=agency,
                    doc_type=doc_type,
                    page=page_of[combo],
                    limit=per_keyword,
                    date_from=date_from,
                    date_to=date_to,
                    timeout=mcp_timeout,
                )
            except SourceError as exc:
                raise PipelineError(f"抓取失败：{exc}") from exc
            except Exception as exc:  # noqa: BLE001 - 统一转成设计稿要求的提示文案
                raise PipelineError(
                    f"抓取失败：连接 {source.fetched_by} 超时或异常（{exc}），"
                    "请确认本地服务已启动后重试"
                ) from exc
            calls += 1

            if not raw_items:
                # 该组合翻到底（越界页 gov.cn 回空列表 / 非 200 code 也表现为 items=[]）
                exhausted.add(combo)

            page_fresh: list[dict[str, Any]] = []  # 本页的新增篇目（判别后再定是否占名额）
            for item in raw_items:
                # 站点特有的条目标过滤（公报 / 标题分词匹配 / 日期窗口，见 GovCnSource.validate_item）
                if not source.validate_item(
                    item,
                    keyword=keyword,
                    date_from=date_from,
                    date_to=date_to,
                    facet_active=facet_active,
                ):
                    continue
                url = str(item.get("url") or "").strip()
                if not url or url in seen:
                    continue
                # ④ 定时任务跨轮去重：历史轮次已入库的链接不再重复抓取
                if exclude_urls and url in exclude_urls:
                    continue
                seen.add(url)
                merged.append(item)
                round_added += 1
                # 「新增」= 不在成果库（占不占名额等本页判别后定）；已在库的保留在结果里置灰展示
                if url not in library:
                    page_fresh.append(item)

            # 入库判别（Phase 13）+ 正文预检（第二道筛）：本页新增逐篇「laya 标题判别 →
            # 正文预检」两级筛选、结果写回 item——被标注「不建议入库」的不占 target_new
            # 名额，翻页会继续补齐「建议入库」的。未启用 / fail-open（判别失败按建议
            # 入库）照常计数，与 fail-open 语义一致。
            if page_fresh:
                batch = await judge.judge_rows(
                    [
                        {
                            "source_url": it.get("url"),
                            "title": it.get("title"),
                            "agency": it.get("issuing_agency"),
                            "doc_no": it.get("document_number"),
                        }
                        for it in page_fresh
                    ]
                )
                for it in page_fresh:
                    verdict = batch.verdicts.get(it.get("url"))
                    if verdict:
                        it["judge_tagged"] = verdict.tagged
                        it["judge_prob"] = verdict.prob
                # laya 通过（含 fail-open）的再抓全文预检——图片版页面壳与薄壳转发通知
                # 只有标题看不出来（2026-10-13 漏网两类，见 _preflight_bodies）
                await _preflight_bodies(
                    source,
                    [it for it in page_fresh if not it.get("judge_tagged")],
                    mcp_timeout,
                    judge_stats,
                )
                for it in page_fresh:
                    if not it.get("judge_tagged"):
                        new_count += 1
                if judge_stats is not None and batch.failures:
                    judge_stats["failures"] = judge_stats.get("failures", 0) + batch.failures
            page_of[combo] += 1

        rounds_done += 1
        if target_new is not None and round_added == 0:
            # 整轮零收获（连 merged 都没进新链接）：要么 gov.cn 忽略 p 参数（翻页失效，
            # 重复页全被 seen 去重拦下，每组合最多多花 1 次调用），要么全部翻空——停，省预算。
            # 故意不看新增数：重跑场景第 1 页常整页都是已在库的篇目（merged 有进账），
            # 这正是要继续翻页凑新增的时候，停了就永远翻不过第 1 页。
            break

    return merged


async def _preflight_bodies(
    source: PolicySource,
    items: list[dict[str, Any]],
    mcp_timeout: int,
    judge_stats: dict[str, int] | None,
) -> None:
    """正文预检：laya 通过的候选抓全文，清洗后正文「不达标」的同样标「不建议入库」。

    laya 判别只看标题，拦不住两类漏网（2026-10-13 用户反馈，均已进过成果库）：
    ① 图片版文件——原文全是 <img>，抓回来的「正文」是页面壳（CSS/JS 残渣，CJK 可达
    555 但句末标点为 0，清洗的 5% 安全网反而会保住这份垃圾）；
    ② 薄壳转发通知——只有「现将《方案》印发给你们」百余字，实质内容不在抓取结果里。
    阈值 `PIPELINE_MIN_BODY_CHARS` / `PIPELINE_MIN_SENT_ENDERS` 由
    scripts/test_ingest_filter.py 用真实链接校准（好例 cjk≥1062 / 句末≥29，坏例
    句末≤1，空档巨大）。两阈值都 ≤0 = 预检整体关闭。

    结果只写回 item 的 `judge_tagged` / `judge_reason`（内存标注，落库与确认面板置灰走
    既有判别通道）；此时 `policy_document` 行还没插，全文不落盘，提取阶段照旧重抓。
    抓取失败按不达标处理并单独计数——抓不到正文 = 不该入库，但日志里要说清。
    """
    settings = get_settings()
    min_cjk = settings.pipeline_min_body_chars
    min_enders = settings.pipeline_min_sent_enders
    if (min_cjk <= 0 and min_enders <= 0) or not items:
        return

    if not source.fulltext_available():
        # fail-open：MCP 都没起来的话检索早就失败了，这里只是兜底不阻断
        logger.warning("正文预检跳过：未加载 get_policy_fulltext 工具")
        return

    semaphore = asyncio.Semaphore(max(1, settings.pipeline_preflight_concurrency))

    async def one(it: dict[str, Any]) -> None:
        async with semaphore:
            cjk = enders = imgs = 0
            try:
                url = str(it.get("url") or "").strip()
                if not url:
                    raise ValueError("缺少原文链接")
                result = await source.fetch_fulltext(url, timeout=mcp_timeout)
                _, clean = split_fulltext(result.body)
                cjk, enders = body_metrics(clean)
                imgs = result.image_count
            except Exception as exc:  # noqa: BLE001 - 单篇预检故障不能拖垮检索
                logger.warning(
                    "正文预检抓取失败（%s）——《%s》按不建议入库处理", exc, it.get("title")
                )
                if judge_stats is not None:
                    judge_stats["preflight_failed"] = judge_stats.get("preflight_failed", 0) + 1
                it["judge_tagged"] = True
                it["judge_prob"] = None
                it["judge_reason"] = "预检抓取失败"
                return
            if (min_cjk > 0 and cjk < min_cjk) or (min_enders > 0 and enders < min_enders):
                it["judge_tagged"] = True
                it["judge_prob"] = None
                it["judge_reason"] = "图片版（正文为页面壳）" if imgs else "正文过短（薄壳/附件版）"
                if judge_stats is not None:
                    judge_stats["body_rejected"] = judge_stats.get("body_rejected", 0) + 1
                logger.info(
                    "正文预检剔除《%s》：cjk=%d enders=%d imgs=%d（阈值 %d/%d）",
                    it.get("title"), cjk, enders, imgs, min_cjk, min_enders,
                )

    await asyncio.gather(*(one(it) for it in items))


def _to_policy_row(item: dict[str, Any], source: str | None) -> PolicyRow | None:
    """MCP 检索结果 → `policy_document` 一行（标题/链接缺失的直接丢弃）。"""
    adapter = get_by_label(source)
    normalized = adapter.normalize_item(item)
    if normalized is None:
        return None
    return {
        "title": normalized.title,
        "source_url": normalized.url,
        "publish_date": normalized.date,
        "source_name": source,
        "agency": normalized.agency,
        "doc_no": normalized.doc_no,
        "catalog": normalized.catalog_key or normalized.catalog,
        "fetched_by": adapter.fetched_by,
        # 检索循环内判别写回的标注（见 _search_policies）；未判别（服务停用）时为 None
        "judge_tagged": normalized.judge_tagged,
        "judge_prob": normalized.judge_prob,
    }


# ---------- extract ----------


async def extract_node(state: PipelineState) -> dict:
    """逐篇提取。单篇的失败在 `_extract_one` 内部消化，这里只负责调度与并发上限。"""
    task_id = state["task_id"]
    policies = state.get("policies") or []
    total = len(policies)
    semaphore = asyncio.Semaphore(max(1, _effective(state, "pipeline_extract_concurrency")))

    async def run(index: int, policy: dict[str, Any]) -> None:
        async with semaphore:
            await _extract_one(task_id, index, total, policy, overrides=state)

    await asyncio.gather(*(run(i, p) for i, p in enumerate(policies, start=1)))
    return {}


async def _extract_one(
    task_id: int,
    index: int,
    total: int,
    policy: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> None:
    policy_id = policy["id"]
    title = policy["title"]
    url = policy.get("source_url")

    # 可观测性（app/tracing.py）：**一篇一条 trace**，按任务归到同一个 session。
    # 刻意不做成「整个任务一条大 trace」——检索段（图 A）与提取段之间隔着人工确认、是两个
    # 独立进程片段，硬套一条 trace 反而丢上下文；一篇一条既贴合「这次提取花了多少 token、
    # 有没有走 JSON 回退」的实际排查问题，也让并发的多篇各自干净。未启用时这里是 `{}`。
    extract_config = trace_config(
        name=f"提取：{title[:40]}",
        session_id=f"task-{task_id}",
        tags=["pipeline", "extract"],
        metadata={"task_id": task_id, "policy_id": policy_id},
    )

    # 干净起点：清掉旧结果（重跑/重试时幂等）
    await repo.reset_extraction(policy_id)
    await repo.set_task_stage(
        task_id, f"正在调用大模型提取：第 {index}/{total} 篇 ——《{title}》"
    )
    await repo.add_task_log(task_id, f"正在提取第 {index} 篇 …", tone="active")

    try:
        source = get_by_label((overrides or {}).get("source"))
        fulltext = await _fetch_fulltext(
            source, url, policy_id, timeout=_effective(overrides, "pipeline_mcp_timeout")
        )
        body = str(fulltext.get("body") or "").strip()
        if len(body) < 50:
            raise ValueError("政策正文为空或过短，无法提取")

        payload, model = await extract_policy(
            title=str(fulltext.get("title") or title),
            body=body,
            url=url,
            max_body_chars=_effective(overrides, "pipeline_max_body_chars"),
            model_id=(overrides or {}).get("pipeline_model"),
            config=extract_config,
        )
        await repo.save_extraction(
            policy_id,
            model=model,
            facts=[{"label": f.label, "content": f.content} for f in payload.facts],
            info_summary=payload.info.summary,
            # 落库形态与 knowledge_concepts 一致：[{"point": "..."}]（见 schema.sql）
            info_points=[{"point": p} for p in payload.info.points],
            knowledge_concepts=[
                {"term": c.term, "desc": c.desc} for c in payload.knowledge.concepts
            ],
            knowledge_fields=list(payload.knowledge.fields),
            knowledge_extension=payload.knowledge.extension,
        )
        await repo.add_task_log(
            task_id,
            f"第 {index} 篇提取成功（事实 {len(payload.facts)} · "
            f"信息 {len(payload.info.points)} · 知识 {len(payload.knowledge.concepts)}）",
            tone="success",
        )
    except Exception as exc:  # noqa: BLE001 - 单篇失败不能拖垮整个任务
        message = _short_error(exc)
        logger.warning("第 %d 篇《%s》提取失败：%s", index, title, exc)
        await repo.mark_extraction_failed(policy_id, message)
        await repo.add_task_log(task_id, f"第 {index} 篇提取失败：{message}", tone="info")
    finally:
        # 无论成败都重算计数，保证进度面板与结果表格始终一致
        await repo.refresh_task_counts(task_id)


async def _fetch_fulltext(
    source: PolicySource,
    url: str | None,
    policy_id: int,
    *,
    timeout: int | None = None,
) -> dict[str, Any]:
    """取政策全文，并用解析出的机构/文号回填 policy_document。"""
    if not url:
        raise ValueError("缺少原文链接，无法抓取全文")

    try:
        result = await source.fetch_fulltext(
            url, policy_id, timeout=timeout if timeout else get_settings().pipeline_mcp_timeout
        )
    except SourceError as exc:
        raise PipelineError(
            f"无法连接 {source.fetched_by}（{exc}），请确认本地服务已启动后重试"
        ) from exc
    except ValueError:
        raise  # 全文接口返回了非预期内容——保持原样冒泡
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"抓取全文失败（{exc}）") from exc

    # 清洗：剥掉整页抓取带入的 CSS/JS/导航噪声，元信息表（发文机关/来源/主题分类…）
    # 单独解析出来。干净正文写回 data["body"]，让下游 LLM 提取与 fulltext 归档都用干净版本。
    meta, clean_body = split_fulltext(result.body)
    body = clean_body or result.body

    # `publish_date` 只放**发布日期**，所以这里要按来源判断全文接口报的日期算不算数
    # （见 `PolicySource.fulltext_date_is_publish`）。gov.cn 报的是成文日期——拿它覆盖会把
    # 跨年文件分错年份（年份筛选/直方图都按 `EXTRACT(YEAR FROM publish_date)`），也会和抓取时
    # 按发布日期判的日期窗口对不上。成文日期没有丢：它照样从归档全文解析出来，详情页单独展示
    # （`FulltextMeta.signed_date`）。列表轮没给日期时这里传 None，那一行就诚实地留空，
    # 不拿另一个语义的日期顶上（`update_policy_from_fulltext` 的 COALESCE 只在非空时覆盖）。
    await repo.update_policy_from_fulltext(
        policy_id,
        agency=(result.issuer or "").strip() or (meta.agency or ""),
        doc_no=(result.doc_no or "").strip() or (meta.doc_no or ""),
        publish_date=result.date if source.fulltext_date_is_publish else None,
        fulltext=body.strip(),
    )
    return {
        "body": body,
        "title": result.title,
        "issuer": result.issuer,
        "doc_no": result.doc_no,
        "date": result.date,
        "image_count": result.image_count,
    }


def _short_error(exc: Exception) -> str:
    """错误文案压到一行一句话（前端日志与抽屉里直接展示）。"""
    text = " ".join(str(exc).split())
    return text[:180] or exc.__class__.__name__


# ---------- summarize / fail ----------


async def summarize_node(state: PipelineState) -> dict:
    task_id = state["task_id"]
    row = await repo.refresh_task_counts(task_id) or {}
    total = row.get("total_count", 0)
    extracted = row.get("extracted_count", 0)
    failed = row.get("failed_count", 0)
    await repo.finish_task(task_id, "completed", "已完成")
    await repo.add_task_log(
        task_id,
        f"任务完成：共 {total} 篇，提取成功 {extracted} 篇，失败 {failed} 篇",
        tone="success",
    )
    return {}


async def fail_node(state: PipelineState) -> dict:
    task_id = state["task_id"]
    error = state.get("error") or "抓取失败，请重试"
    await repo.refresh_task_counts(task_id)
    await repo.finish_task(task_id, "failed", error)
    await repo.add_task_log(task_id, error, tone="info")
    return {}


def _route_after_fetch(state: PipelineState) -> str:
    """检索到篇目 → 停下来等确认；出错或 0 结果 → 直接失败（0 结果即「近 N 天无相关政策」）。"""
    return "fail" if state.get("error") or not state.get("policies") else "await_confirm"


def build_pipeline_graph():
    """第一段：`prepare → fetch → (await_confirm | fail)`。

    注意这里**不包含** `extract` / `summarize`——人工确认把它们隔到了第二段
    （见 `run_extraction_phase`），两个节点函数仍在本模块，被直接调用。
    """
    builder = StateGraph(PipelineState)
    builder.add_node("prepare", prepare_node)
    builder.add_node("fetch", fetch_node)
    builder.add_node("await_confirm", await_confirm_node)
    builder.add_node("fail", fail_node)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "fetch")
    builder.add_conditional_edges(
        "fetch", _route_after_fetch, {"await_confirm": "await_confirm", "fail": "fail"}
    )
    builder.add_edge("await_confirm", END)
    builder.add_edge("fail", END)
    return builder.compile()


# ---------- 后台执行器 ----------

_graph = None
# 在跑的后台任务，**按 task_id 归组**：既可能是整条流水线，也可能是单篇重跑。
# 终止 / 清空 / 关停都据此精确定位要取消哪些任务（见 cancel_pipeline）。
_running: dict[int, set[asyncio.Task]] = {}


def get_pipeline_graph():
    """进程内单例。工具是运行时按名字查找的，因此无需等待 MCP 加载完成即可编译。"""
    global _graph
    if _graph is None:
        _graph = build_pipeline_graph()
    return _graph


def _track(task_id: int, coro, name: str) -> asyncio.Task:
    """登记一个后台任务，结束后自动从 `_running` 摘除（空桶一并删掉）。"""
    # 独立执行上下文：流水线图不要继承发起方（请求 / 调度循环）里残留的 OTel 活动 span，
    # 否则 trace 没有真正的根（见 tracing.isolated_context）
    task = asyncio.create_task(coro, name=name, context=isolated_context())
    _running.setdefault(task_id, set()).add(task)

    def _discard(finished: asyncio.Task) -> None:
        bucket = _running.get(task_id)
        if bucket is None:
            return
        bucket.discard(finished)
        if not bucket:
            _running.pop(task_id, None)

    task.add_done_callback(_discard)
    return task


def start_pipeline(task_id: int) -> None:
    """把流水线丢进后台 asyncio 任务（由 `POST /api/tasks` 调用），立即返回。"""
    _track(task_id, run_pipeline(task_id), f"pipeline-{task_id}")


def start_single_extraction(task_id: int, policy_id: int) -> None:
    """重新提取单篇（由重试 / 重新提取接口调用）。"""
    _track(task_id, run_single_extraction(task_id, policy_id), f"reextract-{policy_id}")


def start_extraction(task_id: int) -> None:
    """用户确认后跑第二段（由 `POST /api/tasks/{id}/confirm` 调用）。

    未勾选的篇目已被确认接口删掉，这里只需要把剩下的逐篇提取完。
    """
    _track(task_id, run_extraction_phase(task_id), f"extract-{task_id}")


async def cancel_pipeline(task_id: int) -> bool:
    """取消某个任务在跑的后台任务并等它们真正结束；返回是否确实取消了东西。

    调用方应在返回后再落终态：`run_pipeline` 的 `CancelledError` 分支写的是「服务重启…」，
    由调用方覆盖成终止文案（见 `POST /api/tasks/{id}/cancel`）。
    """
    tasks = list(_running.get(task_id, ()))
    for task in tasks:
        task.cancel()
    if tasks:
        # return_exceptions：被取消的任务会抛 CancelledError，这里只需等它们退出
        await asyncio.gather(*tasks, return_exceptions=True)
    return bool(tasks)


async def cancel_all_pipelines() -> int:
    """取消全部在跑的后台任务，返回取消掉的**任务数**（每个 task_id 计 1）。

    清空数据前必须先调用：否则流水线会继续往已删除的任务里写日志 / 计数。
    """
    cancelled = 0
    for task_id in list(_running):
        if await cancel_pipeline(task_id):
            cancelled += 1
    return cancelled


async def run_single_extraction(task_id: int, policy_id: int) -> None:
    """只处理一篇：状态置 running → 提取 → 按剩余篇目决定终态。

    任务只有在**没有** pending/extracting 的篇目时才回到 `completed`；
    否则保持 running 并在 stage 里说明还剩多少篇（seed 这种只提取了一部分的样本任务会走这一支）。
    """
    try:
        rows = await repo.fetch_task_policies(task_id)
        target = next((p for p in rows if p["id"] == policy_id), None)
        if target is None:
            logger.warning("重新提取失败：政策 %s 不属于任务 %s", policy_id, task_id)
            return

        # 覆盖参数跟着任务行走（单篇重跑也尊重创建任务时的高级设置）
        task_row = await repo.fetch_task(task_id) or {}
        await repo.set_task_running(task_id, f"正在重新提取：《{target['title']}》")
        total = len(rows)
        done = sum(
            1
            for p in rows
            if p["id"] != policy_id and p["status"] in ("extracted", "failed")
        )
        await _extract_one(
            task_id,
            done + 1,
            total,
            {"id": target["id"], "title": target["title"], "source_url": target["source_url"]},
            overrides=task_row,
        )

        await repo.refresh_task_counts(task_id)
        remaining = [
            p
            for p in await repo.fetch_task_policies(task_id)
            if p["status"] in ("pending", "extracting")
        ]
        if remaining:
            await repo.set_task_stage(
                task_id, f"《{target['title']}》处理完成，仍有 {len(remaining)} 篇待提取"
            )
        else:
            await repo.finish_task(task_id, "completed", "已完成")
            await repo.add_task_log(task_id, "全部篇目提取完成", tone="success")
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给事件循环
        logger.exception("重新提取政策 %s 失败", policy_id)
        await repo.set_task_stage(task_id, f"重新提取失败：{_short_error(exc)}")


async def run_extraction_phase(task_id: int) -> None:
    """第二段：把任务里**剩下的**篇目逐篇提取完并汇总。

    进入时任务已被确认接口置为 `running`（`set_task_running`），因此这里从读库开始。
    `extract_node` / `summarize_node` 是纯函数式的 `dict → dict`（内部只写数据库、不改状态），
    所以不需要再包一层图，直接调用即可。
    """
    try:
        rows = await repo.fetch_task_policies(task_id)
        policies = [
            {"id": p["id"], "title": p["title"], "source_url": p["source_url"]}
            for p in rows
            if p["status"] in ("pending", "extracting")  # 只处理还没出结果的
        ]
        # 覆盖参数从任务行重读（确认与检索可能隔了很久，创建请求早已结束）
        task_row = await repo.fetch_task(task_id) or {}
        await extract_node({"task_id": task_id, "policies": policies, **{
            key: task_row.get(key)
            for key in (
                "pipeline_max_policies",
                "pipeline_per_keyword_limit",
                "pipeline_extract_concurrency",
                "pipeline_max_body_chars",
                "pipeline_mcp_timeout",
                "pipeline_model",
            )
        }})
        await summarize_node({"task_id": task_id})
    except asyncio.CancelledError:  # 终止 / 关停
        logger.info("任务 %s 的提取阶段被取消", task_id)
        await repo.finish_task(task_id, "failed", "服务重启导致任务中断，请重试")
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底，任务必须有终态
        logger.exception("任务 %s 的提取阶段异常退出", task_id)
        message = f"提取失败：{_short_error(exc)}"
        await repo.finish_task(task_id, "failed", message)
        await repo.add_task_log(task_id, message, tone="info")


async def run_pipeline(task_id: int) -> None:
    """执行一次流水线。任何未捕获异常都会把任务落到 failed 终态，避免前端一直转圈。"""
    task_row = await repo.fetch_task(task_id)
    if task_row is None:
        logger.error("流水线启动失败：任务 %s 不存在", task_id)
        return

    state: PipelineState = {
        "task_id": task_id,
        "source": task_row["source"],
        "keywords": list(task_row["keywords"] or []),
        "date_from": task_row["date_from"],
        "date_to": task_row["date_to"],
        # 用 .get 防御未迁移的老库（schema 迁移是先决条件）
        "agencies": list(task_row.get("agencies") or []),
        "doc_types": list(task_row.get("doc_types") or []),
        # 高级设置的本任务覆盖（老库没有这些列时为 None = 用 .env 默认）
        "pipeline_max_policies": task_row.get("pipeline_max_policies"),
        "pipeline_per_keyword_limit": task_row.get("pipeline_per_keyword_limit"),
        "pipeline_extract_concurrency": task_row.get("pipeline_extract_concurrency"),
        "pipeline_max_body_chars": task_row.get("pipeline_max_body_chars"),
        "pipeline_mcp_timeout": task_row.get("pipeline_mcp_timeout"),
        "pipeline_model": task_row.get("pipeline_model"),
    }
    try:
        # 图 A（检索段）自身不调大模型，但 fetch 节点的 MCP 检索 / 判别会走工具——挂上
        # callback 后这几步会作为子 span 出现在 trace 里，正好补上「检索为什么慢 / 打了
        # 几次 MCP」的视野。未启用可观测性时 trace_config 返回 {}。
        await get_pipeline_graph().ainvoke(
            state,
            config=trace_config(
                name=f"任务 {task_id}·检索段",
                session_id=f"task-{task_id}",
                tags=["pipeline", "fetch"],
                metadata={"task_id": task_id},
            ),
        )
    except asyncio.CancelledError:  # 服务关停
        logger.info("任务 %s 的流水线被取消", task_id)
        await repo.finish_task(task_id, "failed", "服务重启导致任务中断，请重试")
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底，任务必须有终态
        logger.exception("任务 %s 的流水线异常退出", task_id)
        message = f"抓取失败：{_short_error(exc)}"
        await repo.finish_task(task_id, "failed", message)
        await repo.add_task_log(task_id, message, tone="info")


async def shutdown_pipelines() -> None:
    """应用关停时取消在跑的流水线（由 lifespan 调用）。"""
    await cancel_all_pipelines()
