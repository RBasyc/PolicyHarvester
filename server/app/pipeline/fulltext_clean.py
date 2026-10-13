"""已归档全文的清洗：元信息提取 + 正文去噪。

历史归档的全文是 MCP 工具旧版「整页 body 文本」抓的，混有 CSS/JS/导航噪声和
gov.cn 元信息表（发文机关/发文字号/来源/主题分类/公文种类/成文日期）。
`split_fulltext` 把两者拆开：元信息上移到详情页 head-card，正文只留政策原文。

两处共用本模块：
- 归档时（`crawl_graph._fetch_fulltext`）——新抓的全文入库前就清洗干净；
- 读取时（`GET /api/policies/{id}`）——已入库的历史脏数据兜底清洗。

清洗是启发式的（gov.cn 页面模板专属规则 + 通用代码行识别），安全兜底：
结果若短于原文 5% 就原样返回，宁可展示原文也不清空。
"""

from __future__ import annotations

import re

from app.schemas.policy import FulltextMeta

# 元信息标签（允许标签内带空白，如「标 题：」「来 源：」），值取同行剩余文本
_META_LABELS = {
    "标题": "title",
    "发文机关": "agency",
    "发文字号": "doc_no",
    "来源": "source",
    "主题分类": "category",
    "公文种类": "doc_kind",
    "成文日期": "signed_date",
}

_META_LINE_RE = re.compile(
    r"^\s*(标\s*题|发文机关|发文字号|来\s*源|主题分类|公文种类|成文日期)\s*[:：]\s*(.*)$"
)

# gov.cn 页头/页脚样板：导航、面包屑、工具栏、页脚链接与备案信息
_BOILERPLATE_EXACT = {
    "首页", "简", "繁", "EN", "登录", "个人中心", "退出", "邮箱", "无障碍",
    "打印", "收藏", "大", "超大", "默认", "|", "||", "×",
    "回到顶部", "注册", "电脑版", "客户端", "小程序", "微博", "微信",
    "全国人大", "全国政协", "国家监察委员会", "最高人民法院", "最高人民检察院",
    "国务院部门网站", "地方政府网站", "驻港澳机构网站", "驻外机构",
    "中国政府网", "关于本网", "网站声明", "联系我们", "网站纠错",
    "国务院客户端", "国务院客户端小程序", "中国政府网微博、微信", "链接：",
}
_BOILERPLATE_HINT_RE = re.compile(
    r"网站标识码|ICP备|公网安备|主办单位：|版权所有：|中文域名："
)
_URL_LINE_RE = re.compile(r"^\s*(https?://|//)\S+\s*$")
_CSS_RULE_RE = re.compile(r"[{}]")
_JS_HINTS = (
    "function ", "document.", "window.", "encodeURIComponent", "addEventListener",
    "preventDefault", "keyCode", "localStorage", ".attr(", ".eq(", "indexOf(",
    "getElementById", "=>", "$(function",
)
_CODE_CHARS_RE = re.compile(r"^[\w\s$@.'\"()\[\],+\-*/<>!&|?=:;%#\\{}]+$")
_CJK_RE = re.compile(r"[一-鿿]")
_CJK_SENT_RE = re.compile(r"[，。、；！？]")
_BREADCRUMB_RE = re.compile(r"^首页\s*[>＞·]")

_MIN_KEEP_RATIO = 0.05

# 零宽/格式字符（U+200B 零宽空格、BOM 等）：gov.cn 页面模板会塞在元信息值开头
# （如「​商务部 …」）。str.strip() 与正则 \s 都不把它们当空白（Unicode 分类是 Cf），
# 不专门剥掉就会带着进库、再原样显示到页面上。
# 显式 \u 转义而非字面零宽字符：源码里放不可见字符易被编辑器/工具悄悄剥掉
_ZWSP_RE = re.compile(r"[​‌‍⁠﻿]")


def strip_zero_width(text: str) -> str:
    """剥掉零宽/格式字符；归档清洗与检索元数据落库（`_to_policy_row`）共用。"""
    return _ZWSP_RE.sub("", text)


# 句末标点（陈述/疑问/感叹 + 分号）：真政策条文的密度信号，页面壳与薄壳通知几乎为零
_SENT_ENDER_RE = re.compile(r"[。！？；]")


def body_metrics(clean_body: str) -> tuple[int, int]:
    """正文预检指标：`(CJK 字数, 句末标点数)`。输入用 `split_fulltext` 清洗后的正文。

    「值得入库」的第二道筛（laya 判别只看标题，2026-10-13 漏网两类：图片版文件抓回来
    的是页面壳——CJK 可达 555 但句末为 0；「现将《方案》印发给你们」式薄壳通知百余字
    1 句）。实测好例 cjk≥1062 / 句末≥29，坏例 cjk≤642 / 句末≤1，见
    scripts/test_ingest_filter.py 的校准矩阵。
    """
    body = clean_body or ""
    return len(_CJK_RE.findall(body)), len(_SENT_ENDER_RE.findall(body))


