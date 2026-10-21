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
    `date` 已解析成 `date`（解析不了为 None）。它是不是**发布日期**由来源声明
    （`PolicySource.fulltext_date_is_publish`）——流水线只把发布日期写进 `publish_date`，
    成文日期走归档全文解析（`FulltextMeta.signed_date`），两者不互相顶替。

    `attachments` 是本页附带的原始文件清单（`{"url","name","ext"}`），元素形态由各源自行
    填充、消费方按 dict 取值——gov.cn 侧没有这项，配置化来源才产出。它只是**发现结果**：
    真正下载归档走 `source_artifact`（试运行阶段只记不落地，见 services/onboarding.py）。

    `metadata` 是字段规则（`config.DetailConfig.fields`）取到的**非保留字段**，键值都是
    字符串（索引号、成文日期、主题分类、发文机关……）。流水线把它合并进
    `policy_document.metadata`（JSONB），不参与正文清洗，也不覆盖标准列。

    `page_text` 是详情页的**整页文本**（导航噪声原样保留），只给「结构解析拿不到发布日期」
    时的模型兜底用：发布日期常常在正文容器之外的页头/元信息块里，只给 `body` 模型看不到。
    不落库、不参与清洗；来源没有提供时为 None（gov.cn 的 `body` 本身就是整页文本）。
    """

    body: str
    title: str | None = None
    issuer: str | None = None
    doc_no: str | None = None
    date: date | None = None
    # 日期来源（label/field/meta/selector）：预检只让可见标签或 meta 覆盖列表日期，
    # 避免把相关推荐/页脚里抓到的日期当成发布日期。
    date_source: str | None = None
    image_count: int = 0
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    page_text: str | None = None


class PolicySource:
    """抓取源基类。类属性是站点静态元信息；方法是站点接口约定。"""

    key: str = ""
    label: str = ""
    doc_types: tuple[str, ...] = ()  # 文种白名单（gov.cn 检索 pcodeJiguan 参数的可选值）
    fetched_by: str = ""  # 落库 `fetched_by` 列的来源标识
    # `fetch_fulltext` 报的 `date` 是不是**发布日期**。默认是——配置化来源的详情页上标的
    # 就是站点点名的那一天。gov.cn 是例外：全文接口报的是元信息表里的**成文日期**
    # （见 tools/China-Central-Policy-MCP/src/tools/getPolicyFulltext.ts），而成文日期
    # 常比发布日期早几周到几个月。流水线据此决定要不要拿它回填 `publish_date`：
    # 不区分的话，成文日期会顶掉列表轮写进去的发布日期，跨年文件被分错年份、成果库的
    # 日期在抓完全文后凭空变一天，用户按「我选的日期窗口」核对时看着像越界。
    fulltext_date_is_publish: bool = True

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

    def filter_capabilities(self) -> dict[str, Any]:
        """这个来源的**筛选能力**（`filters` 接口据此决定前端隐藏什么、怎么解释）。

        默认值 = 「服务端全支持」：基类只服务于 gov.cn 那种带检索接口的来源，
        新引擎必须**显式覆盖**它——不覆盖就是默认声称「筛选都在服务端生效」，
        而那正是最容易被用户当成事实的谎话。字段含义见
        `app/sources/config.capability_summary`。
        """
        return {
            "keyword": "server",
            "date": "server",
            "agency": "server",
            "doc_type": "server",
            "search_entrypoints": [],
        }

    def normalize_item(self, raw: dict[str, Any]) -> PolicyItem | None:
        """原始 dict → `PolicyItem`；标题/链接缺失返回 None（由调用方丢弃）。"""
        raise NotImplementedError
