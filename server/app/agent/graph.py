"""LangGraph 图构建。

结构（经典 ReAct 循环）：

    START -> agent --(有工具调用)--> tools --+
              ^                              |
              |--------(结束)------> END     |
              |------------------------------+

编译时挂载 InMemorySaver，按 thread_id 区分会话，实现多轮对话记忆。
"""

from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.nodes import call_model
from app.agent.state import AgentState
from app.agent.tools import all_tools


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(all_tools()))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=InMemorySaver())


@lru_cache
def get_graph():
    """进程内单例：复用同一个编译好的图（及其 checkpointer）。"""
    return build_graph()
