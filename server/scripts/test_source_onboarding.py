"""网站来源接入（Phase 14）离线验收测试。

用法（**默认不需要联网、不需要数据库**）：

    uv run python scripts/test_source_onboarding.py            # 只跑离线用例
    uv run python scripts/test_source_onboarding.py --db       # 追加落库用例（需 server-db-1 + 已建表）
    uv run python scripts/test_source_onboarding.py --live     # 追加真实站点勘察（联网）

覆盖四条主线，对应用户要求的「正常接入 / 部分失败 / 无法抓取 / 人工确认」：

1. 解析与安全底座：选择器引擎、列表/详情解析、翻页地址生成、SSRF 校验、编码嗅探；
2. 结构分析：列表容器 / 正文容器 / 元信息 / 翻页 / 栏目入口的候选识别；
3. 配置：可执行性校验、指纹稳定性、非法配置拦下；
4. 试运行与注册：用**假站点**替换取数层，让 `dry_run_ingestion` 走真实代码路径
   （含 `_search_policies` 检索编排、正文预检），分别验证通过 / 失败 / 受阻 /
   人工确认四类结果，并检查验收报告的「通过项 / 失败项 / 未测试项」是否如实。

替换只发生在**网络出口**（`net.fetch_text` / `net.fetch_bytes` / `net.robots_status`)
与判别服务（`judge.judge_rows`）——安全校验、解析、编排、验收判定全部是真的在跑。

数据库：默认不连；连不上或表未建（Phase 14 新增的 4 张表要先跑 `database/init.sh`）时，
需要落库的用例会明确标成「跳过」并写明原因，**不会算通过**（不拿没跑的东西冒充通过）。
`--db` 会打开连接池并跑落库用例，结束前把本次用例建的来源行删掉（不留脏数据）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources import net  # noqa: E402
from app.sources.analyze import analyze_page  # noqa: E402
from app.sources.config import SourceConfig, validate_source_config  # noqa: E402
from app.sources.html_source import build_list_url, parse_detail_page, parse_list_page  # noqa: E402
from app.sources.htmlparse import decode_html, parse_html, select, selector_supported  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
SKIPPED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}{(' — ' + detail) if detail else ''}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}{(' — ' + detail) if detail else ''}")


def skip(name: str, reason: str) -> None:
    SKIPPED.append(name)
    print(f"  [SKIP] {name} — {reason}")


# ---------- 测试用假站点（fixture） ----------

BASE = "https://demo.example.gov.cn"

BODY_PARAGRAPHS = [
    "为深入贯彻落实国家关于推动人工智能产业高质量发展的决策部署，加快构建自主可控的产业生态，"
    "结合本省实际，现提出如下实施意见。",
    "一、总体要求。坚持以应用为牵引、以场景为驱动，到二〇二七年，全省人工智能核心产业规模"
    "力争突破三千亿元，建成一批具有全国影响力的创新平台和产业集群。",
    "二、重点任务。加快算力基础设施建设，统筹布局智能计算中心；支持大模型技术攻关与开源生态"
    "建设，鼓励企业开展行业模型研发和落地应用。",
    "三、保障措施。加强组织领导，建立跨部门协调机制；加大财政金融支持力度，引导社会资本投入；"
    "强化人才引育，完善评价激励机制。",
    "四、工作要求。各地各部门要结合实际制定具体措施，加强跟踪评估，重要情况及时报告省政府。",
    "本实施意见自发布之日起施行，有效期五年。此前有关规定与本意见不一致的，按本意见执行。",
]

LIST_PAGE_1 = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>政策文件 - 浙江省人民政府门户网站</title></head>
<body>
<div class="header"><ul class="nav">
  <li><a href="/">首页</a></li>
  <li><a href="/zwgk/">政务公开</a></li>
  <li><a href="/bmdt/">部门动态</a></li>
</ul></div>
<div id="list">
  <ul class="news-list">
    <li><a href="./art1.html">浙江省人民政府关于加快推进人工智能产业发展的实施意见</a><span class="date">2026-09-12</span></li>
    <li><a href="./art2.html">浙江省人民政府办公厅关于印发数字经济发展三年行动计划的通知</a><span class="date">2026-09-08</span></li>
    <li><a href="./art3.html">浙江省关于进一步优化营商环境的若干措施</a><span class="date">2026-08-30</span></li>
    <li><a href="./art4.html">浙江省人民政府关于印发数据要素市场建设方案的通知</a><span class="date">2026-08-21</span></li>
  </ul>
</div>
<div class="pager"><a href="index_2.html">下一页</a><a href="index_2.html">2</a><a href="index_3.html">3</a></div>
</body></html>"""

LIST_PAGE_2 = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>政策文件 - 浙江省人民政府门户网站</title></head>
<body>
<div id="list">
  <ul class="news-list">
    <li><a href="./art5.html">浙江省人民政府关于支持中小企业数字化转型的通知</a><span class="date">2026-07-15</span></li>
    <li><a href="./art6.html">浙江省关于加强科技创新平台建设的实施意见</a><span class="date">2026-07-02</span></li>
  </ul>
