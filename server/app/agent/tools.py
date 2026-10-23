"""Agent 可用工具。

使用 @tool 装饰器定义，LangGraph 的 ToolNode 会自动执行。
在这里添加你自己的工具函数即可。

工具分五块：本文件的通用工具、`library_tools.py` 的**政策库检索**工具
（search_policy_library：列已启用来源 → 只读检索 → 取正文；search_library：查成果库）、
`onboarding_tools.py` 的**网站来源接入**工具
（inspect_source / dry_run_ingestion / register_source 等 9 个，阶段边界见该文件 docstring）、
`ingestion_tools.py` 的**抓取入库**工具（ingest_policies：检索 → 确认 → 提取入库）、
`schedule_tools.py` 的**定时任务**工具（manage_schedule：列表 / 新建 / 启停 / 触发 / 删除）。
后三者只依赖服务层、不依赖 MCP，所以可以顶层导入并直接进 `TOOLS`。
"""

from langchain_core.tools import BaseTool

from app.agent.ingestion_tools import INGESTION_TOOLS
from app.agent.library_tools import LIBRARY_TOOLS
from app.agent.onboarding_tools import ONBOARDING_TOOLS
from app.agent.schedule_tools import SCHEDULE_TOOLS
from app.browser.tools import browser_tools


# 本地工具注册表。
# 当天日期不单独给工具：`call_model` 每次拼系统提示时直接写入「今天是 YYYY-MM-DD」，
# 比让模型多调一次工具便宜，也避免相对时间在工具往返之间漂移。
# 浏览器工具（browser_*）必须无条件进 `ToolNode`，否则模型发出的工具调用会「没有对应工具」；
# 但**绑定给模型**时按引擎可用性过滤（见 agent/nodes.py）：不可用（BROWSER_ENABLED=false
# 或没装 playwright）的对话看不到这些 schema，省 token。
TOOLS = [
    *LIBRARY_TOOLS,
    *ONBOARDING_TOOLS,
    *INGESTION_TOOLS,
    *SCHEDULE_TOOLS,
    *browser_tools(),
]

# MCP 工具（China-Central-Policy-MCP 等），由应用启动时（lifespan）异步加载后填充
MCP_TOOLS: list[BaseTool] = []


def all_tools() -> list[BaseTool]:
    """本地工具 + MCP 工具的完整列表，graph 构建时统一引用。"""
    return [*TOOLS, *MCP_TOOLS]
