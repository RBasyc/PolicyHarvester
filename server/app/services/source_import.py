"""页面内「导入网址」入口的确定性编排（Phase 15）。

**为什么单独一个模块**：`app/services/onboarding.py` 是「能力层」——每个阶段一个纯函数，
9 个 Agent 工具逐个调用它们。页面入口需要的是「把这几步串起来 + 推进度」，
属于编排，不属于能力。所以这里**不碰 onboarding.py 的任何函数签名**，只按顺序调用它，
在阶段之间发进度事件。代价：拿不到试运行**内部**的「第 2/3 篇」粒度（只能给一句
「正在试运行（真实联网，可能需要几十秒）」）；好处：生产代码一行不改，风险最低。

**确定性从哪来**：不经过大模型。勘察报告里每个候选都自带分数（列表容器按「同签名兄弟数
× 平均文本长度」打分，正文容器按「文本量 / 链接密度 / 常见 id-class 名」加权），
这里直接**取最高分**组装配置，其余候选原样列进报告让人复核——「自动取最高分 +
可切换重跑」= 人只做最后的启用审批。

**不做的事**（与 Agent 工具同口径）：不绕过访问控制；`blocked`（robots 禁止 / 401 /
403 / 内网地址）与 `failed`（配置写错）严格分开；只有 `status == passed` 才允许启用，
`force` 放行会写进变更说明。验收报告里的 `untested` / `coverage_limits` 原样透传给页面
——「这次没验证什么」不许被折叠成「通过」。

**作业状态只活在进程内**（`JOBS`）：一次导入是短时过程，持久留痕已经在
`source_onboarding_run`（每次勘察/试运行各写一行）。服务重启丢的只是进度条，
草案来源已落库，重新看来源列表即可。

**批量导入（Phase 16）**：`start_batch` 把 N 个地址建成 N 个**独立** ImportJob（完全
复用上面的六阶段流水线与注册闸），并发满了不拒绝而是排队（queued）——`asyncio.Queue`
+ 一个 lazy worker 协程，出队间隔 `_QUEUE_DRAIN_SECONDS` 兼作对目标站点的礼貌节流。
批内不受单条入口的 5 秒冷却限制（冷却只约束页面连点，见 `start_import` 的
`respect_cooldown`）。聚合视图（批量 SSE / 作业列表）读 `BATCHES` + `JOBS` 两个
进程内注册表。

**内存边界（Phase 16）**：`JOBS`/`BATCHES` 都有惰性清扫（`_evict_finished_jobs`，
在新建作业时触发）：终态作业保留 `_JOB_RETENTION_SECONDS`（30 分钟），总量兜底
`_MAX_RETAINED_JOBS`（50）按最老先踢；running/queued 永不清理。被踢的只是进度
快照——持久证据在 `source_onboarding_run`，页面文案要说明这一点。
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.schemas.source import (
    SourceBatchImportCreated,
    SourceBatchImportItem,
    SourceBatchImportRequest,
    SourceImportRequest,
)
from app.sources import net
from app.sources.analyze import analyze_page
from app.sources.config import (
    SEARCH_PROBE_KEYWORD,
    DetailConfig,
    EntrypointConfig,
    FieldRule,
    config_from_row,
)
from app.sources.net import domain_allowed
from app.sources.pagination_probe import probe_single_page_entry, rule_from_evidence
from app.sources.field_rules import apply_rule, rules_from_field_candidates
from app.sources.html_source import date_texts_by_selector, parse_detail_page, parse_list_page
from app.sources.htmlparse import parse_html, selector_supported, strip_nth_of_type
from app.sources.util import parse_date
from app.sources.registry import get, register_configured, unregister

logger = logging.getLogger(__name__)

# ---------- 作业注册表（进程内） ----------

# 同时在跑的导入作业上限与启动冷却：导入会真实联网抓别人的站点，页面入口只该是
# 「一次一个」的节奏；冷却不引依赖，纯进程内计时。
# 批量导入（respect_cooldown=False）不受这两个限制：满了走 queued 排队，不拒绝。
_MAX_CONCURRENT_JOBS = 3
_START_COOLDOWN_SECONDS = 5.0
# 排队出队间隔：吸收批内连击 + 对目标站点的礼貌节流（等位结束到真正开跑之间）
_QUEUE_DRAIN_SECONDS = 2.0

# 终态作业的进程内保留窗口与总量兜底（见模块 docstring 的「内存边界」）
_JOB_RETENTION_SECONDS = 30 * 60
_MAX_RETAINED_JOBS = 50
_TERMINAL_JOB_STATUSES = ("done", "error", "cancelled")

# 内容发现往下追几层栏目页：0 = 只看给的这一页。给门户首页时必须往下走一层才是
# 真正列出条目的栏目页；用户直接给栏目页时这层只是多花几次抓取（有节流与白名单兜底）。
_DISCOVER_FOLLOW = 1

# 报告页最多列几个**普通列表**候选供切换重跑（逐个试抓，用第一个能定出正文规则的）。
# **只管普通列表候选，不占命名区域的名额**：区域是「同页全都要」——被名额挤掉的那一个
# 连展开的机会都没有（静默少一个栏目），而它有自己的上限（`analyze.region_candidates(limit=8)`）。
_MAX_CANDIDATES = 5

# 「内容发现到头了」时统一附在最常见死路上的解释：中文政府站大多已改成 JS 渲染列表，
# 静态引擎在这些页面上只能看到站点的骨架（导航/页脚）——要说清楚这是能力边界，
# 不是「我们没配好」，也不是「站点拒绝我们」（后者会走 blocked）。
_STATIC_ONLY_HINT = (
    "本引擎只解析服务端返回的静态 HTML：列表由 JS 动态加载的站点解析不到条目"
    "（这是已知能力边界，需要无头浏览器渲染支持，当前未实现）"
)
_PAGINATION_MAX_PAGES = 10
_DETAIL_CONTAINER_LIMIT = 3
_DETAIL_META_LIMIT = 2
# 每个字段最多几条规则（规则链本身有 fallback，2 条足够：主规则 + 一个备选形状）
_DETAIL_FIELD_RULE_LIMIT = 2
# 字段总数上限：模型可自由命名，但自动导入只采纳分析器认得的标签，避免元信息块刷屏
_DETAIL_FIELD_LIMIT = 12

# 自动加进来的检索型入口的固定名（重名时后面补序号，见 `_unique_entry_name`）
_SEARCH_ENTRY_NAME = "站内检索"

# 「这条样本到底是不是一篇正文」的最低门槛，与 `onboarding.sample_content` 判 body_ok 的
# 口径一致（CJK ≥ 100 且句末 ≥ 2）。**故意比验收标准松**（验收看详情的 min_body_chars，
# 默认 200）：这里只用来在选候选时挡掉页脚/导航这类同签名链接块，
# 真实的正文质量交由后面的试运行判据去裁判。
_SAMPLE_MIN_CJK = 100

# 跨页核对日期候选时比几篇样本（见 `_verify_date_selectors`）：1 篇时模板日期与真日期长得
# 一模一样（这正是那个 bug 的成因）；3 篇既是「全都取到同一个值才算恒定」的判定门槛，
# 也是「每次导入多抓几页」的上限。样本比不满 3 篇就不做判定，只如实报数。
_DATE_CHECK_SAMPLES = 3


@dataclass
class ImportJob:
    """一个导入作业的进程内状态（DTO 见 `app/schemas/source.py`）。"""

    job_id: str
    url: str
    batch_id: str | None = None  # 批量导入时指向父批（聚合视图用）；单条为 None
    status: str = "running"  # queued / running / done / error / cancelled
    phase: str = "inspect"
    phase_text: str = "排队中"
    logs: list[dict[str, Any]] = field(default_factory=list)
    seq: int = 0
    config: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] | None = None
    stage_reports: dict[str, Any] = field(default_factory=dict)
    registered: dict[str, Any] | None = None
    error: str | None = None
    error_kind: str | None = None
    started_at: str = ""
    finished_at: str | None = None
    task: asyncio.Task | None = None


JOBS: dict[str, ImportJob] = {}
# 批量导入的聚合注册表：batch_id → {"entries": [(job_id, url), ...], "created_at": 浮点秒}
# 纯进程内视图，不落库；每个子作业照常写 source_onboarding_run。
BATCHES: dict[str, dict[str, Any]] = {}
_job_queue: asyncio.Queue[tuple[ImportJob, SourceImportRequest]] | None = None
_queue_worker: asyncio.Task | None = None
_last_start: float = 0.0


def _ensure_queue() -> asyncio.Queue[tuple[ImportJob, SourceImportRequest]]:
    """lazy 起排队 worker（模块 import 时还没有事件循环）。"""
    global _job_queue, _queue_worker
    if _job_queue is None:
        _job_queue = asyncio.Queue()
        _queue_worker = asyncio.create_task(_queue_worker_loop())
    return _job_queue


async def _queue_worker_loop() -> None:
    """排队作业的空位调度：出队 → 礼貌间隔 → 仍 queued 才启动（等位期间可被取消）。"""
    assert _job_queue is not None
    while True:
        job, request = await _job_queue.get()
        try:
            await asyncio.sleep(_QUEUE_DRAIN_SECONDS)
            if job.status == "queued":
                job.task = asyncio.create_task(run_import_job(job, request))
        except asyncio.CancelledError:
            raise
        finally:
            _job_queue.task_done()


def _parse_ts(iso: str | None) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else 0.0
    except ValueError:
        return 0.0


def _evict_finished_jobs() -> None:
    """惰性清扫终态作业（新建作业时触发；running/queued 永不清理）。

    ① 终态作业保留 `_JOB_RETENTION_SECONDS`（30 分钟）；② 总量（含在跑的）超过
    `_MAX_RETAINED_JOBS` 时按 finished_at 最老的终态作业先踢。清掉的只是进度快照，
    持久证据在 `source_onboarding_run`。空批（子作业全被清出 JOBS）一并移除。
    """
    now = time.time()
    for job_id, job in list(JOBS.items()):
        if job.status in _TERMINAL_JOB_STATUSES and now - _parse_ts(job.finished_at) > _JOB_RETENTION_SECONDS:
            del JOBS[job_id]
    while len(JOBS) > _MAX_RETAINED_JOBS:
        finished = [j for j in JOBS.values() if j.status in _TERMINAL_JOB_STATUSES]
        if not finished:
            break
        oldest = min(finished, key=lambda j: _parse_ts(j.finished_at))
        del JOBS[oldest.job_id]
    for batch_id, record in list(BATCHES.items()):
        if all(job_id not in JOBS for job_id, _ in record["entries"]):
            del BATCHES[batch_id]


class ImportRejected(RuntimeError):
    """作业没被建立（冷却中 / 并发已满）——路由据此回 429。"""


class ImportUnavailable(RuntimeError):
    """入参本身不合法（地址校验不通过）——路由据此回 400。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(job: ImportJob, message: str, level: str = "info") -> None:
    job.seq += 1
    job.logs.append(
        {
            "seq": job.seq,
            "phase": job.phase,
            "level": level,
            "message": message,
            "created_at": _now(),
        }
    )


