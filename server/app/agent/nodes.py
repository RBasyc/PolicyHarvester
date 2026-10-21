"""图节点：调用 LLM 的 agent 节点 + 包了 SSE 事件发射的工具节点。

工具执行本体由 LangGraph 预置的 ToolNode 承担；`call_tools` 只是在它前后
各发一条 `tool` SSE 事件，让前端能看到普通工具的调用过程（浏览器工具自带
事件，跳过，见 `call_tools` docstring）。
"""

from collections.abc import Callable
from contextvars import ContextVar

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import ToolNode

from app.agent.state import AgentState
from app.agent.tools import all_tools
from app.browser.context import get_browser_run
from app.browser.tools import all_browser_tool_names, pipeline_only_tool_names
from app.config import get_settings
from app.prompts import load_prompt

# 对话流（POST /api/chat/stream）注入的 SSE 发射器：普通工具的 start/done 事件走它。
# 非流式 /api/chat 与提取流水线不设这个变量 → get() 拿到 None → call_tools 退化为裸 ToolNode。
chat_emit_var: ContextVar[Callable[[dict], None] | None] = ContextVar(
    "chat_emit", default=None
)


def _build_llm(
    model: str | None = None,
    max_tokens: int | None = None,
    *,
    thinking: bool | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """构建 OpenAI（兼容网关/端点）聊天模型；参数缺省时用 .env 的 MODEL_NAME / MAX_TOKENS。

    一切支持 `/v1/chat/completions` 的网关都走这一条路径：DeepSeek、Moonshot（Kimi）、
    官方 OpenAI、本地 vLLM…… 认证统一一把 `api_key`（OpenAI SDK 以 Bearer 头发出，
    不再有 x-api-key / Authorization 双头冲突的问题）。

    提取流水线会按「高级设置」选的模型传入覆盖值（见 app/pipeline/extract.py）：
    - `base_url` / `api_key`：注册表里自带端点的模型用它们；缺省时沿用 .env 的
      OPENAI_BASE_URL / OPENAI_API_KEY；
    - `thinking=True`：传 `reasoning_effort="high"` 显式开思考；False/None = 不传该参数、
      交给服务端默认——OpenAI 协议没有通用的「关思考」参数。
    """
    settings = get_settings()
    kwargs: dict = {
        "model": model or settings.model_name,
        "base_url": base_url or settings.openai_base_url,
        "max_tokens": max_tokens or settings.max_tokens,
    }
    api_key = api_key or settings.openai_api_key
    if api_key:
        kwargs["api_key"] = api_key
    # 留空则完全不传，让 openai SDK 兜底读 OPENAI_API_KEY 环境变量
    if thinking is True:
        kwargs["reasoning_effort"] = "high"
    return ChatOpenAI(**kwargs)


def _tools_for_current_run() -> list[BaseTool]:
    """按当前请求是否挂着浏览器运行来裁剪绑定给模型的工具。

    `ToolNode` 始终持有全部工具（含 browser_*），否则开关打开后模型发来的调用会找不到
    执行器；这里只控制**模型能看到哪些 schema**：
    - 没开「浏览网页」→ 全部 browser_* 都不绑定（省 token）；
    - 开了 → 绑定对话可用的 11 个；`browser_collect_links` / `browser_finish` 是流水线
      发现循环专用的，对话模型看不到。
    """
    run = get_browser_run()
    hidden = pipeline_only_tool_names() if run is not None else all_browser_tool_names()
    return [item for item in all_tools() if item.name not in hidden]


async def call_model(state: AgentState) -> dict:
    """agent 节点：携带工具定义调用模型，返回模型回复。"""
    run = get_browser_run()
    llm = _build_llm().bind_tools(_tools_for_current_run())
    # 提示词**在调用时**从 prompts/ 读（不进图、不进缓存）：改完即刻生效，不用重启。
    # 浏览器提示词只在本次请求真的开了浏览器时追加。
    system_parts = [load_prompt("chat")]
    if run is not None:
        system_parts.append(load_prompt("chat_browser"))
    messages = [SystemMessage(content="\n\n".join(system_parts)), *state["messages"]]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def call_tools(state: AgentState) -> dict:
    """工具节点：执行前后各发一条 `tool` SSE 事件（start / done）。

    - 事件只带**工具名**，不带参数：用户要的是「调了什么」，参数JSON又长又吵
      （用户 2026-10-20 明确）；要看参数详情去 Langfuse 跟踪里找。
    - 浏览器工具（browser_*）在工具函数内部自己发事件（带更细的进度），这里**跳过**避免双发；
    - 没挂发射器（非流式 /api/chat、提取流水线）时退化为裸 `ToolNode`，一行多余开销都没有。
    """
    emit = chat_emit_var.get()
    last = state["messages"][-1]
    browser_names = all_browser_tool_names()
    pending: list[str] = []
    if emit is not None and isinstance(last, AIMessage):
        for call in last.tool_calls:
            if call["name"] in browser_names:
                continue
            emit(
                {
                    "type": "tool",
                    "name": call["name"],
                    "detail": "",
                    "status": "start",
                    "ok": False,
                }
            )
            pending.append(call["name"])

    result = await ToolNode(all_tools()).ainvoke(state)

    if emit is not None and pending:
        answered: set[str] = set()
        for msg in result.get("messages", []):
            if not isinstance(msg, ToolMessage) or msg.name not in pending:
                continue
            if msg.name in answered:
                continue  # 同名并发调用按「最近一次 start」配对，与前端逻辑一致
            answered.add(msg.name)
            ok = getattr(msg, "status", "success") != "error"
            emit(
                {"type": "tool", "name": msg.name, "detail": "", "status": "done", "ok": ok}
            )
        for name in pending:  # 没配上的（ToolNode 内部异常路径）补一条 done，前端不吊死
            if name not in answered:
                emit({"type": "tool", "name": name, "detail": "", "status": "done", "ok": False})
    return result
