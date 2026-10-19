"""网站来源接入服务：把「接一个新网站」的全过程做成可调用、可留痕的函数。

对应接入 Skill 的七个阶段，一个阶段一个（或几个）服务函数：

1. `inspect_source`       阶段 1–2：地址安全校验 + 取回首页/入口页 → 勘察报告
2. `discover_content`     阶段 2：从入口页找内容栏目 / 订阅源 / 检索入口 / 翻页 / 列表候选
3. `sample_content`       阶段 3：取代表性样本详情页 → 结构与元信息命中情况
4. `analyze_structure`    阶段 3：单页结构分析（纯离线可用，喂 HTML 字符串即可）
5. `generate_config`      阶段 4：把勘察结论组装成 `SourceConfig` 并做可执行性校验
6. `dry_run_ingestion`    阶段 5：小批量试运行 + 验收报告（**走真实检索链路**，见下）
7. `register_source`      阶段 6：注册落库（需验收通过或显式确认）
8. `manage_source`        阶段 7：来源管理（启停 / 查历史 / 发起真实试抓任务）
9. `diagnose`             阶段 7：失败归因（入口发现 / 访问限制 / 正文解析 / 附件 / 字段）

**试运行走的是真实链路**：`dry_run_ingestion` 把配置临时装进进程内注册表，然后调用
`pipeline/crawl_graph._search_policies`——也就是正式抓取用的那个检索编排函数（扇出预算、
条目标过滤、判别、正文预检全都在里面），再逐篇真正取详情页。因此它的结论不是「模拟跑通」，
而是「这条链路在真实站点上跑出来是什么样」。它**不建 crawl_task、不写 policy_document**
——那是注册后正式抓取的事（`manage_source(action="run_trial")` 会发起一次真实的小批量任务）。

**本模块只做编排**：取数在 `sources/net.py`（SSRF 校验 / 逐跳复验 / robots / 限频），
解析在 `sources/htmlparse.py`，候选打分在 `sources/analyze.py`，
正文指标复用 `pipeline/fulltext_clean.body_metrics`。

**所有对外承诺的东西都必须是真的**：取不到就报失败，没测就报未测试，需要人工确认就写进
报告——不把「没跑」写成「通过」（Skill 第七节的硬要求）。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.sources import net
from app.sources.analyze import analyze_page
from app.sources.config import (
    SEARCH_PROBE_KEYWORD,
    EntrypointConfig,
    SourceConfig,
    validate_source_config,
)
from app.sources.field_rules import rules_from_field_candidates
from app.sources.html_source import (
    HtmlListSource,
    build_entry_url,
    build_list_url,
    parse_list_page,
)
from app.sources.registry import get, get_by_label, register_configured, unregister
from app.sources.util import parse_date

logger = logging.getLogger(__name__)

# 仓库根目录（server/app/services/onboarding.py -> 上四级 = Agent-Demo）
_REPO_ROOT = Path(__file__).resolve().parents[3]

# ---------- 验收阈值（可执行标准，报告里逐条给出实测值与是否通过） ----------
_MIN_SAMPLES_FOR_BODY = 3  # 少于这么多篇样本时，正文完整性只作初步结论
_BODY_PASS_RATIO = 0.8  # 正文达标的样本占比下限
_MIN_SENT_ENDERS = 2  # 「读起来像政策条文」的句末标点下限（比预检的全局阈值宽松）
_DATE_HIT_RATIO = 0.6  # 详情页日期命中率下限
_AGENCY_HIT_RATIO = 0.5  # 详情页机构命中率下限
_ATTACHMENT_PASS_RATIO = 0.8  # 附件可达率下限
# 试运行最多探测几个附件（每个都可能几 MB）：本常量是**上限**，实际取
# `min(本值, SOURCE_MAX_ARTIFACTS_PER_RUN)`——试运行只是抽查可达性，不是全量归档。
_MAX_ATTACHMENT_PROBE = 3

_STATUS_PASSED = "passed"
_STATUS_FAILED = "failed"
_STATUS_BLOCKED = "blocked"

# 受阻（不是配置写错，而是当前环境/站点不允许）：报告里要把原因说清楚，不得写成失败或通过
_BLOCK_HINTS = (
    ("robots.txt", "站点 robots.txt 禁止抓取"),
    ("非公网地址", "目标地址被安全策略拒绝（私网 / 本机）"),
    ("不在来源白名单", "目标地址不在来源域名白名单内"),
    ("拒绝访问本机", "目标地址是本机 / 内部主机名"),
    ("HTTP 401", "站点要求身份认证（401）——按接入规范不绕过访问控制"),
    ("HTTP 403", "站点拒绝访问（403）——按接入规范不绕过访问控制"),
)


# ---------- 小工具 ----------


def _ok(detail: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True}
    if detail:
        out["detail"] = detail
    return out


def _fail(reason: str, *, kind: str = "failed") -> dict[str, Any]:
    return {"ok": False, "kind": kind, "error": reason}


def _blocked_reason(message: str) -> str | None:
    """从异常文案判断这是「受阻」还是「失败」。"""
    for hint, reason in _BLOCK_HINTS:
        if hint in message:
            return f"{reason}（{message.split('：')[-1][:120]}）"
    return None


def _safe_filename(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", (name or "").strip())[:80].strip("._ ")
    return cleaned or fallback


def _artifact_dir(run_id: int | None) -> Path:
    base = get_settings().source_artifact_dir.strip()
    root = Path(base) if Path(base).is_absolute() else _REPO_ROOT / base
    return root / (f"run-{run_id}" if run_id else "adhoc")


def _save_bytes(run_id: int | None, filename: str, payload: bytes) -> str | None:
    """把原始文件落盘（相对仓库根返回路径）；落盘失败只告警（元信息仍会入库）。"""
    try:
        directory = _artifact_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest()[:8]
        target = directory / f"{digest}-{_safe_filename(filename, 'artifact.bin')}"
        target.write_bytes(payload)
        return str(target.relative_to(_REPO_ROOT)).replace("\\", "/")
    except Exception as exc:  # noqa: BLE001 - 磁盘问题不该让接入流程崩掉
        logger.warning("原始文件落盘失败：%s", exc)
        return None


async def _open_run(kind: str, *, source_key: str | None, source_id: int | None, payload: dict[str, Any]) -> int | None:
    """开一条接入记录；数据库不可用时返回 None（流程继续，只是没有留痕）。"""
    from app.db import repositories as repo

    try:
        return await repo.create_onboarding_run(
            kind=kind, source_key=source_key, source_id=source_id, input=payload
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("接入记录写入失败（数据库不可用？）：%s", exc)
        return None


async def _close_run(
    run_id: int | None,
    *,
    status: str,
    summary: dict[str, Any],
    report: dict[str, Any],
    failure_reason: str | None = None,
) -> None:
    if run_id is None:
        return
    from app.db import repositories as repo

    try:
        await repo.finish_onboarding_run(
            run_id, status=status, summary=summary, report=report, failure_reason=failure_reason
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("接入记录收尾失败：%s", exc)


# ---------- 阶段 1–2：勘察 ----------


async def inspect_source(
    url: str,
    *,
    target: str | None = None,
    allowed_domains: list[str] | None = None,
    source_key: str | None = None,
) -> dict[str, Any]:
    """阶段 1–2：校验地址 → 取回页面 → 给出勘察报告（不是完整站点理解，够生成草案即可）。

    `allowed_domains` 为空时按目标 URL 的主机自动收敛白名单（用户只给了一个地址时，
    这一条就是唯一的来源边界；后续发现的其他域名会被拦下并在报告里列为「范围外」）。
    """
    domains = [d for d in (allowed_domains or []) if d.strip()]
    run_id = await _open_run(
        "inspect", source_key=source_key, source_id=None, payload={"url": url, "target": target}
    )
    report: dict[str, Any] = {"url": url, "target": target, "allowed_domains": domains}
    try:
        checked = net.check_url(url, allowed_domains=domains or None)
    except net.UnsafeURLError as exc:
        report.update({"status": _STATUS_BLOCKED, "reason": str(exc)})
        await _close_run(
            run_id, status=_STATUS_BLOCKED, summary={"reason": str(exc)}, report=report,
            failure_reason=str(exc),
        )
        return {**report, "ok": False, "kind": "blocked", "error": f"地址校验未通过：{exc}"}

    host = (urlsplit(checked).hostname or "").lower()
    if not domains:
        domains = [host]
        report["allowed_domains"] = domains
        report["allowed_domains_inferred"] = True

    robots = await net.robots_status(checked)
    report["robots"] = robots
    if not robots.get("allowed", True):
        reason = str(robots.get("reason") or "robots.txt 禁止抓取")
        report.update({"status": _STATUS_BLOCKED, "reason": reason})
        await _close_run(
            run_id, status=_STATUS_BLOCKED, summary={"reason": reason}, report=report,
            failure_reason=reason,
        )
        return {**report, "ok": False, "kind": "blocked", "error": reason}

    try:
        fetched = await net.fetch_text(checked, allowed_domains=domains)
    except (net.FetchError, net.UnsafeURLError) as exc:
        message = str(exc)
        blocked = _blocked_reason(message)
        status = _STATUS_BLOCKED if blocked else _STATUS_FAILED
        report.update({"status": status, "reason": message})
        await _close_run(
            run_id, status=status, summary={"reason": message}, report=report, failure_reason=message
        )
        return {**report, "ok": False, "kind": "blocked" if blocked else "failed", "error": message}

    analysis = analyze_page(fetched.text, fetched.url, listed_as="entry")
    hosts = _hosts_in(analysis, fetched.url)
    report.update(
        {
            "final_url": fetched.url,
            "http_status": fetched.status,
            "redirects": fetched.redirects,
            "bytes": fetched.bytes_len,
            "truncated": fetched.truncated,
            "host": host,
            "page_title": analysis["page_title"],
            "text_len": analysis["text_len"],
            "list_candidates": analysis["list_candidates"],
            "detail_candidates": analysis["detail_candidates"],
            "meta_candidates": analysis["meta_candidates"],
            "pagination": analysis["pagination"],
            "columns": analysis["columns"],
            "feeds": analysis["feeds"],
            "search_forms": analysis["search_forms"],
            "attachments_on_page": analysis["attachments"],
            "other_hosts": hosts,
            "status": "inspected",
        }
    )
    summary = {
        "page_title": analysis["page_title"],
        "list_candidates": len(analysis["list_candidates"]),
        "columns": len(analysis["columns"]),
        "feeds": len(analysis["feeds"]["feeds"]),
        "search_forms": len(analysis["search_forms"]),
    }
    await _close_run(run_id, status=_STATUS_PASSED, summary=summary, report=report)
    return {**report, "ok": True, "run_id": run_id, "summary": summary}


def _hosts_in(analysis: dict[str, Any], base_url: str) -> list[str]:
    """页面里出现过的**其他**主机（供给报告提示「范围外链接」用）。"""
    base_host = (urlsplit(base_url).hostname or "").lower()
    hosts: set[str] = set()
    for column in analysis.get("columns", []):
        host = (urlsplit(column["url"]).hostname or "").lower()
        if host and host != base_host:
            hosts.add(host)
    for url in analysis.get("attachments", []):
        host = (urlsplit(url).hostname or "").lower()
        if host and host != base_host:
            hosts.add(host)
    return sorted(hosts)


async def discover_content(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    keywords: list[str] | None = None,
    follow: int = 0,
    source_key: str | None = None,
) -> dict[str, Any]:
    """阶段 2 的内容发现：入口页 +（可选）若干栏目页 → 内容入口清单与列表候选。

    `follow` 是**再往下取几层栏目页**（默认 0 = 只看入口页）：深层栏目页才是真正列出
    条目列表的那一页。每层都对同一批候选做结构分析，合并去重。
    """
    run_id = await _open_run(
        "discover",
        source_key=source_key,
        source_id=None,
        payload={"url": url, "keywords": keywords or [], "follow": follow},
    )
    domains = [d for d in (allowed_domains or []) if d.strip()]
    pages: list[dict[str, Any]] = []
    errors: list[str] = []
    queue: list[str] = [url]
    visited: set[str] = set()
    depth = max(0, min(follow, 2))

    for level in range(depth + 1):
        next_queue: list[str] = []
        for page_url in queue:
            if page_url in visited:
                continue
            visited.add(page_url)
            if not domains:
                domains = [(urlsplit(page_url).hostname or "").lower()]
            try:
                fetched = await net.fetch_text(page_url, allowed_domains=domains or None)
            except (net.FetchError, net.UnsafeURLError) as exc:
                errors.append(f"{page_url}：{exc}")
                continue
            analysis = analyze_page(fetched.text, fetched.url, listed_as=f"level-{level}")
            analysis["url"] = fetched.url
            pages.append(analysis)
            if level < depth:
                for column in analysis["columns"]:
                    if column["url"] not in visited:
                        next_queue.append(column["url"])
                next_queue = next_queue[:6]  # 每层最多追 6 个栏目，防爆炸
        queue = next_queue
        if not queue:
            break

    entrypoints = _collect_entrypoints(pages, keywords or [])
    summary = {
        "pages_analyzed": len(pages),
        "entrypoints": len(entrypoints),
        "errors": len(errors),
    }
    report = {
        "url": url,
        "allowed_domains": domains,
        "pages": pages,
        "entrypoint_candidates": entrypoints,
        "errors": errors,
        "status": _STATUS_PASSED if entrypoints else _STATUS_FAILED,
    }
    await _close_run(
        run_id,
        status=report["status"],
        summary=summary,
        report=report,
        failure_reason=None if entrypoints else "未发现可用的内容入口（列表候选为空）",
    )
    return {
        **report,
        "ok": bool(entrypoints),
        "run_id": run_id,
        "summary": summary,
        "error": None if entrypoints else "未发现可用的内容入口（列表候选为空）",
    }


def _collect_entrypoints(pages: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    """把各页的列表候选整理成入口清单（选择器 + 建议的栏目名 + 翻页规则）。"""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    columns = [c for page in pages for c in page.get("columns", [])]
    for page in pages:
        for candidate in page.get("list_candidates", []):
            if not candidate.get("selector"):
                continue
            key = (page["url"], candidate["selector"])
            if key in seen:
                continue
            seen.add(key)
            name = _entrypoint_name(page, columns, keywords)
            out.append(
                {
                    "page_url": page["url"],
                    "name_suggestion": name,
                    "list_selector": candidate["selector"],
                    "item_count": candidate.get("item_count"),
                    "sample_title": candidate.get("sample_title"),
                    "sample_url": candidate.get("sample_url"),
                    "pagination": page.get("pagination") or {},
                }
            )
    out.sort(key=lambda row: -(row.get("item_count") or 0))
    return out[:10]


def _entrypoint_name(page: dict[str, Any], columns: list[dict[str, Any]], keywords: list[str]) -> str:
    """给入口起个可读名字：优先用指向该页的栏目链接文本，其次页面标题。"""
    for column in columns:
        if column["url"].rstrip("/") == page["url"].rstrip("/"):
            return column["text"][:40]
    title = (page.get("page_title") or "").strip()
    for keyword in keywords:
        if keyword and keyword in title:
            return title[:40]
    return title[:40] or "内容栏目"


# ---------- 阶段 3：样本与结构分析 ----------


async def analyze_structure(
    *,
    url: str | None = None,
    html: str | None = None,
    allowed_domains: list[str] | None = None,
    listed_as: str = "page",
    include_outline: bool = True,
) -> dict[str, Any]:
    """阶段 3：单页结构分析。给 `html` 直接分析（离线可用），给 `url` 则先取回。

    `include_outline` 控制是否附带压缩 DOM 提纲（模型据此判断字段取值位置）；
    列表页勘察不需要它，详情页补字段时需要。
    """
    if html is None:
        if not url:
            return {**_fail("需要提供 url 或 html 之一"), "ok": False, "kind": "failed"}
        try:
            fetched = await net.fetch_text(url, allowed_domains=allowed_domains or None)
        except (net.FetchError, net.UnsafeURLError) as exc:
            blocked = _blocked_reason(str(exc))
            return {**_fail(str(exc), kind="blocked" if blocked else "failed"), "ok": False}
        analysis = analyze_page(
            fetched.text, fetched.url, listed_as=listed_as, include_outline=include_outline
        )
        analysis["url"] = fetched.url
        analysis["http_status"] = fetched.status
        return {"ok": True, **analysis}

    analysis = analyze_page(
        html, url or "", listed_as=listed_as, include_outline=include_outline
    )
    analysis["url"] = url
    return {"ok": True, **analysis}


async def sample_content(
    url: str,
    *,
    count: int = 3,
    selectors: dict[str, Any] | None = None,
    allowed_domains: list[str] | None = None,
    list_selector: str | None = None,
    source_key: str | None = None,
) -> dict[str, Any]:
    """阶段 3：取代表性样本详情页并逐字段核对（发现规则 → 验证字段）。

    `url` 既可以是**列表页**（先按 `list_selector` 或自动候选取出条目，再逐篇取样），
    也可以是**详情页**（直接分析这一页，`count` 失效）。给 `selectors` 就用给定的解析
    规则，否则用自动候选里得分最高的那组（并在报告里标明「候选，待确认」）。
    """
    run_id = await _open_run(
        "sample",
        source_key=source_key,
        source_id=None,
        payload={"url": url, "count": count, "selectors": selectors or {}},
    )
    domains = [d for d in (allowed_domains or []) if d.strip()]
    if not domains:
        domains = [(urlsplit(url).hostname or "").lower()]
    try:
        fetched = await net.fetch_text(url, allowed_domains=domains)
    except (net.FetchError, net.UnsafeURLError) as exc:
        message = str(exc)
        blocked = _blocked_reason(message)
        return {**_fail(message, kind="blocked" if blocked else "failed"), "ok": False, "run_id": run_id}

    page = analyze_page(fetched.text, fetched.url, listed_as="sample-entry")
    rules = _resolve_rules(page, selectors, list_selector)

    links: list[dict[str, Any]] = []
    if rules["list_selector"]:
        fake_entry = _entry_from_rules(rules, fetched.url)
        links = parse_list_page(fetched.text, fake_entry, fetched.url, limit=max(1, count))
    if not links:
        links = [{"title": page["page_title"], "url": fetched.url, "date": None, "entrypoint": "直接给出"}]

    samples: list[dict[str, Any]] = []
    for link in links[: max(1, count)]:
        samples.append(await _sample_one(link, rules, domains))

    hits = {
        "body_ok": sum(1 for s in samples if s["body_ok"]),
        "date": sum(1 for s in samples if s["date"]),
        "agency": sum(1 for s in samples if s["agency"]),
        "doc_no": sum(1 for s in samples if s["doc_no"]),
        "fields": sum(1 for s in samples if s.get("fields")),
        "attachments": sum(len(s["attachments"]) for s in samples),
    }
    summary = {"samples": len(samples), **hits}
    report = {
        "url": url,
        "rules": rules,
        "rules_confirmed": bool(selectors),
        "samples": samples,
        "hits": hits,
        "status": _STATUS_PASSED if samples and hits["body_ok"] else _STATUS_FAILED,
    }
    await _close_run(
        run_id,
        status=report["status"],
        summary=summary,
        report=report,
        failure_reason=None if samples and hits["body_ok"] else "样本正文均未解析成功（规则待调整）",
    )
    return {**report, "ok": bool(samples and hits["body_ok"]), "run_id": run_id, "summary": summary}


def _resolve_rules(
    page: dict[str, Any], selectors: dict[str, Any] | None, list_selector: str | None
) -> dict[str, Any]:
    """确定本次取样用的解析规则：优先用户给的，其次自动候选里得分最高的。"""
    if selectors:
        return {
            "source": "explicit",
            "list_selector": list_selector,
            "container_selectors": selectors.get("container_selectors") or [],
            "title_selectors": selectors.get("title_selectors") or [],
            "date_selectors": selectors.get("date_selectors") or [],
            "agency_selectors": selectors.get("agency_selectors") or [],
            "doc_no_selectors": selectors.get("doc_no_selectors") or [],
            "fields": selectors.get("fields") or {},
        }
    meta = page.get("meta_candidates", {})
    details = page.get("detail_candidates", [])
    lists = page.get("list_candidates", [])
    return {
        "source": "auto-candidate",
        "list_selector": list_selector or (lists[0].get("selector") if lists else None),
        "container_selectors": [c["selector"] for c in details[:3] if c.get("selector")],
        "title_selectors": [c["selector"] for c in meta.get("title", [])[:2] if c.get("selector")],
        "date_selectors": [c["selector"] for c in meta.get("date", [])[:2] if c.get("selector")],
        "agency_selectors": [c["selector"] for c in meta.get("agency", [])[:2] if c.get("selector")],
        "doc_no_selectors": [c["selector"] for c in meta.get("doc_no", [])[:2] if c.get("selector")],
        # 语义化字段候选：与 source_import 用同一个转换器，预览报告与自动导入口径一致
        "fields": {
            name: [rule.model_dump(exclude_none=True) for rule in rules]
            for name, rules in rules_from_field_candidates(
                page.get("field_candidates") or []
            ).items()
        },
    }


def _entry_from_rules(rules: dict[str, Any], url: str):
    """用规则字典造一个临时 `EntrypointConfig`（只为了让 `parse_list_page` 能跑）。"""
    from app.sources.config import EntrypointConfig

    return EntrypointConfig(
        name="样本",
        url=url,
        list_selector=rules["list_selector"],
        link_selector="a",
        title_selector=None,
        date_selector=None,
    )


async def _sample_one(link: dict[str, Any], rules: dict[str, Any], domains: list[str]) -> dict[str, Any]:
    """取一篇样本详情页并按规则解析，逐字段核对（元信息取不到就是取不到）。"""
    from app.pipeline.fulltext_clean import body_metrics, strip_zero_width
    from app.sources.config import DetailConfig
    from app.sources.html_source import parse_detail_page

    detail = DetailConfig(
        container_selectors=rules["container_selectors"],
        title_selectors=rules["title_selectors"],
        date_selectors=rules["date_selectors"],
        agency_selectors=rules["agency_selectors"],
        doc_no_selectors=rules["doc_no_selectors"],
        # 显式给 `fields` 时手写规则优先；自动候选模式下同样带规则，
        # 于是样本报告能直接反映「标签→值」规则的命中情况，而不只是旧选择器
        fields=rules.get("fields") or {},
    )
    row: dict[str, Any] = {
        "url": link.get("url"),
        "list_title": link.get("title"),
        "list_date": link.get("date"),
        "date": None,
        "agency": None,
        "doc_no": None,
        "fields": {},
        "title": None,
        "body_chars": 0,
        "body_cjk": 0,
        "sent_enders": 0,
        "body_ok": False,
        "container_selector": None,
        "image_count": 0,
        "attachments": [],
        "warnings": [],
    }
    try:
        fetched = await net.fetch_text(str(link.get("url")), allowed_domains=domains)
    except (net.FetchError, net.UnsafeURLError) as exc:
        row["warnings"].append(f"取回失败：{exc}")
        return row

    parsed = parse_detail_page(strip_zero_width(fetched.text), detail, fetched.url)
    row["warnings"].extend(parsed.warnings)
    row["container_selector"] = parsed.container_selector
    row["image_count"] = parsed.image_count
    row["attachments"] = [a.as_dict() for a in parsed.attachments]
    row["title"] = parsed.title
    row["date"] = parsed.date_text
    row["agency"] = parsed.agency
    row["doc_no"] = parsed.doc_no
    row["fields"] = parsed.extra
    body = parsed.body or ""
    row["body_chars"] = len(body)
    cjk, enders = body_metrics(body)
    row["body_cjk"] = cjk
    row["sent_enders"] = enders
    row["body_ok"] = cjk >= 100 and enders >= _MIN_SENT_ENDERS
    if not body:
        row["warnings"].append("正文为空（容器选择器未命中，启发式兜底也未找到达标正文）")
    return row


# ---------- 阶段 4：生成接入配置 ----------


def generate_config(payload: dict[str, Any]) -> dict[str, Any]:
    """阶段 4：把勘察结果组装成 `SourceConfig` 草案，并做**可执行性**校验。

    `payload` 就是配置本体（字段见 `sources/config.py` 的 `SourceConfig`）——
    工具层负责从勘察报告里把字段填进来，这里只管校验与补默认值。
    返回 `{"ok", "config", "problems"}`：`problems` 非空时 `ok=False`，别当草案可用。
    """
    try:
        config = SourceConfig.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - Pydantic 的报错信息本身就是给模型看的
        return {"ok": False, "problems": [f"配置结构不合法：{exc}"], "config": None}

    problems = validate_source_config(config)
    return {
        "ok": not problems,
        "problems": problems,
        "config": config.model_dump(),
        "fingerprint": config.config_fingerprint(),
        "summary": config.summary(),
    }


# ---------- 阶段 5：试运行与验收 ----------


async def dry_run_ingestion(
    payload: dict[str, Any],
    *,
    limit: int = 3,
    pages: int = 1,
    keywords: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    archive_attachments: bool = True,
    source_key: str | None = None,
) -> dict[str, Any]:
    """阶段 5：小批量试运行 + 验收报告。

    **走真实检索链路**：把配置临时装进进程内注册表后调用
    `pipeline/crawl_graph._search_policies`，也就是正式抓取用的检索编排；随后逐篇真正
    取详情页测正文与元信息。不建任务、不写 `policy_document`（注册后由正式抓取负责）。

    产出四类结论：**通过项 / 失败项 / 未测试项 / 覆盖范围限制**，并给出最终 status：
    `passed`（可进入注册）/ `failed`（配置或站点结构不符，需调整）/ `blocked`（当前
    环境或站点不允许，需人工/换途径）。
    """
    built = generate_config(payload)
    if not built["ok"]:
        return {
            "ok": False,
            "kind": "failed",
            "status": _STATUS_FAILED,
            "error": "配置未通过可执行性校验：" + "；".join(built["problems"]),
            "problems": built["problems"],
        }
    config = SourceConfig.model_validate(built["config"])
    run_id = await _open_run(
        "dry_run", source_key=source_key or config.key, source_id=None,
        payload={"key": config.key, "limit": limit, "pages": pages, "keywords": keywords or []},
    )

    # 临时装进注册表：下面的检索链路按 label 解析来源，必须先能解析到
    try:
        register_configured(config, replace=True)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "kind": "failed",
            "status": _STATUS_FAILED,
            "error": f"配置无法装载：{exc}",
            "run_id": run_id,
        }

    report: dict[str, Any] = {
        "key": config.key,
        "label": config.label,
        "fingerprint": config.config_fingerprint(),
        "limit": limit,
        "pages": pages,
        "keywords": keywords or [],
    }
    criteria: list[dict[str, Any]] = []
    untested: list[str] = []
    limits: list[str] = []

    from app.pipeline.crawl_graph import PipelineError, _search_policies
    from app.db import repositories as repo

    try:
        library: set[str] = await repo.fetch_library_urls()
    except Exception as exc:  # noqa: BLE001 - 数据库不可用时「已在库」这项测不了
        library = set()
        untested.append(f"成果库去重比对（数据库不可用：{exc}）")

    overrides = {
        "source": config.label,
        "pipeline_per_keyword_limit": max(limit, 5),
        "pipeline_mcp_timeout": int(get_settings().source_fetch_timeout),
    }
    judge_stats: dict[str, int] = {}
    try:
        items = await _search_policies(
            list(keywords or []),
            date_from,
            date_to,
            None,
            None,
            overrides=overrides,
            exclude_urls=set(),
            library_urls=library,
            target_new=None,
            judge_stats=judge_stats,
        )
        search_error = None
    except PipelineError as exc:
        items = []
        search_error = str(exc)
    except Exception as exc:  # noqa: BLE001
        items = []
        search_error = f"检索链路异常：{exc}"

    if search_error:
        blocked = _blocked_reason(search_error)
        report.update(
            {
                "status": _STATUS_BLOCKED if blocked else _STATUS_FAILED,
                "search_error": search_error,
                "criteria": criteria,
            }
        )
        _cleanup_draft(config.key, passed=False)
        await _close_run(
            run_id,
            status=report["status"],
            summary={"search_error": search_error},
            report=report,
            failure_reason=search_error,
        )
        return {
            **report,
            "ok": False,
            "kind": "blocked" if blocked else "failed",
            "run_id": run_id,
            "error": search_error,
        }

    # 检索型入口的探测关键词：优先用本次试运行给的关键词（就是要抓的那些词），
    # 没给就用一个通用词——**只为验证「检索页取回来能解析出条目」**，与内容相关性无关。
    search_entries = [ep for ep in config.entrypoints if ep.is_search]
    given_keyword = next((k.strip() for k in (keywords or []) if (k or "").strip()), None)
    probe_keyword = given_keyword or (SEARCH_PROBE_KEYWORD if search_entries else None)
    report["search_entrypoints"] = [ep.name for ep in search_entries]
    report["probe_keyword"] = probe_keyword

    # ---- 判据 1：列表发现（每入口第 1 页都能取到条目） ----
    per_entry, entry_errors = await _probe_entries(
        config, date_from, date_to, keyword=probe_keyword
    )

    # 全部入口都失败，且失败原因是「访问控制 / 安全策略」→ 报受阻而不是失败：
    # 这类问题不是配置写错，而是站点不允许，按接入规范不绕过（第七节硬要求）。
    if per_entry and entry_errors and all(row["items"] == 0 for row in per_entry):
        blocked = next((r for r in (_blocked_reason(e) for e in entry_errors) if r), None)
        if blocked:
            report.update(
                {
                    "status": _STATUS_BLOCKED,
                    "reason": blocked,
                    "criteria": criteria,
                    "entrypoints": per_entry,
                    "search_error": None,
                }
            )
            _cleanup_draft(config.key, passed=False)
            await _close_run(
                run_id,
                status=_STATUS_BLOCKED,
                summary={"reason": blocked},
                report=report,
                failure_reason=blocked,
            )
            return {**report, "ok": False, "kind": "blocked", "run_id": run_id, "error": blocked}

    # 栏目入口与检索型入口分开算：检索入口的条数取决于探测关键词在站点索引里的命中数，
    # 拿它一起去 min 会让一个 0 命中的检索入口把判据 1「栏目列表都取到条目」判成失败。
    list_rows = [row for row in per_entry if row.get("kind") != "search"]
    search_rows = [row for row in per_entry if row.get("kind") == "search"]
    min_items = min((row["items"] for row in list_rows), default=0)
    if list_rows:
        criteria.append(
            _criterion(
                "列表发现",
                f"每个栏目入口第 1 页 ≥ 1 条（共 {len(list_rows)} 个栏目入口）",
                f"最少 {min_items} 条",
                passed=min_items >= 1,
                hard=True,
                note="；".join(entry_errors) or None,
            )
        )
    else:
        untested.append("列表发现（这份配置只有检索型入口，没有栏目列表页可探）")

    # 检索型入口单独判：它取回来的条数取决于**探测关键词在站点索引里有多少命中**，
    # 所以「有条目」才算这个入口是活的。用本次试运行自己的关键词探测时是硬判据
    # （用户就是要用这些词抓）；用通用探测词时是软判据——那个词在站点里可能本来就 0 命中。
    if search_rows:
        worst = min(row["items"] for row in search_rows)
        criteria.append(
            _criterion(
                "站内检索入口",
                f"检索型入口用关键词探测时第 1 页 ≥ 1 条（共 {len(search_rows)} 个）",
                f"最少 {worst} 条（探测关键词「{probe_keyword}」）",
                passed=worst >= 1,
                hard=bool(given_keyword),
                note=(
                    "；".join(
                        f"{row['name']}：{row['error']}" for row in search_rows if row.get("error")
                    )
                    or "关键词确实送进了站点检索接口（URL 见 entrypoints[].url）"
                ),
            )
        )
        if not given_keyword:
            untested.append(
                f"站内检索入口只用通用探测词「{probe_keyword}」验证过，"
                "未用你实际要抓的关键词跑一遍（正式抓取时才会用）"
            )

    # ---- 判据 2：检索链路产出（扇出 + 条目标过滤 + 去重后） ----
    criteria.append(
        _criterion(
            "检索链路产出",
            "扇出检索后至少剩 1 条可用条目",
            f"{len(items)} 条",
            passed=bool(items),
            hard=True,
            note=(
                f"判别剔除 {judge_stats.get('body_rejected', 0)} 条（正文预检）"
                f"／{sum(1 for i in items if i.get('judge_tagged'))} 条（已标注）"
                if judge_stats else None
            ),
        )
    )

    # ---- 判据 3：正文完整性（逐篇真实取详情页） ----
    index = {str(item.get("url") or "").strip(): item for item in items}
    url_dupes = len(items) - len(index)
    sample_urls = list(index)[: max(1, limit)]
    details = [
        await _probe_detail(index[url], config) for url in sample_urls
    ]
    body_ok = sum(1 for row in details if row["body_ok"])
    body_ratio = body_ok / max(1, len(details))
    criteria.append(
        _criterion(
            "正文完整性",
            f"抽样 {len(details)} 篇中正文达标（CJK ≥ {config.detail.min_body_chars} 且句末 ≥ {_MIN_SENT_ENDERS}）的比例 ≥ {_BODY_PASS_RATIO:.0%}",
            f"{body_ok}/{len(details)} = {body_ratio:.0%}",
            passed=bool(details) and body_ratio >= _BODY_PASS_RATIO,
            hard=True,
        )
    )
    if len(details) < _MIN_SAMPLES_FOR_BODY:
        untested.append(
            f"正文完整性只有 {len(details)} 篇样本（< {_MIN_SAMPLES_FOR_BODY} 篇），结论为初步"
        )

    # ---- 判据 4：元信息命中率（软判据：取不到就如实报低，不算失败） ----
    date_hit = sum(1 for row in details if row["date"]) / max(1, len(details))
    agency_hit = sum(1 for row in details if row["agency"]) / max(1, len(details))
    # 抽样日期**值**恒定 = 假命中：日期候选多半取到了页脚/模板里那个固定日期，命中率再高也没用
    # （2026-10-15 用户报的「其他来源的日期一直是 3 月 20 日」就是这样一路绿灯进来的）。
    sampled_dates = sorted({str(row["date"]) for row in details if row["date"]})
    same_date_note = (
        f"⚠ 抽样 {len(details)} 篇的发布日期**全部相同**（{sampled_dates[0]}）：疑似取到了"
        "页脚/模板元素，命中率高不代表取对了——请逐篇核对 detail.date_selectors"
        if len(details) >= 2 and len(sampled_dates) == 1
        else None
    )
    criteria.append(
        _criterion(
            "详情页日期命中率",
            f"≥ {_DATE_HIT_RATIO:.0%}",
            f"{date_hit:.0%}",
            passed=date_hit >= _DATE_HIT_RATIO,
            hard=False,
            note=(
                same_date_note
                or "缺日期的条目在时间窗口生效时会按配置决定去留（drop_undated_when_windowed）"
            ),
        )
    )
    if same_date_note:
        limits.append(same_date_note)
    criteria.append(
        _criterion(
            "详情页机构命中率",
            f"≥ {_AGENCY_HIT_RATIO:.0%}",
            f"{agency_hit:.0%}",
            passed=agency_hit >= _AGENCY_HIT_RATIO,
            hard=False,
            note="机构取不到通常是该站详情页没有固定字段，需要补 agency_selectors",
        )
    )

    # ---- 判据 5：翻页（配置声明了翻页就必须真的能翻） ----
    paginated = [ep for ep in config.entrypoints if ep.pagination.kind != "none"]
    if not paginated:
        untested.append("翻页（配置为单页模式，未声明翻页规则）")
    else:
        page2 = await _probe_page2(config, date_from, date_to, keyword=probe_keyword)
        criteria.append(
            _criterion(
                "翻页可用性",
                "声明翻页的入口第 2 页能取到且不是第 1 页的重复",
                page2["detail"],
                passed=page2["passed"],
                hard=True,
                note=page2["note"],
            )
        )

    # ---- 判据 5b：单页入场的召回边界（软性，只如实说清楚，不做拦截） ----
    # 「单页」过去是静默默认值，这条路上没有任何环节会说出来，于是「抓了 N 篇就不动了」
    # 无法与「站点就这么多」区分。这里逐个点名，并带上组装阶段写好的入口备注（如有）。
    single_page = [
        ep for ep in config.entrypoints if ep.pagination.kind == "none" and not ep.is_search
    ]
    if single_page:
        limits.append(
            f"有 {len(single_page)} 个入口声明为单页（{'、'.join(ep.name for ep in single_page)}）："
            "只取第 1 页。页内命名区域的预览块默认如此——若该栏目其实有更多页，"
            "召回会被截断（组装阶段已用页面证据核对过，能补的翻页规则已补上）"
        )
    for ep in config.entrypoints:
        note = (ep.notes or "").strip()
        if note:
            limits.append(f"入口「{ep.name}」备注：{note}")

    # ---- 判据 6：去重 ----
    criteria.append(
        _criterion(
            "URL 去重",
            "合并结果内无重复 URL",
            f"{len(items)} 条 / 重复 {url_dupes}",
            passed=url_dupes == 0,
            hard=True,
        )
    )

    # ---- 判据 7：来源标识与原始证据（结构性检查 + 附件探测） ----
    attachments = [
        (row["url"], attachment)
        for row in details
        for attachment in row["attachments"]
    ]
    criteria.append(
        _criterion(
            "来源可追溯",
            "每条都带原始 URL 与来源标识",
            f"{len(details)} 篇均带 source_url",
            passed=all(row["url"] for row in details),
            hard=True,
            note="落库时 source_name = 来源展示名，fetched_by = html_list:<key>",
        )
    )
    # 探测上限取 `min(常量, SOURCE_MAX_ARTIFACTS_PER_RUN)`：试运行只抽查可达性，不是全量归档。
    # 这个值同时决定**报告里列出哪几个附件**——报告必须与真实探测范围一致（见下方 attachments_probed）。
    probe_cap = max(1, min(_MAX_ATTACHMENT_PROBE, get_settings().source_max_artifacts_per_run))
    probed_attachments = attachments[:probe_cap]
    if not attachments:
        untested.append("附件可达性（抽样页面未发现附件）")
        limits.append("本次抽样未发现附件，附件链路未验证")
    else:
        probes = await _probe_attachments(config, probed_attachments, run_id)
        ok_count = sum(1 for row in probes if row["ok"])
        ratio = ok_count / max(1, len(probes))
        criteria.append(
            _criterion(
                "附件可达性",
                f"抽查 {len(probes)} 个附件可达率 ≥ {_ATTACHMENT_PASS_RATIO:.0%}",
                f"{ok_count}/{len(probes)} = {ratio:.0%}",
                passed=ratio >= _ATTACHMENT_PASS_RATIO,
                hard=False,
                note="可达性只验证「能取回」，附件正文（PDF/OFD 文字）本轮不解析",
            )
        )
        limits.append("附件只做了可达性与归档验证，未做正文抽取（PDF/OFD 文字提取为已知缺口）")
        if len(attachments) > len(probes):
            limits.append(
                f"抽样页共发现 {len(attachments)} 个附件，只抽查了 {len(probes)} 个（其余未验证）"
            )

    # ---- 覆盖范围限制（必须写清楚这次**没**验证什么） ----
    limits.extend(
        [
            f"只试抓了 {len(details)} 篇 / 每个入口前 {pages} 页，未覆盖全站历史存量",
            "未验证登录/验证码/付费墙后的内容（按接入规范不绕过访问控制）",
            "未验证 JS 动态渲染出来的内容（本引擎只解析服务端返回的静态 HTML）",
        ]
    )
    if any(row["image_count"] > 0 and row["body_cjk"] < 100 for row in details):
        limits.append("抽样中存在图片版页面（正文 CJK 极少）——需人工确认该栏目是否以附件为主")

    hard_failed = [c for c in criteria if c["hard"] and not c["passed"]]
    status = _STATUS_FAILED if hard_failed else _STATUS_PASSED
    report.update(
        {
            "status": status,
            "criteria": criteria,
            "entrypoints": per_entry,
            "items_found": len(items),
            "details": details,
            "judge_stats": judge_stats,
            "attachments_probed": probed_attachments,
            "untested": untested,
            "coverage_limits": limits,
            "search_error": None,
        }
    )
    _cleanup_draft(config.key, passed=status == _STATUS_PASSED)
    await _close_run(
        run_id,
        status=status,
        summary={
            "passed": [c["name"] for c in criteria if c["passed"]],
            "failed": [c["name"] for c in hard_failed],
            "items": len(items),
        },
        report=report,
        failure_reason=None if status == _STATUS_PASSED else "；".join(c["name"] for c in hard_failed),
    )
    return {
        **report,
        "ok": status == _STATUS_PASSED,
        "run_id": run_id,
        "error": None if status == _STATUS_PASSED else "验收未通过：" + "；".join(c["name"] for c in hard_failed),
    }


def _criterion(
    name: str, threshold: str, actual: str, *, passed: bool, hard: bool, note: str | None = None
) -> dict[str, Any]:
    """一条可执行验收标准：标准 / 实测 / 是否通过 / 硬软性。"""
    row: dict[str, Any] = {
        "name": name,
        "threshold": threshold,
        "actual": actual,
        "passed": passed,
        "hard": hard,
    }
    if note:
        row["note"] = note
    return row


def _cleanup_draft(key: str, *, passed: bool) -> None:
    """试运行后收拾临时装载的草案：**没通过就摘掉**，避免失败的配置留在注册表里被误用。"""
    if passed:
        return
    source = get(key)
    if source is not None:
        unregister(key)


async def _probe_entries(
    config: SourceConfig,
    date_from: date | None,
    date_to: date | None,
    *,
    keyword: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """逐入口取第 1 页，核对列表解析是否真的出条目（并记录每页取数错误）。

    入口分两类，**探测方式不同**（口径见 `build_entry_url`）：

    - 栏目型入口：取入口地址本身（`keyword=None`，即「栏目怎么翻」这一路）；
    - 检索型入口：把 `keyword` 拼进站内检索地址——检索页没有关键词参数时返回的通常是
      空结果页，拿它去探测只会得出「这个入口坏了」的错误结论。

    `keyword` 为 None 时检索型入口**如实记为未探测**，不伪装成 0 条。
    """
    source = HtmlListSource(config)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for entry in config.entrypoints:
        kind = "search" if entry.is_search else "list"
        url = build_entry_url(
            entry, keyword=keyword, date_from=date_from, date_to=date_to, page=1
        )
        if url is None:
            if entry.is_search:
                rows.append(
                    {
                        "name": entry.name,
                        "kind": kind,
                        "url": entry.url,
                        "items": 0,
                        "probe_keyword": None,
                        "error": "本次探测没有可用关键词，检索型入口未探测",
                    }
                )
                continue
            rows.append(
                {"name": entry.name, "kind": kind, "url": entry.url, "items": 0,
                 "error": "第 1 页地址为空"}
            )
            continue
        try:
            fetched = await net.fetch_text(url, allowed_domains=config.scope.allowed_domains)
        except (net.FetchError, net.UnsafeURLError) as exc:
            errors.append(f"{entry.name}：{exc}")
            rows.append({"name": entry.name, "kind": kind, "url": url, "items": 0, "error": str(exc)})
            continue
        items = parse_list_page(
            fetched.text, entry, fetched.url, limit=config.scope.max_items_per_entry
        )
        # 检索型入口按探测关键词复核（站内检索同样会分词命中，这里量的是「取回来还能剩几条」）；
        # 栏目型入口不复核关键词——那一路量的是「这个栏目本身有多少条」。
        kept = [
            item
            for item in items
            if source.validate_item(
                item,
                keyword=keyword if entry.is_search else None,
                date_from=date_from,
                date_to=date_to,
                facet_active=False,
            )
        ]
        rows.append(
            {
                "name": entry.name,
                "kind": kind,
                "url": url,
                "items": len(items),
                "kept_after_filter": len(kept),
                "probe_keyword": keyword if entry.is_search else None,
                "sample_titles": [item["title"][:40] for item in items[:3]],
            }
        )
    return rows, errors


async def _probe_page2(
    config: SourceConfig,
    date_from: date | None,
    date_to: date | None,
    *,
    keyword: str | None = None,
) -> dict[str, Any]:
    """逐**每个**声明了翻页的入口取第 2 页，核对「确实翻得动」。

    早期实现只验第一个声明翻页的入口就返回——多入口配置里第二、三个入口的翻页规则坏了
    不会被发现（2026-10-16 修）。全部通过才算通过；每条结论都报出来，坏的是哪几个一眼可见。
    """
    details: list[str] = []
    failures: list[str] = []
    for entry in config.entrypoints:
        if entry.pagination.kind == "none":
            continue
        if entry.is_search and not (keyword or "").strip():
            continue  # 检索型入口没有关键词就没有可翻的第 2 页（见 build_search_url）
        result = await _probe_one_page2(config, entry, date_from, date_to, keyword=keyword)
        details.append(f"{entry.name}：{result['detail']}")
        if not result["passed"]:
            failures.append(f"{entry.name}：{result['note'] or result['detail']}")
    if not details:
        return {"passed": False, "detail": "无参与探测的入口", "note": None}
    return {
        "passed": not failures,
        "detail": "；".join(details),
        "note": "；".join(failures) or None,
    }


async def _probe_one_page2(
    config: SourceConfig,
    entry: EntrypointConfig,
    date_from: date | None,
    date_to: date | None,
    *,
    keyword: str | None = None,
) -> dict[str, Any]:
    """单个入口的第 2 页核对（`_probe_page2` 的逐入口实现）。"""
    url = build_entry_url(entry, keyword=keyword, date_from=date_from, date_to=date_to, page=2)
    if url is None:
        return {"passed": False, "detail": "第 2 页地址为空（max_pages=1？）", "note": None}
    try:
        fetched = await net.fetch_text(url, allowed_domains=config.scope.allowed_domains)
    except (net.FetchError, net.UnsafeURLError) as exc:
        return {"passed": False, "detail": "第 2 页取回失败", "note": str(exc)}
    items2 = parse_list_page(fetched.text, entry, fetched.url)
    if not items2:
        return {
            "passed": False,
            "detail": "第 2 页无条目",
            "note": f"翻页规则 kind={entry.pagination.kind} 可能与该站不符",
        }
    page1 = build_entry_url(entry, keyword=keyword, date_from=date_from, date_to=date_to, page=1)
    try:
        first = await net.fetch_text(str(page1), allowed_domains=config.scope.allowed_domains)
        items1 = parse_list_page(first.text, entry, first.url)
    except (net.FetchError, net.UnsafeURLError):
        items1 = []
    urls1 = {item["url"] for item in items1}
    fresh = [item for item in items2 if item["url"] not in urls1]
    return {
        "passed": bool(fresh),
        "detail": f"第 2 页 {len(items2)} 条，其中新增 {len(fresh)} 条",
        "note": None if fresh else "第 2 页与第 1 页完全重复——参数可能被站点忽略",
    }


async def _probe_detail(item: dict[str, Any], config: SourceConfig) -> dict[str, Any]:
    """取一篇详情页，按配置解析并测正文指标（用真实 HTML，不猜）。"""
    from app.pipeline.fulltext_clean import body_metrics, strip_zero_width
    from app.sources.html_source import parse_detail_page

    url = str(item.get("url") or "")
    row: dict[str, Any] = {
        "url": url,
        "list_title": item.get("title"),
        "title": None,
        "date": None,
        "agency": None,
        "doc_no": None,
        "fields": {},
        "body_chars": 0,
        "body_cjk": 0,
        "sent_enders": 0,
        "body_ok": False,
        "container_selector": None,
        "image_count": 0,
        "attachments": [],
        "judge_tagged": item.get("judge_tagged"),
        "judge_reason": item.get("judge_reason"),
        "in_library": None,
        "warnings": [],
        "error": None,
    }
    try:
        fetched = await net.fetch_text(url, allowed_domains=config.scope.allowed_domains)
    except (net.FetchError, net.UnsafeURLError) as exc:
        row["error"] = f"取回失败：{exc}"
        row["warnings"].append(row["error"])
        return row

    parsed = parse_detail_page(strip_zero_width(fetched.text), config.detail, fetched.url)
    row["warnings"].extend(parsed.warnings)
    row["container_selector"] = parsed.container_selector
    row["image_count"] = parsed.image_count
    row["attachments"] = [a.as_dict() for a in parsed.attachments]
    row["title"] = parsed.title
    row["date"] = parsed.date_text
    row["agency"] = parsed.agency
    row["doc_no"] = parsed.doc_no
    row["fields"] = parsed.extra
    body = parsed.body or ""
    row["body_chars"] = len(body)
    cjk, enders = body_metrics(body)
    row["body_cjk"] = cjk
    row["sent_enders"] = enders
    row["body_ok"] = cjk >= config.detail.min_body_chars and enders >= _MIN_SENT_ENDERS
    return row


async def _probe_attachments(
    config: SourceConfig, attachments: list[tuple[str, dict[str, Any]]], run_id: int | None
) -> list[dict[str, Any]]:
    """抽查附件可达性：真取回字节、算 sha256、落盘并登记原始证据。

    这是「原始文件另存」这条要求的落点——登记的 `source_artifact` 里带 URL、哈希、
    落盘路径，与来源标识、抓取时间一起构成可追溯的原始证据链。
    """
    from app.db import repositories as repo

    settings = get_settings()
    out: list[dict[str, Any]] = []
    for page_url, attachment in attachments:
        url = str(attachment.get("url") or "")
        row: dict[str, Any] = {"url": url, "name": attachment.get("name"), "ok": False}
        try:
            result = await net.fetch_bytes(
                url,
                allowed_domains=config.scope.allowed_domains,
                max_bytes=settings.source_fetch_max_bytes,
            )
        except (net.FetchError, net.UnsafeURLError) as exc:
            row["error"] = str(exc)
            await _record_artifact(
                run_id, config, attachment, url, status="failed", error=str(exc)
            )
            out.append(row)
            continue

        digest = hashlib.sha256(result.raw).hexdigest()
        saved = _save_bytes(run_id, str(attachment.get("name") or ""), result.raw) if result.raw else None
        row.update(
            {
                "ok": True,
                "bytes": result.bytes_len,
                "truncated": result.truncated,
                "sha256": digest[:16],
                "saved_path": saved,
            }
        )
        await _record_artifact(
            run_id,
            config,
            attachment,
            url,
            status="truncated" if result.truncated else "ok",
            final_url=result.url,
            content_type=result.content_type,
            bytes_len=result.bytes_len,
            sha256=digest,
            saved_path=saved,
            page_url=page_url,
        )
        out.append(row)
    return out


async def _record_artifact(
    run_id: int | None,
    config: SourceConfig,
    attachment: dict[str, Any],
    url: str,
    *,
    status: str,
    page_url: str | None = None,
    final_url: str | None = None,
    content_type: str | None = None,
    bytes_len: int | None = None,
    sha256: str | None = None,
    saved_path: str | None = None,
    error: str | None = None,
) -> None:
    """登记一条原始证据（登记失败只告警——证据登记不该阻断接入流程）。"""
    from app.db import repositories as repo

    try:
        await repo.insert_source_artifact(
            kind="attachment",
            url=url,
            run_id=run_id,
            source_key=config.key,
            final_url=final_url,
            filename=str(attachment.get("name") or "")[:200] or None,
            ext=str(attachment.get("ext") or "") or None,
            content_type=content_type,
            bytes_len=bytes_len,
            sha256=sha256,
            saved_path=saved_path,
            status=status,
            error=error or (page_url and f"发现页：{page_url}") or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("原始证据登记失败：%s", exc)


# ---------- 阶段 6：注册 ----------


async def register_source(
    payload: dict[str, Any],
    *,
    approved: bool = False,
    force: bool = False,
    change_note: str | None = None,
    changed_by: str = "agent",
) -> dict[str, Any]:
    """阶段 6：注册来源（校验 → 落库 → 装进进程内注册表）。

    三道闸：
    1. 配置必须通过 `validate_source_config`（可执行性）；
    2. **默认注册为 `draft`（草案）**，只有 `approved=True` 才直接置 `active`；
    3. `approved=True` 时要求存在**指纹一致**的通过试运行；否则除非 `force=True`
       （人工明确拍板，会写进变更说明）一律拒绝——不把未验证的配置当成已启用。
    """
    from app.db import repositories as repo

    built = generate_config(payload)
    if not built["ok"]:
        return {
            "ok": False,
            "error": "配置未通过可执行性校验：" + "；".join(built["problems"]),
            "problems": built["problems"],
        }
    config = SourceConfig.model_validate(built["config"])
    fingerprint = config.config_fingerprint()

    verification: dict[str, Any] = {"fingerprint": fingerprint, "verified": False}
    if approved or force:
        verification = await _latest_passing_run(config)
        if not verification["verified"] and not force:
            return {
                "ok": False,
                "error": _enable_refusal(verification),
                "verification": verification,
            }
        if not verification["verified"] and force:
            change_note = (change_note or "") + "（未经验收强制启用，人工拍板）"

    status = "active" if approved else "draft"
    try:
        saved = await repo.save_content_source(
            config=config.model_dump(),
            label=config.label,
            level=config.level,
            organization=config.organization,
            source_type=config.source_type,
            engine=config.engine,
            status=status,
            fingerprint=fingerprint,
            entrypoint_count=len(config.entrypoints),
            notes=config.notes,
            change_note=change_note or ("启用并注册" if approved else "注册为草案"),
            changed_by=changed_by,
        )
    except Exception as exc:  # noqa: BLE001 - 唯一约束冲突等要说人话
        return {"ok": False, "error": f"落库失败：{exc}"}

    row = saved["row"]
    try:
        register_configured(config, replace=True)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"已落库但装载失败（重启服务会重新装载）：{exc}",
            "source_id": row["id"],
        }

    return {
        "ok": True,
        "source_id": row["id"],
        "key": config.key,
        "label": config.label,
        "status": row["status"],
        "version": row["version"],
        "created": saved["created"],
        "version_bumped": saved["version_bumped"],
        "fingerprint": fingerprint,
        "verification": verification,
        "next": (
            f"已启用，可在抓取配置里选择来源「{config.label}」发起正式抓取"
            if row["status"] == "active"
            else "已注册为草案；试运行通过后调用 manage_source(action='enable') 启用"
        ),
    }


def _enable_refusal(verification: dict[str, Any]) -> str:
    """把「拒绝启用」的原因写成**一句人话**——三种「没通过」分开说，见 `_latest_passing_run`。

    口径只许住在这里：`register_source` 与 `manage_source(action="enable")` 是同一道闸的
    两个入口，各写一份文案就会漂成两种说法（同一个来源，页面说一套、Agent 说另一套）。
    """
    if verification.get("db_error"):
        # 设备坏了 ≠ 配置没验收过：把库连不上写成「没有通过试运行」，会让人去重跑一次
        # 根本不会改变结论的试运行。
        reason = f"拒绝启用：无法核对验收凭据（{verification['db_error']}）。"
    else:
        reason = "拒绝启用：没有指纹一致的通过试运行。" + str(verification.get("hint") or "")
    return reason + "请先跑 dry_run_ingestion；如确需人工拍板放行，用 force=true（会记录变更说明）。"


async def _latest_passing_run(config: SourceConfig) -> dict[str, Any]:
    """找指纹一致的通过试运行记录（注册启用前的验收凭据）。

    **按指纹在全表里找，不设条数窗口**：凭据是既成事实（那份配置真的被跑过一次并通过
    了），同一来源之后又勘察过多少回、失败过多少次，都不该让它失效。2026-10-15 用户报的
    「手动停用的项目无法再次启用」就是窗口造出来的——列表页按指纹全表找、徽标显示
    「已验收」，闸门却只看「该来源最近 20 条记录」，凭据被挤出窗口后同一个来源两边各说
    各话（按钮说能启用，点了被拒）。判定口径住在 `repo._PASSING_DRY_RUN_SQL` 一处，
    这里（和列表页那次批量查询）都不许再抄一遍。
    """
    from app.db import repositories as repo

    fingerprint = config.config_fingerprint()
    try:
        row = await repo.fetch_latest_passing_run(fingerprint=fingerprint)
        if row is not None:
            return {
                "fingerprint": fingerprint,
                "verified": True,
                "run_id": row["id"],
                "verified_at": str(row.get("finished_at") or row.get("started_at")),
            }
        # 没找到：再说清是**哪一种**「没有」。改过配置（要重跑验收）与压根没跑过（要先跑），
        # 用户要做的事不一样，不能都只丢一句「没有指纹一致的」让人去猜。
        older = await repo.fetch_latest_passing_run(source_key=config.key)
    except Exception as exc:  # noqa: BLE001
        # 查不动（库不可用/表没建）≠ 没验收过：`db_error` 让 `_enable_refusal` 把两者分开说
        return {
            "fingerprint": fingerprint,
            "verified": False,
            "db_error": str(exc),
            "error": f"无法核对验收记录：{exc}",
        }
    old_fp = ((older or {}).get("report") or {}).get("fingerprint")
    hint = (
        f"该来源最近一次通过的试运行用的是指纹 {old_fp}（当前配置 {fingerprint}）——"
        "说明配置在验收之后改过。"
        if old_fp and old_fp != fingerprint
        else None
    )
    return {
        "fingerprint": fingerprint,
        "verified": False,
        "hint": hint,
        "error": "没有指纹一致的通过试运行记录" + (f"：{hint}" if hint else ""),
    }


async def verify_source_config(config: SourceConfig) -> dict[str, Any]:
    """公开的验收入口：这份配置有没有**指纹一致的通过试运行**。

    `register_source(approved=True)` 与 `manage_source(action="enable")` 用的就是它
    （内部同一个 `_latest_passing_run`）。来源列表/详情页要单独展示「验没验收过」时
    调这个——**别在别处再抄一遍判定规则**，抄一遍就会漂，闸门就漏了。
    """
    return await _latest_passing_run(config)


# ---------- 阶段 7：来源管理与诊断 ----------


async def manage_source(
    action: str,
    *,
    key: str | None = None,
    source_id: int | None = None,
    keywords: list[str] | None = None,
    max_policies: int = 3,
) -> dict[str, Any]:
    """阶段 7：来源管理。

    `action`：
    - `list`     列出来源（库里的 + 进程内已装载的）+ 状态
    - `detail`   单个来源的配置摘要 + 变更历史 + 最近接入记录
    - `enable` / `disable` 启停（启用同样要求验收通过，除非该来源已在跑）
    - `run_trial` 发起一次**真实的小批量抓取任务**（走完整流水线，可在抓取页看到）
    - `delete`   删除来源配置
    - `health`   来源健康检查（列出配置里的入口地址，说明检查方式）
    """
    from app.db import repositories as repo

    if action == "list":
        try:
            rows = await repo.list_content_sources()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"读取来源列表失败：{exc}"}
        return {
            "ok": True,
            "count": len(rows),
            "sources": [
                {
                    "id": row["id"],
                    "key": row["key"],
                    "label": row["label"],
                    "level": row["level"],
                    "status": row["status"],
                    "version": row["version"],
                    "entrypoints": row["entrypoint_count"],
                    "loaded": get(row["key"]) is not None,
                }
                for row in rows
            ],
        }

    row = await _resolve_source_row(key=key, source_id=source_id)
    if row is None:
        return {"ok": False, "error": "未找到该来源（用 key 或 source_id 指定）"}

    if action == "detail":
        revisions = await repo.list_source_revisions(row["id"])
        runs = await repo.list_onboarding_runs(source_key=row["key"], limit=5)
        return {
            "ok": True,
            "source": {
                "id": row["id"],
                "key": row["key"],
                "label": row["label"],
                "status": row["status"],
                "version": row["version"],
                "fingerprint": row["fingerprint"],
                "config": row["config"],
                "loaded": get(row["key"]) is not None,
            },
            "revisions": [
                {
                    "version": r["version"],
                    "fingerprint": r["fingerprint"],
                    "change_note": r["change_note"],
                    "changed_by": r["changed_by"],
                    "created_at": str(r["created_at"]),
                }
                for r in revisions
            ],
            "runs": [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "status": r["status"],
                    "started_at": str(r["started_at"]),
                    "failure_reason": r["failure_reason"],
                }
                for r in runs
            ],
        }

    if action in ("enable", "disable"):
        status = "active" if action == "enable" else "disabled"
        if action == "enable":
            from app.sources.config import config_from_row

            verification = await _latest_passing_run(config_from_row(row))
            if not verification["verified"]:
                return {
                    "ok": False,
                    "error": _enable_refusal(verification),
                    "verification": verification,
                }
        updated = await repo.set_content_source_status(row["id"], status)
        if updated is None:
            return {"ok": False, "error": "更新来源状态失败"}
        if status == "active":
            from app.sources.config import config_from_row

            register_configured(config_from_row(updated), replace=True)
        else:
            unregister(row["key"])
        return {"ok": True, "key": row["key"], "status": status}

    if action == "delete":
        removed = await repo.delete_content_source(row["id"])
        unregister(row["key"])
        return {"ok": removed, "key": row["key"], "error": None if removed else "删除失败"}

    if action == "run_trial":
        return await _run_trial(row, keywords=keywords or [], max_policies=max_policies)

    if action == "health":
        from app.sources.config import config_from_row

        try:
            config = config_from_row(row)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"配置无法解析：{exc}"}
        checked = []
        for entry in config.entrypoints:
            if entry.is_search:
                # 健康检查不猜关键词：检索页在没有关键词参数时返回的是空结果页，
                # 取回来只会得出「0 条」这种看着像故障、实际是探测方式不对的结论。
                checked.append(
                    {
                        "name": entry.name,
                        "kind": "search",
                        "url": entry.url,
                        "reachable": None,
                        "note": "检索型入口需要关键词才有结果，健康检查不取数；正式抓取时才带关键词",
                    }
                )
                continue
            url = build_list_url(entry, 1)
            status = {"name": entry.name, "kind": "list", "url": url, "reachable": False}
            try:
                fetched = await net.fetch_text(str(url), allowed_domains=config.scope.allowed_domains)
                status.update(
                    {
                        "reachable": True,
                        "http_status": fetched.status,
                        "bytes": fetched.bytes_len,
                        "items": len(parse_list_page(fetched.text, entry, fetched.url)),
                    }
                )
            except (net.FetchError, net.UnsafeURLError) as exc:
                status["error"] = str(exc)
            checked.append(status)
        probed = [item for item in checked if item.get("reachable") is not None]
        return {
            # 一个入口都没探到时**不报健康**：全检索型入口的配置本来就没被检查过
            "ok": bool(probed) and all(item["reachable"] for item in probed),
            "key": row["key"],
            "loaded": get(row["key"]) is not None,
            "entrypoints": checked,
        }

    return {"ok": False, "error": f"未知的 action：{action}"}


async def _resolve_source_row(*, key: str | None, source_id: int | None) -> dict[str, Any] | None:
    from app.db import repositories as repo

    try:
        if source_id is not None:
            return await repo.fetch_content_source(source_id)
        if key:
            return await repo.fetch_content_source_by_key(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取来源失败：%s", exc)
    return None


async def _run_trial(row: dict[str, Any], *, keywords: list[str], max_policies: int) -> dict[str, Any]:
    """发起一次真实的小批量抓取任务（走完整流水线，用户可在抓取页看到进度与结果）。

    这是「真的跑一遍」的那一步：建 `crawl_task`（source=来源展示名）→ `start_pipeline`。
    任务会停在**人工确认**这一道闸（阶段一跑完等确认），与手动抓取完全一致——
    试抓也不越过确认环节。
    """
    from app.db import repositories as repo
    from app.pipeline.crawl_graph import start_pipeline

    source = get_by_label(row["label"])
    if source.key != row["key"]:
        return {
            "ok": False,
            "error": (
                f"来源「{row['label']}」尚未装载到进程内注册表（进程重启过？）。"
                "重新 register_source 或重启服务后再试。"
            ),
        }
    if row["status"] != "active":
        return {"ok": False, "error": f"来源状态为 {row['status']}，未启用不能发起抓取"}

    today = date.today()
    try:
        task_id = await repo.create_task(
            source=row["label"],
            date_from=None,
            date_to=today,
            keywords=list(keywords or []),
            agencies=None,
            doc_types=None,
            pipeline_overrides={
                "pipeline_max_policies": max(1, min(max_policies, 5)),
                "pipeline_per_keyword_limit": max(1, min(max_policies * 2, 10)),
            },
        )
        start_pipeline(task_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"发起试抓任务失败：{exc}"}
    return {
        "ok": True,
        "task_id": task_id,
        "status": "已发起，任务会在检索完成后停在「等确认」，可在抓取页查看",
        "note": "这是走完整流水线的真实抓取（含判别与正文预检），不是模拟",
    }


async def diagnose(
    *,
    url: str | None = None,
    key: str | None = None,
    allowed_domains: list[str] | None = None,
) -> dict[str, Any]:
    """阶段 7：故障归因。对同一地址/来源逐层试，指出**卡在哪一层**并给替代途径。

    分层：① 地址与访问权限 → ② 页面取回 → ③ 列表解析 → ④ 正文容器 → ⑤ 元信息 → ⑥ 附件。
    """
    from app.sources.config import config_from_row

    out: dict[str, Any] = {"layers": [], "suggestions": []}
    config: SourceConfig | None = None
    target = url

    if key:
        row = await _resolve_source_row(key=key, source_id=None)
        if row is None:
            return {"ok": False, "error": f"未找到来源 {key}"}
        try:
            config = config_from_row(row)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"配置无法解析：{exc}"}
        target = target or config.entrypoints[0].url
        allowed_domains = allowed_domains or config.scope.allowed_domains

    if not target:
        return {"ok": False, "error": "需要 url 或 key 之一"}

    # ① 地址与访问权限
    try:
        checked = net.check_url(target, allowed_domains=allowed_domains or None)
        out["layers"].append({"layer": "地址校验", "ok": True, "detail": checked})
    except net.UnsafeURLError as exc:
        out["layers"].append({"layer": "地址校验", "ok": False, "detail": str(exc)})
        out["suggestions"].append("地址不在允许范围（协议 / 主机 / 白名单 / 公网校验），先修正来源边界")
        return {"ok": False, **out}

    robots = await net.robots_status(checked)
    out["layers"].append(
        {"layer": "robots.txt", "ok": bool(robots.get("allowed")), "detail": robots.get("reason")}
    )
    if not robots.get("allowed"):
        out["suggestions"].append("站点 robots.txt 禁止抓取该路径：换栏目地址或与站点方确认")
        return {"ok": False, **out}

    # ② 页面取回
    try:
        fetched = await net.fetch_text(checked, allowed_domains=allowed_domains or None)
        out["layers"].append(
            {
                "layer": "页面取回",
                "ok": True,
                "detail": f"HTTP {fetched.status}，{fetched.bytes_len} 字节，跳转 {fetched.redirects} 次",
            }
        )
    except (net.FetchError, net.UnsafeURLError) as exc:
        out["layers"].append({"layer": "页面取回", "ok": False, "detail": str(exc)})
        out["suggestions"].extend(
            [
                "核对地址是否可达（DNS / 网络 / 证书）",
                "若返回 401/403：站点有访问控制，按规范不绕过，改用其它公开入口",
            ]
        )
        return {"ok": False, **out}

    analysis = analyze_page(fetched.text, fetched.url, listed_as="diagnose")

    # ③ 列表解析
    lists = analysis["list_candidates"]
    list_ok = bool(lists)
    out["layers"].append(
        {
            "layer": "列表结构",
            "ok": list_ok,
            "detail": f"发现 {len(lists)} 个候选列表容器" if list_ok else "未发现成组条目结构",
        }
    )
    if not list_ok:
        out["suggestions"].append(
            "该页可能不是列表页（是首页或正文页），或列表由 JS 动态渲染——"
            "改抓栏目列表页，或确认站点是否提供 RSS / 检索接口"
        )

    # ④ 正文容器
    details = analysis["detail_candidates"]
    container_ok = bool(details)
    out["layers"].append(
        {
            "layer": "正文结构",
            "ok": container_ok,
            "detail": f"发现 {len(details)} 个候选正文容器" if container_ok else "未发现长文本容器",
        }
    )
    if not container_ok:
        out["suggestions"].append("这看起来不是详情页；正文容器要在**详情页**上取样（sample_content 给定详情页 URL）")

    # ⑤ 元信息
    meta = analysis["meta_candidates"]
    meta_hits = {name: len(items) for name, items in meta.items()}
    out["layers"].append(
        {
            "layer": "元信息",
            "ok": bool(meta_hits.get("date")) or bool(meta_hits.get("title")),
            "detail": meta_hits,
        }
    )
    if not meta_hits.get("date"):
        out["suggestions"].append("页面没有可识别的日期节点：日期可能只在列表页，或需要人工补充选择器")
    if not meta_hits.get("agency"):
        out["suggestions"].append("未发现「来源：」式机构字段：机构可能需要从列表页栏目名推断")

    # ⑥ 附件
    attachments = analysis["attachments"]
    out["layers"].append(
        {
            "layer": "附件",
            "ok": True,
            "detail": f"本页发现 {len(attachments)} 个附件链接" if attachments else "本页未发现附件",
        }
    )
    out["final_url"] = fetched.url
    out["list_candidates"] = lists
    out["detail_candidates"] = details
    out["pagination"] = analysis["pagination"]
    if config is not None:
        out["config_key"] = config.key
        out["note"] = "来源已配置：如本页与配置不符，说明站点改版，建议重新勘察并生成新版本配置"
    return {"ok": all(layer["ok"] for layer in out["layers"]), **out}


# ---------- 供工具层复用的轻量查询 ----------


async def list_runs(*, key: str | None = None, limit: int = 10) -> dict[str, Any]:
    """最近接入记录（给对话回复用的精简视图）。"""
    from app.db import repositories as repo

    try:
        rows = await repo.list_onboarding_runs(source_key=key, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"读取接入记录失败：{exc}"}
    return {
        "ok": True,
        "runs": [
            {
                "id": row["id"],
                "key": row.get("source_key"),
                "kind": row["kind"],
                "status": row["status"],
                "started_at": str(row["started_at"]),
                "summary": row.get("summary"),
                "failure_reason": row.get("failure_reason"),
            }
            for row in rows
        ],
    }


__all__ = [
    "analyze_structure",
    "diagnose",
    "discover_content",
    "dry_run_ingestion",
    "generate_config",
    "inspect_source",
    "list_runs",
    "manage_source",
    "parse_date",
    "register_source",
    "sample_content",
]
