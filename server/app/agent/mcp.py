"""MCP 工具接入：加载 China-Central-Policy-MCP（国务院政策检索与解析）。

通过 stdio 方式以 node 启动本地构建产物（tools/China-Central-Policy-MCP/build/index.js），
无需 API Key。langchain-mcp-adapters 会把 MCP 工具转成 LangChain BaseTool，
可直接交给 ToolNode 执行、bind_tools 绑定。

提供的工具：
- get_latest_policies：按关键词 / 日期范围检索国务院政策列表
- get_policy_fulltext：抓取并解析指定 gov.cn 政策全文

服务不可用时返回空列表，由调用方降级为仅本地工具，不影响主服务启动。
"""

import json
import logging
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agent.tools import all_tools
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


# ---------- 结果解析 ----------
#
# MCP 工具统一把结果放在 content 里（`[{"type": "text", "text": "<JSON 字符串>"}]`），
# 由 langchain-mcp-adapters 转成 LangChain 工具输出。不同版本可能是 str、
# content 块列表或 (content, artifact) 元组，这里统一收敛成一个字符串 / 解析好的对象。


def tool_text(result: Any) -> str:
    """把 MCP 工具返回值摊平成纯文本。"""
    if isinstance(result, tuple):  # (content, artifact)
        return tool_text(result[0])
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for block in result:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(result)


def tool_json(result: Any) -> Any:
    """解析 MCP 工具返回的 JSON 文本；解析失败抛 ValueError（由调用方标记失败）。"""
    text = tool_text(result).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP 返回内容不是合法 JSON：{text[:200]}") from exc


def find_tool(name: str) -> BaseTool | None:
    """按名字取工具（本地 + MCP）；未加载到返回 None。"""
    return next((t for t in all_tools() if t.name == name), None)