</div>
<div class="pager"><a href="index_3.html">下一页</a><a href="index_1.html">1</a><a href="index_3.html">3</a></div>
</body></html>"""


def detail_page(index: int, title: str, doc_no: str, agency: str, pub_date: str) -> str:
    body = "".join(f"<p>{paragraph}</p>" for paragraph in BODY_PARAGRAPHS)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title} - 浙江省人民政府</title></head>
<body>
<div class="crumb">首页 &gt; 政务公开 &gt; 政策文件</div>
<div class="article">
  <h1>{title}</h1>
  <div class="info">
    <span class="source">来源：{agency}</span>
    <span class="docno">{doc_no}</span>
    <span class="pubdate">发布日期：{pub_date}</span>
  </div>
  <div id="zoom">{body}<div class="attach">
    <a href="./files/art{index}.pdf">附件：实施意见全文（PDF）</a>
    <a href="./files/art{index}.docx">附件：实施意见全文（DOCX）</a>
  </div></div>
</div>
</body></html>"""


DETAIL_TITLES = {
    1: ("浙江省人民政府关于加快推进人工智能产业发展的实施意见", "浙政发〔2026〕15号", "浙江省人民政府", "2026-09-12"),
    2: ("浙江省人民政府办公厅关于印发数字经济发展三年行动计划的通知", "浙政办发〔2026〕42号", "浙江省人民政府办公厅", "2026-09-08"),
    3: ("浙江省关于进一步优化营商环境的若干措施", "浙政发〔2026〕11号", "浙江省人民政府", "2026-08-30"),
    4: ("浙江省人民政府关于印发数据要素市场建设方案的通知", "浙政发〔2026〕9号", "浙江省人民政府", "2026-08-21"),
    5: ("浙江省人民政府关于支持中小企业数字化转型的通知", "浙政发〔2026〕7号", "浙江省人民政府", "2026-07-15"),
    6: ("浙江省关于加强科技创新平台建设的实施意见", "浙政发〔2026〕6号", "浙江省人民政府", "2026-07-02"),
}

GBK_PAGE = (
    "<html><head><title>浙江省人民政府</title></head><body><div id=\"zoom\">"
    "关于加强人工智能产业发展的实施意见</div></body></html>"
).encode("gbk")

# U+FFFD 替换字符（宽容解码的「编不出来」标记）。用 chr() 而非字面量：源码保持纯 ASCII
REPLACEMENT = chr(0xFFFD)


class FakeSite:
    """内存假站点：URL → 页面 / 附件字节 / 指定失败。"""

    def __init__(self, base: str = BASE) -> None:
        self.base = base
        self.pages: dict[str, str] = {}
        self.binaries: dict[str, bytes] = {}
        self.failures: dict[str, str] = {}

    def add(self, path: str, html: str) -> None:
        self.pages[self.base + path] = html

    def add_binary(self, path: str, payload: bytes) -> None:
        self.binaries[self.base + path] = payload

    def fail(self, path: str, message: str) -> None:
        self.failures[self.base + path] = message

    def url(self, path: str) -> str:
        return self.base + path


def build_demo_site() -> FakeSite:
    """搭一个「看起来像政务门户」的假站点：2 页列表 + 6 篇详情 + 2 类附件。"""
    site = FakeSite()
    site.add("/col/index.html", LIST_PAGE_1)
    site.add("/col/index_2.html", LIST_PAGE_2)
    site.add("/col/index_3.html", LIST_PAGE_2.replace("art5.html", "art7.html").replace("art6.html", "art8.html"))
    for index, (title, doc_no, agency, pub_date) in DETAIL_TITLES.items():
        site.add(f"/col/art{index}.html", detail_page(index, title, doc_no, agency, pub_date))
        site.add_binary(f"/col/files/art{index}.pdf", f"%PDF-1.4 demo attachment {index}".encode() * 40)
        site.add_binary(f"/col/files/art{index}.docx", f"PK\x03\x04 demo attachment {index}".encode() * 40)
    return site


def install_fake_network(site: FakeSite, *, robots_allowed: bool = True) -> None:
    """把网络出口换成本地假站点；安全校验 / 编码解码 / 解析仍是真实实现。"""
    import ipaddress

    # 域名→公网 IP 的固定映射：SSRF 校验照跑，但不真做 DNS
    net.set_resolver(lambda host: [ipaddress.ip_address("93.184.216.34")])

    async def fake_fetch_text(url, *, allowed_domains=None, timeout=None, max_bytes=None, respect_robots=None):
        net.check_url(url, allowed_domains=allowed_domains)  # 真跑 SSRF / 白名单校验
        if url in site.failures:
            raise net.FetchError(site.failures[url])
        html = site.pages.get(url)
        if html is None:
            raise net.FetchError(f"HTTP 404：{url}")
        return net.FetchResult(
            url=url, status=200, text=html, raw=html.encode("utf-8"),
            content_type="text/html; charset=utf-8", bytes_len=len(html),
        )

    async def fake_fetch_bytes(url, *, allowed_domains=None, timeout=None, max_bytes=None, respect_robots=None):
        net.check_url(url, allowed_domains=allowed_domains)
        if url in site.failures:
            raise net.FetchError(site.failures[url])
        payload = site.binaries.get(url)
        if payload is None:
            raise net.FetchError(f"HTTP 404：{url}")
        return net.FetchResult(
            url=url, status=200, text="", raw=payload, content_type="application/octet-stream",
            bytes_len=len(payload),
        )

    async def fake_robots(url):
        if robots_allowed:
            return {"checked": True, "found": True, "allowed": True, "reason": ""}
        return {"checked": True, "found": True, "allowed": False, "reason": f"robots.txt 禁止抓取 {url}"}

    net.fetch_text = fake_fetch_text  # type: ignore[assignment]
    net.fetch_bytes = fake_fetch_bytes  # type: ignore[assignment]
    net.robots_status = fake_robots  # type: ignore[assignment]


