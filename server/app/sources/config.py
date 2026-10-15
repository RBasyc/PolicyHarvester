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


class EntrypointConfig(BaseModel):
    """一个内容入口（栏目列表页 / 检索结果页）。

    `name` 同时是「发布机构分类」筛选的匹配值：用户在抓取配置里选 `agencies` 时，
    与入口名一致的那些入口才会被取数（见 `HtmlListSource.search`）。
    """

    name: str = Field(..., min_length=1, max_length=60)
    url: str = Field(..., min_length=1)
    list_selector: str = Field(..., min_length=1, description="条目容器选择器（命中多个 = 多条）")
    link_selector: str = "a"  # 相对条目容器的链接选择器

    title_selector: str | None = None  # 不填 = 用链接文本
    date_selector: str | None = None  # 不填 = 不取日期（详情页再补）
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    notes: str | None = None


class DetailConfig(BaseModel):
    """详情页解析规则。

    `container_selectors` 是**候选链**：按顺序取第一个命中的容器；命中多个同类容器时
    取文本最长的一个（政务站常见「正文 + 打印版副本」两份）。全部落空 = 该页解析失败，
    记入验收报告的失败项（**不**退回整页文本——那会把导航/脚本当成正文）。
    """

    container_selectors: list[str] = Field(default_factory=list)
    title_selectors: list[str] = Field(default_factory=list)
    date_selectors: list[str] = Field(default_factory=list)
    agency_selectors: list[str] = Field(default_factory=list)
    doc_no_selectors: list[str] = Field(default_factory=list)
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
            "pagination": sorted({ep.pagination.kind for ep in self.entrypoints}),
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
            ("title_selector", ep.title_selector),
            ("date_selector", ep.date_selector),
        ):
            if not selector:
                continue
            ok, reason = selector_supported(selector)
            if not ok:
                problems.append(f"入口「{ep.name}」的 {field_name} 不可用：{reason}")

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

    if not config.detail.container_selectors:
        problems.append(
            "detail.container_selectors 为空：没有正文容器候选，详情页将整页解析失败"
            "（先跑 analyze_content_structure 取得候选选择器）"
        )
    return problems


def config_from_row(row: dict[str, Any]) -> SourceConfig:
    """数据库行 → `SourceConfig`（`config` 列是 JSONB，psycopg 已解成 dict）。"""
    raw = row.get("config") or {}
    if isinstance(raw, str):  # 兜底：某些驱动/手工导入会给字符串
        raw = json.loads(raw)
    return SourceConfig.model_validate(raw)
