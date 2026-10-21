"""日期信号（发布日期 / 成文日期）的纯函数回归——不联网、不连库。

    cd server && uv run python scripts/test_dates.py

覆盖：
1. 页面元数据：JSON-LD / `<meta>` / `<time>`；
2. 正文落款：机关名 + 日期、日期独占一行、中文数字、日期后紧跟「印发」；
3. 误判防护：施行日期、有效期、页脚版权、正文事件日期、`<time>` 无提示；
4. 详情页集成：没有日期选择器时，落款日期进 `metadata.signed_date`，
   且不写 `date_text`（publish_date）。

预期：末尾输出「全部通过」。任何一项失败都会打印期望/实际并以非零码退出。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.config import DetailConfig  # noqa: E402
from app.sources.date_signals import (  # noqa: E402
    publish_date_from_label,
    publish_date_from_label_text,
    publish_date_from_meta,
    signature_date,
)
from app.sources.html_source import _date_from_item_text, parse_detail_page  # noqa: E402
from app.sources.htmlparse import parse_html, select  # noqa: E402

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    if actual != expected:
        FAILURES.append(f"{name}: 期望 {expected!r}，实际 {actual!r}")
        print(f"✗ {name}: 期望 {expected!r}，实际 {actual!r}")
    else:
        print(f"✓ {name}")


def hit(value):
    """把 DateHit 压成 ISO 字符串，便于断言。"""
    return value.value.isoformat() if value is not None else None


def test_publish_meta() -> None:
    jsonld = """
    <html><head>
      <script type="application/ld+json">
        {"@type": "NewsArticle", "datePublished": "2026-09-08T10:30:00+08:00"}
      </script>
    </head><body></body></html>
    """
    check("JSON-LD datePublished", hit(publish_date_from_meta(jsonld)), "2026-09-08")

    meta = '<html><head><meta name="pubdate" content="2026-09-07 09:00"></head></html>'
    check("meta pubdate", hit(publish_date_from_meta(meta)), "2026-09-07")

    article = (
        '<html><head><meta property="article:published_time" '
        'content="2026-09-06T08:00:00+08:00"></head></html>'
    )
    check("meta article:published_time", hit(publish_date_from_meta(article)), "2026-09-06")

    time_tag = '<html><body><div class="meta"><time datetime="2026-09-05">9月5日</time></div></body></html>'
    check("<time> 带日期提示", hit(publish_date_from_meta(time_tag)), "2026-09-05")

    # 正文里的 <time>（事件日期）没有日期提示，不能被当成发布日期
    noise = '<html><body><article><time datetime="2026-09-04">活动时间</time></article></body></html>'
    check("<time> 无提示不采信", hit(publish_date_from_meta(noise)), None)

    future = '<html><head><meta name="pubdate" content="2999-01-01"></head></html>'
    check("未来日期不采信", hit(publish_date_from_meta(future)), None)


def test_publish_label() -> None:
    # 用户实测页面的形态：成文日期在元信息块，页头另有一个「日期：…来源：…」
    page = parse_html(
        "<html><body><h1>政策标题</h1>"
        "<div>日期： 2026-09-23 11:09来源： 省政府办公厅字体：[ 大 中 小 ]</div>"
        "<div>成文日期：2026-07-27</div></body></html>"
    )
    check("页头日期 + 来源", hit(publish_date_from_label(page)), "2026-09-23")

    check(
        "纯日期、无来源不采信",
        hit(publish_date_from_label(parse_html("<div>日期：2026-08-27</div>"))),
        None,
    )
    check(
        "成文日期 + 来源不采信",
        hit(
            publish_date_from_label(
                parse_html("<div>成文日期：2026-07-27 来源：省政府办公厅</div>")
            )
        ),
        None,
    )
    check(
        "发布日期标签",
        hit(publish_date_from_label(parse_html("<div>发布日期：2026-09-23</div>"))),
        "2026-09-23",
    )
    check(
        "可见文本兜底",
        hit(publish_date_from_label_text("日期：2026-09-23 11:09 来源：省政府办公厅")),
        "2026-09-23",
    )


def test_signature() -> None:
    body = "这是正文第一段。\n\n国务院\n2026年9月1日"
    check("机关名 + 日期独占一行", hit(signature_date(body)), "2026-09-01")

    body = "正文。\n\nXX市人民政府\n二〇二六年九月八日"
    check("中文数字落款", hit(signature_date(body)), "2026-09-08")

    body = "正文。\n\n国务院 2026年9月1日"
    check("机关名与日期同一行", hit(signature_date(body)), "2026-09-01")

    body = "正文。\n\n2026年9月1日印发"
    check("日期后紧跟「印发」", hit(signature_date(body)), "2026-09-01")

    body = "正文。\n\n（2026年9月1日）"
    check("括号包裹的落款日期", hit(signature_date(body)), "2026-09-01")

    body = "正文。\n\n2026年9月1日\n扫一扫在手机打开当前页"
    check("页脚噪声不影响落款", hit(signature_date(body)), "2026-09-01")

    body = "正文。\n\n2026年9月1日\n（此件公开发布）"
    check("落款后带公开说明", hit(signature_date(body)), "2026-09-01")

    body = "正文。\n\n2026年9月1日\n\n2026年9月5日"
    check("多个候选取最后一个", hit(signature_date(body)), "2026-09-05")


def test_signature_rejects() -> None:
    cases = {
        "施行日期": "正文。\n\n2026年9月1日起施行",
        "自……起": "正文。\n\n自2026年9月1日",
        "有效期": "正文。\n\n有效期至2026年9月1日",
        "发布日期不落款": "正文。\n\n2026年9月1日发布",
        "页脚版权": "正文。\n\n版权所有 © 2026-09-01",
        "正文日期区间": "正文。\n\n2026年9月1日至2026年9月5日",
        "日期带时间": "正文。\n\n2026年9月1日 17:30",
        "未来落款": "正文。\n\n2999年9月1日",
        "1990 年前": "正文。\n\n1989年9月1日",
        "来源行": "正文。\n\n来源：中国政府网 2026年9月1日",
    }
    for name, body in cases.items():
        check(f"拒绝：{name}", hit(signature_date(body)), None)


def test_list_item_text() -> None:
    root = parse_html('<ul><li><a href="/a">政策标题</a><span class="date">2026-09-08</span></li></ul>')
    item = select(root, "li")[0]
    check("列表条目文本兜底", _date_from_item_text(item, "政策标题"), "2026-09-08")

    # 标题里的日期不能当发布日期
    root = parse_html('<ul><li><a href="/a">2026年9月8日新闻发布会</a></li></ul>')
    item = select(root, "li")[0]
    check("标题里的日期不采信", _date_from_item_text(item, "2026年9月8日新闻发布会"), None)

    # 列表里明确标成成文日期的，不能顶替发布日期；标成发布时间的可以
    root = parse_html('<ul><li><a href="/a">政策标题</a><span>成文日期：2026年9月1日</span></li></ul>')
    item = select(root, "li")[0]
    check("成文日期不顶替发布日期", _date_from_item_text(item, "政策标题"), None)
    root = parse_html('<ul><li><a href="/a">政策标题</a><span>发布时间：2026年9月1日</span></li></ul>')
    item = select(root, "li")[0]
    check("发布时间兜底可用", _date_from_item_text(item, "政策标题"), "2026-09-01")


def test_detail_integration() -> None:
    html = """
    <html><body>
      <div class="content">
        <p>这是政策正文，足够长的一段内容。</p>
        <p>XX市人民政府</p>
        <p>2026年9月8日</p>
      </div>
    </body></html>
    """
    parsed = parse_detail_page(html, DetailConfig(container_selectors=[".content"]), "http://example.com/a")
    check("详情页：publish_date 保持为空", parsed.date_text, None)
    check("详情页：落款进 signed_date", parsed.extra.get("signed_date"), "2026-09-08")

    html = """
    <html><head><meta name="pubdate" content="2026-09-06"></head><body>
      <div class="content"><p>正文。</p><p>2026年9月8日</p></div>
    </body></html>
    """
    parsed = parse_detail_page(html, DetailConfig(container_selectors=[".content"]), "http://example.com/b")
    check("详情页：meta 补发布日期", parsed.date_text, "2026-09-06")
    check("详情页：落款仍单独保存", parsed.extra.get("signed_date"), "2026-09-08")

    # 用户实测形态：页头「日期：…来源：…」是 09-23，正文里另有 08-27 也不能顶替
    html = """
    <html><body>
      <h1>政策标题</h1>
      <div>日期： 2026-09-23 11:09来源： 省政府办公厅字体：[ 大 中 小 ]</div>
      <div class="content"><p>正文。</p><p>文件提到 2026年8月27日 的事项。</p></div>
    </body></html>
    """
    parsed = parse_detail_page(html, DetailConfig(container_selectors=[".content"]), "http://example.com/c")
    check("详情页：页头发布日期优先", parsed.date_text, "2026-09-23")
    check("详情页：日期来源标为 label", parsed.date_source, "label")


def main() -> int:
    test_publish_meta()
    test_publish_label()
    test_signature()
    test_signature_rejects()
    test_list_item_text()
    test_detail_integration()
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