def _set_phase(job: ImportJob, phase: str, text: str) -> None:
    job.phase = phase
    job.phase_text = text
    _log(job, text)


def start_import(
    request: SourceImportRequest,
    *,
    batch_id: str | None = None,
    respect_cooldown: bool = True,
) -> ImportJob:
    """校验地址 → 建作业 → 起后台任务。地址不合法抛 `ImportUnavailable`。

    `respect_cooldown=True`（单条页面入口）：并发满 / 冷却中抛 `ImportRejected`（429），
    行为与 Phase 15 完全一致。`False`（批量导入）：不拒绝，并发满了置 queued 排队，
    由 worker 按出队间隔调度开跑。
    """
    global _last_start

    url = (request.url or "").strip()
    if not url:
        raise ImportUnavailable("地址为空")
    try:
        checked = net.check_url(url, allowed_domains=None)
    except net.UnsafeURLError as exc:
        # 前端也会拦一道，但服务端这道才是权威：公网 http/https、非内网、非云元数据地址
        raise ImportUnavailable(str(exc)) from exc

    if respect_cooldown:
        # —— 单条入口的既有闸口，行为不许变（批量走下方排队分支）——
        running = sum(1 for job in JOBS.values() if job.status == "running")
        if running >= _MAX_CONCURRENT_JOBS:
            raise ImportRejected(f"同时最多跑 {_MAX_CONCURRENT_JOBS} 个导入作业，请稍后再试")
        elapsed = time.monotonic() - _last_start
        if _last_start and elapsed < _START_COOLDOWN_SECONDS:
            raise ImportRejected(
                f"导入作业启动过于频繁（{_START_COOLDOWN_SECONDS:.0f} 秒冷却，还需 "
                f"{_START_COOLDOWN_SECONDS - elapsed:.0f} 秒）"
            )
        _last_start = time.monotonic()

    _evict_finished_jobs()
    job = ImportJob(job_id=uuid.uuid4().hex[:12], url=checked, batch_id=batch_id, started_at=_now())
    JOBS[job.job_id] = job

    if not respect_cooldown:
        running_others = sum(
            1 for j in JOBS.values() if j.status == "running" and j.job_id != job.job_id
        )
        if running_others >= _MAX_CONCURRENT_JOBS:
            job.status = "queued"
            _log(job, f"并发已满（{_MAX_CONCURRENT_JOBS}），排队等待空位")
            _ensure_queue().put_nowait((job, request))
            return job

    job.task = asyncio.create_task(run_import_job(job, request))
    return job


def start_batch(request: SourceBatchImportRequest) -> SourceBatchImportCreated:
    """批量导入：逐条校验 → 立即建作业（有空位即跑、满了排队），行序保持请求顺序。

    批内**不拒绝**——地址非法标 `invalid` 并给原因，其余照常入队；单个作业的行为
    与单条导入完全一致（同一套六阶段、同一道注册闸），因此验收通过的来源与
    单条导入一样落为草案，等人工点「启用」。
    """
    batch_id = uuid.uuid4().hex[:12]
    items: list[SourceBatchImportItem] = []
    accepted: list[str] = []
    rejected: list[SourceBatchImportItem] = []
    entries: list[tuple[str, str]] = []
    for url in request.urls:
        item = SourceBatchImportItem(url=url, accepted=False)
        try:
            checked = net.check_url(url, allowed_domains=None)
        except net.UnsafeURLError as exc:
            item.error = str(exc)
            items.append(item)
            rejected.append(item)
            continue
        try:
            job = start_import(
                SourceImportRequest(
                    url=checked,
                    target=request.target,
                    keywords=list(request.keywords),
                    limit=request.limit,
                    pages=request.pages,
                ),
                batch_id=batch_id,
                respect_cooldown=False,
            )
        except ImportUnavailable as exc:  # 兜底：上面已 check 过，正常到不了这儿
            item.error = str(exc)
            items.append(item)
            rejected.append(item)
            continue
        item.job_id = job.job_id
        item.accepted = True
        items.append(item)
        accepted.append(url)
        entries.append((job.job_id, url))
    BATCHES[batch_id] = {"entries": entries, "created_at": time.time()}
    return SourceBatchImportCreated(
        batch_id=batch_id,
        stream=f"/api/sources/import/batch/{batch_id}/stream",
        jobs=items,
        accepted=accepted,
        rejected=rejected,
    )


def list_jobs(limit: int = 50) -> list[ImportJob]:
    """进程内作业列表（倒序快照，新→旧）；顺带触发惰性清扫。"""
    _evict_finished_jobs()
    jobs = sorted(JOBS.values(), key=lambda j: j.started_at, reverse=True)
    return jobs[:limit]


def batch_snapshot(batch_id: str) -> dict[str, Any] | None:
    """一次批量导入的聚合快照（None = 批不存在或已被清扫）。

    `gone` 表示子作业的进度快照已被内存边界清出 JOBS——按终态对待，页面引导去
    `source_onboarding_run` 看持久留痕。
    """
    record = BATCHES.get(batch_id)
    if record is None:
        return None
    queue: list[dict[str, Any]] = []
    pending_position = 0
    all_terminal = True
    running_count = 0
    for job_id, url in record["entries"]:
        job = JOBS.get(job_id)
        if job is None:
            queue.append(
                {
                    "job_id": job_id,
                    "url": url,
                    "status": "done",
                    "phase": "finished",
                    "phase_text": "进度快照已清理（持久记录见「接入记录」）",
                    "error": None,
                    "error_kind": None,
                    "position": None,
                    "gone": True,
                    "started_at": "",
                    "finished_at": None,
                }
            )
            continue
        if job.status == "queued":
            pending_position += 1
            position: int | None = pending_position
        else:
            position = None
        if job.status == "running":
            running_count += 1
        if job.status not in _TERMINAL_JOB_STATUSES:
            all_terminal = False
        queue.append(
            {
                "job_id": job.job_id,
                "url": job.url,
                "status": job.status,
                "phase": job.phase,
                "phase_text": job.phase_text,
                "error": job.error,
                "error_kind": job.error_kind,
                "position": position,
                "gone": False,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
            }
        )
    return {
        "batch_id": batch_id,
        "queue": queue,
        "running_count": running_count,
        "pending_count": pending_position,
        "all_terminal": all_terminal,
    }


def get_job(job_id: str) -> ImportJob | None:
    return JOBS.get(job_id)


def cancel_job(job_id: str) -> bool:
    """取消作业；返回是否真的取消了一个在跑/排队中的作业。

    queued 状态直接置 cancelled——队列里那条由 worker 出队时检查状态、跳过不启动。
    已落库的证据（`source_onboarding_run`）保留。
    """
    job = JOBS.get(job_id)
    if job is None:
        return False
    if job.status == "queued":
        job.status = "cancelled"
        job.phase = "finished"
        job.phase_text = "已取消（排队阶段）"
        job.finished_at = _now()
        _log(job, "作业在排队阶段被取消", "warn")
        return True
    if job.status != "running" or job.task is None:
        return False
    job.task.cancel()
    return True


async def run_import_job(job: ImportJob, request: SourceImportRequest) -> None:
    """作业主体：异常一律收敛成作业状态，不让后台任务静默炸掉。"""
    try:
        await _run(job, request)
    except asyncio.CancelledError:
        _log(job, "作业已取消（已抓到的证据保留在接入记录里）", "warn")
        job.status = "cancelled"
        job.phase = "finished"
        job.phase_text = "已取消"
        job.finished_at = _now()
        raise
    except Exception as exc:  # noqa: BLE001 - 后台任务必须自己收口
        logger.exception("导入作业 %s 异常", job.job_id)
        _log(job, f"作业异常终止：{exc}", "error")
        job.status = "error"
        job.phase = "finished"
        job.phase_text = "异常终止"
        job.error = f"内部错误：{exc}"
        job.error_kind = "failed"
        job.finished_at = _now()


# ---------- 编排 ----------


