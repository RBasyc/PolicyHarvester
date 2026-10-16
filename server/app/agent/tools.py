"""Agent 可用工具。

使用 @tool 装饰器定义，LangGraph 的 ToolNode 会自动执行。
在这里添加你自己的工具函数即可。

工具分两块：本文件的通用工具，以及 `onboarding_tools.py` 的**网站来源接入**工具
（inspect_source / dry_run_ingestion / register_source 等 9 个，阶段边界见该文件 docstring）。
后者只依赖服务层、不依赖 MCP，所以可以顶层导入并直接进 `TOOLS`。
"""

from datetime import datetime

from langchain_core.tools import BaseTool, tool

from app.agent.onboarding_tools import ONBOARDING_TOOLS


@tool
def get_current_time() -> str:
    """获取当前的日期和时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 本地工具注册表
TOOLS = [get_current_time, *ONBOARDING_TOOLS]

# MCP 工具（China-Central-Policy-MCP 等），由应用启动时（lifespan）异步加载后填充
MCP_TOOLS: list[BaseTool] = []


def all_tools() -> list[BaseTool]:
    """本地工具 + MCP 工具的完整列表，graph 构建时统一引用。"""
    return [*TOOLS, *MCP_TOOLS]