def install_fake_judge() -> None:
    """判别服务换成本地空实现（离线；生产路径 fail-open 也是同样语义）。"""
    from app.pipeline import judge

    async def fake_judge_rows(rows, *, instructions=None):
        return judge.JudgeBatch()

    judge.judge_rows = fake_judge_rows  # type: ignore[assignment]


async def db_available() -> bool:
    """数据库是否可用（连接池是否已开）。不可用时**涉及落库的用例明确跳过**，不算通过。"""
    from app.db import repositories as repo

    try:
        await repo.list_content_sources()
        return True
    except Exception:  # noqa: BLE001 - 未开池 / 容器没起，都是「测不了」
        return False


# 用例会注册/落库的来源 key（--db 结束时按这份清单清理，不留脏数据）
_DEMO_KEYS = (
    "zj_demo", "zj_thin", "demo_unverified", "demo_forced", "demo_draft", "another_key",
    "zj_tool_blocked", "zj_tool_dup_label", "demo_tool",
)

# --db-scratch 用的临时库名（用完即删，绝不动 agent_demo）
_SCRATCH_DB = "agent_demo_onboarding_test"


def _swap_db(url: str, dbname: str) -> str:
    """把连接串里的库名换掉（其余部分原样保留）。"""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def create_scratch_db(base_url: str, name: str = _SCRATCH_DB) -> str:
    """建一个临时库并应用 `database/schema.sql`，返回它的连接串。

    **只动这个新库**：`agent_demo` 不会被碰。需要角色有 CREATEDB 权限（本仓库的 docker
    容器里 kos 是超级用户）。跑完由 `drop_scratch_db` 删掉。
    """
    import psycopg

    schema_path = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")
    target = _swap_db(base_url, name)

    with psycopg.connect(_swap_db(base_url, "postgres"), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(target, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(schema)
    return target


def drop_scratch_db(base_url: str, name: str = _SCRATCH_DB) -> str:
    """删掉临时库；返回一句话结果（连接还占着时会失败，所以要在关池之后调）。"""
    import psycopg

    try:
        with psycopg.connect(_swap_db(base_url, "postgres"), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        return f"临时库 {name} 已删除"
    except Exception as exc:  # noqa: BLE001 - 删不掉不影响用例结论
        return f"临时库 {name} 删除失败（可手动删除）：{exc}"


async def cleanup_demo_sources() -> list[str]:
    """删掉本次用例建出来的来源行（`content_source` 会级联删版本记录）。"""
    from app.db import repositories as repo
    from app.sources.registry import get, unregister

    removed: list[str] = []
    for key in _DEMO_KEYS:
        try:
            row = await repo.fetch_content_source_by_key(key)
            if row is not None and await repo.delete_content_source(row["id"]):
                removed.append(key)
        except Exception:  # noqa: BLE001 - 清理失败不该让测试结果失真
            pass
        if get(key) is not None:
            unregister(key)
    return removed


def demo_config_payload(**overrides) -> dict:
    payload = {
        "key": "zj_demo",
        "label": "浙江省人民政府门户（演示）",
        "level": "province",
        "organization": "浙江省人民政府",
        "source_type": "government_portal",
        "engine": "html_list",
        "scope": {
            "allowed_domains": ["demo.example.gov.cn"],
            "start_urls": [f"{BASE}/col/index.html"],
            "max_items_per_entry": 20,
        },
        "entrypoints": [
            {
                "name": "政策文件",
                "url": f"{BASE}/col/index.html",
                "list_selector": "ul.news-list > li",
                "link_selector": "a",
                "title_selector": "a",
                "date_selector": "span.date",
                "pagination": {
                    "kind": "path_index",
                    "path_template": "index_{page}.html",
                    "start": 1,
                    "step": 1,
                    "max_pages": 3,
                },
            }
        ],
        "detail": {
            "container_selectors": ["div#zoom", "div.article"],
            "title_selectors": ["h1"],
            "date_selectors": ["span.pubdate"],
            "agency_selectors": ["span.source"],
            "doc_no_selectors": ["span.docno"],
            "drop_selectors": ["div.attach"],
            "min_body_chars": 150,
        },
    }
    payload.update(overrides)
    return payload


# ---------- 1. 解析与安全底座 ----------


def test_parser_and_security() -> None:
    print("\n=== 1. 解析与安全底座 ===")
    # 编码嗅探：GBK 页面不解成乱码
    text = decode_html(GBK_PAGE, "text/html")
    check("GBK 页面按国标解码（不出现乱码）", "人工智能产业" in text)

    # 编码嗅探回归（2026-10-15 用户报「有些文章正文乱码」）：同一族的三种「整页乱码」形态
    # ① 声明 gb2312 / gbk，内容其实早已是 UTF-8（改版只改了页面没改 meta）——
    #    旧逻辑下 gb18030 能否"成功"解码取决于正文长度，所以只有一部分文章乱码
    utf8_content_gbk_meta = (
        '<html><head><meta http-equiv="Content-Type" '
        'content="text/html; charset=gb2312"></head><body><div id="zoom">'
        "关于加强人工智能产业发展的实施意见</div></body></html>"
    ).encode("utf-8")
    text = decode_html(utf8_content_gbk_meta)
    check("声明 gb2312 但内容是 UTF-8：不整页乱码", "人工智能产业" in text)
    check("声明 gb2312 但内容是 UTF-8：无替换字符", REPLACEMENT not in text)

    # ② GBK 页面里混进一个 gb18030 解不了的字节（最典型是单字节的「€」= 0x80）——
    #    旧逻辑会落到 `utf-8, errors="replace"` 兜底，整页变成「� + 拉丁扩展字母」
    #    （正是用户看到的那种乱码），现在只该坏这一个字节
    gbk_prefix = "关于加强".encode("gbk")
    gbk_page_stray_byte = GBK_PAGE.replace(gbk_prefix, b"\x80" + gbk_prefix)
    check("用例自检：非法字节确实插进去了", len(gbk_page_stray_byte) == len(GBK_PAGE) + 1)
    text = decode_html(gbk_page_stray_byte, "text/html")
    check("GBK 页面混入非法字节：正文仍可读", "人工智能产业" in text)
    check("GBK 页面混入非法字节：只坏一个字节", text.count(REPLACEMENT) == 1, str(text.count(REPLACEMENT)))

    # ③ 反向：UTF-8 页面里混进个别脏字节——旧逻辑在 utf-8 严格解失败后会落到 gb18030，
    #    若那段字节恰好合法就整页变汉字乱码。页面按真实长度补一段 ASCII 注释，
    #    让第一条规则「坏字节占比极低」的按比例容差真的生效
    padded = utf8_content_gbk_meta.replace(b"<body>", b"<body><!--" + b"x" * 1500 + b"-->")
    utf8_page_dirty = bytearray(padded)
    utf8_page_dirty[utf8_page_dirty.index("产业".encode("utf-8"))] = 0xFF
    text = decode_html(bytes(utf8_page_dirty), "text/html; charset=utf-8")
    check("UTF-8 页面混入脏字节：不整页乱码", "人工智能" in text)

    # 选择器子集
    root = parse_html(LIST_PAGE_1)
    check("选择器：类 + 子元素", len(select(root, "ul.news-list > li")) == 4)
    check("选择器：后代", len(select(root, "div#list li")) == 4)
    check("选择器：属性尾部匹配", len(select(root, 'a[href$=".html"]')) >= 4)
    ok, reason = selector_supported("div#zoom > p:first-child")
    check("伪类被拒绝（不会静默匹配不到）", not ok, reason)

    # 列表解析
    entry = SourceConfig.model_validate(demo_config_payload()).entrypoints[0]
    items = parse_list_page(LIST_PAGE_1, entry, f"{BASE}/col/index.html")
    check("列表解析：条目数", len(items) == 4, f"{len(items)} 条")
    check("列表解析：相对链接转绝对", items[0]["url"] == f"{BASE}/col/art1.html", items[0]["url"])
    check("列表解析：日期字段", items[0]["date"] == "2026-09-12", str(items[0]["date"]))

    # 翻页地址
    check("翻页：第 1 页", build_list_url(entry, 1) == entry.url)
    check("翻页：第 2 页", build_list_url(entry, 2) == f"{BASE}/col/index_2.html", str(build_list_url(entry, 2)))
    check("翻页：超出 max_pages 返回 None", build_list_url(entry, 4) is None)

    # 详情解析
    detail = SourceConfig.model_validate(demo_config_payload()).detail
    parsed = parse_detail_page(detail_page(1, *DETAIL_TITLES[1]), detail, f"{BASE}/col/art1.html")
    check("详情解析：正文非空", len(parsed.body) > 200, f"{len(parsed.body)} 字")
    check("详情解析：正文容器命中 div#zoom", parsed.container_selector == "div#zoom", str(parsed.container_selector))
    check("详情解析：正文不含被 drop 的附件区", "DOCX" not in parsed.body)
    check("详情解析：标题", bool(parsed.title and DETAIL_TITLES[1][0] in parsed.title), str(parsed.title))
    check("详情解析：文号", parsed.doc_no == "浙政发〔2026〕15号", str(parsed.doc_no))
    check("详情解析：机构（标签前缀已剥）", parsed.agency == "浙江省人民政府", str(parsed.agency))
    check("详情解析：日期", parsed.date_text == "2026-09-12", str(parsed.date_text))
    check("详情解析：附件发现 2 个", len(parsed.attachments) == 2, str([a.ext for a in parsed.attachments]))

    # 容器全落空 → 明确失败，不退化成整页文本
    empty = parse_detail_page("<html><body><div class='nope'>导航</div></body></html>", detail, "https://demo.example.gov.cn/x.html")
    check("正文容器全落空 → 正文为空且有告警", empty.body == "" and bool(empty.warnings))

    # SSRF / 白名单
    net.set_resolver(lambda host: [__import__("ipaddress").ip_address("93.184.216.34")])
    try:
        for bad in ("http://127.0.0.1/a", "http://localhost/a", "http://169.254.169.254/latest/meta-data/",
                    "http://10.0.0.5/a", "http://192.168.1.10/a", "http://[::1]/a", "file:///etc/passwd"):
            try:
                net.check_url(bad)
                check(f"SSRF：拒绝 {bad}", False, "竟然通过了")
            except net.UnsafeURLError:
                check(f"SSRF：拒绝 {bad.split('/')[2]}", True)
        try:
            net.check_url("https://demo.example.gov.cn/a", allowed_domains=["other.gov.cn"])
            check("白名单：白名单外主机被拒", False, "竟然通过了")
        except net.UnsafeURLError as exc:
            check("白名单：白名单外主机被拒", "不在来源白名单" in str(exc))
        check("白名单：子域匹配", net.domain_allowed("col.demo.example.gov.cn", ["demo.example.gov.cn"]))
        check("白名单：非子域不匹配", not net.domain_allowed("evil-demo.example.gov.cn", ["demo.example.gov.cn"]))
    finally:
        net.set_resolver(None)


# ---------- 2. 结构分析 ----------


def test_analysis() -> None:
    print("\n=== 2. 结构分析（候选识别） ===")
    analysis = analyze_page(LIST_PAGE_1, f"{BASE}/col/index.html", listed_as="entry")
    selectors = [c["selector"] for c in analysis["list_candidates"]]
    check("列表候选命中 ul.news-list > li", "ul.news-list > li" in selectors, str(selectors))
    check("列表候选未把导航菜单当条目", all("nav" not in s for s in selectors), str(selectors))
    pagination = analysis["pagination"]
    check("翻页识别：路径模板", pagination.get("kind") == "path_index", str(pagination))
    check("翻页识别：模板正确", pagination.get("path_template") == "/col/index_{page}.html", str(pagination.get("path_template")))
    check("翻页识别：start 反推为 1", pagination.get("start") == 1, str(pagination.get("start_reason")))
    check("翻页识别：下一页链接", pagination.get("next_href") == f"{BASE}/col/index_2.html", str(pagination.get("next_href")))

    detail_analysis = analyze_page(detail_page(1, *DETAIL_TITLES[1]), f"{BASE}/col/art1.html", listed_as="detail")
    top = detail_analysis["detail_candidates"][0]["selector"] if detail_analysis["detail_candidates"] else None
    check("正文候选首选 div#zoom", top == "#zoom", str(top))
    meta = detail_analysis["meta_candidates"]
    check("元信息候选：日期", any("2026-09-12" in c.get("sample", "") for c in meta["date"]), str(meta["date"][:1]))
    check("元信息候选：文号", bool(meta["doc_no"]), str(meta["doc_no"][:1]))
    check("元信息候选：机构标签", bool(meta["agency"]), str(meta["agency"][:1]))
    check("附件识别 2 个", len(detail_analysis["attachments"]) == 2, str(detail_analysis["attachments"]))

    home = analyze_page(
        f'<html><head><title>首页</title></head><body><ul><li><a href="/col/">政策文件</a></li>'
        f'<li><a href="/zwgk/">政务公开</a></li><li><a href="/login">登录</a></li></ul>'
        f'<link rel="alternate" type="application/rss+xml" href="/rss.xml"></body></html>',
        BASE,
    )
    texts = [c["text"] for c in home["columns"]]
    check("栏目入口候选（政策/公开）", "政策文件" in texts and "政务公开" in texts, str(texts))
    check("栏目入口排除登录类链接", "登录" not in texts, str(texts))
    check("订阅入口识别", f"{BASE}/rss.xml" in home["feeds"]["feeds"], str(home["feeds"]))


# ---------- 3. 配置 ----------


def test_config() -> None:
    print("\n=== 3. 接入配置 ===")
    from app.services.onboarding import generate_config

    good = generate_config(demo_config_payload())
    check("合法配置通过可执行性校验", good["ok"], str(good["problems"]))

    no_container = generate_config(demo_config_payload(detail={"container_selectors": []}))
    check("缺正文容器 → 被拦下", not no_container["ok"], str(no_container["problems"]))
    check("缺正文容器 → 给出修复指引", any("container_selectors" in p for p in no_container["problems"]))

    outside = generate_config(
        demo_config_payload(entrypoints=[{
            "name": "越界", "url": "https://other.gov.cn/list.html", "list_selector": "li",
            "pagination": {"kind": "none"},
        }])
    )
    check("入口域名不在白名单 → 被拦下", not outside["ok"], str(outside["problems"]))

    bad_selector = generate_config(
        demo_config_payload(entrypoints=[{
            "name": "伪类", "url": f"{BASE}/col/index.html", "list_selector": "ul li:nth-child(2)",
            "pagination": {"kind": "none"},
        }])
    )
    check("不支持的选择器 → 被拦下", not bad_selector["ok"], str(bad_selector["problems"]))

    duplicate = generate_config(
        demo_config_payload(entrypoints=[
            {"name": "同名", "url": f"{BASE}/col/index.html", "list_selector": "li", "pagination": {"kind": "none"}},
            {"name": "同名", "url": f"{BASE}/col/index2.html", "list_selector": "li", "pagination": {"kind": "none"}},
        ])
    )
    check("入口名重复 → 被拦下", not duplicate["ok"], str(duplicate["problems"])[:80])

    config_a = SourceConfig.model_validate(demo_config_payload())
    config_b = SourceConfig.model_validate(demo_config_payload(notes="改了备注"))
    config_c = SourceConfig.model_validate(
        demo_config_payload(detail={**demo_config_payload()["detail"], "container_selectors": ["div.article"]})
    )
    check("指纹：备注变化不影响指纹", config_a.config_fingerprint() == config_b.config_fingerprint())
    check("指纹：取数规则变化改变指纹", config_a.config_fingerprint() != config_c.config_fingerprint())


# ---------- 4. 试运行与注册（假站点上跑真实链路） ----------


async def test_dry_run() -> None:
    print("\n=== 4. 试运行与验收（假站点 · 真实编排） ===")
    from app.services.onboarding import dry_run_ingestion, register_source

    site = build_demo_site()
    install_fake_network(site)
    install_fake_judge()

    # --- 正常接入 ---
    report = await dry_run_ingestion(demo_config_payload(), limit=3, pages=2)
    checks = {row["name"]: row for row in report.get("criteria", [])}
    check("正常接入：验收通过", report.get("status") == "passed", str(report.get("error")))
    check("正常接入：列表发现判据通过", checks.get("列表发现", {}).get("passed") is True,
          str(checks.get("列表发现", {}).get("actual")))
    check("正常接入：检索链路产出 ≥3 条", (report.get("items_found") or 0) >= 3, f"{report.get('items_found')} 条")
    check("正常接入：正文完整性通过", checks.get("正文完整性", {}).get("passed") is True,
          str(checks.get("正文完整性", {}).get("actual")))
    check("正常接入：翻页可用性通过（第 2 页有新增）", checks.get("翻页可用性", {}).get("passed") is True,
          str(checks.get("翻页可用性", {}).get("actual")))
    check("正常接入：日期命中率通过", checks.get("详情页日期命中率", {}).get("passed") is True,
          str(checks.get("详情页日期命中率", {}).get("actual")))
    check("正常接入：机构命中率通过", checks.get("详情页机构命中率", {}).get("passed") is True,
          str(checks.get("详情页机构命中率", {}).get("actual")))
    # 四类结论的字段必须都在（未测试项允许为空，但绝不能没有这一栏）
    check("正常接入：报告含 通过/失败/未测试/覆盖范围 四栏",
          all(key in report for key in ("criteria", "untested", "coverage_limits")))
    check("正常接入：覆盖范围限制写清了没验证什么", len(report.get("coverage_limits", [])) >= 3,
          str(report.get("coverage_limits", [])[:2]))
    check("正常接入：来源可追溯判据通过",
          checks.get("来源可追溯", {}).get("passed") is True)
    detail_sample = (report.get("details") or [{}])[0]
    check("正常接入：正文实测按真实 HTML 统计", detail_sample.get("body_cjk", 0) > 150,
          f"cjk={detail_sample.get('body_cjk')} 句末={detail_sample.get('sent_enders')}")
    check("正常接入：附件被探测", bool(report.get("attachments_probed")), str(len(report.get("attachments_probed") or [])))

    # --- 样本量不足时的如实标注（limit=1 → 正文完整性只能算初步结论） ---
    thin = await dry_run_ingestion(demo_config_payload(key="zj_thin", label="样本不足（演示）"), limit=1, pages=1)
    check("样本不足：结论如实标注为初步",
          any("初步" in item for item in thin.get("untested", [])), str(thin.get("untested")))

    # --- 部分失败：详情页取不回来（正文完整性必须判失败） ---
    partial = build_demo_site()
    for index in (1, 2, 3, 5):
        partial.fail(f"/col/art{index}.html", "HTTP 500：详情页服务异常")
    install_fake_network(partial)
    partial_report = await dry_run_ingestion(demo_config_payload(), limit=3, pages=1)
    partial_checks = {row["name"]: row for row in partial_report.get("criteria", [])}
    check("部分失败：整体判定为失败", partial_report.get("status") == "failed", str(partial_report.get("status")))
    check("部分失败：正文完整性判据失败", partial_checks.get("正文完整性", {}).get("passed") is False,
          str(partial_checks.get("正文完整性", {}).get("actual")))
    check("部分失败：报告里如实写出取回失败原因",
          any("取回失败" in w for row in partial_report.get("details", []) for w in row.get("warnings", [])))

    # --- 无法抓取：站点要求认证（403）→ 受阻，不是失败 ---
    forbidden = build_demo_site()
    forbidden.fail("/col/index.html", "HTTP 403：Forbidden")
    forbidden.fail("/col/index_2.html", "HTTP 403：Forbidden")
    install_fake_network(forbidden)
    blocked_report = await dry_run_ingestion(demo_config_payload(), limit=2, pages=1)
    check("无法抓取：判定为受阻（blocked）", blocked_report.get("status") == "blocked",
          str(blocked_report.get("status")))
    check("无法抓取：原因点明访问控制且说明不绕过",
          "访问控制" in str(blocked_report.get("error")), str(blocked_report.get("error"))[:80])

    # --- 无法抓取：robots 禁止 ---
    install_fake_network(build_demo_site(), robots_allowed=False)
    from app.services.onboarding import inspect_source

    robots_report = await inspect_source(f"{BASE}/col/index.html")
    check("robots 禁止：勘察即受阻", robots_report.get("kind") == "blocked", str(robots_report.get("error"))[:80])

    # --- 人工确认：没有通过验收时不得启用（这一条不依赖数据库） ---
    install_fake_network(build_demo_site())
    refused = await register_source(
        demo_config_payload(key="demo_unverified", label="未验收来源（演示）"), approved=True
    )
    check("人工确认：无验收记录时拒绝启用", not refused["ok"] and "试运行" in str(refused.get("error")),
          str(refused.get("error"))[:80])

    # --- 注册表：key / label 冲突必须拒绝（抓取任务按 label 解析来源，撞名 = 抓错站点） ---
    # 从干净状态开始：前面的失败试运行会把临时装载的草案摘掉（_cleanup_draft），
    # 不能依赖「此刻 zj_demo 还在注册表里」。
    from app.sources.registry import register_configured
    from app.sources.registry import unregister as unregister_source

    unregister_source("zj_demo")
    demo_config = SourceConfig.model_validate(demo_config_payload())
    register_configured(demo_config)
    try:
        register_configured(demo_config)
        check("注册表：重复 key 被拒（replace=False）", False, "竟然通过了")
    except ValueError as exc:
        check("注册表：重复 key 被拒（replace=False）", "已被占用" in str(exc), str(exc)[:60])
    try:
        register_configured(
            SourceConfig.model_validate(
                demo_config_payload(key="another_key", label="浙江省人民政府门户（演示）")
            )
        )
        check("注册表：同名 label 冲突被拒", False, "竟然通过了")
    except ValueError as exc:
        check("注册表：同名 label 冲突被拒", "冲突" in str(exc), str(exc)[:60])

    # --- 落库相关用例：需要 server-db-1 + 连接池 + Phase 14 新表；测不了就明确跳过（不算通过） ---
    if not await db_available():
        reason = "数据库不可用（未加 --db／连接池未初始化／Phase 14 新表未建，先跑 database/init.sh）"
        skip("人工确认：force 明确拍板才放行", reason)
        skip("人工确认：未确认时注册为草案（draft）", reason)
        skip("启动装载：active 来源可被 load_configured_from_db 装载", reason)
    else:
        forced = await register_source(
            demo_config_payload(key="demo_forced", label="强制启用来源（演示）"),
            approved=True, force=True, change_note="演示强制放行",
        )
        check("人工确认：force 明确拍板才放行", forced.get("ok") is True, str(forced.get("error"))[:80])
        draft = await register_source(
            demo_config_payload(key="demo_draft", label="草案来源（演示）"), approved=False
        )
        check("人工确认：未确认时注册为草案（draft）", draft.get("ok") and draft.get("status") == "draft",
              str(draft.get("status")))

        # 启用后的来源必须能被启动装载逻辑读回（「重启后来源还在」靠这一条）
        from app.sources.registry import load_configured_from_db

        loaded, load_failures = await load_configured_from_db()
        check("启动装载：active 来源可被 load_configured_from_db 装载",
              forced.get("key") in loaded and not load_failures, f"装载 {loaded}／失败 {load_failures}")


async def test_tools() -> None:
    """工具层：Agent 真正调用的是这些 `@tool`，所以它们也要按同样的口径验一遍。

    重点验两件在服务层测不出来的事：① 返回的是**能解析的 JSON 文本**（模型读的就是它）；
    ② 任何失败都**不抛异常**，而是 `{"ok": false, "error": ...}`。
    """
    print("\n=== 5. 工具层（Agent 的调用入口） ===")
    from app.agent import tools as registry

    expected = {
        "inspect_source", "discover_content", "analyze_content_structure", "sample_content",
        "generate_source_config", "dry_run_ingestion", "register_source",
        "manage_ingestion_job", "diagnose_source",
    }
    names = {tool.name for tool in registry.TOOLS}
    check("9 个接入工具都已注册进 TOOLS", expected <= names, str(sorted(expected - names)))

    tools = {tool.name: tool for tool in registry.TOOLS}

    async def call(name: str, **kwargs: object) -> tuple[dict | None, str]:
        raw = await tools[name].ainvoke(kwargs)
        try:
            return json.loads(raw), raw
        except json.JSONDecodeError:
            return None, raw

    install_fake_network(build_demo_site())

    payload, raw = await call("inspect_source", url=f"{BASE}/col/index.html")
    check("工具 inspect_source：返回可解析 JSON", payload is not None, raw[:80])
    check("工具 inspect_source：识别出列表候选",
          bool(payload and any("news-list" in (c.get("selector") or "")
                               for c in payload.get("list_candidates", []))), raw[:120])
    check("工具 inspect_source：写明候选未经确认",
          bool(payload and "候选" in str(payload.get("note"))), str((payload or {}).get("note"))[:60])

    payload, raw = await call("generate_source_config", config_json="{不是 JSON")
    check("工具 generate_source_config：坏 JSON 报错而不抛异常",
          payload is not None and payload.get("ok") is False and "解析失败" in str(payload.get("error")),
          raw[:80])

    payload, raw = await call(
        "generate_source_config",
        config_json=json.dumps(demo_config_payload(detail={"container_selectors": []}), ensure_ascii=False),
    )
    check("工具 generate_source_config：不可执行配置被拦下并给指引",
          payload is not None and payload.get("ok") is False
          and any("container_selectors" in p for p in payload.get("problems", [])), raw[:120])

    payload, raw = await call(
        "dry_run_ingestion", config_json=json.dumps(demo_config_payload(), ensure_ascii=False),
        limit=3, pages=2,
    )
    check("工具 dry_run_ingestion：正常接入 status=passed",
          payload is not None and payload.get("status") == "passed", raw[:120])
    check("工具 dry_run_ingestion：报告四要素齐（判据/未测试/覆盖限制/逐篇实测）",
          bool(payload and payload.get("criteria") and "untested" in payload
               and payload.get("coverage_limits") and payload.get("details")), raw[:120])

    forbidden = build_demo_site()
    forbidden.fail("/col/index.html", "HTTP 403：Forbidden")
    forbidden.fail("/col/index_2.html", "HTTP 403：Forbidden")
    install_fake_network(forbidden)
    payload, raw = await call(
        "dry_run_ingestion",
        config_json=json.dumps(
            demo_config_payload(key="zj_tool_blocked", label="403 站点（演示）"), ensure_ascii=False
        ),
        limit=2,
    )
    check("工具 dry_run_ingestion：站点拒绝访问 → blocked（不是成功）",
          payload is not None and payload.get("status") == "blocked", raw[:150])

    # 同 label 不同 key 的草案在试运行时会被拒绝装载：抓取任务按 label 解析来源，
    # 撞名会抓到别的站点，这个坑要在接入阶段就暴露出来。
    payload, raw = await call(
        "dry_run_ingestion",
        config_json=json.dumps(demo_config_payload(key="zj_tool_dup_label"), ensure_ascii=False),
        limit=1,
    )
    check("工具 dry_run_ingestion：来源展示名撞车被拒（不会静默抓错站）",
          payload is not None and payload.get("ok") is False and "冲突" in str(payload.get("error")),
          raw[:150])

    install_fake_network(build_demo_site())
    payload, raw = await call(
        "register_source",
        config_json=json.dumps(demo_config_payload(key="demo_tool", label="工具用例来源（演示）"),
                               ensure_ascii=False),
        approved=True,
    )
    check("工具 register_source：没有通过验收就拒绝启用",
          payload is not None and payload.get("ok") is False and "试运行" in str(payload.get("error")),
          raw[:120])

    payload, raw = await call("manage_ingestion_job", action="list")
    check("工具 manage_ingestion_job：返回可读结果（库不可用时是可读错误，不抛异常）",
          payload is not None and (payload.get("ok") is True or bool(payload.get("error"))), raw[:100])

    if await db_available():
        payload, raw = await call("manage_ingestion_job", action="health", key="demo_forced")
        check("工具 manage_ingestion_job：已注册来源的健康检查可达",
              payload is not None and payload.get("ok") is True, raw[:150])
    else:
        skip("工具 manage_ingestion_job：已注册来源的健康检查", "数据库不可用")

    payload, raw = await call("diagnose_source", key="不存在的来源")
    check("工具 diagnose_source：未知来源返回可读错误",
          payload is not None and payload.get("ok") is False, raw[:100])


async def test_live(url: str) -> None:
    print(f"\n=== 5. 真实站点勘察（--live：{url}） ===")
    from app.services.onboarding import inspect_source

    report = await inspect_source(url)
    check("真实站点：地址可达并取回", report.get("ok") is True, str(report.get("error"))[:120])
    if report.get("ok"):
        print(f"    页面标题：{report.get('page_title')}")
        print(f"    列表候选：{[c['selector'] for c in report.get('list_candidates', [])][:5]}")
        print(f"    正文候选：{[c['selector'] for c in report.get('detail_candidates', [])][:5]}")
        print(f"    栏目入口：{[c['text'] for c in report.get('columns', [])][:8]}")
        print(f"    翻页：{report.get('pagination')}")


# ---------- 主入口 ----------


async def main() -> int:
    parser = argparse.ArgumentParser(description="网站来源接入离线验收测试")
    parser.add_argument("--db", action="store_true",
                        help="打开数据库连接池，追加落库用例（需 server-db-1 与新表；结束前清理测试行）")
    parser.add_argument("--db-url", default=None,
                        help="改用指定的数据库地址跑落库用例（如一次性建的临时库，避免脏数据落在 agent_demo）")
    parser.add_argument("--db-scratch", action="store_true",
                        help=f"自动建临时库 {_SCRATCH_DB}（应用 schema.sql）跑落库用例，结束即删除；不影响 agent_demo")
    parser.add_argument("--live", nargs="?", const="https://www.gov.cn/zhengce/zuixin/", default=None,
                        help="追加真实站点勘察（可选传入 URL，默认国务院最新政策页）")
    args = parser.parse_args()

    # 测试期把节流关掉（假站点无需礼貌限频）、产物落到测试目录，避免污染正式配置
    from app.config import get_settings

    settings = get_settings()
    settings.source_request_interval_ms = 0
    settings.source_artifact_dir = "server/.artifacts/test-onboarding"

    scratch_base: str | None = None
    pool_opened = False
    if args.db_scratch:
        scratch_base = settings.database_url
        try:
            settings.database_url = create_scratch_db(scratch_base)
            print(f"已建临时库并应用 schema.sql：{settings.database_url}")
            args.db = True
        except Exception as exc:  # noqa: BLE001 - 建不出来就照常跑离线用例
            print(f"临时库建不出来（{exc}），落库用例将跳过")
            scratch_base = None
    if args.db or args.db_url:
        if args.db_url:
            # 覆盖连接地址（`open_pool` 调用时才读 settings，所以在这里改是有效的）
            settings.database_url = args.db_url
            print(f"落库用例使用数据库：{args.db_url}")
        from app.db.pool import open_pool

        await open_pool()  # 连不上只告警不抛；下面 db_available() 会如实判定
        pool_opened = True

    try:
        test_parser_and_security()
        test_analysis()
        test_config()
        await test_dry_run()
        await test_tools()
        if args.live:
            await test_live(args.live)
    finally:
        net.set_resolver(None)
        if pool_opened:
            try:
                removed = await cleanup_demo_sources()
                print(f"\n已清理测试来源行：{removed or '（无）'}")
            finally:
                from app.db.pool import close_pool

                await close_pool()  # 必须在删库之前关池，否则库被连接占着删不掉
        if scratch_base:
            print(drop_scratch_db(scratch_base))

    print("\n" + "=" * 60)
    print(f"通过 {len(PASSED)} 项 / 失败 {len(FAILED)} 项 / 跳过 {len(SKIPPED)} 项")
    for name in FAILED:
        print(f"  失败：{name}")
    for name in SKIPPED:
        print(f"  跳过：{name}")
    scope = "离线用例"
    if args.live:
        scope += " + 真实站点勘察"
    if args.db:
        scope += " + 落库用例"
    if FAILED:
        print("\n结果：存在失败项")
        return 1
    if SKIPPED:
        # 跳过 ≠ 通过：如实说明还有哪些没跑，别把「没测」混进「通过」
        print(f"\n结果：{scope}全部通过；另有 {len(SKIPPED)} 项未跑（见上，不计入通过）")
    else:
        print(f"\n结果：{scope}全部通过，无跳过项")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        # Windows 控制台默认 GBK，中文输出会炸；这里强制 UTF-8
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    sys.exit(asyncio.run(main()))
