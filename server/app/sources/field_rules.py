"""语义化字段规则的执行器：把 `config.FieldRule` 解释成「标签 → 值」。

背景：旧的 `*_selectors` 只能表达「第几个选择器命中什么」。政务站详情页里最常见的
三种形状都表达不了：

1. 标签在 `<span>`、值在父节点的**直接文本**里（模板拼接）：
   `<div class="col-6"><span class="fw-bold">文　　号：</span> 惠府〔2025〕13号</div>`
2. 标签和值在同一个节点里：`<div>发布日期：2025-03-10</div>`
3. 标签与值分居兄弟节点：`<dt>发布机构</dt><dd>惠州市政府办公室</dd>`

本模块只做**确定性解释**，不做任何猜测：模型/接入流程负责产出规则，流水线照着执行。
规则的解析口径集中在这里，`html_source.parse_detail_page` / `parse_list_page` 只调用
`extract_fields()`；新增取值方式只改本模块 + `FieldValueFrom` 字面量。

规则链语义：**按顺序尝试，第一个取到非空值的规则获胜**。每条规则内部：
找到一个或多个候选节点 → 过滤（标签 / reject）→ 取值 → 截取（pattern）→ 规范化。
"""

from __future__ import annotations

import re
from typing import Iterable

from app.sources.config import FieldRule
from app.sources.htmlparse import Node, select, selector_supported, strip_nth_of_type
from app.sources.util import parse_date

_WHITESPACE_RE = re.compile(r"\s+")
# 标签比较前统一去掉所有空白（含全角空格 U+3000）与末尾冒号。
# 政务模板里「文　　号：」「文号」「文 号 ：」是同一个标签。
_LABEL_TRAIL_RE = re.compile(r"[:：]+$")


def normalize_label(text: str) -> str:
    """标签归一：去所有空白 + 去末尾冒号。"""
    return _LABEL_TRAIL_RE.sub("", _WHITESPACE_RE.sub("", text or ""))


def own_text(node: Node) -> str:
    """节点**直接**文本子节点（不含后代元素里的文字）。

    这是区分「标签节点」和「值节点」的关键：`<span>文号：</span>` 的 own_text 是
    「文号：」，而 `text_content()` 还会把后代文字带上。
    """
    return "".join(child.text for child in node.children if child.tag == "#text")


def _next_element_sibling(node: Node) -> Node | None:
    parent = node.parent
    if parent is None:
        return None
    siblings = parent.children
    try:
        index = siblings.index(node)
    except ValueError:
        return None
    for sibling in siblings[index + 1 :]:
        if sibling.tag != "#text":
            return sibling
    return None


def _label_of(node: Node) -> str:
    """候选节点的标签文本（归一后）。取直接文本——标签通常就是节点自己。"""
    return normalize_label(own_text(node))


def _label_matches(rule: FieldRule, node: Node) -> bool:
    """标签过滤。

    - `label_equals`：**相等或作为前缀**都算。前缀口径是为了兼容
      `<div>发布日期：2025-03-10</div>` 这种标签与值同节点的写法（归一后以标签开头）。
    - `label_matches`：在归一后的标签文本上跑正则。
    两者都没填 = 不按标签过滤（用选择器直接定位值节点）。
    """
    if rule.label_equals is None and rule.label_matches is None:
        return True
    label = _label_of(node)
    if rule.label_equals is not None:
        wanted = normalize_label(rule.label_equals)
        return label == wanted or label.startswith(wanted + ":") or label.startswith(wanted + "：")
    assert rule.label_matches is not None
    return re.search(rule.label_matches, label) is not None


def _raw_value(rule: FieldRule, node: Node) -> str:
    """按 `value_from` 取原始文本。"""
    if rule.value_from == "self_text":
        return node.text_content()
    if rule.value_from == "own_text":
        return own_text(node)
    if rule.value_from == "parent_text":
        return node.parent.text_content() if node.parent else ""
    if rule.value_from == "parent_own_text":
        return own_text(node.parent) if node.parent else ""
    if rule.value_from == "next_sibling_text":
        sibling = _next_element_sibling(node)
        return sibling.text_content() if sibling else ""
    if rule.value_from == "attr":
        return node.get(rule.attr or "")
    return ""


def strip_leading_label(value: str, node: Node, label: str | None = None) -> str:
    """去掉取值开头出现的标签文本（含中间任意空白与冒号分隔）。

    不能简单 `value.replace(label, "")`：`node_text()` 会把 `文　　号：` 收敛成
    `文 号：`，而配置里的 label 可能来自另一份 HTML。这里按「跳过空白逐字比对
    归一标签」实现，然后吃掉紧随其后的冒号与空白。

    `label` 显式给出时（规则有 `label_equals`）以它为准：同节点写法
    （`<div>责任处室：市司法局</div>`）里节点的 own_text 是**整段**文本，
    拿它当标签会把值一起剥光。
    """
    label = normalize_label(label) if label is not None else _label_of(node)
    if not label:
        return value
    pos = 0
    matched = 0
    while pos < len(value) and matched < len(label):
        char = value[pos]
        if char.isspace():
            pos += 1
            continue
        if char == label[matched]:
            matched += 1
            pos += 1
            continue
        return value
    if matched < len(label):
        return value
    while pos < len(value) and (value[pos].isspace() or value[pos] in ":："):
        pos += 1
    return value[pos:]


