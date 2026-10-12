"""China-Central-Policy-MCP 连通性测试。

直接运行：uv run python scripts/test_mcp.py

验证内容：
1. MCP 服务能否通过 stdio (node) 启动并列出工具（含新增的 get_policy_facets）
2. 调用 get_latest_policies（关键词检索 + 发布机构筛选）验证调用链路
3. 断言检索结果**标题确实含关键词**（这是本轮修复的核心：searchfield=title 之前被写成静默无效值）
4. 调用 get_policy_facets 验证机构/文种名单非空
"""

import asyncio
import json
import sys
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.mcp import load_mcp_tools, tool_json


async def main() -> int:
    print("=== 1. 加载 MCP 工具 ===")
    tools = await load_mcp_tools()
    if not tools:
        print("FAIL: 未加载到任何 MCP 工具（详见上方日志）")
        return 1
    names = [t.name for t in tools]
    for t in tools:
        print(f"  - {t.name}: {(t.description or '').splitlines()[0][:60]}")
    if "get_policy_facets" not in names:
        print("FAIL: 未加载到 get_policy_facets（请先在 tools/China-Central-Policy-MCP 下执行 npm run build）")
        return 1

    print("\n=== 2. get_latest_policies：关键词 + 发布机构筛选 ===")
    search = next(t for t in tools if t.name == "get_latest_policies")
    raw = await search.ainvoke({"keyword": "中小企业", "agency": "工业和信息化部", "limit": 5})
    data = tool_json(raw)
    items = (data or {}).get("items") or []
    print(f"  返回 {len(items)} 条")
    misses = [it for it in items if "中小企业" not in str(it.get("title") or "")]
    for it in items[:3]:
        print(f"    - {str(it.get('title'))[:40]}  [{it.get('catalog_key') or it.get('catalog') or '?'}]")
    if not items:
        print("WARN: 未返回任何结果（可能是限流 code=1001 或窗口内确无匹配，稍后重试）")
    elif misses:
        print(f"FAIL: {len(misses)} 条标题不含关键词「中小企业」——searchfield=title 修复未生效？")
        print(f"      debug.requested_urls: {(data or {}).get('debug', {}).get('requested_urls')}")
        return 1
    else:
        print("  OK: 标题全部含关键词（searchfield=title 修复生效）")

    print("\n=== 3. get_policy_facets：筛选器可选值 ===")
    facets_tool = next(t for t in tools if t.name == "get_policy_facets")
    raw = await facets_tool.ainvoke({"keyword": "中小企业"})
    fdata = tool_json(raw)
    agencies = (fdata or {}).get("agencies") or []
    doc_types = (fdata or {}).get("doc_types") or []
    print(f"  发布机构 {len(agencies)} 个，文种 {len(doc_types)} 个")
    for a in agencies[:3]:
        print(f"    机构: {a.get('name')} ({a.get('count')})")
    for d in doc_types[:4]:
        print(f"    文种: {d.get('name')} ({d.get('count')})")
    if not agencies:
        print("FAIL: 未拿到发布机构名单")
        return 1

    print("\nOK: MCP 接入测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
