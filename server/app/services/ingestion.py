"""抓取任务的发起 / 查看 / 确认：**HTTP 接口与对话 Agent 共用的一条通路**。

为什么单独成模块：抓取任务的生命周期（检索 → 人工确认 → 提取入库）过去只长在
`app/api/routes/tasks.py` 里，于是「让 Agent 走一遍入库」无路可走——Agent 手上只有
接入工具（接一个新网站）与**只读**的 MCP 检索工具，没有任何一个能建 `crawl_task`，
也没有任何一个能让结果进 `policy_document`。本模块把这三个动作抽出来，
路由（`POST /api/tasks`、`POST /api/tasks/{id}/confirm`）与工具（`ingest_policies`）
都调它：**一处实现，两个出入口**——确认那几步的顺序（丢弃未选中 → 重算计数 → 落
running → 跑提取段）一旦两处各写一份，迟早会走样，而走样的是计数器与提取状态。

边界（与仓库既有约定一致）：

- 只做编排：落库在 `app/db/repositories.py`，取数与解析在 `app/sources/`，
  两段流水线在 `app/pipeline/crawl_graph.py`（`start_pipeline` / `start_extraction`）。
- **确认闸门不删，只是搬家**：定时任务自动确认是既有的另一条路（`app/scheduler.py`）；
  对话侧要求模型先跟用户确认篇目再确认——见 `confirm_ingestion(user_approved=...)`。
- **不碰来源名解析的口径**：`start_ingestion` 收到的来源名原样落进 `crawl_task.source`，
  与 `POST /api/tasks` 完全一致（`get_by_label` 对认不出的名字回退内置源，这是流水线
  与 `routes/filters.py` 共同依赖的既有行为，见 `scripts/test_sources.py`）。
  要让名字先过一遍校验的是**模型**这条入口：`resolve_source_label(..., strict=True)`
  会明确拒绝未知来源——否则模型编错一个名字，任务会静默抓 gov.cn，
  而任务行里还留着那个错名字。

**三个异常的分工**（路由与工具各自翻译成自己的语言：HTTPException / `{"ok": false}`）：
`IngestionNotFound` → 404，`IngestionConflict` → 409，`IngestionRejected` → 400。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from app.db import repositories as repo
from app.sources.browser_any import BROWSER_SOURCE_KEY, browser_source_available
from app.sources.registry import all_sources, get, get_by_label, get_default

logger = logging.getLogger(__name__)

# 检索段结束后任务会停在的状态：等确认 / 已失败 / 已完成
_SETTLED_STATUSES = ("awaiting_confirmation", "completed", "failed")
# 「检索完成、等人确认要抓哪些篇目」——确认闸门
_AWAITING_STATUS = "awaiting_confirmation"

# 等检索段结束的轮询节奏与默认上限（工具侧用它把「一次调用」变成「一次可读的结论」）
_POLL_SECONDS = 1.5
DEFAULT_WAIT_SECONDS = 120.0

# 本任务最多处理多少篇（与 TaskCreate 的 ge=1/le=50 对齐）
_MAX_POLICIES_MIN = 1
_MAX_POLICIES_MAX = 50


class IngestionError(RuntimeError):
    """抓取任务动作的基类异常。"""


class IngestionNotFound(IngestionError):
    """任务不存在。"""


class IngestionConflict(IngestionError):
    """任务当前状态不允许这个动作（如没停在等确认就要确认）。

    `status` 带上任务当时的真实状态：路由要把它翻成 409 的文案，工具要把「为什么不行」
    讲给模型听——两边都不该去解析异常消息里的字符串。
    """

    def __init__(self, message: str, *, status: str | None = None) -> None:
        super().__init__(message)
        self.status = status


class IngestionRejected(IngestionError):
    """参数或前置条件不满足；改一改可以重试。"""


# ---------- 来源名 ----------


def available_sources() -> list[dict[str, Any]]:
    """当前进程内可用的抓取源（内置源 + 已装载的 active 配置化来源）。

    这是「来源名写错了」时能给模型看的那份清单——它必须来自**注册表**而不是代码里的
    常量：配置化来源是随接入工作流长出来的，清单写死在工具里第二天就过期。
    """
    default_key = get_default().key
    return [
        {
            "key": source.key,
            "label": source.label,
            "default": source.key == default_key,
        }
        for source in all_sources()
        # 遗留伪来源「任意网站」始终在注册表里（存量任务 label 解析需要），但不该出现在
        # 给模型/页面看的「可用来源」清单里——新任务一律走持久化的 browser_driven 来源。
        if source.key != BROWSER_SOURCE_KEY
    ]


def resolve_source_label(raw: str | None, *, strict: bool = False) -> str:
    """把来源名解析成注册表里的**展示名**（任务行以它为外键）。

    接受 key（`gov_cn`）、完整展示名（`国务院政策文件库（gov.cn）`）、或名字里的一段
    （`政策文件库`）；留空 = 默认源。命中多个子串时要求调用方写明是哪一个——
    猜错等于抓错站点。

    `strict=False`（默认）时匹配不到就回退默认源，与 `get_by_label` / 页面口径一致；
    `strict=True` 时抛 `IngestionRejected` 并带上可用清单。**模型那条入口用 strict**：
    见模块 docstring。
    """
    text = (raw or "").strip()
    if not text:
        return get_default().label

    by_key = get(text)
    if by_key is not None:
        return by_key.label

    sources = all_sources()
    for source in sources:
        if source.label == text:
            return source.label

    hits = [s for s in sources if text in s.label or text in s.key]
    if len(hits) == 1:
        return hits[0].label
    if hits and strict:
        raise IngestionRejected(
            f"来源名「{text}」匹配到多个：" + "、".join(f"{s.label}（{s.key}）" for s in hits)
            + "——请用完整展示名或 key 指定"
        )
    if hits:
        return hits[0].label
    if strict:
        raise IngestionRejected(
            f"未知来源「{text}」。当前可用："
            + "、".join(f"{s.label}（{s.key}）" for s in sources)
        )
    logger.warning("未知抓取源「%s」，按页面口径回退默认源", text)
    return get_default().label


# ---------- 发起 ----------


def _origin_suffix(origin: str | None) -> str:
    """日志后缀：页面/定时任务不传（日志保持原样），对话传「对话」。

    任务日志是**用户看得见的**（抓取页的日志区）——「这个任务是跟模型聊出来的」
    必须写在日志里，否则事后没人知道这几篇是谁点的头。
    """
    return f"，由{origin}发起" if origin else ""


def _clamp_max_policies(value: int | None) -> int | None:
    if value is None:
        return None
    return max(_MAX_POLICIES_MIN, min(int(value), _MAX_POLICIES_MAX))


def _sanitize_overrides(
    overrides: dict[str, int | str | None] | None,
) -> dict[str, int | str | None]:
    """本任务覆盖参数的过筛：`pipeline_max_policies` 夹到 [1, 50]（与 `TaskCreate` 同界）。

    其余键原样透传——它们来自页面「高级设置」，服务层没有理由替它做业务判断；
    `None` = 用服务端 `.env` 默认（`repo.create_task` 写 NULL）。
    """
    clean = dict(overrides or {})
    if "pipeline_max_policies" in clean:
        clean["pipeline_max_policies"] = _clamp_max_policies(clean["pipeline_max_policies"])
    return clean


def _validate_range(date_from: date | None, date_to: date | None) -> None:
    if date_from and date_to and date_from > date_to:
        raise IngestionRejected(
            f"时间范围反了：date_from（{date_from}）晚于 date_to（{date_to}）"
        )


async def start_ingestion(
    *,
    source: str,
    keywords: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
    browse_url: str | None = None,
    browse_goal: str | None = None,
    pipeline_overrides: dict[str, int | str | None] | None = None,
    origin: str | None = None,
) -> int:
    """建任务（status=pending）+ 首条日志 → 后台跑**检索段**，返回 `task_id`。

    与 `POST /api/tasks` 同一条路：检索段结束（判别 + 正文预检之后）任务停在
    `awaiting_confirmation`，**不会自己入库**——入库要等 `confirm_ingestion`。
    本函数立即返回，不等检索；要等就用 `wait_for_settled`。

    `browse_url` / `browse_goal` 只对无头浏览器来源有效。两种形态：

    - **持久化的 browser_driven 来源**（推荐）：入口网址来自来源配置（`source_obj
      .entry_url`），payload 里的 `browse_url` 被**忽略**（来源才是入口的权威）；
      `browse_goal` 取自 payload（抓取时填的自然语言目标）。
    - **遗留伪来源「任意网站（无头浏览器）」**（仅存任务兼容）：`browse_url` 必填。
      新代码不要再生成这种任务——从来源管理保存无头来源后在抓取页选它。

    非浏览器来源传了入口地址会直接拒绝（否则用户以为它生效了，实际流水线走的是
    站点检索）。`pipeline_overrides` 是页面「高级设置」的直通口（键为 `pipeline_*`
    列名，见 `TaskCreate`）；对话侧只填 `pipeline_max_policies`，其余沿用 `.env` 默认。
    """
    _validate_range(date_from, date_to)

    source_obj = get_by_label(source)
    url_value = (browse_url or "").strip() or None
    goal_value = (browse_goal or "").strip()[:500] or None
    if getattr(source_obj, "browser_driven", False):
        if not browser_source_available():
            from app.browser import runtime as browser_runtime

            raise IngestionRejected(browser_runtime.unavailable_reason())
        # browser_driven：入口以来源配置为准；遗留 browser_any：用 payload 的地址。
        url_value = url_value or str(source_obj.entry_url).strip()
        if not url_value:
            raise IngestionRejected("无头浏览器来源缺少入口网址（来源配置里没有 browse.url）")
        from app.browser.guard import BrowserBlocked, ensure_navigable_sync

        try:
            url_value = await asyncio.to_thread(ensure_navigable_sync, url_value)
        except BrowserBlocked as exc:
            raise IngestionRejected(f"入口网址不可用：{exc}") from exc
    else:
        if url_value:
            raise IngestionRejected(
                "只有无头浏览器来源可以携带入口网址；"
                f"当前来源「{source}」请改用关键词检索"
            )
        goal_value = None

    task_id = await repo.create_task(
        source=source,
        date_from=date_from,
        date_to=date_to,
        keywords=list(keywords or []),
        agencies=list(agencies or []),
        doc_types=list(doc_types or []),
        browse_url=url_value,
        browse_goal=goal_value,
        pipeline_overrides=_sanitize_overrides(pipeline_overrides),
    )

    if getattr(source_obj, "browser_driven", False):
        log_line = f"已创建浏览器抓取任务（入口：{url_value}；目标：{goal_value or '未填写'}）"
    else:
        keyword_hint = "、".join(keywords) if keywords else "未设置"
        filter_bits = []
        if agencies:
            filter_bits.append(f"发布机构：{'、'.join(agencies)}")
        if doc_types:
            filter_bits.append(f"文种：{'、'.join(doc_types)}")
        filter_hint = ("；" + "；".join(filter_bits)) if filter_bits else ""
        log_line = f"已创建抓取任务（源：{source}；关键词：{keyword_hint}{filter_hint}）"
    await repo.add_task_log(task_id, log_line + _origin_suffix(origin), tone="info")

    # 延迟导入：`crawl_graph` 会拖上 pipeline 与 sources，路由历来在顶层导入它，
    # 但服务层被 agent 工具引用时（tools → ingestion_tools → services）顶层导入会成环。
    from app.pipeline.crawl_graph import start_pipeline

    start_pipeline(task_id)
    return task_id


# ---------- 查看 ----------


def _judge_suggests(row: dict[str, Any]) -> bool:
    """判别是否建议入库：`judge_tagged` TRUE = 不建议入库（NULL = 未判别，按建议算）。"""
    return not row.get("judge_tagged")


def _candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    """一条候选的精简视图（模型看的是「够不够挑」，不是整行）。"""
    return {
        "id": row["id"],
        "title": row["title"],
        "publish_date": row["publish_date"],
        "catalog": row.get("catalog"),
        "url": row.get("source_url"),
        "suggested": _judge_suggests(row),
        "in_library": bool(row.get("in_library")),
        "status": row["status"],
    }


async def inspect_ingestion(
    task_id: int | None = None,
    *,
    candidate_limit: int = 40,
    log_limit: int = 20,
) -> dict[str, Any]:
    """看一个任务：状态 / 阶段 / 计数 + 候选篇目（含判别与「已在库」标注）。

    候选列表就是**确认闸门要人过的那一眼**：调用方把它念给用户，用户挑篇目，
    再由 `confirm_ingestion` 落地。`candidate_limit` 只影响返回条数，不影响任何写操作；
    全量口径在 `counts` 与 `*_count` 字段里。

    `task_id` 留空 = **最近一次任务**（`repo.fetch_current_task`）：跨会话问
    「刚才那个任务怎么样了」时，模型手上往往没有 id。带 `logs` 是因为检索 0 结果
    或大量篇目被判「不建议」时，光看状态与计数猜不出原因——日志里写着。
    """
    if task_id is None:
        current = await repo.fetch_current_task()
        if current is None:
            raise IngestionNotFound("当前没有任何抓取任务")
        task_id = int(current["id"])

    task = await repo.fetch_task(task_id)
    if task is None:
        raise IngestionNotFound(f"任务不存在：{task_id}")

    rows = await repo.fetch_task_policies(task_id)
    suggested = [row for row in rows if _judge_suggests(row)]
    # 默认确认口径：建议入库 **且** 不在成果库（已在库的是重复抓取，除非点名要）
    default_keep = [row for row in suggested if not row.get("in_library")]

    log_rows = await repo.fetch_task_logs(task_id)
    capped_logs = max(0, int(log_limit))
    return {
        "task_id": task_id,
        "source": task["source"],
        "status": task["status"],
        "stage": task.get("stage"),
        "keywords": list(task.get("keywords") or []),
        "date_from": task.get("date_from"),
        "date_to": task.get("date_to"),
        "counts": {
            "total": task.get("total_count") or 0,
            "fetched": task.get("fetched_count") or 0,
            "extracted": task.get("extracted_count") or 0,
            "failed": task.get("failed_count") or 0,
        },
        "candidate_count": len(rows),
        "suggested_count": len(suggested),
        "already_in_library_count": sum(1 for row in rows if row.get("in_library")),
        "default_keep_count": len(default_keep),
        "candidates": [_candidate_row(row) for row in rows[:candidate_limit]],
        "candidates_truncated": len(rows) > candidate_limit,
        "logs": [
            {
                "tone": row.get("tone"),
                "message": row.get("message"),
                "created_at": str(row.get("created_at")),
            }
            for row in log_rows[-capped_logs:] if capped_logs
        ],
        "logs_truncated": len(log_rows) > capped_logs,
    }


async def wait_for_settled(
    task_id: int, *, timeout: float = DEFAULT_WAIT_SECONDS
) -> dict[str, Any]:
    """等检索段停下来（等确认 / 已失败 / 已完成）；超时**如实返回还在跑**，不抛错。

    只轮询任务行，不阻塞流水线：流水线在 `start_pipeline` 的后台 asyncio 任务里跑
    （`_track` 登记，取消/关停都能定位到它）。超时不是错误——大关键词集的检索本身
    可能要一分多钟，调用方应据此告诉用户「还在检索，稍后再看」。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        task = await repo.fetch_task(task_id)
        if task is None:
            raise IngestionNotFound(f"任务不存在：{task_id}")
        if task["status"] in _SETTLED_STATUSES:
            return {"settled": True, "timed_out": False, "status": task["status"]}
        if loop.time() >= deadline:
            return {
                "settled": False,
                "timed_out": True,
                "status": task["status"],
                "stage": task.get("stage"),
            }
        await asyncio.sleep(_POLL_SECONDS)