async def _run(job: ImportJob, request: SourceImportRequest) -> None:
    from app.services.onboarding import (
        discover_content,
        dry_run_ingestion,
        generate_config,
        inspect_source,
        register_source,
    )

    domains = _host_domains(request.url)

    if request.candidate:
        # 候选重跑：只换选择器组合重跑试运行，不重新勘察。`base_config` 是**可选**的——
        # 上一次导入可能在「选候选」那一步就失败了（那时还没有配置骨架），从失败页面上点另一个
        # 候选仍然要能重试；骨架按同一套规则重建（见 `_rebuild_from_candidate`）。
        _log(
            job,
            "候选重跑：沿用上一份配置，只替换选择器组合后重跑试运行"
            if request.base_config
            else "候选重跑：上一次没有留下配置骨架，按同一套规则重建后重跑试运行",
        )
        payload, candidates, error = await _rebuild_from_candidate(job, request, domains)
        if not candidates and request.candidate:
            # 重跑在选候选阶段就失败时，也要把点的那条留在清单里并注明原因——
            # 否则页面的候选切换区会整块消失，用户没法再换一个候选试。
            candidates = [{**request.candidate, "index": 0, "selected": True, "rejected_reason": error}]
        job.candidates = candidates
        if payload is None:
            return _fail(job, error or "候选重跑失败", "failed")
    else:
        _set_phase(job, "inspect", "勘察入口页（校验地址、取回页面、识别页面结构）")
        inspected = await inspect_source(request.url, target=request.target, allowed_domains=domains)
        job.stage_reports["inspect"] = inspected
        if not inspected.get("ok"):
            return _fail_from_stage(job, inspected, "勘察")
        _log(
            job,
            f"勘察通过：{inspected.get('page_title') or request.url}"
            f"（HTTP {inspected.get('http_status')}，{inspected.get('bytes')} 字节）",
        )
        robots = inspected.get("robots") or {}
        if robots:
            _log(job, f"robots.txt：{robots.get('reason') or robots}", "info")

        _set_phase(job, "discover", "发现内容入口（找列出条目的栏目页与列表容器）")
        discovered = await discover_content(
            request.url,
            allowed_domains=domains,
            keywords=request.keywords,
            follow=_DISCOVER_FOLLOW,
        )
        job.stage_reports["discover"] = discovered
        if not discovered.get("ok"):
            return _fail_from_stage(job, discovered, "内容发现")
        summary = discovered.get("summary") or {}
        _log(
            job,
            f"内容发现完成：分析 {summary.get('pages_analyzed')} 页，"
            f"得到 {summary.get('entrypoints')} 个入口候选",
        )
        candidates = _build_candidates(discovered)
        # 先挂到作业上：候选是把「为什么没成」讲清楚的唯一依据，失败路径也必须看得见
        # （逐个试抓的结果写在各自的 rejected_reason 里，页面逐条显示）。
        job.candidates = candidates
        if not candidates:
            return _fail(job, f"内容发现没有给出可用的列表容器候选。{_STATIC_ONLY_HINT}", "failed")

        # 命名区域若自带独立栏目页，先改接栏目页（页内预览块列几条就是几条、且没有翻页）
        await _prefer_column_pages(job, candidates, domains)

        payload, error = await _pick_candidate(job, candidates, domains, request)
        if payload is None:
            _log(job, f"候选逐个试过都不行：{error}", "error")
            return _fail(job, f"没有可用的列表容器候选（{error}）。{_STATIC_ONLY_HINT}", "failed")

        # 顺手看一眼站点有没有**自己的检索页**：有就多配一个检索型入口，关键词从「只在本页
        # 已翻到的条目里比对标题」变成「送进站点检索」。这一步是**真取一页**验证的，
        # 试不出来就不加（也可能页面里根本没说）——绝不猜一个 /search 塞进配置。
        await _maybe_add_search_entry(job, payload, domains, request)

        # 同页命名区域（tab 标签页 / 标题模块）逐个接成独立入口——「只抓政策解读 /
        # 同时抓政府规章+市政府文件」由抓取页现成的「发布机构 = 入口名」筛选完成。
        await _expand_named_regions(job, payload, candidates, domains)

    # 收尾：声明为单页的入口，都用自己页面的证据回问一次「你还有下一页吗」——
    # 能推导出翻页规则的就真取第 2 页验过补进配置，推导不出的把「召回被截断」写进入口 notes。
    await _resolve_single_page_entries(job, payload, domains)

    _mark_selected(job.candidates, payload)

    _set_phase(job, "generate", "生成接入配置（校验选择器语法、域名边界、正文容器）")
    built = generate_config(payload)
    job.config = built.get("config")
    if not built["ok"]:
        problems = built.get("problems") or []
        for problem in problems:
            _log(job, f"配置不可执行：{problem}", "error")
        return _fail(job, "配置未通过可执行性校验：" + "；".join(problems), "failed")
    _log(
        job,
        f"配置校验通过：{built.get('summary') or ''}（指纹 {built.get('fingerprint')}）",
    )

    _set_phase(job, "dry_run", "试运行验收（真实联网抓取，可能需要几十秒）")
    report = await dry_run_ingestion(
        payload,
        limit=request.limit,
        pages=request.pages,
        keywords=request.keywords,
        source_key=payload.get("key"),
    )
    job.report = report
    status = report.get("status")
    if status == "blocked":
        return _fail(
            job,
            report.get("error") or "站点或当前环境不允许抓取（不绕过访问控制）",
            "blocked",
        )
    if report.get("ok"):
        _log(
            job,
            f"验收通过：{report.get('items_found')} 条候选，"
            f"{len(report.get('details') or [])} 篇真实取回正文",
        )
    else:
        failed = [c["name"] for c in (report.get("criteria") or []) if c.get("hard") and not c.get("passed")]
        _log(job, "验收未通过：" + "；".join(failed or ["见验收报告"]), "warn")

    # 注册：**通过**才（按既有状态）注册；**不通过**只在「本来就没有在跑的来源」时留个草案，
    # 免得一次失败的试运行把已经在生产的来源改坏。同一道闸、同一段代码见 register_source。
    _set_phase(job, "register", "登记来源")
    registered = await _register_after_dry_run(
        job,
        payload,
        passed=bool(report.get("ok")),
        register_source=register_source,
        domains=domains,
    )
    job.registered = registered

    job.status = "done"
    job.phase = "finished"
    job.phase_text = "验收通过" if report.get("ok") else "验收未通过"
    job.finished_at = _now()
    if not report.get("ok"):
        job.error = report.get("error") or "验收未通过"
        job.error_kind = "failed"


async def _register_after_dry_run(
    job: ImportJob,
    payload: dict[str, Any],
    *,
    passed: bool,
    register_source,
    domains: list[str],
) -> dict[str, Any] | None:
    """试运行结束后的登记动作。

    规则（每一档都在日志里说明，不悄悄改状态）：

    - 验收通过 + 该 key 已经是 `active` → 按启用注册（同一指纹刚拿到通过验收，闸门不破），
      新配置直接生效，不打断生产；
    - 验收通过 + 其他状态（新建 / 草案 / 已停用）→ 注册为 `draft`，等人工点「启用」；
    - 验收不通过 + 该 key 是 `active` / `disabled` → **不登记**，保留现有配置不动；
    - 验收不通过 + 没有这个 key（或只是草案）→ 落一个草案，方便在列表里看到并重试。
    """
    from app.db import repositories as repo

    key = (payload or {}).get("key") or ""
    note = f"页面导入：{'验收通过' if passed else '验收未通过'}"
    existing: dict[str, Any] | None = None
    if key:
        try:
            existing = await repo.fetch_content_source_by_key(key)
        except Exception as exc:  # noqa: BLE001 - 读不到就当「没有」，register_source 自己会报落库错误
            _log(job, f"读取同名来源失败（按新建处理）：{exc}", "warn")

    previous_status = (existing or {}).get("status")
    if not passed and previous_status in ("active", "disabled"):
        # dry_run_ingestion 会在试运行前把这份**新**配置临时装进注册表，失败时自己摘掉——
        # 于是「本来在跑」的配置就从注册表里消失了。这里必须放回去，否则一次没跑通的导入
        # 会把一个生产中的来源静默停掉（直到重启才恢复）。
        if previous_status == "active" and existing is not None:
            try:
                register_configured(config_from_row(existing), replace=True)
                _log(job, "已恢复原配置（进程内注册表），生产来源不受影响", "warn")
            except Exception as exc:  # noqa: BLE001
                _log(job, f"恢复原配置失败（重启服务会重新装载）：{exc}", "error")
        _log(
            job,
            f"本次验收未通过，且该来源已存在（{previous_status}）→ 不覆盖现有配置，仅保留验收报告",
            "warn",
        )
        return None

    approved = passed and previous_status == "active"
    result = await register_source(
        payload,
        approved=approved,
        change_note=note,
        changed_by="web",
    )
    if not result.get("ok"):
        _log(job, f"登记失败：{result.get('error')}", "error")
        return result

    if previous_status == "disabled":
        # 草案注册会把状态冲成 draft；人工停用的意思要保留，恢复到 disabled
        try:
            await repo.set_content_source_status(result["source_id"], "disabled")
            result["status"] = "disabled"
            _log(job, "该来源此前已停用，登记后恢复为「已停用」（需人工重新启用）", "warn")
        except Exception as exc:  # noqa: BLE001
            _log(job, f"恢复「已停用」状态失败（当前为草案）：{exc}", "warn")

    _log(
        job,
        f"已登记来源「{result.get('label')}」（key={result.get('key')}，"
        f"status={result.get('status')}，version={result.get('version')}）",
    )
    _sync_registry(job, key, str(result.get("status") or ""))
    return result


def _sync_registry(job: ImportJob, key: str, status: str) -> None:
    """让进程内注册表与数据库状态保持一致：**只有 active 才留在注册表里**。

    这条口径与重启后的 `load_configured_from_db()`（只装载 active）完全一致；
    不一致会导致「不重启能选、重启就没了」这种最难排查的错。
    草案虽然可能已经通过验收，但「生产任务启用必须有确认机制」——人工点「启用」那一下
    才 `register_configured`（见 `register_source(approved=True)`）。
    """
    if not key:
        return
    if status == "active":
        return
    if get(key) is not None and unregister(key):
        _log(job, f"来源「{key}」当前不是已启用状态，未装入抓取来源注册表（与重启后的装载口径一致）")


def _fail_from_stage(job: ImportJob, report: dict[str, Any], stage: str) -> None:
    """勘察 / 内容发现阶段的失败：`blocked` 与 `failed` 分开说，绝不写成成功。"""
    kind = report.get("kind")
    if kind not in ("blocked", "failed"):
        kind = "blocked" if report.get("status") == "blocked" else "failed"
    reason = report.get("error") or report.get("reason") or "未知原因"
    _log(job, f"{stage}未通过：{reason}", "warn" if kind == "blocked" else "error")
    _fail(job, f"{stage}未通过：{reason}", kind)


def _fail(job: ImportJob, message: str, kind: str) -> None:
    job.status = "error"
    job.phase = "finished"
    job.phase_text = "受阻（未绕过访问控制）" if kind == "blocked" else "未通过"
    job.error = message
    job.error_kind = kind
    job.finished_at = _now()


# ---------- 配置组装（确定性的那部分） ----------


