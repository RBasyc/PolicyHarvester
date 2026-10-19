"""入口翻页的证据核对：**「只有一页」必须由站点页面自己证明**。

背景（2026-10-16）：过去「单页」是配置里的**默认值**——反推不出翻页规则就静默写
`kind="none"`，而验收只对**声明了**翻页的入口发问（没声明就记为「未测试」放行）。
于是「站点其实有几十页、抓取永远只取第 1 页」这条路上没有任何环节会报警：用户看到的
是「抓到 N 篇就不动了，显示无新增」，且说不清是站点没内容还是我们没翻页。

本模块把这件事翻过来：拿入口**自己的页面**去问站点「你还有下一页吗」。能推导出规则的
就真取第 2 页验一遍——验过才写进配置；推导不出或验不过，就如实说明「召回被截断在第 1
页」，而不是假装只有一页。

口径（**通用，不含任何站点专属规则**）：

- 证据只来自 `analyze.pagination_candidates`——即页面里真实的「下一页」链接与页码链接；
- 采纳的门槛是**真取第 2 页、真解析出第 1 页没有的条目**：只推不验等于把猜测写进配置，
  那正是这条 bug 的成因；
- 推不出 / 验不过一律**保持单页**并给出人话理由，绝不硬塞一个可能被站点忽略的参数
  （猜错的参数会让站点把第 1 页原样回一遍，看起来「翻页了」，其实全是重复）。

纯函数（`has_pagination_evidence` / `rule_from_evidence`）离线可测，不碰网络。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.sources import net
from app.sources.config import EntrypointConfig, PaginationConfig
from app.sources.html_source import build_entry_url, parse_list_page
from app.sources.htmlparse import parse_html
from app.sources.analyze import pagination_candidates


def has_pagination_evidence(evidence: dict[str, Any] | None) -> bool:
    """页面自己有没有露出「还有更多页」的迹象（页码链接或「下一页」链接）。

    这是「单页声明成不成立」的唯一依据：页面没有翻页入口，单页就是事实；有，就必须解释
    为什么配置里是单页。
    """
    if not evidence:
        return False
    if int(evidence.get("page_links") or 0) > 0:
        return True
    return bool(evidence.get("next_href"))


def rule_from_evidence(
    evidence: dict[str, Any] | None,
    *,
    max_pages: int,
    default_start: int = 1,
    default_step: int = 1,
) -> dict[str, Any] | None:
    """`pagination_candidates` 的输出 → 可直接写进入口配置的 pagination dict。

    只认**反推得出**的两种规则：

    - `query_param`：页码落在查询参数里（`?page=2`）；
    - `path_index`：页码落在路径里（`index_2.html`）。

    其余情形（只看到「下一页」链接、页码太少、规律认不出）返回 `None`——**不臆造**
    param 或模板。调用方据此保持单页并把原因写清楚。

    与 `services/source_import._simplify_pagination` 是同一套口径（那边已改为委托本函数），
    避免「分析报告说能翻、组装配置说不能翻」两处各说各话。
    """
    if not evidence:
        return None
    kind = evidence.get("kind")
    if kind == "query_param" and evidence.get("param"):
        rule: dict[str, Any] = {"kind": "query_param", "param": str(evidence["param"])}
    elif kind == "path_index" and evidence.get("path_template"):
        rule = {"kind": "path_index", "path_template": str(evidence["path_template"])}
    else:
        return None
    rule["start"] = int(evidence.get("start") or default_start)
    rule["step"] = int(evidence.get("step") or default_step)
    rule["max_pages"] = int(max_pages)
    return rule


@dataclass(frozen=True)
class PaginationProbe:
    """一个入口的翻页核对结论。

    - `verified`：规则通过**真实第 2 页**验证，可以直接写进配置；
    - `rule`：验过的规则（仅 `verified` 时非空）；
    - `has_evidence`：页面自己看得出还有更多页（用于区分「真单页」与「翻不动」）；
    - `detail`：一句人话结论（进日志 / 入口 notes / 覆盖范围说明）。
    """

    verified: bool
    rule: dict[str, Any] | None
    has_evidence: bool
    detail: str

    @property
    def truncated(self) -> bool:
        """召回是否**确定**被截断：页面有翻页迹象但配置只能取第 1 页。"""
        return self.has_evidence and not self.verified


async def probe_single_page_entry(
    entry: EntrypointConfig,
    *,
    allowed_domains: list[str] | None,
    page1_html: str | None = None,
    max_pages: int | None = None,
) -> PaginationProbe:
    """给一个声明为单页的入口找翻页规则：页面证据 → 推导 → 真取第 2 页验证。

    `page1_html` 已在手上时传进来可省一次抓取（调用方通常刚取过同一页）；
    `max_pages` 不传则沿用入口自身的上限——调用方应传入与 `_simplify_pagination`
    相同的值，否则「探测阶段补的规则」与「组装阶段反推的规则」会出现两个上限。
    """
    url = entry.url
    if page1_html is None:
        try:
            fetched = await net.fetch_text(url, allowed_domains=allowed_domains)
        except (net.FetchError, net.UnsafeURLError) as exc:
            return PaginationProbe(False, None, False, f"第 1 页取回失败，翻页未核对（{exc}）")
        html = fetched.text
    else:
        html = page1_html

    evidence = pagination_candidates(parse_html(html), url)
    if not has_pagination_evidence(evidence):
        return PaginationProbe(False, None, False, "页面无翻页入口——单页声明成立")

    rule = rule_from_evidence(
        evidence, max_pages=max_pages if max_pages is not None else entry.pagination.max_pages
    )
    if rule is None:
        return PaginationProbe(
            False,
            None,
            True,
            "页面有翻页链接但未能推导出规律，召回被截断在第 1 页"
            "——请人工确认翻页地址规律后补 pagination",
        )

    # 推导出的规则必须**真取第 2 页**验证过才算数（只推不验 = 把猜测写进配置）
    try:
        probe_entry = entry.model_copy(update={"pagination": PaginationConfig(**rule)})
    except Exception as exc:  # noqa: BLE001 - 推出来的规则不合法时如实退回单页
        return PaginationProbe(False, None, True, f"推导出的规则不合法（{exc}），保持单页")

    page2_url = build_entry_url(probe_entry, keyword=None, date_from=None, date_to=None, page=2)
    if page2_url is None:
        return PaginationProbe(False, None, True, "推导出的规则算不出第 2 页地址，保持单页")
    try:
        fetched2 = await net.fetch_text(page2_url, allowed_domains=allowed_domains)
    except (net.FetchError, net.UnsafeURLError) as exc:
        return PaginationProbe(False, None, True, f"第 2 页取回失败，规则未验证（{exc}）")

    items2 = parse_list_page(fetched2.text, probe_entry, fetched2.url)
    if not items2:
        return PaginationProbe(False, None, True, "规则算出的第 2 页解析不出条目，保持单页")

    first_urls = {item["url"] for item in parse_list_page(html, entry, url)}
    fresh = [item for item in items2 if item["url"] not in first_urls]
    if not fresh:
        # 推错的参数会被站点忽略、把第 1 页原样回一遍——这就是「看着翻了、其实没翻」
        return PaginationProbe(
            False, None, True, "规则算出的第 2 页与第 1 页完全重复（参数可能被站点忽略），保持单页"
        )
    return PaginationProbe(
        True,
        rule,
        True,
        f"已从页面证据推导出翻页规则并验证：第 2 页 {len(items2)} 条、其中新增 {len(fresh)} 条",
    )