# ---------- 终止 ----------


# 手动终止后写进 `stage` 的文案（进度面板直接展示这一段）
CANCELLED_STAGE = "任务已手动终止"
# 任务终态：这两个状态下的任务不需要（也不能）再终止
_TERMINAL_STATUSES = ("completed", "failed")


async def cancel_ingestion(task_id: int | None = None) -> dict[str, Any]:
    """终止运行中的任务：取消后台流水线 → 落 `failed` 终态 → 记一条日志。

    **与页面同一段实现**：`POST /api/tasks/{id}/cancel` 现在转调这里（路由只留
    HTTP 语义）。顺序不能反——`run_pipeline` 的取消分支写的是「服务重启…」，
    由这里的 `finish_task` 覆盖成终止文案。

    `task_id` 留空 = 最近一次任务（与 `inspect_ingestion` 同口径）。
    """
    if task_id is None:
        current = await repo.fetch_current_task()
        if current is None:
            raise IngestionNotFound("当前没有任何抓取任务")
        task_id = int(current["id"])

    task = await repo.fetch_task(task_id)
    if task is None:
        raise IngestionNotFound(f"任务不存在：{task_id}")
    if task["status"] in _TERMINAL_STATUSES:
        raise IngestionConflict(
            f"任务已结束（{task['status']}），无需终止", status=task["status"]
        )

    # 局部导入：crawl_graph 会拖上 pipeline 与 sources，顶层导入会成环
    from app.pipeline.crawl_graph import cancel_pipeline

    await cancel_pipeline(task_id)
    await repo.finish_task(task_id, "failed", CANCELLED_STAGE)
    await repo.add_task_log(task_id, CANCELLED_STAGE, tone="info")
    return {"task_id": task_id, "status": "failed", "stage": CANCELLED_STAGE}


