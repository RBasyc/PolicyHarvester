"""抓取入库工具（对话 Agent 驱动「检索 → 确认 → 提取入库」）。

与 `onboarding_tools.py` 的分工是**阶段不同，不是能力重叠**：

    onboarding_tools：接一个**新网站**（勘察 → 配置 → 试运行 → 注册来源）
    ingestion_tools ：对**已有来源**发起一次真实的入库（建任务 → 看候选 → 确认 → 提取）

在这之前，Agent 手上没有任何一个动作能入库：`get_policy_fulltext` 那几个 MCP 工具是**只读**的
（把内容读给模型看，不落库），`dry_run_ingestion` 是接入前的试运行（明说不写正式结果表），
`manage_ingestion_job(action="run_trial")` 只能发起、且**只能对已注册的配置化来源**，
发起完还得等人去抓取页点确认。本模块补的就是这条链。

**本工具的硬约束**（与 onboarding_tools 同款，改了要同步改 `prompts/chat.md` 与本模块 docstring）：

- 工具**不抛异常**：任何失败都返回 `{"ok": false, "error": "<中文原因>"}`；
- **确认闸门不删，只是搬到对话里**：`start` 只跑检索段，一定停在等确认；
  `confirm` 只有在用户明确说了要入库哪些篇目之后才能调用（服务的 `user_approved` 兜底）；
- **不许替用户改条件**：来源名对不上就报错（不静默换成默认源），篇目 id 对不上就报错
  （不静默少抓）——报错可以纠正，静默替换用户不会发现。

服务层的导入写在函数体内：`app.agent.tools` → `ingestion_tools` → `services/ingestion`
→ `pipeline/crawl_graph`，顶层导入会成环（同 `onboarding_tools.py` 的说明）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.agent.tool_utils import dump as _dump
from app.agent.tool_utils import error as _error
from app.agent.tool_utils import parse_iso_date as _parse_date

# 一次返回给模型的候选条数上限（模型要的是「够不够挑」，全量在 *_count 字段里）
_CANDIDATE_LIMIT = 25

_ACTIONS = ("start", "inspect", "confirm")


def _note_for_start(result: dict[str, Any]) -> str:
    """`start` 之后该说什么——把「检索是否跑完」翻成模型能照做的下一步。"""
    if not result.get("retrieval_finished"):
        return (
            "检索还在跑：不要重复发起（那会建重复任务），过一会儿用 action=inspect 看结果。"
        )
    status = result.get("status")
    if status == "awaiting_confirmation":
        return (
            "检索完成，已停在**等确认**：把候选篇目讲给用户（共几篇、其中几篇建议入库、"
            "被判别为不建议入库的有哪几篇要提醒），用户说了要哪几篇（或「全部」）之后，"
            "再调 action=confirm 并带上 user_approved=true。"
        )
    if status == "failed":
        return (
            "检索失败（stage 里是原因）：不要用同样的条件重试，先与用户核对来源 / 关键词 / "
            "时间范围。"
        )
    return "任务已结束，用 action=inspect 查看确切状态。"


async def _task_view(svc: Any, task_id: int) -> dict[str, Any]:
    """任务的可提交形态：状态 / 阶段 / 计数 / 候选（供 start 与 inspect 复用）。

    **`inspect_ingestion` 是协程**：忘了 await 拿到的就是一个协程对象，
    下一个 `.update(...)` 会以 `'coroutine' object has no attribute 'update'` 炸掉
    （2026-10-16 实机三次复现的正是这一处），因此这里和两个调用点都必须是 async。
    """
    return await svc.inspect_ingestion(task_id, candidate_limit=_CANDIDATE_LIMIT)


@tool
async def ingest_policies(
    action: str,
    source: str | None = None,
    keywords: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
    browse_url: str | None = None,
    browse_goal: str | None = None,
    max_policies: int | None = None,
    task_id: int | None = None,
    policy_ids: list[int] | None = None,
    include_rejected: bool = False,
    user_approved: bool = False,
) -> str:
    """抓取并入库政策文件：把「检索 → 确认 → 提取入库」这条链在对话里走完（三个动作）。

    这是**唯一**能让内容真正入库的动作：`get_policy_fulltext` 之类的检索工具只把内容读给你看，
    不会落库；`dry_run_ingestion` 是接入前对新来源的试运行，也不写正式结果表。

    三个动作：

    - `start`   发起一次抓取（给来源 / 关键词 / 时间范围 / 篇数上限）→ 建任务并跑检索段。
                本工具会**等检索段结束**（最多约两分钟）再把候选篇目给你；检索段一定停在
                「等用户确认」，不会自动入库。
    - `inspect` 看一个任务：状态 / 阶段 / 计数 + 候选篇目（含「不建议入库」标注与「已在库」标记）。
                任务还在跑、或想复查进度时用它。
    - `confirm` 确认入库 → 开始提取。**必须 user_approved=true**，且只能在用户明确说了要哪几篇
                （或「全部」）之后。提取按篇计费、入库后进成果库，这道确认不能由你代劳。

    Args:
        action: `start` / `inspect` / `confirm` 之一。
        source: 来源名——展示名（`国务院政策文件库（gov.cn）`）、key（`gov_cn`）或其中一段
            （`政策文件库`）；留空 = 默认源。**名字对不上直接报错**并给出可用清单，不会替你换源。
        keywords: 检索关键词（可多个）；留空 = 该来源的最新文件。
        date_from / date_to: 时间范围（YYYY-MM-DD），留空 = 不限。
        agencies / doc_types: gov.cn 的发布机构分类与文种筛选（有哪些值先用 `get_policy_facets` 看）。
        browse_url / browse_goal: 只有无头浏览器来源时用。`browse_goal` 是发现循环的自然语言
            目标（如「找最近三个月的储能政策」），对持久化的无头来源（来源管理里「保存为无头
            浏览器来源」创建）必填可选、入口网址以**来源配置为准**（browse_url 传了也忽略）；
            遗留的「任意网站」来源才用 browse_url 传入口。其它（静态/渲染）来源传 browse_url 会被拒绝。
        max_policies: 本任务最多处理多少篇（1–50，留空 = 服务端默认）。**提取按篇计费**，别贪多。
        task_id: `inspect` / `confirm` 用（`start` 的返回里有）。
        policy_ids: `confirm` 用——要入库的篇目 id（来自候选列表）；留空 = 按判别结果全部建议入库的
            篇目（被判别为「不建议入库」与已在成果库的会剔除）。
        include_rejected: `confirm` 时连「不建议入库」的一起入库（默认 false）。
        user_approved: 只有**用户明确同意**之后才能传 true。这不是让你替用户拍板，而是让你如实
            说明用户是否已经同意；没传 true 时确认会被拒绝。

    Returns:
        JSON 文本。`ok=true` 时含 `task_id`、`status`、`stage`、`counts`、候选 `candidates`
        （含 `suggested` / `in_library`）与 `next`（下一步该做什么）；失败时 `ok=false` + 中文原因。
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _error(f"action 需要是 {' / '.join(_ACTIONS)} 之一，收到：{action or '（空）'}")

    try:
        from app.services import ingestion as svc
    except Exception as exc:  # noqa: BLE001 - 服务不可用时也要给模型一句人话
        return _error(f"入库服务不可用：{exc}")

    try:
        if action == "start":
            return await _start(
                svc, source, keywords, date_from, date_to, agencies, doc_types,
                browse_url, browse_goal, max_policies,
            )
        if action == "inspect":
            return await _inspect(svc, task_id)
        return await _confirm(svc, task_id, policy_ids, include_rejected, user_approved)
    except svc.IngestionError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - 兜底：工具绝不抛异常
        return _error(f"入库动作失败：{exc}")