def split_fulltext(text: str) -> tuple[FulltextMeta, str]:
    """把已归档全文拆成 (元信息, 干净正文)。元信息取不到的字段为 None。"""
    raw = strip_zero_width(text or "").strip()
    if not raw:
        return FulltextMeta(), ""

    meta, found = _extract_meta(raw)
    body = _clean_body(raw, found)
    # 安全兜底：清洗疑似误伤（页面结构与预期差异大）时原样返回
    if len(body) < len(raw) * _MIN_KEEP_RATIO:
        body = raw
    return meta, body


def _extract_meta(raw: str) -> tuple[FulltextMeta, dict[str, str]]:
    """按行抓元信息标签，取第一个非空值。返回 (DTO, 原始 key→值)。

    元信息表的标签和值在归档文本里经常分成两行（<td>/<li> 文本节点各自成行），
    标签行取不到同行值时，顺延取下一行（本身也是标签行则跳过）。
    """
    found: dict[str, str] = {}
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        m = _META_LINE_RE.match(line)
        if not m:
            continue
        label = re.sub(r"\s+", "", m.group(1))
        key = _META_LABELS.get(label)
        if not key or key in found:
            continue
        value = m.group(2).strip()
        if not value:
            for nxt in lines[i + 1 : i + 4]:
                nxt_s = nxt.strip()
                if not nxt_s:
                    continue
                if _META_LINE_RE.match(nxt_s):
                    break
                value = nxt_s
                break
        if value:
            found[key] = value
    return (
        FulltextMeta(
            agency=found.get("agency"),
            doc_no=found.get("doc_no"),
            source=found.get("source"),
            category=found.get("category"),
            doc_kind=found.get("doc_kind"),
            signed_date=found.get("signed_date"),
        ),
        found,
    )


def _clean_body(raw: str, found: dict[str, str]) -> str:
    # 含标题在内的全部元信息值行都从正文剔除（gov.cn 元信息表和纯文本版各出现一次）
    meta_values = {re.sub(r"\s+", "", v) for v in found.values() if v}
    kept: list[str] = []
    prev_key = ""
    for line in raw.splitlines():
        s = line.strip()
        key = re.sub(r"\s+", "", s)
        # 与上一行重复的非空行丢弃（gov.cn 正文有 PC/移动两份拷贝）
        if key and key == prev_key:
            continue
        if _is_junk_line(s, meta_values):
            continue
        if key:
            prev_key = key
        kept.append(line.rstrip())

    # 短行若被包含在更长的正文行里（gov.cn 纯文本版正文是首段的截断重复），丢弃短行
    non_empty = [l for l in kept if l.strip()]
    if non_empty:
        longest = max(non_empty, key=lambda l: len(l.strip()))
        norm_long = re.sub(r"\s+", "", longest)
        kept = [
            l for l in kept
            if not l.strip() or l is longest or re.sub(r"\s+", "", l) not in norm_long
        ]
    return _collapse_blanks(kept).strip()


def _is_junk_line(s: str, meta_values: set[str]) -> bool:
    if not s:
        return False  # 空行交给 _collapse_blanks 处理
    if _META_LINE_RE.match(s):
        return True  # 元信息行上移到 head-card，不进正文
    key = re.sub(r"\s+", "", s)
    if key in meta_values:
        return True  # 元信息表/纯文本版的值行（标签行已在上一条丢弃）
    if s in _BOILERPLATE_EXACT:
        return True
    if _BOILERPLATE_HINT_RE.search(s):
        return True
    if _URL_LINE_RE.match(s):
        return True
    if s.startswith(("/*", "*/", "//")):
        return True  # CSS/JS 注释，允许含中文（如「//繁体和简体相互转换」）
    if s.startswith(("}", ")", "]);", "});")):
        return True  # 代码块收尾行（如「}//简体」）
    # 面包屑：首页 > 政策 > …；字号工具条：字号：默认 大 超大
    if _BREADCRUMB_RE.match(s) or s.startswith("字号"):
        return True
    # CSS：成对的 {} 规则块或纯属性声明行（无中文）
    if _CSS_RULE_RE.search(s) and not _CJK_RE.search(s):
        return True
    if s.endswith(";") and ":" in s and not _CJK_RE.search(s):
        return True
    # JS/DOM 调用残留（含中文也丢，如 $('.header_toolbar li').eq(2)…）
    if any(h in s for h in _JS_HINTS):
        return True
    # JS 对象字面量行（如「init: init」「key: value,」）
    if re.match(r"^[\w$.]+\s*:\s*[\w$.'\"]+\s*,?$", s) and not _CJK_RE.search(s):
        return True
    # 纯代码行：字符集全是代码符号（含中文，\w 覆盖 CJK），带赋值/调用特征、无中文句子标点
    if (
        _CODE_CHARS_RE.match(s)
        and not _CJK_SENT_RE.search(s)
        and len(s) < 300
        and ("=" in s or ";" in s or "(" in s)
    ):
        return True
    return False


def _collapse_blanks(lines: list[str]) -> str:
    out: list[str] = []
    prev_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and prev_blank:
            continue
        out.append(line)
        prev_blank = blank
    return "\n".join(out)
