"""图节点：调用 LLM 的 agent 节点。

工具执行节点由 LangGraph 预置的 ToolNode 承担，无需手写。
"""

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage

from app.agent.state import AgentState
from app.agent.tools import all_tools
from app.config import get_settings
from app.prompts import load_prompt


def _build_llm(
    model: str | None = None,
    max_tokens: int | None = None,
    *,
    thinking: bool | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatAnthropic:
    """构建 Anthropic（兼容网关/端点）聊天模型；参数缺省时用 .env 的 MODEL_NAME / MAX_TOKENS。

    提取流水线会按「高级设置」选的模型传入覆盖值（见 app/pipeline/extract.py）：
    - `base_url` / `api_key`：注册表里自带端点的模型（如 DeepSeek 的 Anthropic 兼容地址）
      用它们做 x-api-key 认证；缺省时沿用 ANTHROPIC_* 网关配置；
    - `thinking=False`：显式关思考（`{"type": "disabled"}`）。None/True = 不发该参数、
      交给服务端默认——chat agent 走的就是这条，行为不受提取模型注册表影响。
    """
    settings = get_settings()
    kwargs: dict = {
        "model": model or settings.model_name,
        "base_url": base_url or settings.anthropic_base_url,
        "max_tokens": max_tokens or settings.max_tokens,
    }
    if api_key:
        # 注册表项自带的 key（如 DeepSeek）：标准 x-api-key 认证，不走网关 Bearer
        kwargs["api_key"] = api_key
    elif settings.anthropic_auth_token:
        # ChatAnthropic 不支持 auth_token 参数，Bearer 认证通过 default_headers 传递
        kwargs["default_headers"] = {
            "Authorization": f"Bearer {settings.anthropic_auth_token}"
        }
    elif settings.anthropic_api_key:
        kwargs["api_key"] = settings.anthropic_api_key
    if thinking is False:
        kwargs["thinking"] = {"type": "disabled"}
    return ChatAnthropic(**kwargs)


async def call_model(state: AgentState) -> dict:
    """agent 节点：携带工具定义调用模型，返回模型回复。"""
    llm = _build_llm().bind_tools(all_tools())
    # 提示词**在调用时**从 prompts/chat.md 读（不进图、不进缓存）：改完即刻生效，不用重启
    messages = [SystemMessage(content=load_prompt("chat")), *state["messages"]]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}
