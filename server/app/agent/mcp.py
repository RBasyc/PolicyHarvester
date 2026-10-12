"""MCP 工具接入：加载 chuance-policy-mcp（川策智行 · 权威政策检索）。

通过 stdio 方式以 uvx 启动 MCP 服务，无需 API Key。
langchain-mcp-adapters 会把 MCP 工具转成 LangChain BaseTool，
可直接交给 ToolNode 执行、bind_tools 绑定。

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
            "chuance-policy": {
                "command": settings.mcp_chuance_command,
                "args": settings.mcp_chuance_args,
                "transport": "stdio",
            }
        }
    )


async def load_mcp_tools() -> list[BaseTool]:
    """加载 MCP 工具列表；未启用或连接失败时返回空列表。"""
    settings = get_settings()
    if not settings.mcp_chuance_enabled:
        logger.info("chuance-policy MCP 未启用，跳过加载")
        return []
    try:
        tools = await _build_client().get_tools()
        logger.info(
            "chuance-policy MCP 已加载 %d 个工具: %s",
            len(tools),
            [t.name for t in tools],
        )
        return tools
    except Exception:
        logger.exception("chuance-policy MCP 加载失败，降级为仅本地工具")
        return []
