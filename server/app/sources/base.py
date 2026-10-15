"""抓取源适配层基类与数据结构。

把「某站点的检索/全文接口约定」从流水线（pipeline/crawl_graph）里抽出来：
每类抓取源实现一个 `PolicySource` 子类，负责——

- `search_plan`   关键词 × 机构 × 文种怎么组合扇出（gov.cn 的多选只能服务端逐值扇出）；
- `search`        调底层工具检索一页，返回**原始 dict 列表**（判别标注仍由流水线写回 dict）；
- `validate_item` 站点特有的条目标过滤（如 gov.cn 的公报剔除、分词标题匹配、日期窗口复验）；
- `fetch_fulltext`取一篇正文；
- `normalize_item`原始 dict → `PolicyItem`（字段映射、零宽字符清理等）。

流水线只保留编排：预算 / 翻页续抓、入库判别（judge）、正文预检、落库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


class SourceError(Exception):
    """抓取源调用失败（工具未加载 / 超时 / 返回非预期内容）。

    由调用方（流水线）翻译成设计稿要求的用户可读文案。
    """


@dataclass
class PolicyItem:
    """检索结果条目的统一形态（`normalize_item` 的产物，落库前的中间表示）。"""

    title: str
    url: str
    date: date | None
    agency: str | None
    doc_no: str | None
    catalog_key: str | None
    catalog: str | None
    judge_tagged: bool | None = None
    judge_prob: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class FulltextResult:
    """`fetch_fulltext` 的产物：一篇政策的全文与元信息。

    `body` 是**未清洗**的原文（清洗 `split_fulltext` 是流水线的事）；
    `date` 已解析成 `date`（解析不了为 None）。

    `attachments` 是本页附带的原始文件清单（`{"url","name","ext"}`），元素形态由各源自行
    填充、消费方按 dict 取值——gov.cn 侧没有这项，配置化来源才产出。它只是**发现结果**：
    真正下载归档走 `source_artifact`（试运行阶段只记不落地，见 services/onboarding.py）。
    """

    body: str
    title: str | None = None
    issuer: str | None = None
    doc_no: str | None = None
    date: date | None = None
    image_count: int = 0
    attachments: list[dict[str, Any]] = field(default_factory=list)


class PolicySource:
    """抓取源基类。类属性是站点静态元信息；方法是站点接口约定。"""

    key: str = ""
    label: str = ""
    doc_types: tuple[str, ...] = ()  # 文种白名单（gov.cn 检索 pcodeJiguan 参数的可选值）
    fetched_by: str = ""  # 落库 `fetched_by` 列的来源标识

    def search_plan(
        self,
        keywords: list[str],
        agencies: list[str] | None = None,
        doc_types: list[str] | None = None,
    ) -> list[tuple[str | None, str | None, str | None]]:
        """检索组合扇出计划：返回 (keyword, agency, doc_type) 元组列表。"""
        raise NotImplementedError

    async def search(
        self,
        *,
        keyword: str | None,
        agency: str | None,
        doc_type: str | None,
        page: int,
        limit: int,
        date_from: date | None,
        date_to: date | None,
        timeout: int,
    ) -> list[dict[str, Any]]:
        """检索一页，返回原始条目 dict 列表；翻空/出错表现为空列表或抛 `SourceError`。"""
        raise NotImplementedError

    def validate_item(
        self,
        item: dict[str, Any],
        *,
        keyword: str | None,
        date_from: date | None,
        date_to: date | None,
        facet_active: bool,
    ) -> bool:
        """站点特有的条目标过滤；False = 丢弃（URL 去重等通用逻辑在流水线）。"""
        raise NotImplementedError

    async def fetch_fulltext(
        self, url: str, policy_id: int | None = None, *, timeout: int | None = None
    ) -> FulltextResult:
        """取一篇政策全文；失败抛异常（调用方决定按单篇失败还是整体失败处理）。"""
        raise NotImplementedError

    def fulltext_available(self) -> bool:
        """全文工具是否已加载（fail-open 预检用：没加载就整段跳过，不阻断检索）。"""
        return True

    def normalize_item(self, raw: dict[str, Any]) -> PolicyItem | None:
        """原始 dict → `PolicyItem`；标题/链接缺失返回 None（由调用方丢弃）。"""
        raise NotImplementedError
