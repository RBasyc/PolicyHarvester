"""MCP 工具接入：加载 China-Central-Policy-MCP（国务院政策检索与解析）。

通过 stdio 方式以 node 启动本地构建产物（tools/China-Central-Policy-MCP/build/index.js），
无需 API Key。langchain-mcp-adapters 会把 MCP 工具转成 LangChain BaseTool，
可直接交给 ToolNode 执行、bind_tools 绑定。

提供的工具：
- get_latest_policies：按关键词 / 日期范围检索国务院政策列表
- get_policy_fulltext：抓取并解析指定 gov.cn 政策全文

服务不可用时返回空列表，由调用方降级为仅本地工具，不影响主服务启动。
"""

import logging

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import get_settings

logger = logging.getLogger(__name__)


def _build_client() -> MultiServerMCPClient:
    settings = get_settings()
    return MultiServerMCPClient(
        {
            "central-policy": {
                "command": settings.mcp_policy_command,
                "args": settings.mcp_policy_args,
                "transport": "stdio",
            }
        }
    )


async def load_mcp_tools() -> list[BaseTool]:
    """加载 MCP 工具列表；未启用或连接失败时返回空列表。"""
    settings = get_settings()
    if not settings.mcp_policy_enabled:
        logger.info("central-policy MCP 未启用，跳过加载")
        return []
    try:
        tools = await _build_client().get_tools()
        logger.info(
            "central-policy MCP 已加载 %d 个工具: %s",
            len(tools),
            [t.name for t in tools],
        )
        return tools
    except Exception:
        logger.exception("central-policy MCP 加载失败，降级为仅本地工具")
        return []
