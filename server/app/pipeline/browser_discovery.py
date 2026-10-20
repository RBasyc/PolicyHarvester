"""浏览器发现循环：用本项目自己的紧凑 ReAct 循环在任意站点上收集政策原文链接。

它不是 `C:\\Users\\MP\\Desktop\\webagent` 的 planner/executor 移植——那两个角色由
「一次 LLM 调用 + 一组共享的 browser_* 工具」直接承担：

1. `BrowserRun`（mode="pipeline"）放进 `ContextVar`，工具据此拿到同一个会话与预算；
2. `browser_open` 打开入口 → 模型看编号快照 → `browser_collect_links` 从**真实快照**
   里挑链接（模型不能直接提交 URL，因此不能编造候选）；
3. 可继续点击分页 / 栏目，直到 `browser_finish`、模型不再调工具或预算耗尽；
4. 产出 `run.collected`（没有时退回 `run.extracted`），交给 `crawl_graph._browse_policies`
   做日期复核、laya 判别与正文预检。

与对话入口的分工：对话用 `chat_browser_tools()`（看不到 collect / finish），这里用
`pipeline_browser_tools()`；两者共用同一套动作实现与安全门禁（guard / 只读 / 预算）。

失败口径：
- 浏览器不可用或入口打不开 → `PipelineError`（任务直接失败，文案可读）；
- 模型调用中途失败 → 已收集到候选就带着「部分结果」返回，否则 `PipelineError`；
  不让一次网关抖动把已经点开的页面白费。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.agent.nodes import _build_llm
from app.browser import runtime as browser_runtime
from app.browser.budget import Budget
from app.browser.context import BrowserRun, browser_run_var
from app.browser.tools import pipeline_browser_tools
from app.config import get_settings
from app.db import repositories as repo
from app.pipeline.crawl_graph import PipelineError
from app.prompts import load_prompt

logger = logging.getLogger(__name__)

# 舞台文案里动作描述的最大长度（任务进度行是单行展示）
_STAGE_DETAIL_CHARS = 80


def _short(text: Any, limit: int = _STAGE_DETAIL_CHARS) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


def _describe_call(name: str, args: dict[str, Any]) -> str:
    """把一次工具调用翻成进度面板上的一行中文（不追求穷尽，能看出在做什么即可）。"""
    if name == "browser_open":
        return f"打开 {_short(args.get('url'))}"
    if name == "browser_click":
        return f"点击 [{args.get('ref')}]"
    if name == "browser_type":
        return f"在 [{args.get('ref')}] 输入「{_short(args.get('text'), 40)}」"
    if name == "browser_select":
        return f"在 [{args.get('ref')}] 选择「{_short(args.get('option'), 40)}」"
    if name == "browser_press":
        return f"按键 {_short(args.get('key'), 20)}"
    if name == "browser_scroll":
        return f"滚动 {args.get('direction') or 'down'}"
    if name == "browser_wait":
        return f"等待 {args.get('seconds') or 1} 秒"
    if name == "browser_back":
        return "后退一页"
    if name == "browser_snapshot":
        return "查看页面快照"
    if name == "browser_collect_links":
        refs = args.get("refs") or []
        return f"收集 {len(refs)} 个链接"
    if name == "browser_extract_page":
        return "抽取当前页正文"
    if name == "browser_extract_articles":
        refs = args.get("refs") or []
        return f"批量抽取 {len(refs)} 篇"
    if name == "browser_finish":
        return "结束浏览"
    return name.removeprefix("browser_")


def _tool_result(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return {"ok": False, "error": _short(content, 200)}
    return data if isinstance(data, dict) else {"ok": False, "error": "工具返回了非对象结果"}


def _system_prompt(
    *,
    entry_url: str,
    goal: str,
    keywords: list[str],
    date_from: date | None,
    date_to: date | None,
    max_candidates: int,
) -> str:
    """通用规则（prompts/browser_browse.md）+ 本次任务的硬约束。"""
    window = "不限"
    if date_from or date_to:
        window = f"{date_from or '不限'} ~ {date_to or '不限'}"
    constraints = f"""
## 本次任务

- 入口网址：{entry_url}
- 浏览目标：{goal or "按时间范围收集最新政策原文"}
- 关键词：{'、'.join(keywords) if keywords else "（未指定）"}
- 时间范围：{window}
- 最多收集 {max_candidates} 个候选；达到后立刻调用 browser_finish。

