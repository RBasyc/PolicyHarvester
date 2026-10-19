"""网站来源接入配置（`SourceConfig`）：把「某个网站怎么取数」写成一份可版本化的数据。

设计口径（对应接入 Skill 的第四阶段）：

- **配置负责差异，代码负责通用能力**。列表页/详情页/分页/附件这些差异全部落在配置里；
  取数（SSRF、限频、重定向复验）与解析（DOM、选择器）由 `app/sources/net.py`、
  `app/sources/htmlparse.py` 提供，新增站点原则上不改代码。
- **不预设获取途径**。`engine` 现有 `html_list`（列表页 + 详情页）；确定性更强的
  （官方 API / RSS）走同一份配置的 `entrypoints`（同样是「取回文本 → 选择器解析」），
  确实覆盖不了的才新增引擎。
- **配置必须能被校验**。`validate_source_config()` 在注册与试运行前跑一遍，把
  「域名不在白名单」「选择器引擎不支持」「正文容器为空」这类问题在落库前拦下。
- **版本可追溯**。配置落库时带 `version`，每次变更写一条 `content_source_revision`；
  `config_fingerprint()` 给出内容指纹，便于判断「这次是不是真的改了」。

注意 `scope.allowed_domains` 是**必填**的：它既是来源边界，也是 SSRF 白名单的一部分
（见 `app/sources/net.py`）。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.sources.htmlparse import selector_supported
from app.sources.net import domain_allowed

SourceLevel = Literal["national", "province", "city", "county", "other"]
SourceType = Literal["government_portal", "open_api", "rss", "mixed"]
SourceEngine = Literal["html_list"]

# 落库状态机：draft（草案，仅试运行）→ validated（试运行通过）→ active（已注册启用）
#                                  ↘ disabled（人工停用，可再启用）
SourceStatus = Literal["draft", "validated", "active", "disabled"]

# 探测试运行检索型入口的**兜底关键词**：没有用户关键词时用它验证「检索页取回后能不能解析
# 出条目」。选「通知」是因为它在政务站几乎必然有命中——这一步量的是**检索页的结构**，
# 与内容相关性无关，所以它命中什么内容都无所谓；报告与日志里都会如实写明用的是它。
# 页面导入（`services/source_import.py`）与试运行验收（`services/onboarding.py`）共用同一个，
# 两处口径必须一致，否则「导入时试通了、验收时又不通」会变成一场没有结论的排查。
SEARCH_PROBE_KEYWORD = "通知"

class PaginationConfig(BaseModel):
    """列表页翻页规则。

    - `none`      ：只有一页（取第 2 页起返回空 → 流水线判定该入口翻到底）；
    - `query_param`：`?{param}={start + (page-1)*step}`，最常见；
    - `path_index`：路径模板替换，`{page}` 取自 `start + (page-1)*step`
      （如 `index_{page}.html`、`list_{page}.htm`）。
    """

    kind: Literal["none", "query_param", "path_index"] = "none"
    param: str = "page"  # query_param 用的参数名
    path_template: str | None = None  # path_index 用的模板，必须含 {page}
    start: int = 1  # 第 1 页的取值
    step: int = 1
    max_pages: int = 20  # 硬上限：翻到这一页后不再翻（防止把站点掏空）

    @model_validator(mode="after")
    def _check(self) -> "PaginationConfig":
        if self.kind == "path_index" and not (self.path_template or "").strip():
            raise ValueError("path_index 翻页必须提供 path_template（含 {page} 占位）")
        if self.kind == "path_index" and "{page}" not in (self.path_template or ""):
            raise ValueError("path_template 必须含 {page} 占位符")
        if self.max_pages < 1:
            raise ValueError("max_pages 至少为 1")
        return self


class SearchConfig(BaseModel):
    """把入口声明成**站内检索**入口（而不是栏目列表页）。

    这是「关键词/日期筛选能否在服务端生效」的唯一开关：栏目列表页给不了检索能力，
    一个关键词只能靠翻遍栏目页 + 本地比对标题来碰运气（召回受翻页深度限制）；
    而站内检索页接受 `?关键词=…&起始日期=…`，召回由**站点自己的索引**决定。

    - `keyword_param` 是检索页接收关键词的参数名（`keyword` / `searchword` / `q` / `key`…），
      **实测得到，不猜**：`analyze.search_forms()` 会把页面里 GET 表单的 action 与
      input 名一并报出来，照着填。
    - `date_from_param` / `date_to_param` 只在站点检索页确实接受日期参数时才填；
      不填 = 该站检索页不支持按日期收窄，日期仍由 `validate_item` 在取回后复核。
    - `fixed_params` 是检索页的固定参数（TRS 系常见的 `channelid` / `siteid`，
      多半以隐藏 input 的形式躺在表单里）——它们决定「在哪批内容里检索」。
    - **无关键词时不走检索入口**：检索页在没有关键词参数时返回的通常是空结果页或首页，
      不是一条可浏览的路径。关键词为空的那一轮，检索入口会被跳过（`build_search_url`).
    """

    keyword_param: str = Field("keyword", min_length=1, max_length=40)
    date_from_param: str | None = Field(None, max_length=40)
    date_to_param: str | None = Field(None, max_length=40)
    # 日期参数的写法（strftime 格式）；站点各写各的，实测后填
    date_format: str = "%Y-%m-%d"
    # 检索页上的固定参数（隐藏 input / 约定参数），如 {"channelid": "36021"}
    fixed_params: dict[str, str] = Field(default_factory=dict)

    @field_validator("keyword_param")
    @classmethod
    def _clean_keyword_param(cls, value: str) -> str:
        # 全是空格的参数名会拼出 `?%20%20=通知`——地址看着正常、检索页永远不命中，
        # 是最难查的一类错，所以在入口上就拦下（照 `search_forms[].field` 填实测值）。
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("keyword_param 不能为空：照检索报告的 search_forms[].field 填实测参数名")
        return cleaned

    @field_validator("date_from_param", "date_to_param")
    @classmethod
    def _clean_param(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @model_validator(mode="after")
    def _check(self) -> "SearchConfig":
        # date_format 必须在真实日期上产出「年/月/日」三样。strftime 对写错的指令是
        # **原样输出**而不是报错，所以拿一个确定的日期渲染一遍、认输出比认格式串可靠。
        rendered = date(2000, 1, 2).strftime(self.date_format)
        if not all(token in rendered for token in ("2000", "01", "02")):
            raise ValueError(
                f"date_format「{self.date_format}」渲染 2000-01-02 得到「{rendered}」，"
                "看不出年月日——站点要求的写法要实测后填（如 %Y-%m-%d / %Y%m%d）"
            )
        return self


# 字段取值位置。政务站最常见的两种形状都在这里：
#   <span>文　　号：</span> 惠府〔2025〕13号   → 标签在 span，值在父节点的直接文本里（parent_own_text）
#   <div>发布日期：2025-03-10</div>           → 标签和值在同一个节点（self_text + drop_label）
#   <dt>发布机构</dt><dd>惠州市政府办公室</dd> → 标签与值分居兄弟节点（next_sibling_text）
FieldValueFrom = Literal[
    "self_text",
    "own_text",
    "parent_text",
    "parent_own_text",
    "next_sibling_text",
    "attr",
]


class FieldRule(BaseModel):
    """一条**语义化**字段规则：先按标签找节点，再按位置取值，最后用正则收口。

    这是对旧 `xxx_selectors` 的增量：旧字段只能表达「第几个选择器命中什么」，
    遇到「标签在 `<span>`、值在父节点文本里」或「同一个 `div.col-6` 复用于多个字段」
    就束手无策。这里把「标签 → 值」的关系写成数据，模型可以自由组合，流水线只负责解释。

    - `scope`：先定位元信息块（如 `div.row.border`），避免在正文里误命中；
    - `selector`：候选节点（通常指向标签节点 `span.fw-bold`）；
    - `label_equals`：标签文本，比较前会**去掉所有空白与末尾冒号**，所以 `文　　号：`、
      `文号`、`文 号：` 都等价；
    - `label_matches`：同上，但用正则（在去空白后的标签文本上匹配）；
    - `value_from`：取哪个位置的文本，语义见 `FieldValueFrom`；
    - `drop_label`：从取值里去掉标签本身（`self_text`/`parent_text` 常需要）；
    - `pattern` / `group`：正则截取；`reject`：命中任一正则就跳过这个候选；
    - `normalize`：`whitespace` = 压空白；`date` = 解析成 ISO 日期（解析不了返回空）；
      `none` = 只 strip，保留换行；
    - `match_index`：标签可能命中多个节点时，从第几个开始尝试（0 起）；前一个取不到会
      继续尝试后面的，避免把「索引号」那格的空值当成整个字段失败。

    规则按列表顺序尝试，第一个取到非空值的规则获胜；这天然提供了 fallback 链。
    """

    scope: str | None = Field(None, description="在哪个子树内查找；不填 = 全页")
    selector: str = Field("*", min_length=1, description="候选节点选择器；通常指向标签节点")
    label_equals: str | None = Field(
        None, max_length=40, description="标签文本（忽略所有空白与末尾冒号）"
    )
    label_matches: str | None = Field(
        None, max_length=300, description="标签文本正则（在去空白后的标签文本上匹配）"
    )
    value_from: FieldValueFrom = "self_text"
    attr: str | None = Field(None, max_length=40, description="value_from=attr 时取哪个属性")
    drop_label: bool = False
    pattern: str | None = Field(None, max_length=500, description="取值正则；可用 group 指定捕获组")
    group: int = Field(0, ge=0)
    reject: list[str] = Field(default_factory=list, description="取值命中任一正则则跳过该候选")
    normalize: Literal["whitespace", "none", "date"] = "whitespace"
    match_index: int = Field(0, ge=0, description="从第几个标签命中开始尝试（0 起）")

    @field_validator("scope", "selector")
    @classmethod
    def _selector_ok(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        ok, reason = selector_supported(value)
        if not ok:
            raise ValueError(f"{info.field_name} 不可用：{reason}")
        return value

    @field_validator("pattern", "label_matches")
    @classmethod
    def _pattern_ok(cls, value: str | None, info: Any) -> str | None:
        if value:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"{info.field_name} 不是合法正则：{exc}") from exc
        return value

    @field_validator("reject")
    @classmethod
    def _reject_ok(cls, value: list[str]) -> list[str]:
        for item in value:
            try:
                re.compile(item)
            except re.error as exc:
                raise ValueError(f"reject 不是合法正则「{item}」：{exc}") from exc
        return value

    @model_validator(mode="after")
    def _check(self) -> "FieldRule":
        if self.value_from == "attr" and not (self.attr or "").strip():
            raise ValueError("value_from=attr 时必须提供 attr")
        if self.label_equals and self.label_matches:
            raise ValueError("label_equals 与 label_matches 只能二选一")
        return self


class EntrypointConfig(BaseModel):
    """一个内容入口（栏目列表页 / 检索结果页）。

    `name` 同时是「发布机构分类」筛选的匹配值：用户在抓取配置里选 `agencies` 时，
    与入口名一致的那些入口才会被取数（见 `HtmlListSource.search`）。检索型入口
    （`search` 非空）不出现在机构筛选名单里——它不是机构（见 `capability_summary`）。

    `search` 是**可选**的：填了它，这个入口就是站内检索入口，关键词/日期会送进站点
    自己的检索接口；不填就是原来的栏目列表页（关键词与日期只能取回后本地过滤）。
    """

    name: str = Field(..., min_length=1, max_length=60)
    url: str = Field(..., min_length=1)
    list_selector: str = Field(..., min_length=1, description="条目容器选择器（命中多个 = 多条）")
    link_selector: str = "a"  # 相对条目容器的链接选择器
    # 同页多列表的定位（2026-10-16「命名区域」增量，纯确定性）：
    # 一张页面上常有多个同签名的列表（标签页面板 / 并列模块，如门户页的「政府规章 /
    # 市政府文件 / 政策解读」三个 tab）。只写 list_selector 会把它们全部命中、合并、
    # 且无法只取其中一个。先按 scope_selector 定位容器（可命中多个），再在各容器的
    # 子树内跑 list_selector；scope_index 取命中的第几个（0 起），不填 = 全部合并。
    # scope 语义由 `parse_list_page` 执行，不依赖 CSS 伪类（引擎本就不支持 :nth-of-type）。
    scope_selector: str | None = Field(None, description="列表容器的作用域选择器（先定位再在子树内取条目）")
    scope_index: int | None = Field(None, ge=0, description="scope 命中多个时取第几个（0 起）；不填 = 全部")
    title_selector: str | None = None  # 不填 = 用链接文本
    date_selector: str | None = None  # 不填 = 不取日期（详情页再补）
    # 列表条目上的通用字段（与详情页 `fields` 同一套 DSL）。典型用途：列表里的
    # 发布日期、发文机关、文号。取到的值按字段名进入 `PolicyItem.extra`，随后以
    # `metadata` JSONB 落库；`date` 会兼作 `publish_date` 的兜底。
    item_fields: dict[str, list[FieldRule]] = Field(
        default_factory=dict, description="列表条目字段名 → 规则链（第一个取到非空值的规则获胜）"
    )
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    search: SearchConfig | None = None  # 非空 = 站内检索入口（关键词/日期进服务端）
    notes: str | None = None

    @property
    def is_search(self) -> bool:
        return self.search is not None


class DetailConfig(BaseModel):
    """详情页解析规则。

    `container_selectors` 是**候选链**：按顺序取第一个命中的容器；命中多个同类容器时
    取文本最长的一个（政务站常见「正文 + 打印版副本」两份）。全部落空时**先用结构
    启发式兜底挑一块**（`html_source._fallback_container`，会在 `warnings` 里标明
    「未经确认」），兜底也挑不出达标正文才判该页解析失败。两条路径都**不**退回整页
    文本——那会把导航/脚本当成正文。
    """

    container_selectors: list[str] = Field(default_factory=list)
    title_selectors: list[str] = Field(default_factory=list)
    date_selectors: list[str] = Field(default_factory=list)
    agency_selectors: list[str] = Field(default_factory=list)
    doc_no_selectors: list[str] = Field(default_factory=list)
    # 语义化字段规则（新口径）。与上面的 `*_selectors` 并存：`fields` 优先级更高，
    # 取到值的字段写回 title/agency/doc_no/date（按字段名），其余字段进入
    # `DetailResult.extra` → `FulltextResult.metadata` → `policy_document.metadata`。
    # 旧配置不填 `fields` 时行为完全不变。
    fields: dict[str, list[FieldRule]] = Field(
        default_factory=dict, description="字段名 → 规则链；title/agency/doc_no/date 为保留名"
    )
    drop_selectors: list[str] = Field(
        default_factory=list, description="正文内需剔除的节点（分享条/相关阅读/打印提示）"
    )
    attachment_selector: str | None = Field(
        None, description="附件链接选择器；不填 = 用内置扩展名启发式（pdf/doc/docx/xls/xlsx/zip/rar/ofd）"
    )
    min_body_chars: int = 200  # 试运行时判定「正文过短」的 CJK 字数下限


class ScopeConfig(BaseModel):
    """来源边界：域名白名单（必填）+ 起始地址 + 抓取节流参数（可覆盖全局默认）。"""

    allowed_domains: list[str] = Field(..., min_length=1)
    start_urls: list[str] = Field(default_factory=list)
    request_interval_ms: int | None = Field(None, ge=0, le=60_000)
    max_items_per_entry: int = Field(50, ge=1, le=500)


class GovernanceConfig(BaseModel):
    """治理口径：**来源可追溯 + 原文另存**是硬要求，其余按站点许可填。"""

    require_original_evidence: bool = True
    review_required: bool = True
    license_note: str | None = None  # 使用许可 / 转载说明（人工确认用）
    contact: str | None = None  # 对接人（可选）


class SourceConfig(BaseModel):
    """一份来源接入配置（可序列化成 JSONB 落库，见 `content_source.config`）。"""

    schema_version: int = 1
    key: str = Field(..., min_length=2, max_length=60, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
    label: str = Field(..., min_length=2, max_length=200, description="展示名；抓取任务的 source 字段")
    level: SourceLevel = "other"
    organization: str | None = None
    source_type: SourceType = "government_portal"
    engine: SourceEngine = "html_list"
    scope: ScopeConfig
    entrypoints: list[EntrypointConfig] = Field(..., min_length=1)
    detail: DetailConfig = Field(default_factory=DetailConfig)
    # 文号前缀 → 文种（如 {"浙政发": "浙政发"}）；空 = 不做文种归类
    doc_type_patterns: dict[str, str] = Field(default_factory=dict)
    # 列表页关键词复核口径：`substring` = 标题须含完整关键词；`any_char` = 至少共享一个字。
    # 与 gov.cn 的差异在于**这里是客户端过滤**（栏目列表页没有服务端检索），
    # 默认取更严格的 substring，避免把栏目里无关的条目整批抓进来。
    keyword_match: Literal["substring", "any_char"] = "substring"
    # 时间窗口内**缺日期**的条目是否丢弃。gov.cn 侧是丢弃（否则「无」不可信），
    # 但栏目列表页常不显示日期（日期在详情页）——默认保留，由详情页解析补日期。
    drop_undated_when_windowed: bool = False
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    notes: str | None = None

    @field_validator("label")
    @classmethod
    def _clean_label(cls, value: str) -> str:
        return value.strip()

    @field_validator("organization", "notes")
    @classmethod
    def _clean_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _check_unique_entrypoints(self) -> "SourceConfig":
        names = [ep.name.strip() for ep in self.entrypoints]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"入口名重复：{'、'.join(sorted(duplicates))}")
        return self

    # ---------- 便捷视图 ----------

    def entrypoint_urls(self) -> list[str]:
        return [ep.url for ep in self.entrypoints]

    def capabilities(self) -> dict[str, Any]:
        """这份配置的筛选能力（见 `capability_summary`）。"""
        return capability_summary(self.model_dump())

    def config_fingerprint(self) -> str:
        """配置内容指纹（去掉自由文本的展示/许可字段，只看会影响取数的部分）。

        用途是判断「这次注册是不是真的改了配置」——`save_content_source` 只在指纹变化时
        才 +1 版本，否则重复注册会刷出一堆无意义的版本记录。
        """
        payload = self.model_dump(exclude={"notes": True, "governance": {"license_note", "contact"}})
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def summary(self) -> dict[str, Any]:
        """给模型/日志看的精简视图（不要塞整份配置进上下文）。"""
        return {
            "key": self.key,
            "label": self.label,
            "level": self.level,
            "organization": self.organization,
            "engine": self.engine,
            "domains": self.scope.allowed_domains,
            "entrypoints": [{"name": ep.name, "url": ep.url} for ep in self.entrypoints],
            # 检索型入口单列：它们不在「发布机构」筛选名单里（不是机构）
            "search_entrypoints": [ep.name for ep in self.entrypoints if ep.is_search],
            "pagination": sorted({ep.pagination.kind for ep in self.entrypoints}),
            "filters": capability_summary(self.model_dump()),
            "attachments": bool(self.detail.attachment_selector) or "扩展名启发式",
            "fingerprint": self.config_fingerprint(),
        }


def validate_source_config(config: SourceConfig) -> list[str]:
    """校验配置的**可执行性**，返回问题列表（空 = 通过）。

    与 Pydantic 的字段校验互补：这里检查的是「跑起来会不会失败」——
    域名边界、选择器是否落在解析引擎支持子集内、正文容器是否给了。
    """
    problems: list[str] = []
    domains = config.scope.allowed_domains

    for ep in config.entrypoints:
        url = ep.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            problems.append(f"入口「{ep.name}」地址缺少 http(s) 协议：{url}")
        else:
            host = (urlsplit(url).hostname or "").lower()
            if not domain_allowed(host, domains):
                problems.append(
                    f"入口「{ep.name}」的主机 {host} 不在 allowed_domains（{'、'.join(domains)}）内"
                )
        for field_name, selector in (
            ("list_selector", ep.list_selector),
            ("link_selector", ep.link_selector),
            ("scope_selector", ep.scope_selector),
            ("title_selector", ep.title_selector),
            ("date_selector", ep.date_selector),
        ):
            if not selector:
                continue
            ok, reason = selector_supported(selector)
            if not ok:
                problems.append(f"入口「{ep.name}」的 {field_name} 不可用：{reason}")
        _validate_field_rules(
            f"入口「{ep.name}」的 item_fields", ep.item_fields, problems
        )

    for field_name, selectors in (
        ("container_selectors", config.detail.container_selectors),
        ("title_selectors", config.detail.title_selectors),
        ("date_selectors", config.detail.date_selectors),
        ("agency_selectors", config.detail.agency_selectors),
        ("doc_no_selectors", config.detail.doc_no_selectors),
        ("drop_selectors", config.detail.drop_selectors),
    ):
        for selector in selectors:
            ok, reason = selector_supported(selector)
            if not ok:
                problems.append(f"detail.{field_name} 不可用：{reason}")
    if config.detail.attachment_selector:
        ok, reason = selector_supported(config.detail.attachment_selector)
        if not ok:
            problems.append(f"detail.attachment_selector 不可用：{reason}")
    _validate_field_rules("detail.fields", config.detail.fields, problems)

    if not config.detail.container_selectors:
        problems.append(
            "detail.container_selectors 为空：没有正文容器候选，详情页将整页解析失败"
            "（先跑 analyze_content_structure 取得候选选择器）"
        )
    return problems


def _validate_field_rules(
    where: str, fields: dict[str, list[FieldRule]], problems: list[str]
) -> None:
    """字段规则的可执行性校验（选择器子集 + 正则已由 Pydantic validator 管）。"""
    for field_name, rules in fields.items():
        if not rules:
            problems.append(f"{where}.{field_name} 规则链为空")
            continue
        for index, rule in enumerate(rules):
            for label, selector in (("scope", rule.scope), ("selector", rule.selector)):
                if not selector:
                    continue
                ok, reason = selector_supported(selector)
                if not ok:
                    problems.append(
                        f"{where}.{field_name}[{index}].{label} 不可用：{reason}"
                    )


def capability_summary(config: dict[str, Any]) -> dict[str, Any]:
    """这份来源的**筛选能力**：哪些筛选项在服务端生效、哪些只是取回后的本地过滤。

    存在的理由：两种来源的筛选语义**不一样**，而差别不会自己浮出来——

    - gov.cn 有检索接口，关键词、日期、机构、文种都由服务端执行，召回由索引决定；
    - 配一个栏目列表页当来源时（`engine=html_list` 且入口没有 `search`），栏目页给不了
      检索能力，关键词只能拿翻到的那些列表页**逐条比对标题**、日期只能拿列表页上那个
      日期**逐条比对**。召回上限 = 翻页深度 × 每页条数，深处的、更早的文件永远碰不到。

    第二种情况前端必须说出来（否则用户以为「筛过了」），所以这里把口径做成数据。
    `entrypoints[].search` 非空的入口算服务端检索——**站内检索页是能把关键词和日期
    推回站点的唯一途径**（见 `SearchConfig`）。

    按原始 dict（`content_source.config` 列）计算，不做 Pydantic 校验：来源列表接口
    会拿历史行直接调它，一个字段不合规不该让整张列表 500。
    """
    entries = [e for e in (config.get("entrypoints") or []) if isinstance(e, dict)]
    search_entries = [e for e in entries if isinstance(e.get("search"), dict)]
    has_date_param = any(
        (e["search"].get("date_from_param") or e["search"].get("date_to_param"))
        for e in search_entries
    )
    return {
        "keyword": "server" if search_entries else "client_scan",
        "date": "server" if has_date_param else "client",
        "agency": "entry",  # 机构筛选 = 只取同名的入口（见 EntrypointConfig.name）
        "doc_type": "patterns" if config.get("doc_type_patterns") else "none",
        # 检索型入口不参与「发布机构」筛选（它不是机构），故单列出来
        "search_entrypoints": [str(e.get("name") or "") for e in search_entries if e.get("name")],
    }


def config_from_row(row: dict[str, Any]) -> SourceConfig:
    """数据库行 → `SourceConfig`（`config` 列是 JSONB，psycopg 已解成 dict）。"""
    raw = row.get("config") or {}
    if isinstance(raw, str):  # 兜底：某些驱动/手工导入会给字符串
        raw = json.loads(raw)
    return SourceConfig.model_validate(raw)
