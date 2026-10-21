"""政策库只读检索（app/services/library_search.py + app/agent/library_tools.py）离线测试。

直接运行：uv run python scripts/test_library_search.py

**完全离线**：不联网、不连数据库。往来源注册表里塞几个假来源，
逐条验证「Agent 先查来源、再检索、再取正文」这条链路的行为：

1. 列来源：`scope=浙江` 命中该来源，带层级 / 机构 / 栏目
2. 只读检索：`scope=浙江` + 关键词 → 命中条目带机构 / 文号 / **发布日期** / 链接 / in_library
3. 线索词没命中 → 如实回「没有已启用的来源覆盖该范围」，并摊开可用清单
4. 取正文：**口径与流水线对齐**（对齐清单 A1–A4）
   - A1 日期分流：来源声明「全文日期 = 发布日期」才给 `publish_date`；
     gov.cn 那一路（成文日期）`publish_date` 为空、`signed_date` 另给、`date_note` 提醒
   - A2 正文清洗：元信息表与零宽字符不进正文（`raw_chars` > `body_chars`）
   - A3 正文质量：`body_ok` / `body_metrics`；薄壳页被标出来
   - A4 附件：给清单（name / ext / url），不是只给个数
5. 未知来源 → 报错并列出可用来源（不静默换成默认源）
6. **解析失败不许静默丢条目**：`normalize_item` 抛错的来源要报出来，不能读成「没检索到」
7. 工具层：坏 action / 缺关键词 / 坏日期 → `{"ok": false, "error": ...}`，绝不抛异常
8. `search_library`（查库内）：坏 action / 缺 policy_id → 同样不抛异常
9. `ingest_policies` 的 `cancel` / `inspect` 动作：无 task_id 时走「最近一次任务」，
   数据库不可用时也只返回中文错误（工具绝不抛异常）

加 `--db` 参数会打开连接池，追加真正读库的用例（成果库列表 / 详情 / 最近一次任务 /
取消任务），跑完关池。
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from app.agent.ingestion_tools import ingest_policies
from app.agent.library_tools import search_library, search_policy_library
from app.services import library_query, library_search
from app.services import ingestion as ingestion_service
from app.sources import registry
from app.sources.base import FulltextResult, PolicyItem, PolicySource

_FAILED = 0

# 零宽字符（U+200B）：gov.cn 页面模板会塞在元信息值开头。用 \u 转义而非字面量——
# 源码里放不可见字符容易被编辑器/工具悄悄剥掉（与 fulltext_clean 同款理由）。
_ZWSP = "\u200b"


def check(label: str, ok: bool, detail: str = "") -> None:
    global _FAILED
    mark = "OK  " if ok else "FAIL"
    if not ok:
        _FAILED += 1
    print(f"  [{mark}] {label}{('：' + detail) if detail else ''}")


def skip(label: str, reason: str) -> None:
    print(f"  [SKIP] {label}：{reason}")


class _FakeConfig:
    """最小配置：层级 / 机构 / 栏目 / 域名——来源清单要展示的就是这些。"""

    level = "province"
    organization = "浙江省人民政府"
    entrypoints = []
    scope = type("Scope", (), {"allowed_domains": ["zj.gov.cn"]})()


class _FakeGovConfig(_FakeConfig):
    organization = "国务院"
    scope = type("Scope", (), {"allowed_domains": ["gov.cn"]})()


class FakeZhejiangSource(PolicySource):
    """假的浙江省来源：检索与取全文都在内存里完成，不发一个请求。

    正文里故意混进**元信息表**与**零宽字符**——用来验证 A2（正文清洗）。
    """

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
        # 元信息块与正文**分开拼**：相邻字面量会先隐式拼接、整体再乘 2000
        # （写成一段的话，元信息块会重复两千次，测的就不是清洗而是拼接语义了）。
        meta_block = (
            "发文机关：浙江省人民政府\n"
            "发文字号：浙政发〔2026〕15号\n"
            "成文日期：2026年09月12日\n"
            f"来源：{_ZWSP}省政府办公厅\n"
            "\n"
        )
        body = meta_block + "政策正文内容。" * 2000
        return FulltextResult(
            body=body,
            title="浙江省人民政府关于科技的实施意见",
            issuer="浙江省人民政府",
            doc_no="浙政发〔2026〕15号",
            date=date(2026, 9, 12),
            attachments=[
                {"url": "https://www.zj.gov.cn/attach/plan.pdf", "name": "行动方案.pdf", "ext": "pdf"},
                {"url": "https://www.zj.gov.cn/attach/list.xlsx", "name": "任务分工表.xlsx", "ext": "xlsx"},
            ],
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


class FakeGovSource(FakeZhejiangSource):
    """假的 gov.cn 来源：**全文接口给的是成文日期**（`fulltext_date_is_publish=False`）。"""

    key = "gov_fake"
    label = "国务院政策文件库（演示）"
    fetched_by = "fake:gov_fake"

    def __init__(self) -> None:
        self.config = _FakeGovConfig()

    # 与 GovCnSource 同款声明：全文日期是成文日期，不是发布日期
    fulltext_date_is_publish = False


class ThinSource(FakeZhejiangSource):
    """薄壳转发通知：正文只有一句话——用来验证 A3（正文质量）。"""

    key = "zj_thin"
    label = "浙江省某转发通知（薄壳）"

    async def fetch_fulltext(self, url, policy_id=None, *, timeout=None):
        return FulltextResult(
            body="现将《浙江省人工智能产业发展实施方案》印发给你们。",
            title="关于印发《浙江省人工智能产业发展实施方案》的通知",
            issuer="浙江省人民政府办公厅",
            date=date(2026, 9, 12),
        )


class BrokenSource(FakeZhejiangSource):
    """解析必炸的来源：用来验证「解析失败」不会被读成「没检索到」。"""

    key = "zj_broken"
    label = "浙江省某站（解析会失败）"

    def normalize_item(self, raw):
        raise ValueError("正文容器选择器未命中")


async def main() -> int:
    parser = argparse.ArgumentParser(description="政策库只读检索与成果库查询测试")
    parser.add_argument("--db", action="store_true",
                        help="打开数据库连接池，追加读库用例（成果库 / 任务）；需 server-db-1 与已建表")
    args = parser.parse_args()
    args_db = bool(args.db)

    registry.register(FakeZhejiangSource())
    registry.register(FakeGovSource())
    registry.register(ThinSource())
    registry.register(BrokenSource())
    # 注意：**不要**把内置 gov.cn 从注册表摘掉——`app/schemas/task.py` 在 import 时要读
    # 它的文种白名单（`get_default().doc_types`），摘掉会让首次导入 schemas 直接炸。
    # gov.cn 不匹配「浙江」线索词，天然不会参与下面的检索。

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
    check("条目带机构/文号/发布日期", first.get("agency") == "浙江省人民政府"
          and first.get("doc_no") == "浙政发〔2026〕15号" and first.get("publish_date") == "2026-09-12")
    check("条目带原文链接", str(first.get("url") or "").startswith("https://www.zj.gov.cn/"))
    check("带 in_library 标注", "in_library" in first)
    check("总条数", found["total"] == 4, f'{found["total"]}（两个可检索的浙江来源各 2 条 + 解析失败源 0 条）')

    print("\n=== 3. 线索词没命中（scope=广东）===")
    miss = await library_search.search_sources(keywords=["科技"], scope="广东")
    check("没有来源可检索", miss["total"] == 0 and miss["results"] == [])
    check("如实说明覆盖边界", "没有已启用的来源命中" in (miss.get("note") or ""))
    check("摊开可用清单", bool(miss.get("available")))

    print("\n=== 4. 取正文：口径与流水线对齐（A1–A4）===")
    body = await library_search.fetch_source_fulltext("https://www.zj.gov.cn/art/科技-1.html")
    # A1：这一路的全文日期 = 发布日期 → publish_date 有值
    check("A1 发布日期有值", body["publish_date"] == "2026-09-12", str(body.get("publish_date")))
    check("A1 无 date_note", body.get("date_note") is None)
    # A2：正文清洗（元信息表 / 零宽字符不进正文）
    check("A2 正文无元信息表", "发文机关：" not in body["body"])
    check("A2 正文无零宽字符", _ZWSP not in body["body"])
    check("A2 raw_chars > body_chars", body["raw_chars"] > body["body_chars"],
          f'{body["raw_chars"]} > {body["body_chars"]}')
    # A3：正文质量
    check("A3 给预检指标", "cjk" in body["body_metrics"] and "sent_enders" in body["body_metrics"])
    check("A3 正文达标", body["body_ok"] is True and body.get("body_note") is None)
    # A4：附件清单
    check("A4 附件清单", len(body["attachments"]) == 2
          and body["attachments"][0]["name"] == "行动方案.pdf"
          and body["attachments"][0]["ext"] == "pdf"
          and body["attachments"][0]["url"].endswith("plan.pdf"))
    check("A4 附件总数", body["attachments_count"] == 2)
    check("机构/文号", body["agency"] == "浙江省人民政府" and body["doc_no"] == "浙政发〔2026〕15号")
    check("标题", bool(body["title"]))

    print("\n=== 4b. gov.cn 那一路：成文日期不许冒充发布日期 ===")
    gov = await library_search.fetch_source_fulltext(
        "https://www.gov.cn/zhengce/content/2026-09/12/content_1.htm"
    )
    check("A1 publish_date 为空", gov["publish_date"] is None, str(gov.get("publish_date")))
    check("A1 date_note 提醒", "成文日期" in (gov.get("date_note") or ""))
    check("A1 成文日期另给", gov["signed_date"] == "2026-09-12", str(gov.get("signed_date")))

    print("\n=== 4c. 薄壳通知：正文不达标要标出来 ===")
    thin = await library_search.fetch_source_fulltext("https://www.zj.gov.cn/art/科技-1.html", source="zj_thin")
    check("A3 body_ok=false", thin["body_ok"] is False)
    check("A3 给出原因", "正文过短" in (thin.get("body_note") or ""), str(thin.get("body_note")))

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
    check(
        "工具正常检索返回 ok=true",
        payload.get("ok") is True and payload.get("total") == 4,
        str(payload.get("total")),
    )

    raw = await search_policy_library.ainvoke(
        {"action": "fulltext", "url": "https://www.zj.gov.cn/art/科技-1.html"}
    )
    payload = json.loads(raw)
    check("工具取正文返回 ok=true", payload.get("ok") is True and bool(payload.get("body")))
    check("工具取正文给发布日期", payload.get("publish_date") == "2026-09-12")

    print("\n=== 8. search_library（查库内）：坏输入不抛异常 ===")
    for args, expect in (
        ({"action": "bogus"}, "action 需要是"),
        ({"action": "detail"}, "需要 policy_id"),
        ({"action": "detail", "policy_id": "abc"}, "policy_id"),
    ):
        raw = await search_library.ainvoke(args)
        payload = json.loads(raw)
        ok = payload.get("ok") is False and expect in str(payload.get("error"))
        check(f"{args} → ok=false + 中文原因", ok, str(payload.get("error"))[:50])

    print("\n=== 9. ingest_policies 的 cancel / inspect：坏输入不抛异常 ===")
    # 离线（无库）：只断言「返回 ok=false + 中文原因」，具体文案在 --db 用例里断言
    for args in (
        {"action": "cancel", "task_id": 999999},
        {"action": "inspect", "task_id": 999999},
        {"action": "inspect"},
    ):
        raw = await ingest_policies.ainvoke(args)
        payload = json.loads(raw)
        message = str(payload.get("error") or "")
        ok = payload.get("ok") is False and bool(message) and message != "None"
        check(f"{args} → ok=false + 中文原因（不抛异常）", ok, message[:60])

    print("\n=== 10. facets：某来源能按什么筛（离线走静态名单）===")
    raw = await search_policy_library.ainvoke({"action": "facets"})
    payload = json.loads(raw)
    check("facets 返回 ok=true", payload.get("ok") is True)
    check("facets 给机构名单", isinstance(payload.get("agencies"), list))
    check("facets 给文种名单", isinstance(payload.get("doc_types"), list))
    check("facets 说明筛选在哪一层生效", bool(payload.get("note")))

    print("\n=== 11. manage_schedule / import_source：坏输入不抛异常 ===")
    from app.agent.onboarding_tools import import_source
    from app.agent.schedule_tools import manage_schedule

    cases = [
        (manage_schedule, {"action": "bogus"}, "action 需要是"),
        (manage_schedule, {"action": "toggle"}, "需要 job_id"),
        (manage_schedule, {"action": "run"}, "需要 job_id"),
        (manage_schedule, {"action": "delete"}, "需要 job_id"),
        # 周期参数缺失 / 非法：由 ScheduleCreate 的校验器拦（不进数据库）
        (manage_schedule, {"action": "create", "schedule_kind": "weekly"}, "运行时刻"),
        (manage_schedule, {"action": "create", "schedule_kind": "daily", "run_time": "25:00"}, "运行时刻"),
        (manage_schedule, {"action": "create", "schedule_kind": "minutely"}, "间隔数值"),
        (import_source, {"action": "bogus"}, "action 需要是"),
        (import_source, {"action": "status"}, "需要 job_id"),
        (import_source, {"action": "import"}, "需要 url"),
        (import_source, {"action": "save_browser"}, "需要 url"),
        (import_source, {"action": "import", "url": "not-a-url"}, "地址不可用"),
    ]
    for tool_fn, args, expect in cases:
        raw = await tool_fn.ainvoke(args)
        payload = json.loads(raw)
        ok = payload.get("ok") is False and expect in str(payload.get("error"))
        check(f"{tool_fn.name} {args} → ok=false + 中文原因", ok, str(payload.get("error"))[:60])

    if args_db:
        await db_cases()

    print(f"\n{'FAIL: ' + str(_FAILED) + ' 项未通过' if _FAILED else '全部通过'}")
    return 1 if _FAILED else 0


async def db_cases() -> None:
    """需要数据库的用例（`--db`）：成果库列表 / 详情 / 最近一次任务 / 取消任务。"""
    from app.db import repositories as repo
    from app.db.pool import close_pool, open_pool

    await open_pool()  # 连不上只告警不抛；下面的判定会如实说「测不了」
    try:
        print("\n=== 10. 成果库（--db）===")
        listed = await library_query.list_library(limit=5)
        check("成果库列表可查", listed.get("ok") is True and isinstance(listed.get("total"), int),
              f'total={listed.get("total")}')
        items = listed.get("items") or []
        if not items:
            skip("成果库详情可读", "库里没有篇目（先在页面上抓一次）")
        else:
            first = items[0]
            check("条目含 id/标题/链接", bool(first.get("id")) and bool(first.get("title")) and
                  bool(first.get("source_url")))
            detail = await library_query.get_library_detail(first["id"])
            check("详情可读（policy + extraction）",
                  detail.get("ok") is True and "policy" in detail and "extraction" in detail)
            missing = False
            try:
                await library_query.get_library_detail(999999999)
            except library_query.LibraryQueryError as exc:
                missing = "库里没有这篇" in str(exc)
            check("不存在的篇目报错（中文）", missing)

        print("\n=== 11. 任务：最近一次 / 日志 / 取消（--db）===")
        try:
            view = await ingestion_service.inspect_ingestion()
            check("inspect 省略 task_id → 最近一次任务", view.get("task_id") is not None,
                  str(view.get("task_id")))
            check("inspect 带任务日志", "logs" in view and "logs_truncated" in view)
        except ingestion_service.IngestionNotFound as exc:
            skip("inspect 省略 task_id → 最近一次任务", str(exc))

        missing = False
        try:
            await ingestion_service.inspect_ingestion(999999999)
        except ingestion_service.IngestionNotFound as exc:
            missing = "任务不存在" in str(exc)
        check("inspect 不存在的任务 → 中文报错", missing)

        # 造一个 pending 任务 → 终止 → 落 failed → 删掉（不留脏数据）
        task_id = await repo.create_task(
            source="国务院政策文件库（gov.cn）",
            date_from=None,
            date_to=None,
            keywords=["演示用任务"],
        )
        try:
            result = await ingestion_service.cancel_ingestion(task_id)
            check("cancel 终止任务并落 failed", result.get("status") == "failed", str(result))
            again = False
            try:
                await ingestion_service.cancel_ingestion(task_id)
            except ingestion_service.IngestionConflict as exc:
                again = "已结束" in str(exc)
            check("cancel 终态任务 → 冲突（中文）", again)
        finally:
            await repo.delete_task(task_id)

        missing = False
        try:
            await ingestion_service.cancel_ingestion(999999999)
        except ingestion_service.IngestionNotFound as exc:
            missing = "任务不存在" in str(exc)
        check("cancel 不存在的任务 → 中文报错", missing)
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