def _host_domains(url: str) -> list[str]:
    """从用户给的地址推出**来源白名单**（绝不由页面传入）。

    只取「主机本身」与「去掉一级 `www.` 的同一主机」两种写法：`domain_allowed` 认子域，
    所以列 `zj.gov.cn` 就同时覆盖 `www.zj.gov.cn`，而不会顺手把别的站点放进来。
    跨主机的跳转会被 `net.fetch_text` 按白名单拦下并报「不在来源白名单」——
    宁可明确报错，也不为了「跑通」把白名单放宽。
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    out: list[str] = []
    if host:
        out.append(host)
        if host.startswith("www.") and len(host) > 4:
            out.insert(0, host[4:])
    return out


def _key_for(domains: list[str]) -> str:
    """从主机推一个稳定 key：同一站点无论从哪个栏目页导入，key 都一样（重复导入 = 更新）。"""
    host = (domains[-1] if domains else "").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", host).strip("_")[:40]
    return f"src_{slug}" if slug else ""


def _label_for(inspect: dict[str, Any] | None, domains: list[str]) -> str:
    """展示名：页面标题 + 主机（主机后缀保证不同站点不会撞名——`label` 是全局唯一的）。"""
    host = (domains[-1] if domains else "unknown") or "unknown"
    title = ((inspect or {}).get("page_title") or "").strip()
    title = re.sub(r"\s+", " ", title)[:60]
    if title:
        return f"{title}（{host}）"[:200]
    return f"{host} 政策栏目"[:200]


# ---------- 选择器：只留引擎真能执行的 ----------


def _usable_selector(selector: Any, dropped: list[str], field: str) -> str | None:
    """把候选选择器收敛成**引擎可执行**的写法；收敛不了就记进 `dropped` 并返回 None。

    分析器为了「定位准」，会给带 `:nth-of-type(n)` 的选择器（`analyze.py` 里写的是
    「降权但保留」）。但本引擎的选择器子集**不支持伪类**，写进配置会被
    `validate_source_config` 判为不可执行——而**一个**不可用的选择器会让**整份配置**被拒
    （2026-10-15 实测：`www.stats.gov.cn` 的日期选择器是 `… > h2:nth-of-type(1)`，
    于是每次导入都在「配置校验」这一步整体失败，页面看到的就是「不管导什么站都不过」）。

    处置与分析器对列表候选的做法一致（它自己就调 `strip_nth_of_type`）：去掉位置伪类后
    能用就用去掉后的；去不掉就**留空**——宁可少一个元信息字段（验收报告会如实报「命中率
    低」），也不要一份跑不起来的配置。绝不为了「跑通」把选择器换成别的东西。
    """
    raw = str(selector or "").strip()
    if not raw:
        return None
    ok, reason = selector_supported(raw)
    if ok:
        return raw
    stripped = strip_nth_of_type(raw).strip()
    if stripped != raw:
        ok_stripped, reason_stripped = selector_supported(stripped)
        if ok_stripped:
            dropped.append(f"{field}：{raw} → 改用 {stripped}（去掉位置伪类，等价于「该容器内首个同类元素」）")
            return stripped
        reason = reason_stripped
    dropped.append(f"{field}：{raw}（{reason}）")
    return None


def _registered_host(url: str) -> str:
    """只去掉一级 `www.` 的主机名（比较「是不是同一个站」用，不涉及公网后缀表）。

    `www.zj.gov.cn` 与 `zj.gov.cn` 视为同一站（同一套内容的两种写法很常见）；
    `app.www.gov.cn` 与 `www.gov.cn` **不同**——它通常是客户端下载之类的外挂子域。
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _rank_candidates(candidates: list[dict[str, Any]], target_url: str) -> list[dict[str, Any]]:
    """把「样本条目与栏目页同主机」的候选排到前面先试。

    这是**排序，不是过滤**：站内子域放文章的站点确实存在，过滤会误杀真候选；
    页脚／友情链接块则几乎都指向站外（或本站的另一套子域），排在后面更可能一次就试中。
    真正算数的判定是下面「每个候选都真抓一篇样本详情页看」——不靠猜。
    """
    host = _registered_host(target_url)
    order = sorted(
        range(len(candidates)),
        key=lambda i: 0 if _registered_host(str(candidates[i].get("sample_url") or "")) == host else 1,
    )
    return [candidates[i] for i in order]


def _simplify_pagination(raw: dict[str, Any]) -> dict[str, Any]:
    """把 `pagination_candidates` 的输出收敛成 `PaginationConfig` 认得的字段。

    反推不出规则时声明单页（`kind="none"`）——但**「单页」不再是「就这么算了」**：组装
    阶段的 `_resolve_single_page_entries` 会拿入口自己的页面回问一次「你还有下一页吗」，
    推导得出的规则真取第 2 页验过再补进配置（见 `sources/pagination_probe.py`）。

    规则映射本身委托给同一模块，避免「分析报告说能翻、组装配置说不能翻」两处口径分叉。
    """
    rule = rule_from_evidence(raw, max_pages=_PAGINATION_MAX_PAGES)
    return rule if rule else {"kind": "none"}


def _build_candidates(discovered: dict[str, Any]) -> list[dict[str, Any]]:
    """把内容发现报告的入口候选整理成可选清单（命名区域在前，其余按命中条数排序）。

    **这些是候选，不是结论**——每个候选的选择器组合都要靠试运行的真实抓取验证，
    报告页因此把「这次用了哪一个」和「还剩哪几个」都摆出来，点一下就换一个重跑。

    `kind="region"` 的候选来自 `analyze.regions`（命名内容区域：tab 标签页 / 标题邻近
    模块）：名字是页面上实测到的栏目名，带 `scope_selector`+`scope_index` 定位到同页
    多个同签名列表中的某一个。命名区域排前面——它们既有名字（入口名即可读的栏目名）
    又能单独定位；其余普通列表候选维持原样作为备选。

    `_MAX_CANDIDATES` **只管普通列表候选**：命名区域是「同页全都要」，被名额挤掉的那一个
    连展开的机会都没有（静默少一个栏目），所以区域全部入列，上限交给分析器自己管。

    `rejected_reason` 是这条候选**为什么没被采用**（选择器引擎执行不了 / 试抓样本失败）；
    页面把它显示出来，而不是把不适用的候选悄悄丢掉——「没试」和「试了不行」要分得清。
    """
    out: list[dict[str, Any]] = []
    for page in discovered.get("pages") or []:
        page_url = page.get("url") or ""
        for region in page.get("regions") or []:
            scope = region.get("scope_selector")
            inner = region.get("list_selector")
            if not scope or not inner:
                continue
            dropped: list[str] = []
            usable_scope = _usable_selector(scope, dropped, "entrypoints[].scope_selector")
            usable_inner = _usable_selector(inner, dropped, "entrypoints[].list_selector")
            out.append(
                {
                    "index": len(out),
                    "kind": "region",
                    "page_url": page_url,
                    "name": (region.get("name") or f"栏目{len(out) + 1}")[:60],
                    "scope_selector": usable_scope or str(scope),
                    "scope_index": region.get("scope_index"),
                    "list_selector": usable_inner or str(inner),
                    "item_count": region.get("item_count"),
                    "sample_title": region.get("sample_title"),
                    "sample_url": region.get("sample_url"),
                    "column_url": region.get("column_url"),
                    # 页内区域不带翻页（门户页上的模块通常没有独立翻页）；有栏目页时
                    # 走栏目页入口，翻页由栏目页自己的规则覆盖。
                    "pagination": {"kind": "none"},
                    "detail": None,  # 只有被采用的那条会被补上（其余候选留到重跑时再抓样本页）
                    "rejected_reason": "; ".join(dropped) if (usable_scope is None or usable_inner is None) else None,
                }
            )
    list_count = 0
    for row in discovered.get("entrypoint_candidates") or []:
        if list_count >= _MAX_CANDIDATES:
            break
        selector = row.get("list_selector")
        page_url = row.get("page_url")
        if not selector or not page_url:
            continue
        dropped: list[str] = []
        usable = _usable_selector(selector, dropped, "entrypoints[].list_selector")
        out.append(
            {
                "index": len(out),
                "kind": "list",
                "page_url": page_url,
                "name": (row.get("name_suggestion") or f"栏目{len(out) + 1}")[:60],
                "list_selector": usable or str(selector),
                "item_count": row.get("item_count"),
                "sample_title": row.get("sample_title"),
                "sample_url": row.get("sample_url"),
                "pagination": _simplify_pagination(row.get("pagination") or {}),
                "detail": None,  # 只有被采用的那条会被补上（其余候选留到重跑时再抓样本页）
                "rejected_reason": "; ".join(dropped) if usable is None else None,
            }
        )
        list_count += 1
    return out


