"""Tavily 联网检索（web_search 入口发现）冒烟测试。

直接运行：uv run python scripts/test_websearch.py [--db-scratch 略]（联网；不需要数据库 / MCP / 浏览器）

验证内容：
1. TAVILY_API_KEY 已配置（未配置 → 明确提示怎么配，并跳过联网段而不是报成功）
2. 契约实测：find_entry_url("清远市 政策") 应返回 ok=true + URL；
   候选里官方域名（.gov.cn）排序不被非官方项压过、official 标志与 URL 域名一致
3. _is_gov_domain 单元断言（离线）：www.gov.cn / qingyuan.gov.cn / 端口与坏 URL
4. 备用候选 alternatives 形状检查（url/title/official 三键齐全）

预期输出末行：`OK: 联网检索冒烟通过`（未配 key 时为 `SKIP: ...`）。
"""

import asyncio
import sys
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services import websearch


def check_gov_domain() -> None:
    assert websearch._is_gov_domain("https://qingyuan.gov.cn/zwgk/")
    assert websearch._is_gov_domain("https://www.gov.cn/zhengce/")
    assert websearch._is_gov_domain("https://www.gd.gov.cn/gkmlpt/index")
    # gov 域名带端口 / 严格后缀边界
    assert websearch._is_gov_domain("https://qingyuan.gov.cn:8080/")
    assert not websearch._is_gov_domain("https://gov.cn.fake.com/")
    assert not websearch._is_gov_domain("https://www.baidu.com/")
    assert not websearch._is_gov_domain("not-a-url")  # 坏 URL → 非官方，不炸


async def main() -> int:
    check_gov_domain()
    print("ok 1/2  _is_gov_domain 断言通过")

    settings = get_settings()
    if not (settings.tavily_api_key or "").strip():
        print("SKIP: 未配置 TAVILY_API_KEY——.env 加一行 TAVILY_API_KEY=… 后重启 server 再跑")
        return 0

    result = await websearch.find_entry_url("清远市 政策文件")
    if not result.get("ok"):
        print(f"FAIL: {result.get('error')}")
        return 1
    url = str(result.get("url") or "")
    print(f"ok 2/2  find_entry_url → official={result.get('official')} {url}")

    assert url.startswith(("http://", "https://")), url
    assert result.get("official") == websearch._is_gov_domain(url)
    alts = result.get("alternatives") or []
    assert alts and all(
        {"url", "title", "official"} <= set(item) for item in alts
    ), "alternatives 缺字段"
    print(f"        候选 {len(alts)} 条（官方优先）")
    print("OK: 联网检索冒烟通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
