"""chuance-policy-mcp 连通性测试。

直接运行：uv run python scripts/test_mcp.py

验证内容：
1. MCP 服务能否通过 stdio (uvx) 启动并列出工具
2. 调用 source_catalog（数据源清单，轻量、不依赖外部检索）验证调用链路
"""

import asyncio
import sys

from app.agent.mcp import load_mcp_tools


async def main() -> int:
    print("=== 1. 加载 MCP 工具 ===")
    tools = await load_mcp_tools()
    if not tools:
        print("FAIL: 未加载到任何 MCP 工具（详见上方日志）")
        return 1
    for t in tools:
        print(f"  - {t.name}: {(t.description or '').splitlines()[0][:60]}")

    print("\n=== 2. 调用 source_catalog ===")
    catalog = next(t for t in tools if t.name == "source_catalog")
    result = await catalog.ainvoke({})
    text = result if isinstance(result, str) else str(result)
    print(text[:500])

    print("\nOK: MCP 接入测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
