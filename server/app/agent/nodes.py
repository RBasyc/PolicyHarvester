"""图节点：调用 LLM 的 agent 节点。

工具执行节点由 LangGraph 预置的 ToolNode 承担，无需手写。
"""

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage

from app.agent.state import AgentState
from app.agent.tools import all_tools
from app.config import get_settings

SYSTEM_PROMPT = "你是一个乐于助人的 AI 助手。"


def _build_llm() -> ChatAnthropic:
    settings = get_settings()
    kwargs: dict = {
        "model": settings.model_name,
        "base_url": settings.anthropic_base_url,
        "max_tokens": settings.max_tokens,
    }
    # ChatAnthropic 不支持 auth_token 参数，Bearer 认证通过 default_headers 传递
    if settings.anthropic_auth_token:
        kwargs["default_headers"] = {
            "Authorization": f"Bearer {settings.anthropic_auth_token}"
        }
    elif settings.anthropic_api_key:
        kwargs["api_key"] = settings.anthropic_api_key
    return ChatAnthropic(**kwargs)


async def call_model(state: AgentState) -> dict:
    """agent 节点：携带工具定义调用模型，返回模型回复。"""
    llm = _build_llm().bind_tools(all_tools())
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}
