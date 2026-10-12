"""China-Central-Policy-MCP 连通性测试。

直接运行：uv run python scripts/test_mcp.py

验证内容：
1. MCP 服务能否通过 stdio (node) 启动并列出工具
2. 调用 get_latest_policies（关键词检索，轻量）验证调用链路
"""

import asyncio
import sys
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.mcp import load_mcp_tools


async def main() -> int:
    print("=== 1. 加载 MCP 工具 ===")
    tools = await load_mcp_tools()
    if not tools:
        print("FAIL: 未加载到任何 MCP 工具（详见上方日志）")
        return 1
    for t in tools:
        print(f"  - {t.name}: {(t.description or '').splitlines()[0][:60]}")

    print("\n=== 2. 调用 get_latest_policies ===")
    search = next(t for t in tools if t.name == "get_latest_policies")
    result = await search.ainvoke({"keyword": "人工智能", "limit": 3})
    text = result if isinstance(result, str) else str(result)
    print(text[:500])

    print("\nOK: MCP 接入测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