def _post_process(rule: FieldRule, value: str, node: Node) -> str | None:
    """pattern → drop_label → reject → normalize；返回 None = 该候选无效。"""
    if rule.drop_label:
        value = strip_leading_label(value, node, rule.label_equals)

    if rule.pattern:
        match = re.search(rule.pattern, value)
        if not match:
            return None
        try:
            value = match.group(rule.group)
        except IndexError:
            return None

    for bad in rule.reject:
        if re.search(bad, value):
            return None

    if rule.normalize == "none":
        value = value.strip()
    elif rule.normalize == "date":
        parsed = parse_date(value)
        value = parsed.isoformat() if parsed else ""
    else:  # whitespace
        value = _WHITESPACE_RE.sub(" ", value).strip()
    return value or None


def _candidates(root: Node, rule: FieldRule) -> Iterable[Node]:
    """规则作用域内的候选节点（保持文档序）。"""
    scopes = select(root, rule.scope) if rule.scope else [root]
    for scope in scopes:
        for node in select(scope, rule.selector):
            yield node


def apply_rule(root: Node, rule: FieldRule) -> str | None:
    """执行单条规则；取不到返回 None。"""
    candidates = [node for node in _candidates(root, rule) if _label_matches(rule, node)]
    for node in candidates[rule.match_index :]:
        value = _post_process(rule, _raw_value(rule, node), node)
        if value:
            return value
    return None


def extract_field(root: Node, rules: Iterable[FieldRule]) -> str | None:
    """执行一条规则链：第一个拿到非空值的规则获胜。"""
    for rule in rules:
        value = apply_rule(root, rule)
        if value:
            return value
    return None


def extract_fields(root: Node, fields: dict[str, list[FieldRule]]) -> dict[str, str]:
    """批量执行字段规则，丢掉取不到值的字段。"""
    found: dict[str, str] = {}
    for name, rules in fields.items():
        value = extract_field(root, rules)
        if value:
            found[name] = value
    return found


# ---------- 从结构分析结果生成规则（确定性接入用） ----------

# 分析器给出的 `field_candidates[].relation` 直接映射到取值位置：
#   label_child   —— 候选节点是「标签子节点」，值在父节点的直接文本里
#   label_sibling —— 候选节点是标签，值在下一个兄弟节点
#   inline        —— 标签与值同在节点文本里（如 <div>发布日期：2025-03-10</div>）
_RELATION_TO_VALUE_FROM = {
    "label_child": "parent_own_text",
    "label_sibling": "next_sibling_text",
    "inline": "self_text",
}


def rules_from_field_candidates(
    candidates: list[dict],
    *,
    rule_limit: int = 2,
    field_limit: int = 12,
) -> dict[str, list[FieldRule]]:
    """`analyze.field_candidates` → `detail.fields` 草案（按标准字段名分组）。

    映射口径：

    - `publish_date` 统一写成保留名 `date`（`parse_detail_page` 写回 `date_text`）；
    - `agency` / `doc_no` 是保留名，与旧 `*_selectors` 并存（规则优先、选择器兜底）；
    - 其余（`index_no` / `signed_date` / `theme` / `doc_type` …）进 metadata；
    - `unknown` 用标签本身做键（页面写「责任处室」就存「责任处室」），不替它编英文名。

    每组最多 `rule_limit` 条规则（规则链自带 fallback），字段数封顶 `field_limit`。
    保留字段排在前面，便于日志/配置阅读。
    """
    grouped: dict[str, list[FieldRule]] = {}
    for candidate in candidates or []:
        field = str(candidate.get("field") or "unknown")
        if field == "publish_date":
            field = "date"
        if field == "unknown":
            label = str(candidate.get("label") or "").strip()
            if not label:
                continue
            field = label
        rules = grouped.setdefault(field, [])
        if len(rules) >= rule_limit:
            continue
        try:
            rule = rule_from_candidate(candidate, field)
        except ValueError:
            continue
        if rule is not None:
            rules.append(rule)

    ordered: dict[str, list[FieldRule]] = {}
    for name in ("date", "agency", "doc_no"):
        if grouped.get(name):
            ordered[name] = grouped.pop(name)
    ordered.update(grouped)
    return dict(list(ordered.items())[:field_limit])


def rule_from_candidate(candidate: dict, field_name: str) -> FieldRule | None:
    """把一条 `field_candidates` 记录转成 `FieldRule`。

    分析器记录的形状（见 `sources/analyze.py::field_candidates`）：
    `{field, selector, label, relation, value, score}`。`field` 为 `unknown` 时用
    调用方给的 `field_name`（接入流程会按标签映射到标准字段名）。
    """
    # 候选可能来自模型（或分析器的旧输出）：去掉引擎不支持的 `:nth-of-type(n)`，
    # 剥不掉的（`+`/`~`/其它伪类）直接放弃——带着不可执行的选择器进配置只会静默取空。
    selector = strip_nth_of_type((candidate.get("selector") or "").strip()).strip()
    if not selector or not selector_supported(selector)[0]:
        return None
    relation = candidate.get("relation") or "inline"
    label = candidate.get("label")
    # 分析器已实测出「值在哪」，优先用它（同一 relation 下 `parent_own_text` 与
    # `parent_text` 是两种不同形状）；旧记录没有该字段时按 relation 兜底。
    value_from = candidate.get("value_from") or _RELATION_TO_VALUE_FROM.get(
        relation, "self_text"
    )
    payload: dict = {
        "selector": selector,
        "value_from": value_from,
        # 值里自带标签的两种形状才需要剥离：同节点文本 / 父节点整段文本
        "drop_label": relation == "inline" or value_from == "parent_text",
    }
    scope = (candidate.get("scope") or "").strip()
    if scope:
        payload["scope"] = scope
    if label:
        payload["label_equals"] = str(label)
    # 日期类字段统一归一成 ISO：跨页核对按值比较、筛选/排序也依赖同一口径。
    if field_name in ("date", "publish_date", "signed_date", "effective_date"):
        payload["normalize"] = "date"
    return FieldRule.model_validate(payload)