只收集**政策原文 / 通知公告的详情页**，不要收集栏目首页、搜索结果页、登录页或附件下载链接。
入口页若是首页，先找「政策 / 信息公开 / 通知公告」栏目的链接（优先在当前页快照里点开）。
""".strip()
    return f"{load_prompt('browser_browse')}\n\n{constraints}"


def _human_prompt(*, entry_url: str, goal: str, keywords: list[str]) -> str:
    target = goal or (
        f"收集与「{'、'.join(keywords)}」相关的最新政策原文"
        if keywords
        else "收集页面上最新的政策原文"
    )
    return (
        f"从 {entry_url} 开始浏览，目标：{target}。\n"
        "先看 browser_open 返回的编号快照，再用 browser_collect_links 收集候选；"
        "需要翻页时点击分页链接继续收集。完成后调用 browser_finish。"
    )


def _collected_items(
    run: BrowserRun, *, max_candidates: int
) -> list[dict[str, Any]]:
    """优先用发现循环收集到的链接；没有时退回批量抽取过的详情页。"""
    source = run.collected or run.extracted
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in source:
        url = str(entry.get("url") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not url or url in seen:
            continue
        # 收集链接时标题可能是空字符串（图标链接等）：没有标题的候选在判别/确认面板里
        # 没有意义，直接跳过；详情页兜底来源一般都有标题。
        if not title:
            continue
        seen.add(url)
        item: dict[str, Any] = {"title": title, "url": url}
        for key in ("date", "agency", "doc_no", "metadata"):
            if entry.get(key):
                item[key] = entry[key]
        items.append(item)
        if len(items) >= max_candidates:
            break
    return items


async def discover(
    *,
    task_id: int,
    entry_url: str,
    goal: str,
    keywords: list[str],
    date_from: date | None,
    date_to: date | None,
    max_candidates: int,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """跑一次发现循环，返回 `{items, pages, actions, stop_reason}`。

    `overrides` 目前只用来读 `pipeline_max_body_chars` 之类的任务覆盖；预算上限仍取
    `.env` 的 `BROWSER_*`（与对话入口同一套硬上限）。
    """
    settings = get_settings()
    if not browser_runtime.available():
        raise PipelineError(browser_runtime.unavailable_reason())

    tools = pipeline_browser_tools()
    tools_by_name = {tool.name: tool for tool in tools}

    run = BrowserRun(mode="pipeline", budget=Budget())
    token = browser_run_var.set(run)
    try:
        # 入口页由工具打开：协议 / 公网 / robots 校验、请求拦截、快照都在同一条路径上。
        await repo.set_task_stage(task_id, f"浏览器：打开 {_short(entry_url)}")
        opened = await tools_by_name["browser_open"].ainvoke({"url": entry_url})
        open_data = _tool_result(str(opened))
        if not open_data.get("ok"):
            raise PipelineError(
                f"无头浏览器无法打开入口 {entry_url}："
                f"{open_data.get('error') or '未知原因'}"
            )

        llm = _build_llm(model=settings.browser_model or None).bind_tools(tools)
        messages: list[Any] = [
            SystemMessage(
                content=_system_prompt(
                    entry_url=entry_url,
                    goal=goal,
                    keywords=keywords,
                    date_from=date_from,
                    date_to=date_to,
                    max_candidates=max_candidates,
                )
            ),
            HumanMessage(
                content=_human_prompt(
                    entry_url=entry_url, goal=goal, keywords=keywords
                )
            ),
        ]

        # 每一步至少一次工具调用；预算本身还会拒绝超限动作。+6 给「看快照 → 收集 →
        # finish」这类无动作步骤留余量。
        max_steps = int(settings.browser_max_actions) + 6
        stop_reason = "模型结束"
        for _ in range(max_steps):
            if run.finished:
                stop_reason = run.stop_reason or "模型主动结束"
                break
            if run.budget.expired():
                stop_reason = "浏览超时"
                break
            if not (run.budget.can_action() or run.budget.can_page()):
                stop_reason = "动作预算耗尽"
                break

            response = await llm.ainvoke(messages)
            messages.append(response)
            calls = list(getattr(response, "tool_calls", None) or [])
            if not calls:
                stop_reason = _short(getattr(response, "content", ""), 200) or "模型未再调用工具"
                break

            for index, call in enumerate(calls):
                name = str(call.get("name") or "")
                args = dict(call.get("args") or {})
                await repo.set_task_stage(
                    task_id, f"浏览器：{_describe_call(name, args)}"
                )
                tool = tools_by_name.get(name)
                if tool is None:
                    content = json.dumps(
                        {"ok": False, "error": f"未知工具：{name}"}, ensure_ascii=False
                    )
                else:
                    try:
                        content = str(await tool.ainvoke(args))
                    except Exception as exc:  # noqa: BLE001 - 工具自身不抛，这里只兜底
                        logger.warning("浏览器工具 %s 执行失败：%s", name, exc)
                        content = json.dumps(
                            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                            ensure_ascii=False,
                        )
                messages.append(
                    ToolMessage(
                        content=content,
                        tool_call_id=str(call.get("id") or f"call-{index}"),
                    )
                )
                if run.finished:
                    stop_reason = run.stop_reason or "模型主动结束"
                    break
            if run.finished:
                break
        else:
            stop_reason = "达到步数上限"

        items = _collected_items(run, max_candidates=max_candidates)
        return {
            "items": items,
            "pages": run.budget.pages,
            "actions": run.budget.actions,
            "extracts": run.budget.extracts,
            "stop_reason": stop_reason,
        }
    except asyncio.CancelledError:
        raise
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001 - 模型/网关异常：能保留部分结果就保留
        items = _collected_items(run, max_candidates=max_candidates)
        if items:
            logger.exception("浏览器发现循环中断，保留已收集的 %d 个候选", len(items))
            return {
                "items": items,
                "pages": run.budget.pages,
                "actions": run.budget.actions,
                "extracts": run.budget.extracts,
                "stop_reason": f"浏览中断（{_short(exc, 120)}），保留已收集候选",
            }
        raise PipelineError(f"无头浏览器浏览失败：{_short(exc, 200)}") from exc
    finally:
        browser_run_var.reset(token)
        await run.aclose()
