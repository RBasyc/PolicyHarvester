"""Agent 状态定义。

使用 LangGraph 内置的 messages 状态：`add_messages` reducer 负责
消息追加 / 按 id 更新，是所有对话型图的标准状态。
"""

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """图在节点之间传递的状态。"""

    messages: Annotated[list[AnyMessage], add_messages]