def _mark_selected(candidates: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    """按最终配置反标「这次用了哪些候选」（重跑时前端据此高亮当前项）。

    区域的 `(url, list_selector)` 可能撞车（多个 tab 面板的 list_selector 都是 `li`），
    所以匹配键带 scope：`(url, list_selector, scope_selector, scope_index)`。
    """
    used = {
        (e.get("url"), e.get("list_selector"), e.get("scope_selector"), e.get("scope_index"))
        for e in (payload or {}).get("entrypoints") or []
    }
    for candidate in candidates:
        candidate["selected"] = (
            candidate.get("page_url"),
            candidate.get("list_selector"),
            candidate.get("scope_selector"),
            candidate.get("scope_index"),
        ) in used


def _meta_selectors(
    meta: dict[str, Any], kind: str, dropped: list[str], *, limit: int = _DETAIL_META_LIMIT
) -> list[str]:
    """取某一类元信息（日期 / 机构 / 文号 / 标题）得分最高、且**引擎执行得了**的几个选择器。

    标题那条要排掉字面量 `title` 标签——它是**整页**标题（通常带站名后缀），
    当成正文标题会污染每一篇；留空则走详情解析器自己的兜底，比塞一个错的好。
    """
    out: list[str] = []
    for item in meta.get(kind) or []:
        selector = item.get("selector")
        if not selector:
            continue
        if kind == "title" and str(selector).strip().lower() == "title":
            continue
        usable = _usable_selector(selector, dropped, f"detail.{kind}_selectors")
        if usable and usable not in out:
            out.append(usable)
        if len(out) >= limit:
            break
    return out


def _field_rules_from_analysis(
    analysis: dict[str, Any], dropped: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """`field_candidates` → `detail.fields` 草案（分组/命名规则见 `rules_from_field_candidates`）。"""
    candidates = analysis.get("field_candidates") or []
    fields = rules_from_field_candidates(
        candidates,
        rule_limit=_DETAIL_FIELD_RULE_LIMIT,
        field_limit=_DETAIL_FIELD_LIMIT,
    )
    if candidates and not fields:
        dropped.append("字段候选没有一条能转成可执行规则（标签/取值形状不完整）")
    return {
        name: [rule.model_dump(exclude_none=True) for rule in rules]
        for name, rules in fields.items()
    }


def _verify_field_rules(
    pages: list[str], fields: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """跨页核对**日期类**字段规则：恒定值的规则当模板元素丢掉。

    与 `_verify_date_selectors` 同一个理由、同一套门槛，只是执行的是 `FieldRule`。
    只核对日期字段：机构/主题分类在一组样本里恒为同一个值是合理的（同一栏目同一单位），
    日期恒为同一天则几乎一定是页脚模板（「本站维护日期」之类）。

    返回 `(保留的 fields, 说明)`；说明进作业日志。
    """
    date_fields = [name for name in fields if name in ("date", "signed_date", "effective_date")]
    if len(pages) < _DATE_CHECK_SAMPLES or not date_fields:
        return fields, []

    roots = [parse_html(html) for html in pages]
    result = dict(fields)
    notes: list[str] = []
    for name in date_fields:
        kept: list[dict[str, Any]] = []
        for raw in fields.get(name) or []:
            try:
                rule = FieldRule.model_validate(raw)
            except Exception:  # noqa: BLE001 - 不合法的规则不参与核对，原样保留由校验兜底
                kept.append(raw)
                continue
            seen = [value for root in roots if (value := apply_rule(root, rule))]
            if len(seen) >= _DATE_CHECK_SAMPLES and len(set(seen)) == 1:
                notes.append(
                    f"字段规则「{name}」（{raw.get('selector') or ''}）在所比 "
                    f"{len(seen)} 篇样本里恒为「{seen[0]}」——与页脚/模板元素无法区分，"
                    "已按「宁可缺失也不写错」丢弃"
                )
                continue
            kept.append(raw)
        if kept:
            result[name] = kept
        else:
            result.pop(name, None)
            notes.append(f"字段「{name}」的候选规则全部被判为模板常量，已整组移除")
    return result, notes


def _verify_date_selectors(pages: list[str], selectors: list[str]) -> tuple[list[str], list[str]]:
    """跨页核对日期候选链：**恒定值**的候选当模板元素丢掉，能解析出日期的提到前面。

    返回 `(保留的选择器, 说明)`；说明会进作业日志与样本指标（`dropped_selectors`），
    因为「少了一个日期候选」是能力边界，不能是「悄悄没了」。

    **为什么非要跨页**：导入来源的列表轮不给日期（`entrypoints[].date_selector` 不填），
    `publish_date` 于是完全由详情页这条候选链决定，而链子是在**一页**上扫出来的
    （`analyze.meta_candidates`：文档顺序、不打分）——页脚/模板里那个固定日期与文章自己的
    日期，在那一页上长得一模一样。跟着栏目翻下去，它就会把同一个日期写进该来源的**每一篇**
    （2026-10-15 用户报的「其他来源进来的日期一直都是 3 月 20 日」就是这个）。错的日期比
    没有日期更糟：年份筛选、时间窗口核对、成果库排序都会跟着错，所以宁可留空。

    **判定门槛故意保守**：只有**每一篇**核对样本都取到同一个值才算恒定（`len(pages) ==
    _DATE_CHECK_SAMPLES` 篇全中）。两篇相同不算——同一个栏目同一天发两条通知很常见，
    拿它当模板元素会把**正确**的日期也丢掉。「这一轮判不出来」比「判错了」好，判定不了的
    情形留给试运行（`dry_run_ingestion` 会在日期值恒定时明确提示人工核对）。
    """
    if len(pages) < _DATE_CHECK_SAMPLES or not selectors:
        return list(selectors), []

    rows = [date_texts_by_selector(html, selectors) for html in pages]
    kept: list[str] = []
    notes: list[str] = []
    for selector in selectors:
        seen = [row[selector] for row in rows if row.get(selector)]
        if len(seen) >= _DATE_CHECK_SAMPLES and len(set(seen)) == 1:
            notes.append(
                f"日期候选「{selector}」在所比 {len(seen)} 篇样本里恒为「{seen[0]}」——"
                "与页脚/模板元素无法区分，已按「宁可缺失也不写错」丢弃；"
                "若确认它就是该站的发布日期，请把该选择器手工写回 detail.date_selectors"
            )
            continue
        kept.append(selector)

    # 能解析出真实日期的排前面：`_text_of` 只认「第一个取到非空文本」的候选，
    # 一个取得到文本却 `parse_date` 不了的候选会挡住后面的好候选。
    parseable = [s for s in kept if any(parse_date(row.get(s)) for row in rows)]
    if parseable and parseable != kept:
        notes.append("日期候选链已重排（能解析出真实日期的候选提到前面）：" + "、".join(parseable))
    return parseable + [s for s in kept if s not in parseable], notes


async def _sibling_detail_pages(
    job: ImportJob, candidate: dict[str, Any], domains: list[str], *, limit: int
) -> list[str]:
    """回候选自己的列表页再挑几篇同栏目条目，抓回来做跨页核对（**尽力而为**）。

    取不到就少比几篇：只记日志、不失败——核对样本少不代表这次导入有问题，
    但日志里必须看得见「只比了几篇」，否则「已核对」会被当成本该如此。

    复用候选自己的 `page_url` + `list_selector`（不新增任何字段）：这两样正是正式抓取
    列表时用的同一对，所以这里挑到的条目就是那份配置将来会抓到的条目。
    """
    pages: list[str] = []
    page_url = str(candidate.get("page_url") or "")
    list_selector = str(candidate.get("list_selector") or "")
    if not page_url or not list_selector:
        return pages
    try:
        listing = await net.fetch_text(page_url, allowed_domains=domains)
        entry = EntrypointConfig(
            name=str(candidate.get("name") or "样本"),
            url=listing.url,
            list_selector=list_selector,
            link_selector="a",
            scope_selector=candidate.get("scope_selector") or None,
            scope_index=candidate.get("scope_index"),
        )
        items = parse_list_page(listing.text, entry, listing.url, limit=limit + 2)
    except Exception as exc:  # noqa: BLE001 - 核对取不到只是少比几篇，不该让整次导入失败
        _log(job, f"跨页核对日期候选：列表页取不到（{exc}），本次只比 1 篇样本", "warn")
        return pages

    for item in items:
        if len(pages) >= limit:
            break
        url = str(item.get("url") or "")
        if not url or url == candidate.get("sample_url"):
            continue  # 这一篇的 HTML 调用方已经有了，不重复抓
        try:
            fetched = await net.fetch_text(url, allowed_domains=domains)
        except (net.FetchError, net.UnsafeURLError) as exc:
            _log(job, f"跨页核对日期候选：样本条目「{url}」取回失败（{exc}）", "warn")
            continue
        pages.append(fetched.text)
    return pages


def _detail_from_analysis(analysis: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """样本详情页的结构分析 → `DetailConfig` 草案（每个字段都是候选里的最高分）。

    返回 `(detail, dropped)`：`dropped` 是**被丢掉或改写过**的选择器说明，调用方要把它写进
    作业日志——少一个日期字段是能力边界，但不能是「悄悄没了」。
    """
    containers: list[str] = []
    dropped: list[str] = []
    for item in analysis.get("detail_candidates") or []:
        usable = _usable_selector(item.get("selector"), dropped, "detail.container_selectors")
        if usable and usable not in containers:
            containers.append(usable)
        if len(containers) >= _DETAIL_CONTAINER_LIMIT:
            break
    meta = analysis.get("meta_candidates") or {}
    settings = get_settings()
    detail: dict[str, Any] = {
        "container_selectors": containers,
        "title_selectors": _meta_selectors(meta, "title", dropped),
        # 旧选择器链保留作 fallback：`parse_detail_page` 先跑字段规则，取不到再走这里。
        # 两套都留着，老站点的行为与命中率不变，新规则只做增量。
        "date_selectors": _meta_selectors(meta, "date", dropped),
        "agency_selectors": _meta_selectors(meta, "agency", dropped),
        "doc_no_selectors": _meta_selectors(meta, "doc_no", dropped),
    }
    fields = _field_rules_from_analysis(analysis, dropped)
    if fields:
        detail["fields"] = fields
    if settings.pipeline_min_body_chars > 0:
        # 与检索阶段的正文预检同口径（预检把不达标的标「不建议入库」）
        detail["min_body_chars"] = int(settings.pipeline_min_body_chars)
    return detail, dropped


async def _sample_detail_analysis(
    job: ImportJob, candidate: dict[str, Any], domains: list[str]
) -> tuple[dict[str, Any] | None, list[str], str | None]:
    """抓一个候选自己那条样本详情页：结构分析 → 正文规则草案 + 跨页核对日期候选。

    返回 `(detail, dropped, error)`：`detail` 是**已经核过日期候选**的 `DetailConfig` 草案，
    调用方直接拿去用、并用同一个 `dropped` 写日志。为什么把 `detail` 从里面交出来而不是让
    调用方自己再 `_detail_from_analysis(analysis)` 一次：那样会把跨页核对的结果丢掉
    （核对只发生在这里），拿到的日期候选就还是原来那条「第一页上看着没毛病」的链子。

    取不到就**如实报错**，不拿列表页的候选凑数——列表页的「正文容器」是整块列表，
    塞进配置会把导航和条目全当正文。

    一次抓取两用（分析 + 量指标）：`analyze_structure` 支持直接喂 HTML，所以不必抓两遍。
    指标门槛与 `onboarding.sample_content` 的 `body_ok` 一致（CJK ≥ 100 且句末 ≥ 2）——
    这是「这一页到底是不是一篇正文」的最低门槛，用来在**选候选**时就挡住页脚/导航/下载页，
    而不是等整份配置试运行到「正文完整性」判据才失败。
    """
    # 局部导入：fulltext_clean 经 schemas 反向依赖 sources，顶层导入会成环（见 html_source）
    from app.pipeline.fulltext_clean import body_metrics, strip_zero_width
    from app.services.onboarding import _MIN_SENT_ENDERS, analyze_structure

    sample_url = candidate.get("sample_url")
    title = str(candidate.get("sample_title") or sample_url or "")[:40]
    try:
        fetched = await net.fetch_text(sample_url, allowed_domains=domains)
    except (net.FetchError, net.UnsafeURLError) as exc:
        return None, [], f"样本条目「{title}」取回失败：{exc}（{sample_url}）"
    analysis = await analyze_structure(html=fetched.text, url=fetched.url, listed_as="sample_detail")
    if not (analysis.get("detail_candidates") or []):
        return None, [], f"样本条目「{title}」页面里没有找到正文容器候选（{sample_url}）"

    detail, dropped = _detail_from_analysis(analysis)
    parsed = parse_detail_page(strip_zero_width(fetched.text), DetailConfig(**detail), fetched.url)
    cjk, enders = body_metrics(parsed.body)
    _log(
        job,
        f"样本条目「{title}」：正文容器 {parsed.container_selector or '（未命中）'}，"
        f"中文 {cjk} 字、句末 {enders}",
    )
    if cjk < _SAMPLE_MIN_CJK or enders < _MIN_SENT_ENDERS:
        # 这一条候选不是正文（页脚/导航/下载页）：不合格就地退回，**不做**跨页核对
        # ——多抓的那两篇是给「会采用的候选」花的钱，被否掉的候选一分都不该多花。
        return (
            None,
            dropped,
            f"样本条目「{title}」不像一篇正文（中文 {cjk} 字、句末 {enders}）"
            f"——该候选多半是页脚/导航/下载页这类同签名链接块，不是内容列表",
        )

    # 日期候选跨页核对：第一页就是刚抓到的这一篇，再回候选自己的列表页挑同栏目条目做对照
    siblings = await _sibling_detail_pages(job, candidate, domains, limit=_DATE_CHECK_SAMPLES - 1)
    pages = [fetched.text, *siblings]
    detail["date_selectors"], date_notes = _verify_date_selectors(
        pages, detail.get("date_selectors") or []
    )
    dropped.extend(date_notes)
    if detail.get("fields"):
        detail["fields"], field_notes = _verify_field_rules(pages, detail["fields"])
        dropped.extend(field_notes)
    _log(
        job,
        f"日期候选跨页核对：比了 {1 + len(siblings)} 篇样本"
        + (
            f"，有 {len(date_notes)} 条候选被丢弃或重排（原因见「正文规则」那条）"
            if date_notes
            else "，未发现恒定值"
        ),
        "warn" if date_notes else "info",
    )
    analysis["sample_metrics"] = {
        "cjk": cjk,
        "enders": enders,
        "container_selector": parsed.container_selector,
        "dropped_selectors": dropped,
        "date_samples": 1 + len(siblings),
    }
    return detail, dropped, None


def _log_detail_rule(job: ImportJob, detail: dict[str, Any], dropped: list[str]) -> None:
    """把生成出来的正文规则写进日志（含被丢弃/改写的选择器，不悄悄丢）。"""
    _log(
        job,
        "正文规则：容器 "
        + ("、".join(detail.get("container_selectors") or []) or "（无）")
        + f"；元信息 标题 {len(detail.get('title_selectors') or [])} 条／"
        + f"日期 {len(detail.get('date_selectors') or [])} 条／"
        + f"机构 {len(detail.get('agency_selectors') or [])} 条／"
        + f"文号 {len(detail.get('doc_no_selectors') or [])} 条"
        + (
            "；语义字段 "
            + "、".join(
                f"{name}({len(rules)} 条)"
                for name, rules in (detail.get("fields") or {}).items()
            )
            if detail.get("fields")
            else ""
        ),
    )
    if dropped:
        _log(job, "以下候选选择器引擎执行不了，已按其降级写法处理或留空：" + "；".join(dropped), "warn")


async def _build_payload_from_candidate(
    job: ImportJob,
    candidate: dict[str, Any],
    domains: list[str],
    request: SourceImportRequest,
) -> tuple[dict[str, Any] | None, str | None]:
    """候选 → 完整配置草案（抓这个候选自己的样本详情页来定正文规则）。"""
    if not candidate.get("sample_url"):
        return None, "该候选没有样本条目链接，取不到样本详情页"

    detail, dropped, error = await _sample_detail_analysis(job, candidate, domains)
    if detail is None:
        return None, error
    candidate["detail"] = detail
    _log_detail_rule(job, detail, dropped)

    key = _key_for(domains)
    if not key:
        return None, "无法从地址推出稳定的来源标识（主机名不合法）"
    label = _label_for(job.stage_reports.get("inspect"), domains)
    payload = _base_payload(key, label, domains, request)
    payload["entrypoints"] = [_entrypoint(candidate, 0)]
    payload["detail"] = detail
    return payload, None


async def _pick_candidate(
    job: ImportJob,
    candidates: list[dict[str, Any]],
    domains: list[str],
    request: SourceImportRequest,
) -> tuple[dict[str, Any] | None, str | None]:
    """按「同主机优先」的顺序逐个候选**真试一篇**，取第一个能定出正文规则的。

    为什么不「取最高分就定」：分析器给的是**结构候选**——列表容器得分高只说明「这里有一堆
    同签名的兄弟节点」，不说明它真是内容列表。页脚／导航／友情链接块完全符合这个形状，而在
    列表由 JS 渲染的页面上，它们往往就是**唯一**候选（2026-10-15 实测：
    `www.gov.cn/zhengce/zuixin/` 的最高分候选是 `div.footer_r.fr > div.fl`，
    样本条目是「国务院客户端」的下载页 `app.www.gov.cn/download/Chinese.html`，
    于是整次导入死在「样本详情页取回失败」——用户看到的就是「什么都取不到」）。

    所以这里改成事实判定：每个候选都去抓它自己的样本详情页，**能解析出正文容器的才算数**，
    其余把原因逐条记进 `rejected_reason`（页面会把「没试」和「试了不行」分开显示）。
    """
    reasons: list[str] = []
    order = _rank_candidates(candidates, request.url)
    _log(
        job,
        f"逐个试抓候选自己的样本条目（共 {len(order)} 个，样本与栏目页同主机的排前面）："
        "能解析出正文的才采用——这一步每个候选都要真抓一页，会慢一些",
    )
    for candidate in order:
        if candidate.get("rejected_reason"):
            reasons.append(f"{candidate.get('name')}：{candidate['rejected_reason']}")
            continue
        _log(job, f"试抓候选「{candidate.get('name')}」（{candidate.get('list_selector')}）的样本条目")
        payload, error = await _build_payload_from_candidate(job, candidate, domains, request)
        if payload is not None:
            return payload, None
        candidate["rejected_reason"] = error or "试抓样本详情页失败"
        reasons.append(f"{candidate.get('name')}（{candidate.get('list_selector')}）：{error}")
    if not reasons:
        reasons.append("内容发现没有给出任何列表容器候选")
    # 逐条原因都要留在候选列表里（页面按候选显示），这里的汇总只给状态行用，够看就行
    summary = "；".join(reasons)
    return None, summary if len(summary) <= 400 else summary[:400] + "……"


async def _rebuild_from_candidate(
    job: ImportJob, request: SourceImportRequest, domains: list[str]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    """候选重跑：沿用上一份配置，只换入口与正文规则。

    候选里带了 `detail` 就直接用（省一次抓取）；没带就按它的样本条目重新分析一次。
    重跑**不重新勘察**——勘察报告已经在 `inspect` 阶段落过 `source_onboarding_run`，
    没必要为换一个选择器再抓一遍整站。

    `base_config` 缺省时（上一次导入在「选候选」阶段就失败，没留下骨架）按 `_base_payload`
    重建，边界仍然是**服务端从地址推出**的 `domains`；来源名只能用主机兜底（那次勘察的标题
    早丢了），不是错，只是不如第一次好看。

    **替换范围**（2026-10-16 收窄，原来是无条件整份重建——会静默丢掉已展开的其它命名区域）：
    点的是**命名区域**候选时，只替换「它自己那一个位置」的入口，其它栏目（含**其它页面的**
    命名区域）与站内检索入口原样保留——纠错不该有超出目标的破坏面。点的是**普通列表**候选时
    维持原语义：整份入口集以它为准重建（保留站内检索）。

    保留条件里**不设「同页」**（2026-10-16 二次收窄）：命名区域可能分布在门户首页与它链接到
    的栏目页两张页面上，早先的「同页」限制会让两页的栏目在重跑时互相替换——于是「这几个栏目
    我都要」永远做不到，点一个丢一个。重跑是**替换**，不是清除。
    """
    base = copy.deepcopy(request.base_config or {})
    candidate = dict(request.candidate or {})
    if not candidate.get("page_url") or not candidate.get("list_selector"):
        return None, [], "重跑需要给出候选的 page_url 与 list_selector"

    if not base:
        key = _key_for(domains)
        if not key:
            return None, [], "无法从地址推出稳定的来源标识（主机名不合法）"
        base = _base_payload(
            key,
            _label_for(job.stage_reports.get("inspect"), domains),
            domains,
            request,
        )

    base["scope"] = {**(base.get("scope") or {}), "allowed_domains": domains}
    entry = _entrypoint(candidate, 0)
    existing = [row for row in (base.get("entrypoints") or []) if isinstance(row, dict)]
    # 检索型入口与「选了哪个栏目候选」无关：重跑只换栏目入口，检索入口原样留着，
    # 否则用户每换一次候选就要丢一次站内检索能力（还得靠人工改配置加回来）。
    kept_search = [row for row in existing if isinstance(row.get("search"), dict)]
    requested_scope = str(candidate.get("scope_selector") or "").strip()
    # 区域候选有两种写法，都是「一个栏目」：页内区域（带 scope 定位）与**已改接独立栏目页**
    # 的区域（`_prefer_column_pages` 把 scope 清空了，只剩 `column_page` 标记）。两种都要按
    # 「只替换它自己」处理，否则后者会掉进整份重建、把别处的栏目一起清掉。
    is_region = bool(requested_scope) or bool(candidate.get("column_page"))
    kept_entries: list[dict[str, Any]] = []
    if is_region:
        page_url = candidate.get("page_url")
        requested_index = candidate.get("scope_index")
        requested_list = candidate.get("list_selector")

        def _same_slot(row: dict[str, Any]) -> bool:
            if row.get("url") != page_url:
                return False
            if requested_scope:
                # 多个 tab 面板**共享同一个 scope 选择器**，靠 scope_index 区分，
                # 所以匹配键必须是 (url, scope_selector, scope_index) 三元组。
                return (
                    str(row.get("scope_selector") or "").strip() == requested_scope
                    and row.get("scope_index") == requested_index
                )
            # 改接过的区域没有 scope 定位，一个栏目页上就一个列表容器：地址 + 选择器即位置。
            return row.get("list_selector") == requested_list

        search_ids = {id(row) for row in kept_search}
        kept_entries = [
            row for row in existing if id(row) not in search_ids and not _same_slot(row)
        ]
        replaced = sum(1 for row in existing if id(row) not in search_ids and _same_slot(row))
        _log(
            job,
            f"区域重跑：替换同位置的入口 {replaced} 个，"
            f"保留其它命名区域/栏目 {len(kept_entries)} 个（跨页的也在内）"
            if kept_entries
            else "区域重跑：上一份配置没有其它命名区域可保留",
        )
    entry_name = str(entry.get("name") or "").strip()
    if entry_name:
        entry["name"] = _unique_entry_name(entry_name, [*kept_entries, *kept_search])
    base["entrypoints"] = [entry, *kept_entries, *kept_search]

    detail = candidate.get("detail")
    if not detail:
        if not candidate.get("sample_url"):
            return None, [], "该候选没有样本条目链接，无法生成正文规则"
        detail, dropped, error = await _sample_detail_analysis(job, candidate, domains)
        if detail is None:
            return None, [], error
        _log_detail_rule(job, detail, dropped)
    candidate["detail"] = detail
    base["detail"] = {**(base.get("detail") or {}), **detail}

    candidates = [candidate]
    candidates[0]["index"] = 0
    candidates[0].setdefault("item_count", candidate.get("item_count"))
    _log(job, f"重跑候选：{entry['name']}（{entry['list_selector']}）")
    return base, candidates, None


def _base_payload(
    key: str, label: str, domains: list[str], request: SourceImportRequest
) -> dict[str, Any]:
    """配置骨架：来源级字段 + 边界。入口与正文规则由候选补齐。"""
    settings = get_settings()
    return {
        "key": key,
        "label": label,
        "level": "other",
        "organization": None,
        "source_type": "government_portal",
        "engine": "html_list",
        "scope": {
            "allowed_domains": domains,
            "start_urls": [request.url],
            "max_items_per_entry": max(1, int(settings.pipeline_max_policies)),
        },
        "entrypoints": [],
        "detail": {},
        "notes": (
            "由页面「导入网址」入口生成（确定性组装：按勘察报告的候选排序，逐个试抓样本条目核实正文"
            f"后采用）；采集目标：{request.target or '未指定'}"
        ),
    }


def _entrypoint(candidate: dict[str, Any], index: int) -> dict[str, Any]:
    """候选 → `EntrypointConfig`。

    入口名同时是「发布机构」筛选的匹配值（见 `EntrypointConfig` 的注释），所以名字要
    可读；翻页规则来自勘察阶段从页码链接反推的结果，反推不出就声明 `none`（一页），
    由试运行的「翻页可用性」判据如实报告。命名区域候选带 `scope_selector`/`scope_index`
    （同页多个同签名列表的定位），一并写进入口配置。
    """
    name = (candidate.get("name") or f"栏目{index + 1}").strip()[:60] or f"栏目{index + 1}"
    entry: dict[str, Any] = {
        "name": name,
        "url": candidate["page_url"],
        "list_selector": candidate["list_selector"],
        "link_selector": "a",
        "pagination": _simplify_pagination(candidate.get("pagination") or {}),
    }
    scope = str(candidate.get("scope_selector") or "").strip()
    if scope:
        entry["scope_selector"] = scope
        scope_index = candidate.get("scope_index")
        if scope_index is not None:
            entry["scope_index"] = int(scope_index)
    return entry


# ---------- 命名区域展开（同页多列表 → 多入口） ----------


async def _probe_region_items(
    job: ImportJob, candidate: dict[str, Any], domains: list[str]
) -> list[dict[str, Any]]:
    """真取区域所在页，按 scope 组合解析一次——验证这个区域定位**真的**出条目。

    区域的 scope/list 选择器是分析器在**这一页**上归纳的，展开成入口前必须用同一对
    选择器真解析一遍（与 `_pick_candidate` 逐个试抓样本同一口径）：解析不出就如实
    记 `rejected_reason`，不静默丢弃，也不把没验证的选择器写进配置。
    """
    page_url = str(candidate.get("page_url") or "")
    if not page_url:
        return []
    try:
        fetched = await net.fetch_text(page_url, allowed_domains=domains)
    except (net.FetchError, net.UnsafeURLError) as exc:
        _log(job, f"同页展开验证：{page_url} 取回失败（{exc}）", "warn")
        return []
    entry = EntrypointConfig(
        name=str(candidate.get("name") or "区域"),
        url=fetched.url,
        list_selector=str(candidate.get("list_selector") or ""),
        link_selector="a",
        scope_selector=candidate.get("scope_selector") or None,
        scope_index=candidate.get("scope_index"),
    )
    try:
        return parse_list_page(fetched.text, entry, fetched.url, limit=5)
    except Exception as exc:  # noqa: BLE001 - 解析异常 = 该区域不可用，不该让整次导入失败
        _log(job, f"同页展开验证：区域「{candidate.get('name')}」解析异常（{exc}）", "warn")
        return []


async def _expand_named_regions(
    job: ImportJob,
    payload: dict[str, Any],
    candidates: list[dict[str, Any]],
    domains: list[str],
) -> None:
    """把入选候选所在页上的其它命名区域接成独立入口（共享同一套正文规则）。

    `_pick_candidate` 只采用一个候选（由它的样本详情页定出正文规则）；门户页上并排的
    命名区域（tab 标签页 / 标题模块）与它共用同一套详情页模板，逐一如实验证后各成一个
    入口。正文规则留在 payload 级共享，不逐区域重复抓样本详情页（那是试运行判据 3 的活）。

    只展开**同一页**上的命名区域：不同页的列表候选走的是「翻栏目页」的老路（各自有
    独立候选，由人选）。展开失败的区域把原因写进 `rejected_reason`——页面候选清单里
    「没试」和「试了不行」要分得清。
    """
    used = {
        (e.get("url"), e.get("list_selector"), e.get("scope_selector"), e.get("scope_index"))
        for e in (payload or {}).get("entrypoints") or []
    }
    chosen = next(
        (
            c
            for c in candidates
            if (
                c.get("page_url"),
                c.get("list_selector"),
                c.get("scope_selector"),
                c.get("scope_index"),
            )
            in used
        ),
        None,
    )
    if chosen is None:
        return
    page_url = chosen.get("page_url")
    # 归组键取**区域所在的门户页**：入选候选可能已被 `_prefer_column_pages` 改接到独立栏目页
    # （`region_page_url` 记住原门户页），同一门户上并列的其它 tab 仍要从原页面展开。
    group = chosen.get("region_page_url") or page_url
    existing = list(payload.get("entrypoints") or [])
    added = 0
    for candidate in candidates:
        if (candidate.get("region_page_url") or candidate.get("page_url")) != group:
            continue  # 只展开同页命名区域；普通列表候选维持单选备选
        if not candidate.get("scope_selector") and not candidate.get("column_page"):
            # 普通列表候选维持单选备选（它们各有独立候选，由人挑）；
            # 但**改接过栏目页的区域仍是区域**——只是不再需要 scope 定位，照样要展开。
            continue
        if candidate.get("rejected_reason"):
            continue
        key = (
            candidate.get("page_url"),
            candidate.get("list_selector"),
            candidate.get("scope_selector"),
            candidate.get("scope_index"),
        )
        if key in used:
            continue  # 已入选（含被选中的那条本身）
        items = await _probe_region_items(job, candidate, domains)
        if not items:
            candidate["rejected_reason"] = "同页展开验证：真取一页后该区域解析不出条目"
            continue
        entry = _entrypoint(candidate, len(existing))
        entry["name"] = _unique_entry_name(entry["name"], existing)
        existing.append(entry)
        used.add(key)
        added += 1
        _log(
            job,
            f"同页命名区域「{entry['name']}」已接成独立入口"
            f"（样本 {len(items)} 条可解析）：抓取时可按栏目单独勾选",
        )
    if added:
        payload["entrypoints"] = existing
        _log(job, f"共接入 {added + 1} 个命名区域入口（含入选的那个）；正文规则共用一份")


# ---------- 翻页：区域优先接栏目页 + 单页声明必须由证据支撑 ----------


async def _prefer_column_pages(
    job: ImportJob, candidates: list[dict[str, Any]], domains: list[str]
) -> None:
    """命名区域自带独立栏目页时，**就地**把候选改接栏目页（通用规则，不针对任何站点）。

    页内区域只是门户页上的一个**预览块**：列几条就是几条，且没有翻页（见 `_build_candidates`
    里区域候选的 `pagination: none`）。而 tab 自己链接到的栏目页是**完整列表**——有自己的
    列表容器和翻页规则。接栏目页，召回才不被预览块的长度封顶；这正是「抓到 N 篇就不动了」
    的一条成因（2026-10-16 用户实例：门户 tab 区域入口只取到 9 条，之后再无新增）。

    栏目页要**真取一页、真解析出条目**才采用（与选候选同一门槛）。取不到或解析不出就原样
    保留页内区域并写明原因——「试了不行」和「没试」要分得清。

    就地替换而非新增候选：候选清单是「供人切换重跑」的短清单（有数量上限），多插一条会把
    真正可用的候选挤出去。替换后 `kind` 仍是 `region`、入口名不变（它仍然是那个 tab 栏目），
    只是不再需要 `scope_*` 定位——解析的是栏目页，而不是门户页上的那一个模块。
    """
    upgraded = 0
    for candidate in candidates:
        if candidate.get("kind") != "region" or candidate.get("rejected_reason"):
            continue
        column_url = str(candidate.get("column_url") or "").strip()
        if not column_url or column_url.rstrip("/") == str(candidate.get("page_url") or "").rstrip("/"):
            continue
        if not domain_allowed((urlsplit(column_url).hostname or "").lower(), domains):
            continue
        try:
            fetched = await net.fetch_text(column_url, allowed_domains=domains)
        except (net.FetchError, net.UnsafeURLError) as exc:
            _log(job, f"区域「{candidate.get('name')}」的栏目页取回失败，仍接页内区域（{exc}）", "warn")
            continue
        analysis = analyze_page(fetched.text, fetched.url, listed_as="column")
        top = next((row for row in analysis.get("list_candidates") or [] if row.get("selector")), None)
        if top is None:
            _log(job, f"区域「{candidate.get('name')}」的栏目页没有可用的列表容器，仍接页内区域", "warn")
            continue
        try:
            items = parse_list_page(
                fetched.text,
                EntrypointConfig(
                    name=str(candidate.get("name") or "栏目"),
                    url=fetched.url,
                    list_selector=str(top["selector"]),
                    link_selector="a",
                ),
                fetched.url,
                limit=5,
            )
        except Exception as exc:  # noqa: BLE001 - 解析异常 = 该栏目页不可用，保留页内区域
            _log(job, f"区域「{candidate.get('name')}」的栏目页解析异常，仍接页内区域（{exc}）", "warn")
            continue
        if not items:
            _log(job, f"区域「{candidate.get('name')}」的栏目页解析不出条目，仍接页内区域", "warn")
            continue
        first = items[0]
        # region_page_url 取**改接前**的门户页：同一门户上并列的其它 tab 仍要从原页面展开
        # （见 `_expand_named_regions` 的归组口径）
        candidate.update(
            {
                "region_page_url": candidate.get("page_url"),
                "page_url": fetched.url,
                "column_page": True,
                "list_selector": str(top["selector"]),
                "scope_selector": None,
                "scope_index": None,
                "item_count": len(items),
                "sample_title": first.get("title"),
                "sample_url": first.get("url"),
                "pagination": _simplify_pagination(analysis.get("pagination") or {}),
                "detail": None,  # 换了一页，样本正文规则要在试抓那一步重新定
            }
        )
        upgraded += 1
        _log(
            job,
            f"命名区域「{candidate.get('name')}」改用其独立栏目页（样本 {len(items)} 条可解析）："
            "页内预览块只列几条，栏目页才是完整列表（后续仍会用它自己的翻页规则核对）",
        )
    if upgraded:
        _log(job, f"{upgraded} 个命名区域改接独立栏目页；其余区域仍按页内模块接入")


async def _resolve_single_page_entries(
    job: ImportJob, payload: dict[str, Any], domains: list[str]
) -> None:
    """给「声明为单页」的入口回问一次站点：**你还有下一页吗**（通用，逐入口）。

    背景：`kind="none"` 过去是默认值——反推不出翻页规则就静默写单页，而验收只对**声明了**
    翻页的入口发问，没声明就记为「未测试」放行。于是「站点其实有很多页、抓取永远只取第 1
    页」全程无人报警，用户看到的是「抓到 N 篇就不动、显示无新增」，还分不清是站点没内容
    还是我们没翻页。

    这里把口径翻过来：单页声明必须由**站点自己的页面**证明。证据来自页面里的页码链接与
    「下一页」链接；推导得出的规则**真取第 2 页验过**才写进配置，验不过或推导不出就如实
    写进入口 `notes`（配置里、报告里都看得见），绝不硬塞一个可能被忽略的参数。

    检索型入口跳过：它们要带关键词才有第 2 页（见 `build_search_url`），翻页由试运行的
    「站内检索入口」判据覆盖。
    """
    resolved = 0
    truncated: list[str] = []
    for entry in payload.get("entrypoints") or []:
        if entry.get("search"):
            continue
        pagination = entry.get("pagination") or {}
        if str(pagination.get("kind") or "none") != "none":
            continue
        try:
            model = EntrypointConfig.model_validate(
                {**entry, "pagination": entry.get("pagination") or {"kind": "none"}}
            )
        except Exception as exc:  # noqa: BLE001 - 入口本身不合法时交给 generate_config 报错
            _log(job, f"入口「{entry.get('name')}」翻页核对跳过（{exc}）", "warn")
            continue
        probe = await probe_single_page_entry(
            model, allowed_domains=domains, max_pages=_PAGINATION_MAX_PAGES
        )
        if probe.verified and probe.rule:
            entry["pagination"] = probe.rule
            resolved += 1
            _log(job, f"入口「{entry.get('name')}」：{probe.detail}")
        elif probe.truncated:
            entry["notes"] = f"{probe.detail}（本入口实际只取第 1 页）"
            truncated.append(f"{entry.get('name')}：{probe.detail}")
            _log(job, f"入口「{entry.get('name')}」{probe.detail}", "warn")
    if truncated:
        _log(
            job,
            f"有 {len(truncated)} 个入口的单页声明未能提升为翻页（召回被截断在第 1 页）："
            + "；".join(truncated),
            "warn",
        )
    if resolved:
        _log(job, f"{resolved} 个单页入口经页面证据核对后补上了翻页规则（已真取第 2 页验证）")


# ---------- 检索型入口（站内检索） ----------


def _search_form_candidates(job: ImportJob) -> list[dict[str, Any]]:
    """勘察页与内容发现各页里看到的检索表单（按 (action, field) 去重的**候选**）。

    这些只是「页面上有个检索框，参数名是这个」，是不是真能出条目要试过才知道
    （见 `_maybe_add_search_entry`）。
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    pages: list[dict[str, Any]] = []
    inspect = job.stage_reports.get("inspect") or {}
    if inspect.get("search_forms"):
        pages.append({"search_forms": inspect["search_forms"]})
    pages.extend((job.stage_reports.get("discover") or {}).get("pages") or [])
    for page in pages:
        for form in page.get("search_forms") or []:
            field = str(form.get("field") or "").strip()
            action = str(form.get("action") or "").strip()
            if not field or (action, field) in seen:
                continue
            seen.add((action, field))
            out.append(form)
    return out


def _unique_entry_name(base: str, existing: list[dict[str, Any]]) -> str:
    names = {str(row.get("name") or "").strip() for row in existing}
    if base not in names:
        return base
    for suffix in range(2, 10):
        if f"{base}{suffix}" not in names:
            return f"{base}{suffix}"
    return base


async def _probe_search_entry(
    job: ImportJob, form: dict[str, Any], domains: list[str], probe_keyword: str
) -> tuple[dict[str, Any] | None, int]:
    """把一个检索表单**真试一次**：拼出检索地址、取回、按结果页自己的结构解出条目。

    返回 `(入口配置, 取回条目数)`；试不出来返回 `(None, 0)` 并把原因写进日志。
    地址用运行时同一个 `build_search_url` 拼——试的是哪条 URL，正式抓取就是哪条
    （自己拼一遍的话，两处一旦漂移，就会出现「试运行过了、抓取取不到」这种最难查的错）。
    """
    from app.sources.analyze import analyze_page
    from app.sources.config import EntrypointConfig, SearchConfig
    from app.sources.html_source import build_search_url, parse_list_page

    action = str(form.get("action") or "").strip()
    field = str(form.get("field") or "").strip()
    params = {str(k): str(v) for k, v in (form.get("params") or {}).items()}
    search = SearchConfig(keyword_param=field, fixed_params=params)
    probe_entry = EntrypointConfig(
        name="probe", url=action, list_selector="body", search=search
    )
    url = build_search_url(probe_entry, keyword=probe_keyword, date_from=None, date_to=None, page=1)
    if url is None:
        return None, 0
    try:
        fetched = await net.fetch_text(
            url, allowed_domains=domains, timeout=float(get_settings().source_fetch_timeout)
        )
    except (net.FetchError, net.UnsafeURLError) as exc:
        _log(job, f"站内检索入口取回失败（{field} → {action}）：{exc}", "warn")
        return None, 0

    analysis = analyze_page(fetched.text, fetched.url, listed_as="search_probe")
    for candidate in analysis.get("list_candidates") or []:
        dropped: list[str] = []
        selector = _usable_selector(candidate.get("selector"), dropped, "search.list_selector")
        if selector is None:
            continue
        entry = EntrypointConfig(
            name="probe", url=action, list_selector=selector, search=search
        )
        items = parse_list_page(fetched.text, entry, fetched.url, limit=5)
        if items:
            return (
                {
                    "name": _SEARCH_ENTRY_NAME,  # 由调用方去重后覆盖
                    "url": action,
                    "list_selector": selector,
                    "link_selector": "a",
                    "pagination": _simplify_pagination(analysis.get("pagination") or {}),
                    "search": search.model_dump(),
                    "notes": (
                        f"站内检索入口（导入时真取一页探测：关键词「{probe_keyword}」"
                        f"取回 {len(items)} 条后采纳；参数名 {field}"
                        + (f"，固定参数 {params}" if params else "")
                        + "）"
                    ),
                },
                len(items),
            )
    _log(
        job,
        f"站内检索入口取回了页面但解析不出条目（{field} → {action}）："
        f"该页的列表结构没有被识别，本次不加检索入口",
        "warn",
    )
    return None, 0


async def _maybe_add_search_entry(
    job: ImportJob, payload: dict[str, Any], domains: list[str], request: SourceImportRequest
) -> None:
    """站点自己的检索页真能用 → 给配置补一个**检索型入口**（关键词/日期进服务端）。

    为什么值得多花一次取数：没有它，关键词只能拿已经翻到的那几页逐条比对标题，
    召回上限 = 翻页深度 × 每页条数；有它，关键词送进站点自己的索引
    （口径与前后端说明见 `sources/config.capability_summary`）。

    **只有真取回条目才加**。地址拼错的典型症状是取回首页——解析出来的「条目」其实是导航
    链接，只看页面像什么就会把导航当内容列表配进来源。所以这里按「解析不出条目就不采纳」
    处理，并且把每一次失败原因都写进日志：「这站没有检索入口」和「有、但没试出来」是
    两件事，页面与日志都要分得清。
    """
    forms = _search_form_candidates(job)
    if not forms:
        _log(
            job,
            "页面里没有发现站内检索表单：关键词与时间范围将在已翻到的列表页内过滤"
            "（召回受翻页深度限制）",
            "info",
        )
        return

    keyword = next((k.strip() for k in (request.keywords or []) if (k or "").strip()), None)
    probe_keyword = keyword or SEARCH_PROBE_KEYWORD
    _log(
        job,
        f"发现 {len(forms)} 个站内检索表单候选，逐个真取一页试抓"
        f"（探测关键词「{probe_keyword}」"
        + ("，取自你填的关键词）" if keyword else "，通用探测词，只验证检索页结构）"),
    )

    for form in forms:
        action = str(form.get("action") or "").strip()
        field = str(form.get("field") or "").strip()
        if not action:
            _log(
                job,
                f"检索框「{field}」没有 form 提交地址（页面用 JS 拼地址）："
                "地址没法确定，本次不猜、不加检索入口",
                "warn",
            )
            continue
        if (form.get("method") or "get").lower() != "get":
            _log(job, f"检索表单「{field}」是 POST 提交，当前引擎只支持 GET，跳过", "warn")
            continue
        host = (urlsplit(action).hostname or "").lower()
        if not host or not domain_allowed(host, domains):
            _log(
                job,
                f"检索入口在 {host or '未知主机'}，不在来源边界（{'、'.join(domains)}）内，跳过；"
                "要收录它得先人工把该主机加进 allowed_domains",
                "warn",
            )
            continue
        entry, count = await _probe_search_entry(job, form, domains, probe_keyword)
        if entry is None:
            continue
        existing = list(payload.get("entrypoints") or [])
        entry["name"] = _unique_entry_name(_SEARCH_ENTRY_NAME, existing)
        payload["entrypoints"] = [*existing, entry]
        _log(
            job,
            f"已加入站内检索入口「{entry['name']}」：关键词送进 {action}"
            f"（探测关键词「{probe_keyword}」取回 {count} 条）——"
            "不选发布机构时，关键词由该站自己的检索接口执行",
        )
        return

    _log(
        job,
        "试过的站内检索入口都没取回条目：本次不加检索入口，"
        "关键词与时间范围仍在已翻到的列表页内过滤",
        "warn",
    )
