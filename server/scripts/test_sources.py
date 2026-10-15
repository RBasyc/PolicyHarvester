"""抓取源适配层（app/sources）连通性测试。

直接运行：uv run python scripts/test_sources.py

验证内容：
1. 注册表已加载的抓取源列表（含默认源 gov.cn）
2. GovCnSource.search_plan 的关键词 × 机构 × 文种组合扇出
3. 用适配器跑一页真实检索（关键词=人工智能，近一个月，3 条）并打印 PolicyItem
"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.mcp import load_mcp_tools
from app.sources import all_sources, get, get_by_label


async def main() -> int:
    print("=== 0. 加载 MCP 工具 ===")
    tools = await load_mcp_tools()
    if not tools:
        print("FAIL: 未加载到任何 MCP 工具（请先构建 tools/China-Central-Policy-MCP）")
        return 1
    # 与 main.py lifespan 一样，把加载到的工具填进全局注册表，find_tool 才能找到
    from app.agent.tools import MCP_TOOLS
    MCP_TOOLS.extend(tools)

    print("\n=== 1. 注册表 ===")
    sources = all_sources()
    for s in sources:
        print(f"  - {s.key}: {s.label}（fetched_by={s.fetched_by}，文种 {len(s.doc_types)} 个）")
    if not sources:
        print("FAIL: 注册表为空")
        return 1
    print(f"  get_by_label(未知来源) -> {get_by_label('不存在的来源').key}（应回退默认源）")

    source = get("gov_cn")
    if source is None:
        print("FAIL: 未注册 gov_cn 源")
        return 1

    print("\n=== 2. search_plan 组合扇出 ===")
    combos = source.search_plan(["人工智能"], ["工业和信息化部"], ["国发"])
    print(f"  1 关键词 × 1 机构 × 1 文种 = {len(combos)} 个组合: {combos}")
    assert len(combos) == 1

    print("\n=== 3. search + normalize_item（真实检索）===")
    today = date.today()
    month_ago = today - timedelta(days=30)
    raw_items = await source.search(
        keyword="人工智能",
        agency=None,
        doc_type=None,
        page=1,
        limit=3,
        date_from=month_ago,
        date_to=today,
        timeout=60,
    )
    print(f"  返回 {len(raw_items)} 条原始条目")
    for raw in raw_items[:3]:
        ok = source.validate_item(
            raw, keyword="人工智能", date_from=month_ago, date_to=today,
            facet_active=False,
        )
        item = source.normalize_item(raw)
        print(f"  - validate={'通过' if ok else '丢弃'} | {item}")
    if not raw_items:
        print("WARN: 未返回任何结果（可能是限流或窗口内确无匹配，稍后重试）")

    print("\nOK: 抓取源适配层测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