# ---------- 确认 ----------


async def confirm_ingestion(
    task_id: int,
    policy_ids: list[int] | None = None,
    *,
    include_rejected: bool = False,
    user_approved: bool = False,
    origin: str | None = None,
) -> dict[str, Any]:
    """确认要入库的篇目 → 丢弃其余 → 跑**提取段**（Phase 9 的第二段）。

    `policy_ids` 留空 = 按判别结果「全部建议入库」（默认还会剔除已在成果库的重复篇目；
    `include_rejected=True` 才把「不建议入库」的也带上）。给了 id 就以 id 为准，
    并校验它们确实属于本任务——否则报错而不是静默少抓。

    `user_approved` 是**人闸门的落点**：只有用户明确说了要入库哪些篇目，调用方才能传 True
    ——模型的判断替代不了用户的确认。与 `register_source(approved=...)` 同一个口径：
    这道闸门存在的理由不是「多一步」，而是「提取按篇计费、脏数据一旦入库就进成果库」。
    """
    if not user_approved:
        raise IngestionRejected(
            "确认入库需要用户明确同意：先把候选篇目给用户看，用户说了要哪几篇"
            "（或「全部」）之后再调用"
        )

    task = await repo.fetch_task(task_id)
    if task is None:
        raise IngestionNotFound(f"任务不存在：{task_id}")
    if task["status"] != _AWAITING_STATUS:
        # 文案与确认接口一致（页面弹的就是这一句）
        raise IngestionConflict(
            f"任务当前状态为 {task['status']}，无需确认", status=task["status"]
        )

    rows = await repo.fetch_task_policies(task_id)
    valid = {row["id"] for row in rows}

    if policy_ids:
        # 保序去重 + 过滤掉不属于本任务 / 已被删除的 id（与确认接口同口径）
        keep = [pid for pid in dict.fromkeys(int(pid) for pid in policy_ids) if pid in valid]
        if not keep:
            raise IngestionRejected("给的篇目 id 都不属于本任务，请重新查看任务候选篇目")
    else:
        keep = [
            row["id"]
            for row in rows
            if (include_rejected or _judge_suggests(row)) and not row.get("in_library")
        ]
        if not keep:
            raise IngestionRejected(
                "本任务没有可入库的篇目（全部被判别为「不建议入库」或已在成果库）；"
                "确实要入库请显式给出篇目 id"
            )

    dropped = await repo.discard_unselected_policies(task_id, keep)
    await repo.refresh_task_counts(task_id)
    await repo.add_task_log(
        task_id,
        f"已确认抓取 {len(keep)} 篇"
        + (f"，丢弃未勾选的 {dropped} 篇" if dropped else "")
        + _origin_suffix(origin),
        tone="info",
    )
    # 先同步置 running 再丢后台：这样响应与 SSE 立刻反映「提取中」，不会闪回等待态
    await repo.set_task_running(task_id, f"已确认，开始提取 {len(keep)} 篇")

    from app.pipeline.crawl_graph import start_extraction

    start_extraction(task_id)

    return {
        "task_id": task_id,
        "kept": len(keep),
        "dropped": dropped,
        "keep_ids": keep,
        "status": "running",
        "stage": f"已确认，开始提取 {len(keep)} 篇",
    }
