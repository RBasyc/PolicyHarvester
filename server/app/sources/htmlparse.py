"""轻量 HTML 解析：迷你 DOM + CSS 选择器子集 + 中文站点编码嗅探（纯标准库）。

**为什么不用 beautifulsoup4 / lxml**：本项目 server 侧此前不解析 HTML（联网取数走
node 侧的 MCP），加一个解析依赖意味着多一次 `uv sync` 与一个供应链面。来源接入需要
的解析能力其实很窄——「按选择器取节点 / 取文本 / 取属性」，因此这里用标准库
`html.parser` 自建一个**够用就好**的 DOM，并把它的边界写清楚（见下）。

能力边界（有意为之，不要当完整 HTML5 解析器用）：

- 按标签的**显式**闭合（`</div>`）建树；只处理一小张隐式闭合表（`li`/`p`/`td`/`tr`/
  `option`/`dt`/`dd` 等最常见的未闭合写法），不做完整的 HTML5 树构造算法；
- 选择器支持：`tag`、`*`、`.class`、`#id`、`[attr]`、`[attr=v]`、`[attr*=v]`、
  `[attr^=v]`、`[attr$=v]`、`[attr~=v]`，组合子支持**后代**（空格）与**子元素**（`>`），
  逗号分隔多组；不支持伪类 / 伪元素 / `+` / `~`；
- 文本抽取按块级标签补换行，不还原 CSS 布局（与 `pipeline/fulltext_clean` 的行式清洗配套）。

真实站点用到的选择器若超出这张表，接入报告会给出「需补充解析能力」的缺口项，而不是
静默给出错误结果。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# ---------- 编码嗅探 ----------

_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE
)
# 站点常写 gbk/gb2312，用 gb18030 解（超集）能多吃下生僻字。
# latin-1 必须改写掉：它**永远不会解码失败**，留着它等于「声明 latin-1 的页面一律按它解」，
# 中文站误标 latin-1 很常见（同类的 cp1252 也一样，真要接西文站再补）。
_ENCODING_ALIASES = {
    "gbk": "gb18030",
    "gb2312": "gb18030",
    "gb_2312-80": "gb18030",
    "iso-8859-1": "utf-8",
}
# 「这次解码解出了中文吗」——用来判断 UTF-8 是不是真的解对了（扩展 A + 基本区）
_CJK_RE = re.compile("[㐀-鿿]")


# U+FFFD 替换字符：宽容解码（errors="replace"）留下的「这个字节编不出来」标记。
# 用 chr() 而不是字面量/`"�"` 转义：源码里保持纯 ASCII，不依赖编辑器怎么存这个码位。
_REPLACEMENT = chr(0xFFFD)


def _decode(raw: bytes, encoding: str, errors: str = "strict") -> str | None:
    """按 `encoding` 解码；编不出来（非法字节 / 未知编码名）返回 None。"""
    try:
        return raw.decode(encoding, errors=errors)
    except (UnicodeDecodeError, LookupError):
        return None


# 「这段字节基本上就是 UTF-8」的容差：解出来的替换字符占比上限（下不封顶到 2 个，
# 免得短页面按比例算出来的额度还不到一个字符）。
# 真 GBK 页面按 UTF-8 解会有三成以上字节编不出来（实测 38 处 / 约 120 字），
# 与「混进个别脏字节」差两个数量级，所以阈值不敏感。
_UTF8_TOLERANCE = 0.005


def _utf8_ish(raw: bytes) -> str | None:
    """能当 UTF-8 解就返回解出的文本，否则 None（`decode_html` 的第一优先）。

    要求同时满足「坏字节极少」和「解出了中文」：同时挡住两个方向——
    GBK 内容按 UTF-8 解会得到拉丁/阿拉伯字符（无中文）且坏字节比例极大；
    而声明成 gbk/gb2312、内容其实早已是 UTF-8 的页面属于**该用** UTF-8 的那一类。
    """
    text = _decode(raw, "utf-8", "replace")
    if text is None:
        return None
    if raw.isascii():
        return text  # 纯 ASCII：任何编码解出来都一样
    damage = text.count(_REPLACEMENT)
    if damage > max(2, len(text) * _UTF8_TOLERANCE):
        return None
    return text if _CJK_RE.search(text) else None


def _candidate_encodings(raw: bytes, content_type: str | None) -> list[str]:
    """候选编码：响应头 charset → `<meta charset>` → utf-8 → gb18030（别名归一、去重）。"""
    declared: list[str] = []
    if content_type:
        match = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_\-]+)", content_type, re.IGNORECASE)
        if match:
            declared.append(match.group(1))
    meta = _META_CHARSET_RE.search(raw[:4096])
    if meta:
        declared.append(meta.group(1).decode("ascii", "ignore"))

    ordered: list[str] = []
    for name in [*declared, "utf-8", "gb18030"]:
        enc = _ENCODING_ALIASES.get(name.strip().lower(), name.strip().lower())
        if enc and enc not in ordered:
            ordered.append(enc)
    return ordered


def decode_html(raw: bytes, content_type: str | None = None) -> str:
    """把响应体解成文本。判定口径：**谁能解出中文就用谁**，绝不让兜底把整页毁掉。

    1. **看起来就是 UTF-8**（坏字节占比极低，见 `_UTF8_TOLERANCE`）**且解出了中文**
       → 按 UTF-8 解。这是唯一一条硬证据：`gb18030` / `latin-1` 这类编码几乎能把任意
       字节解出个结果，「解通了」本身不算证据，而真 GBK 正文按 UTF-8 解会把三成以上
       的字节变成替换字符。
    2. 否则按声明（响应头 → meta）严格解，再退到 `gb18030`——中文政务站大量用 GBK，
       且响应头常缺失或标错。
    3. 全部解不通（页面里混进了非法字节）→ 取**替换字符最少**的那一次宽容解码。

    这三条是 2026-10-15 的修正，旧实现「按候选顺序取第一个解不报错的」有同一族的坑：
    凡是无条件信任声明 / 兜底的做法，都会让**整页**（不是个别字）变成乱码，而且不报错。

    - 声明 gbk/gb2312、内容其实早已迁到 UTF-8 的老页面（只改了页面没改 meta）：
      `gb18030` 能否「成功」解码取决于正文长度，**同一站点只有一部分文章乱码**，
      且是「涓枃」式汉字乱码 —— 现在由第 1 条救回；
    - GBK 页面里只要有一个 gb18030 解不了的字节（最典型是单字节的「€」= 0x80，
      也有 0xFF / 0xFE），旧兜底那句 `utf-8, errors="replace"` 会把**整页**变成
      「�」与拉丁扩展字母的混合体（实测：正文 38 处 U+FFFD，且不可还原）——
      现在由第 3 条按「替换字符最少」选，只坏那一个字节；
    - 反向的「UTF-8 页面里混进个别脏字节」：旧实现在 utf-8 严格解失败后照样落到
      `gb18030`，若那段字节恰好合法就整页变汉字乱码 —— 现在由第 1 条的容差救回。
    """
    if not raw:
        return ""
    candidates = _candidate_encodings(raw, content_type)

    utf8_text = _utf8_ish(raw)
    if utf8_text is not None:
        return utf8_text

    for enc in candidates:
        text = _decode(raw, enc)
        if text is not None:
            return text

    # 候选表里恒有 utf-8 / gb18030，所以这里的列表不会为空
    lenient = [t for enc in candidates if (t := _decode(raw, enc, "replace")) is not None]
    return min(lenient, key=lambda text: text.count(_REPLACEMENT))


# ---------- 迷你 DOM ----------

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
# 这些标签一起就隐式闭合尚未闭合的 <p>（浏览器行为）
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "dl", "dd", "dt", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
# 隐式闭合表：遇到 key 时，栈顶若在 value 集合里就连连弹出
_IMPLICIT_CLOSE: dict[str, set[str]] = {
    "li": {"li"},
    "p": {"p"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "tr": {"tr", "td", "th"},
    "option": {"option"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "thead": {"thead", "tbody", "tfoot"},
    "tbody": {"thead", "tbody", "tfoot"},
    "a": {"a"},
}
# 抽文本时整体跳过的容器（脚本/样式/页头），它们的「文字」不是内容
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object"}
# 公开别名：结构分析（`sources/analyze.py`）判候选时要跳过同一批标签
SKIP_TAGS = _SKIP_TAGS


class Node:
    """DOM 节点。`tag == "#text"` 表示文本节点（内容在 `text`）。"""

    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(
        self, tag: str, attrs: dict[str, str] | None = None,
        parent: "Node | None" = None, text: str = "",
    ) -> None:
        self.tag = tag
        self.attrs: dict[str, str] = attrs or {}
        self.children: list[Node] = []
        self.parent = parent
        self.text = text

    # -- 基础 --

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)

    def has_class(self, name: str) -> bool:
        return name in self.classes

    def iter_elements(self):
        """深度优先遍历全部**元素**节点（不含文本节点）。"""
        for child in self.children:
            if child.tag == "#text":
                continue
            yield child
            yield from child.iter_elements()

    def text_content(self) -> str:
        """取子树纯文本（按块级标签补换行、跳过 script/style）。"""
        return node_text(self)

    def detach(self) -> None:
        """把自己从父节点摘掉（正文清洗时剔除分享条/相关阅读等噪声节点）。"""
        if self.parent is None:
            return
        try:
            self.parent.children.remove(self)
        except ValueError:
            pass
        self.parent = None

    def select(self, selector: str) -> list[Node]:
        return select(self, selector)

    def select_one(self, selector: str) -> Node | None:
        found = select(self, selector)
        return found[0] if found else None

    def describe(self, max_depth: int = 4) -> str:
        """给节点生成一个可复用的 CSS 选择器（`#id` > `.class` > 标签路径）。"""
        return describe_selector(self, max_depth=max_depth)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        if self.tag == "#text":
            return f"<text {self.text[:20]!r}>"
        return f"<{self.tag} {self.attrs}>"


class _DomBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self._stack: list[Node] = [self.root]

    def _append(self, node: Node) -> None:
        self._stack[-1].children.append(node)
        node.parent = self._stack[-1]

    def _auto_close(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            while len(self._stack) > 1 and self._stack[-1].tag == "p":
                self._stack.pop()
        closers = _IMPLICIT_CLOSE.get(tag)
        if closers:
            while len(self._stack) > 1 and self._stack[-1].tag in closers:
                self._stack.pop()

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self._auto_close(tag)
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._stack[-1])
        self._append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs}, self._stack[-1])
        self._append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return
        # 没找到对应的开标签：忽略（容错，不重建整棵树）

    def handle_data(self, data: str) -> None:
        if data and not data.isspace():
            self._append(Node("#text", None, self._stack[-1], text=data))


def parse_html(text: str) -> Node:
    """解析 HTML 文本，返回 `#document` 根节点（容错解析，不抛异常）。"""
    builder = _DomBuilder()
    try:
        builder.feed(text or "")
        builder.close()
    except Exception:  # noqa: BLE001 - 畸形页面只保留已解析部分，不阻断流程
        pass
    return builder.root


# ---------- 文本抽取 ----------

_SPACE_RE = re.compile(r"[ \t 　]+")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


# 表格单元格分隔。**不能直接往 parts 里塞 `\t`**：空白收敛会把制表符并成空格，
# 行列结构就又没了。这里先塞 US（unit separator，0x1F）占位——它不是空白字符，
# 能安全穿过空白收敛与 `str.strip()`——最后一步再换回 `\t`。
_CELL_SEP = "\x1f"
_CELL_TAGS = {"td", "th"}


def node_text(node: Node) -> str:
    """块级感知的纯文本：块级标签收尾补换行，**同一行的单元格之间补制表符**。

    空白收敛但不改写文字本身。表格单独照顾：`<td>`/`<th>` 若按块级处理会各自成行，
    政策附表的「序号 / 项目 / 金额」就糊成一竖列、行列关系全丢，所以单元格之间用
    `\\t`、行之间才用 `\\n`；单元格内部再出现的块级标签不再补换行（`<td><p>a</p></td>`
    仍然算一个单元格）。

    仍不还原 CSS 布局（跨行/跨列、嵌套表的对齐不保证），与 `pipeline/fulltext_clean`
    的行式清洗配套。
    """
    parts: list[str] = []

    def walk(current: Node, in_cell: bool = False) -> None:
        if current.tag in _SKIP_TAGS:
            return
        if current.tag == "#text":
            parts.append(current.text)
            return
        if current.tag == "br":
            parts.append("\n")
            return
        for child in current.children:
            walk(child, in_cell or current.tag in _CELL_TAGS)
        if current.tag in _CELL_TAGS:
            parts.append(_CELL_SEP)
        elif current.tag in _BLOCK_TAGS and not in_cell:
            parts.append("\n")

    walk(node)
    text = "".join(parts)
    text = _SPACE_RE.sub(" ", text)
    # 行尾多出来的分隔符是 `<td>` 收尾时补的（每行最后一个单元格也会补），一并去掉
    lines = [line.strip().rstrip(_CELL_SEP) for line in text.split("\n")]
    out = _BLANK_RUN_RE.sub("\n\n", "\n".join(lines)).strip()
    return out.replace(_CELL_SEP, "\t").strip()


# ---------- CSS 选择器子集 ----------

_ATTR_PART_RE = re.compile(
    r"""\[\s*([a-zA-Z_:][-\w:.]*)\s*(?:(~|\^|\$|\*|\|)?=\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+))\s*)?\]"""
)
_PART_RE = re.compile(r"([.#])([-\w]+)|(\[[^\]]*\])")
_TAG_RE = re.compile(r"^([*a-zA-Z][-\w]*)")


class _Compound:
    __slots__ = ("tag", "classes", "node_id", "attrs")

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.classes: list[str] = []
        self.node_id: str | None = None
        self.attrs: list[tuple[str, str | None, str | None]] = []


def _parse_compound(text: str) -> _Compound:
    match = _TAG_RE.match(text)
    compound = _Compound((match.group(1) if match else "*").lower())
    rest = text[len(match.group(0)) :] if match else text
    for part in _PART_RE.finditer(rest):
        if part.group(1) == ".":
            compound.classes.append(part.group(2))
        elif part.group(1) == "#":
            compound.node_id = part.group(2)
        else:
            attr = _ATTR_PART_RE.match(part.group(3))
            if not attr:
                continue
            value = attr.group(3) or attr.group(4) or attr.group(5)
            compound.attrs.append((attr.group(1).lower(), attr.group(2), value))
    return compound


def _tokenize(selector: str) -> list[tuple[str, str]]:
    """切成 (组合子, 复合选择器) 序列；组合子取 `" "`（后代）或 `">"`（子元素）。"""
    tokens: list[tuple[str, str]] = []
    buf = ""
    depth = 0
    for ch in selector:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if depth == 0 and (ch.isspace() or ch == ">"):
            if buf:
                tokens.append((" ", buf))
                buf = ""
            if ch == ">":
                tokens.append((">", ""))
            continue
        buf += ch
    if buf:
        tokens.append((" ", buf))

    chain: list[tuple[str, str]] = []
    pending = " "
    for combinator, compound in tokens:
        if combinator == ">":
            pending = ">"
            continue
        chain.append((pending, compound))
        pending = " "
    return chain


def _match_compound(node: Node, compound: _Compound) -> bool:
    if node.tag == "#text":
        return False
    if compound.tag != "*" and node.tag != compound.tag:
        return False
    if compound.node_id is not None and node.get("id") != compound.node_id:
        return False
    node_classes = node.classes
    for cls in compound.classes:
        if cls not in node_classes:
            return False
    for name, op, value in compound.attrs:
        actual = node.attrs.get(name)
        if actual is None:
            return False
        if op is None:
            continue
        actual = actual or ""
        value = value or ""
        if op == "=" and actual != value:
            return False
        if op == "*=" and value not in actual:
            return False
        if op == "^=" and not actual.startswith(value):
            return False
        if op == "$=" and not actual.endswith(value):
            return False
        if op == "~=" and value not in actual.split():
            return False
        if op == "|=" and not (actual == value or actual.startswith(value + "-")):
            return False
    return True


def _match_chain(node: Node, chain: list[tuple[str, str]]) -> bool:
    """自右向左匹配（最后一节是节点自身）。"""
    compounds = [_parse_compound(text) for _, text in chain]
    if not _match_compound(node, compounds[-1]):
        return False
    index = len(compounds) - 2
    current = node
    while index >= 0:
        combinator = chain[index + 1][0]
        target = compounds[index]
        if combinator == ">":
            current = current.parent
            if current is None or not _match_compound(current, target):
                return False
        else:
            parent = current.parent
            found = None
            while parent is not None:
                if _match_compound(parent, target):
                    found = parent
                    break
                parent = parent.parent
            if found is None:
                return False
            current = found
        index -= 1
    return True


def select(root: Node, selector: str) -> list[Node]:
    """在 `root` 子树内按选择器取节点（文档序，自动去重）。"""
    results: list[Node] = []
    seen: set[int] = set()
    for group in (selector or "").split(","):
        group = group.strip()
        if not group:
            continue
        chain = _tokenize(group)
        if not chain:
            continue
        for node in root.iter_elements():
            if id(node) in seen:
                continue
            if _match_chain(node, chain):
                seen.add(id(node))
                results.append(node)
    return results


def select_one(root: Node, selector: str) -> Node | None:
    found = select(root, selector)
    return found[0] if found else None


# ---------- 选择器生成（结构分析用） ----------

def _own_selector(node: Node) -> str | None:
    """能用属性表达就返回（`#id` / `div.a.b`），否则 None。"""
    if node.tag in ("#document", "#text"):
        return None
    node_id = node.get("id")
    if node_id:
        return f"#{node_id}"
    classes = [c for c in node.classes if c]
    if classes:
        return node.tag + "".join(f".{c}" for c in classes[:2])
    return None


def describe_selector(node: Node, max_depth: int = 4) -> str:
    """为一个节点生成**尽量稳**的选择器：优先 `#id`，其次 `.class`，最后标签路径。

    只在必要时向上拼路径，最多 `max_depth` 层——过长的路径对改版极脆弱，宁可让
    接入报告提示「选择器不稳，建议人工确认」。
    """
    own = _own_selector(node)
    if own:
        return own
    parts: list[str] = []
    current: Node | None = node
    depth = 0
    while current is not None and current.tag != "#document" and depth < max_depth:
        own = _own_selector(current)
        if own:
            parts.insert(0, own)
            break
        parts.insert(0, current.tag)
        siblings = [
            s for s in (current.parent.children if current.parent else []) if s.tag == current.tag
        ]
        if len(siblings) > 1 and current.parent is not None:
            parts[0] = f"{current.tag}:nth-of-type({siblings.index(current) + 1})"
        current = current.parent
        depth += 1
    return " > ".join(parts) if parts else node.tag


def strip_nth_of_type(selector: str) -> str:
    """去掉 `:nth-of-type(n)`（选择器引擎不支持伪类，写进配置会静默匹配不到）。"""
    return re.sub(r":nth-of-type\(\d+\)", "", selector)


def selector_supported(selector: str) -> tuple[bool, str]:
    """校验选择器是否落在本引擎支持的子集内；返回 `(是否支持, 说明)`。"""
    if not selector or not selector.strip():
        return False, "选择器为空"
    try:
        for group in selector.split(","):
            group = group.strip()
            if not group:
                return False, "存在空的选择器分组"
            if ":" in group.replace("::", ":"):
                # 允许 [attr*=v] 里出现冒号（值里可能有），只拦伪类写法
                if re.search(r":(?![\w\-]+\])[a-zA-Z]", group):
                    return False, f"不支持伪类/伪元素：{group}"
            if re.search(r"[+~]", group):
                return False, f"不支持兄弟组合子（+ / ~）：{group}"
            if not _tokenize(group):
                return False, f"选择器无法解析：{group}"
    except Exception as exc:  # noqa: BLE001
        return False, f"选择器解析失败：{exc}"
    return True, ""
