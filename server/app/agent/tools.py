"""Agent 可用工具。

使用 @tool 装饰器定义，LangGraph 的 ToolNode 会自动执行。
在这里添加你自己的工具函数即可。
"""

from datetime import datetime

from langchain_core.tools import BaseTool, tool


@tool
def get_current_time() -> str:
    """获取当前的日期和时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 本地工具注册表
TOOLS = [get_current_time]

# MCP 工具（chuance-policy-mcp 等），由应用启动时（lifespan）异步加载后填充
MCP_TOOLS: list[BaseTool] = []


def all_tools() -> list[BaseTool]:
    """本地工具 + MCP 工具的完整列表，graph 构建时统一引用。"""
    return [*TOOLS, *MCP_TOOLS]