async def _start(
    svc: Any,
    source: str | None,
    keywords: list[str] | None,
    date_from: str | None,
    date_to: str | None,
    agencies: list[str] | None,
    doc_types: list[str] | None,
    browse_url: str | None,
    browse_goal: str | None,
    max_policies: int | None,
) -> str:
    start, err = _parse_date(date_from, "date_from")
    if err:
        return _error(err)
    end, err = _parse_date(date_to, "date_to")
    if err:
        return _error(err)

    # strict=True：模型编错来源名时必须报错（流水线对未知名字是回退默认源的）
    label = svc.resolve_source_label(source, strict=True)

    task_id = await svc.start_ingestion(
        source=label,
        keywords=keywords or [],
        date_from=start,
        date_to=end,
        agencies=agencies or [],
        doc_types=doc_types or [],
        browse_url=browse_url,
        browse_goal=browse_goal,
        # 对话侧只覆盖「本任务最多几篇」；其余流水线参数沿用 .env 默认
        pipeline_overrides={"pipeline_max_policies": max_policies},
        origin="对话",
    )

    settled = await svc.wait_for_settled(task_id)
    view = await _task_view(svc, task_id)
    view.update(
        {
            "ok": True,
            "action": "start",
            "retrieval_finished": bool(settled.get("settled")),
            "next": _note_for_start(
                {"retrieval_finished": settled.get("settled"), "status": view.get("status")}
            ),
        }
    )
    return _dump(view)


async def _inspect(svc: Any, task_id: int | None) -> str:
    if task_id is None:
        return _error("inspect 需要 task_id（start 的返回里有）")
    view = await _task_view(svc, int(task_id))
    status = view.get("status")
    if status == "awaiting_confirmation":
        next_step = "检索完成，等用户确认篇目；用户点头后调 action=confirm。"
    elif status == "running":
        counts = view.get("counts") or {}
        next_step = (
            f"正在提取：已提取 {counts.get('extracted', 0)}/{view.get('candidate_count', 0)} 篇。"
            "提取在后台跑，过一会儿再 inspect。"
        )
    elif status == "failed":
        next_step = "任务已失败，stage 里是原因；不要用同样的条件重试。"
    else:
        next_step = "任务已结束；成果在「政策库」页可查。"
    view.update({"ok": True, "action": "inspect", "next": next_step})
    return _dump(view)


async def _confirm(
    svc: Any,
    task_id: int | None,
    policy_ids: list[int] | None,
    include_rejected: bool,
    user_approved: bool,
) -> str:
    if task_id is None:
        return _error("confirm 需要 task_id（start 的返回里有）")
    result = await svc.confirm_ingestion(
        int(task_id),
        policy_ids,
        include_rejected=include_rejected,
        user_approved=user_approved,
        origin="对话",
    )
    result.update(
        {
            "ok": True,
            "action": "confirm",
            "next": (
                f"已确认 {result['kept']} 篇，提取在后台跑（按篇计费）。"
                "过一会儿用 action=inspect 看进度；成果在「政策库」页。"
            ),
        }
    )
    return _dump(result)


# 注册表：`app/agent/tools.py` 把它并进 TOOLS（顶层导入，不含服务依赖）
INGESTION_TOOLS = [ingest_policies]
