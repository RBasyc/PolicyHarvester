"""政策库只读检索（app/services/library_search.py + app/agent/library_tools.py）离线测试。

直接运行：uv run python scripts/test_library_search.py

**完全离线**：不联网、不连数据库。往来源注册表里塞一个假的「浙江省」来源，
逐条验证「Agent 先查来源、再检索、再取正文」这条链路的行为：

1. 列来源：`scope=浙江` 命中该来源，带层级 / 机构 / 栏目
2. 只读检索：`scope=浙江` + 关键词 → 命中条目带机构 / 文号 / 日期 / 链接 / in_library
3. 线索词没命中 → 如实回「没有已启用的来源覆盖该范围」，并摊开可用清单
4. 取正文：标题 / 机构 / 文号 / 日期 / 正文（过长截断并标注）
5. 未知来源 → 报错并列出可用来源（不静默换成默认源）
6. **解析失败不许静默丢条目**：`normalize_item` 抛错的来源要报出来，不能读成「没检索到」
7. 工具层：坏 action / 缺关键词 / 坏日期 → `{"ok": false, "error": ...}`，绝不抛异常
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.library_tools import search_policy_library
from app.services import library_search
from app.sources import registry
from app.sources.base import FulltextResult, PolicyItem, PolicySource

_FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _FAILED
    mark = "OK  " if ok else "FAIL"
    if not ok:
        _FAILED += 1
    print(f"  [{mark}] {label}{('：' + detail) if detail else ''}")


class _FakeConfig:
    """最小配置：层级 / 机构 / 栏目 / 域名——来源清单要展示的就是这些。"""

    level = "province"
    organization = "浙江省人民政府"
    entrypoints = []
    scope = type("Scope", (), {"allowed_domains": ["zj.gov.cn"]})()


class FakeZhejiangSource(PolicySource):
    """假的浙江省来源：检索与取全文都在内存里完成，不发一个请求。"""

    key = "zj_fake"
    label = "浙江省人民政府门户（演示）"
    fetched_by = "fake:zj_fake"

    def __init__(self) -> None:
        self.config = _FakeConfig()

    def search_plan(self, keywords, agencies=None, doc_types=None):
        return [(keyword, None, None) for keyword in (keywords or [None])]

    async def search(self, *, keyword, agency, doc_type, page, limit, date_from, date_to, timeout):
        return [
            {
                "title": f"浙江省人民政府关于{keyword}的实施意见",
                "url": f"https://www.zj.gov.cn/art/{keyword}-1.html",
                "date": "2026-09-12",
                "agency": "浙江省人民政府",
                "doc_no": "浙政发〔2026〕15号",
            },
            {
                "title": f"浙江省关于{keyword}的若干措施",
                "url": f"https://www.zj.gov.cn/art/{keyword}-2.html",
                "date": "2026-08-01",
            },
        ]

    def validate_item(self, item, *, keyword, date_from, date_to, facet_active):
        return True

    async def fetch_fulltext(self, url, policy_id=None, *, timeout=None):
        return FulltextResult(
            body="正文内容。" * 2000,
            title="浙江省人民政府关于科技的实施意见",
            issuer="浙江省人民政府",
            doc_no="浙政发〔2026〕15号",
            date=date(2026, 9, 12),
        )

    def normalize_item(self, raw):
        return PolicyItem(
            title=raw["title"],
            url=raw["url"],
            date=date(2026, 9, 12),
            agency=raw.get("agency"),
            doc_no=raw.get("doc_no"),
            catalog_key=None,
            catalog=None,
        )


class BrokenSource(FakeZhejiangSource):
    """解析必炸的来源：用来验证「解析失败」不会被读成「没检索到」。"""

    key = "zj_broken"
    label = "浙江省某站（解析会失败）"

    def normalize_item(self, raw):
        raise ValueError("正文容器选择器未命中")


async def main() -> int:
    registry.register(FakeZhejiangSource())
    registry.register(BrokenSource())
    # 离线：把内置 gov.cn 摘掉（它要 MCP，联网）
    registry._registry.pop("gov_cn", None)

    print("=== 1. 列来源（scope=浙江）===")
    listed = await library_search.list_sources(scope="浙江")
    labels = [row["label"] for row in listed["sources"]]
    check("命中浙江来源", any("浙江" in label for label in labels), "、".join(labels))
    zj = next((row for row in listed["sources"] if row["key"] == "zj_fake"), None)
    check("带层级/机构", bool(zj) and zj["level"] == "province" and zj["organization"] == "浙江省人民政府")
    check("可检索标记", bool(zj) and zj["searchable"] is True)

    print("\n=== 2. 只读检索（scope=浙江 + 关键词=科技）===")
    found = await library_search.search_sources(keywords=["科技"], scope="浙江")
    hit = next((row for row in found["results"] if row["key"] == "zj_fake"), None)
    check("命中 2 条", bool(hit) and hit["count"] == 2, str(hit and hit["count"]))
    first = hit["items"][0] if hit and hit["items"] else {}
    check("条目带机构/文号/日期", first.get("agency") == "浙江省人民政府"
          and first.get("doc_no") == "浙政发〔2026〕15号" and first.get("date") == "2026-09-12")
    check("条目带原文链接", str(first.get("url") or "").startswith("https://www.zj.gov.cn/"))
    check("带 in_library 标注", "in_library" in first)
    check("总条数", found["total"] == 2, str(found["total"]))

    print("\n=== 3. 线索词没命中（scope=广东）===")
    miss = await library_search.search_sources(keywords=["科技"], scope="广东")
    check("没有来源可检索", miss["total"] == 0 and miss["results"] == [])
    check("如实说明覆盖边界", "没有已启用的来源命中" in (miss.get("note") or ""))
    check("摊开可用清单", bool(miss.get("available")))

    print("\n=== 4. 取正文 ===")
    body = await library_search.fetch_source_fulltext("https://www.zj.gov.cn/art/科技-1.html")
    check("标题/机构/文号/日期", body["title"].startswith("浙江省") and body["agency"] == "浙江省人民政府"
          and body["doc_no"] == "浙政发〔2026〕15号" and body["date"] == "2026-09-12")
    check("正文有内容", body["body_chars"] > 0)
    check("过长截断并标注", body["truncated"] is True and bool(body.get("note")))

    print("\n=== 5. 未知来源 → 报错并列可用清单 ===")
    try:
        await library_search.search_sources(keywords=["科技"], source="不存在的源")
        check("应报错", False)
    except library_search.LibrarySearchError as exc:
        check("报错且带可用清单", "当前可用" in str(exc), str(exc)[:60])

    print("\n=== 6. 解析失败不许静默丢条目 ===")
    broken = await library_search.search_sources(keywords=["科技"], source="zj_broken")
    row = broken["results"][0]
    check("0 条但有原因", row["count"] == 0 and bool(row.get("error")), str(row.get("error"))[:60])
    check("不是「没检索到」", "解析失败" in (row.get("error") or ""))

    print("\n=== 7. 工具层（坏输入不抛异常）===")
    cases = [
        ({"action": "bogus"}, "action 需要是"),
        ({"action": "search"}, "至少一个关键词"),
        ({"action": "search", "keywords": ["科技"], "date_from": "2026/01/01"}, "YYYY-MM-DD"),
        ({"action": "fulltext"}, "需要 url"),
        ({"action": "search", "keywords": ["科技"], "source": "不存在的源"}, "未知来源"),
    ]
    for args, expect in cases:
        raw = await search_policy_library.ainvoke(args)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            check(f"{args} → 返回 JSON", False)
            continue
        ok = payload.get("ok") is False and expect in str(payload.get("error"))
        check(f"{args.get('action')} 坏输入 → ok=false + 中文原因", ok, str(payload.get("error"))[:50])

    raw = await search_policy_library.ainvoke({"action": "search", "keywords": ["科技"], "scope": "浙江"})
    payload = json.loads(raw)
    check("工具正常检索返回 ok=true", payload.get("ok") is True and payload.get("total") == 2)

    print(f"\n{'FAIL: ' + str(_FAILED) + ' 项未通过' if _FAILED else '全部通过'}")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
