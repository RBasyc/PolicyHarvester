"""来源接入 HTTP 入口（Phase 15）：页面内「导入网址」+ 来源管理。

这是 `app/agent/onboarding_tools.py` 那 9 个 Agent 工具的**同一批能力的 HTTP 门**——
编排在 `app/services/source_import.py`，能力在 `app/services/onboarding.py`，
两边调的是同一段代码，因此页面上跑出来的结论与对话里跑出来的结论一致。

规矩：

- **错误一律 `{"detail": "<string>"}`**（见 `app/main.py` 的异常处理），前端直接渲染。
- `blocked`（robots 禁止 / 401 / 403 / 内网地址）与 `failed`（配置写错）**分开**：
  前者是「站点或环境不允许」，后者是「我们配错了」，绝不合并成一句「失败」。
- **启用要过验收闸**：`/enable` 复用 `register_source(approved=True, force=...)`——
  与 Agent 工具同一道门；`force=true` 会写进变更说明（`未经验收强制启用`）。
- 路由顺序：`/import` 系列是静态段，注册在 `/{source_id}` **之前**——
  `/{source_id}` 是 int 型，先注册的话 `import` 会以 422 被当成坏 id 吞掉
  （`policies.py` 里 `/meta` 有同样的坑，注释也在那儿）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.db import repositories as repo
from app.schemas.source import (
    SourceBatchImportCreated,
    SourceBatchImportRequest,
    SourceBatchSnapshot,
    SourceDTO,
    SourceDetailDTO,
    SourceEnableRequest,
    SourceImportCreated,
    SourceImportJobDTO,
    SourceImportRequest,
    SourceJobDTO,
    SourceListResponse,
    SourceTrialRequest,
)
from app.services import source_import
from app.services.onboarding import (
    manage_source,
    register_source,
)
from app.sources.browser_any import (
    BROWSER_SOURCE_KEY,
    BROWSER_SOURCE_LABEL,
    browser_source_available,
)
from app.sources.config import capability_summary
from app.sources.registry import get_default

router = APIRouter(prefix="/api/sources", tags=["sources"])

# 与 /api/tasks/{id}/stream 同一套节奏，前端复用同一个传输层
_STREAM_POLL_SECONDS = 0.6
_STREAM_HEARTBEAT_SECONDS = 15.0
_TERMINAL_JOB_STATUSES = ("done", "error", "cancelled")

_BUILTIN_LEVEL = "national"


def _sse(payload: dict) -> str:
    """与 /api/tasks/{id}/stream 同格式：一行 `data: {...}` + 空行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _get_source_or_404(source_id: int) -> dict:
    row = await repo.fetch_content_source(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"来源不存在：{source_id}")
    return row


async def _builtin_dto() -> SourceDTO:
    """内置来源（gov.cn）：代码里写死的适配器，不在 `content_source` 表里。"""
    source = get_default()
    return SourceDTO(
        id=None,
        key=source.key,
        label=source.label,
        level=_BUILTIN_LEVEL,
        organization="国务院",
        status="active",
        builtin=True,
        version=1,
        verified=True,  # 内置源由代码保证可用，不走「试运行验收」这条路
        entrypoints=[],
        supports_doc_types=bool(source.doc_types),
        notes=f"内置来源（{source.fetched_by}），随服务一起发布，不能删除或停用",
    )


def _browser_dto() -> SourceDTO:
    """内置的「任意网站（无头浏览器）」来源。

    `supports_doc_types=False` 让前端自动隐藏文种筛选；`keyword/date_server_side=False`
    告诉前端这些筛选不是站点检索参数（发现循环会按目标挑选，但不是在服务端过滤）。
    """
    return SourceDTO(
        id=None,
        key=BROWSER_SOURCE_KEY,
        label=BROWSER_SOURCE_LABEL,
        level="other",
        organization=None,
        status="active",
        builtin=True,
        version=1,
        verified=True,  # 内置能力，不需要来源接入验收
        entrypoints=[],
        supports_doc_types=False,
        keyword_server_side=False,
        date_server_side=False,
        search_entrypoints=[],
        notes=(
            "内置来源（无头浏览器）：需要填写入口网址与浏览目标；只读浏览、遵守 robots.txt，"
            "不支持定时任务。"
        ),
    )


def _row_to_dto(row: dict, *, verified: bool) -> SourceDTO:
    """`content_source` 行 → DTO。入口名同时是「发布机构」筛选的可选值。"""
    config = row.get("config") or {}
    if isinstance(config, str):  # 兜底：某些驱动/手工导入会给字符串
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            config = {}
    entrypoints = [str(e.get("name")) for e in (config.get("entrypoints") or []) if e.get("name")]
    caps = capability_summary(config)
    return SourceDTO(
        id=row["id"],
        key=row["key"],
        label=row["label"],
        level=row["level"],
        organization=row.get("organization"),
        status=row["status"],
        builtin=False,
        version=int(row.get("version") or 1),
        verified=verified,
        entrypoints=entrypoints,
        supports_doc_types=bool(config.get("doc_type_patterns")),
        keyword_server_side=caps["keyword"] == "server",
        date_server_side=caps["date"] == "server",
        search_entrypoints=list(caps["search_entrypoints"]),
        notes=row.get("notes"),
    )


def _job_dto(job: source_import.ImportJob) -> SourceImportJobDTO:
    return SourceImportJobDTO(
        job_id=job.job_id,
        url=job.url,
        status=job.status,  # type: ignore[arg-type]
        phase=job.phase,  # type: ignore[arg-type]
        phase_text=job.phase_text,
        logs=job.logs,  # type: ignore[arg-type]
        config=job.config,
        candidates=job.candidates,
        report=job.report,
        stage_reports=job.stage_reports,
        registered=job.registered,
        error=job.error,
        error_kind=job.error_kind,  # type: ignore[arg-type]
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _passing_fingerprints() -> set[str]:
    """全部「通过的试运行」的指纹集合（列表页一次算完，替代逐行 N+1）。

    判定规则与 `onboarding.verify_source_config` 同一条（住在
    `repo.list_passing_dry_run_fingerprints`），这里只是批量口径；读失败按「都没有」处理。
    """
    try:
        return await repo.list_passing_dry_run_fingerprints()
    except Exception:  # noqa: BLE001 - 表还没建/库不可用时不拖垮列表
        return set()


def _run_dto(row: dict) -> dict[str, Any]:
    """`source_onboarding_run` 行 → 页面可读的接入记录（`report` 原样带上，不裁剪）。"""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "started_at": str(row.get("started_at")),
        "finished_at": str(row.get("finished_at")) if row.get("finished_at") else None,
        "failure_reason": row.get("failure_reason"),
        "summary": row.get("summary"),
        "report": row.get("report"),
    }


# ---------- 导入作业（静态段，必须排在 /{source_id} 之前） ----------


@router.post("/import", response_model=SourceImportCreated, status_code=201)
async def create_import(payload: SourceImportRequest) -> SourceImportCreated:
    """发起一次「导入网址」作业。

    地址在**建作业之前**先过 `net.check_url`（公网 http/https、非内网、非云元数据地址），
    不通过直接 400，不会有作业被建出来。作业建好后立即返回 `job_id`，
    进度订阅 `GET /api/sources/import/{job_id}/stream`。
    """
    try:
        job = source_import.start_import(payload)
    except source_import.ImportUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except source_import.ImportRejected as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return SourceImportCreated(job_id=job.job_id, stream=f"/api/sources/import/{job.job_id}/stream")


@router.post("/import/batch", response_model=SourceBatchImportCreated, status_code=201)
async def create_import_batch(payload: SourceBatchImportRequest) -> SourceBatchImportCreated:
    """批量导入：N 个地址排队逐个跑（并发满排队不拒绝，批内无 5 秒冷却）。

    逐条立即给结果：地址非法标 `invalid` 给原因（不建作业），其余入队；
    单个作业的行为与单条导入**完全一致**（同一套六阶段、同一道注册闸），
    验收通过同样落为草案等人工启用。聚合进度订阅
    `GET /api/sources/import/batch/{batch_id}/stream`。
    """
    return source_import.start_batch(payload)


@router.get("/import", response_model=list[SourceJobDTO])
async def list_import_jobs(limit: int = Query(50, ge=1, le=200)) -> list[SourceJobDTO]:
    """进程内导入作业列表（倒序，含排队中的；日志/报告等大字段走单作业接口）。

    只是进度快照：进程重启或超窗（30 分钟）后从这里消失，持久留痕见接入记录。
    """
    return [
        SourceJobDTO(
            job_id=j.job_id,
            url=j.url,
            batch_id=j.batch_id,
            status=j.status,  # type: ignore[arg-type]
            phase=j.phase,  # type: ignore[arg-type]
            phase_text=j.phase_text,
            error=j.error,
            error_kind=j.error_kind,  # type: ignore[arg-type]
            started_at=j.started_at,
            finished_at=j.finished_at,
        )
        for j in source_import.list_jobs(limit)
    ]


@router.get("/import/{job_id}", response_model=SourceImportJobDTO)
async def get_import_job(job_id: str) -> SourceImportJobDTO:
    """作业快照：状态 + 阶段 + 全部日志 + 配置 + 候选 + **完整验收报告** + 登记结果。"""
    job = source_import.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"导入作业不存在：{job_id}")
    return _job_dto(job)


@router.post("/import/{job_id}/cancel")
async def cancel_import_job(job_id: str) -> dict[str, Any]:
    """取消作业：已落库的证据（`source_onboarding_run`）保留，便于事后看为什么中止。"""
    job = source_import.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"导入作业不存在：{job_id}")
    cancelled = source_import.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail=f"作业当前状态为 {job.status}，无法取消")
    return {"ok": True, "job_id": job_id}


@router.get("/import/{job_id}/stream")
async def stream_import_job(job_id: str) -> StreamingResponse:
    """导入进度 SSE。

    事件（每条为 `data: {...}\\n\\n`，与 `/api/tasks/{id}/stream` 同格式）：
    - `phase`：作业快照（阶段 + 已知产物：候选/配置/报告…）
    - `log`  ：单条新日志（按 `seq` 递增，订阅时先补发全部历史）
    - `done` ：作业进入终态（done / error / cancelled），随后连接关闭
    - `error`：读取过程中出错
    """
    if source_import.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"导入作业不存在：{job_id}")

    async def event_source() -> AsyncGenerator[str, None]:
        last_seq = 0
        last_snapshot: str | None = None
        idle_seconds = 0.0
        try:
            while True:
                current = source_import.get_job(job_id)
                if current is None:
                    yield _sse({"type": "error", "message": f"导入作业不存在：{job_id}"})
                    return

                dto = _job_dto(current)
                snapshot = json.dumps(dto.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
                if snapshot != last_snapshot:
                    last_snapshot = snapshot
                    yield _sse({"type": "phase", "job": json.loads(snapshot)})

                for line in list(current.logs):
                    if line["seq"] <= last_seq:
                        continue
                    last_seq = line["seq"]
                    yield _sse({"type": "log", "job_id": job_id, "log": line})

                if current.status in _TERMINAL_JOB_STATUSES:
                    yield _sse({"type": "done", "status": current.status, "job": json.loads(snapshot)})
                    return

                await asyncio.sleep(_STREAM_POLL_SECONDS)
                idle_seconds += _STREAM_POLL_SECONDS
                if idle_seconds >= _STREAM_HEARTBEAT_SECONDS:
                    idle_seconds = 0.0
                    yield ": ping\n\n"  # SSE 注释行，保活
        except asyncio.CancelledError:  # 客户端断开
            raise
        except Exception as exc:  # noqa: BLE001 - 流内错误以事件形式下发
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉反向代理缓冲，日志才能逐条到达
        },
    )


@router.get("/import/batch/{batch_id}/stream")
async def stream_import_batch(batch_id: str) -> StreamingResponse:
    """批量导入的聚合进度 SSE（单连接；EventSource 只支持 GET，所以是 GET）。

    事件：
    - `phase`：批快照（子作业队列 + 各自状态/阶段/排队位置；快照变化才推）
    - `log`  ：任一子作业的新日志透传（带 `job_id`，批量列表内联「最新一行」用）
    - `done` ：全部子作业终态（含被内存边界清理的 `gone` 行），随后连接关闭
    - `error`：读取过程中出错 / 批已被清理
    """
    if source_import.batch_snapshot(batch_id) is None:
        raise HTTPException(status_code=404, detail=f"批量导入不存在或已清理：{batch_id}")

    async def event_source() -> AsyncGenerator[str, None]:
        last_snapshot: str | None = None
        last_seq: dict[str, int] = {}
        idle_seconds = 0.0
        try:
            while True:
                snapshot = source_import.batch_snapshot(batch_id)
                if snapshot is None:
                    yield _sse({"type": "error", "message": f"批量导入不存在或已清理：{batch_id}"})
                    return

                # 过一遍 SourceBatchSnapshot 再下发：`batch_snapshot()` 返回的是裸 dict，
                # 不校验的话 schema 与线上字段会各自漂移（前端 types.ts 是它俩的手抄镜像）。
                body = SourceBatchSnapshot.model_validate(snapshot).model_dump()
                payload = json.dumps(body, ensure_ascii=False, sort_keys=True)
                if payload != last_snapshot:
                    last_snapshot = payload
                    yield _sse({"type": "phase", "batch": body})

                for child in snapshot["queue"]:
                    job = source_import.JOBS.get(child["job_id"])
                    if job is None:
                        continue
                    for line in list(job.logs):
                        if line["seq"] <= last_seq.get(job.job_id, 0):
                            continue
                        last_seq[job.job_id] = line["seq"]
                        yield _sse({"type": "log", "job_id": job.job_id, "log": line})

                if snapshot["all_terminal"]:
                    yield _sse({"type": "done", "batch": body})
                    return

                await asyncio.sleep(_STREAM_POLL_SECONDS)
                idle_seconds += _STREAM_POLL_SECONDS
                if idle_seconds >= _STREAM_HEARTBEAT_SECONDS:
                    idle_seconds = 0.0
                    yield ": ping\n\n"  # SSE 注释行，保活
        except asyncio.CancelledError:  # 客户端断开
            raise
        except Exception as exc:  # noqa: BLE001 - 流内错误以事件形式下发
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- 来源清单 / 详情 / 管理 ----------


@router.get("", response_model=SourceListResponse)
async def list_sources() -> SourceListResponse:
    """来源下拉的数据源：内置 gov.cn + `content_source` 里的全部行。

    列表**不过滤状态**（草案/停用也要看得见），`status` 与 `verified` 交给前端决定
    能不能选：只有 `active` 的来源才应该被正式抓取任务用上。
    """
    items: list[SourceDTO] = [await _builtin_dto()]
    if browser_source_available():
        items.append(_browser_dto())
    try:
        rows = await repo.list_content_sources()
    except Exception:  # noqa: BLE001 - 表还没建 / 库不可用时至少给内置来源，页面不白屏
        return SourceListResponse(items=items, default_label=get_default().label)

    verified_fps = await _passing_fingerprints()
    for row in rows:
        items.append(_row_to_dto(row, verified=(row.get("fingerprint") or "") in verified_fps))
    return SourceListResponse(items=items, default_label=get_default().label)


@router.get("/runs/recent")
async def list_recent_runs(
    limit: int = Query(20, ge=1, le=100),
    kind: str | None = Query(None, pattern="^(inspect|discover|sample|analyze|dry_run|validate)$"),
) -> dict[str, Any]:
    """跨来源的最近接入记录（每来源取最新一条），供「接入记录」总览。

    静态段，注册在 `/{source_id}` 之前——否则 `recent` 会被 int 型 id 吞成 422
    （与 `/import` 同款坑，文件头注释有载）。
    """
    try:
        rows = await repo.list_onboarding_runs(kinds=[kind] if kind else None, limit=limit * 5)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取接入记录失败：{exc}") from exc
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:  # 已按 started_at DESC，每 key 第一条即最新
        key = row.get("source_key") or ""
        if key not in seen:
            seen[key] = row
        if len(seen) >= limit:
            break
    try:
        sources = await repo.list_content_sources()
    except Exception:  # noqa: BLE001
        sources = []
    label_by_key = {s["key"]: s["label"] for s in sources}
    items: list[dict[str, Any]] = []
    for row in seen.values():
        item = _run_dto(row)
        key = row.get("source_key")
        item["source_key"] = key
        item["source_label"] = label_by_key.get(key or "") or key or "（未知来源）"
        items.append(item)
    return {"items": items}


@router.get("/{source_id}", response_model=SourceDetailDTO)
async def get_source(source_id: int) -> SourceDetailDTO:
    """来源详情：配置本体 + 版本历史 + 最近几次接入记录。"""
    row = await _get_source_or_404(source_id)
    detail = await manage_source("detail", source_id=source_id)
    if not detail.get("ok"):
        raise HTTPException(status_code=400, detail=str(detail.get("error") or "读取来源详情失败"))
    verified_fps = await _passing_fingerprints()
    return SourceDetailDTO(
        source=_row_to_dto(row, verified=(row.get("fingerprint") or "") in verified_fps),
        config=detail["source"]["config"],
        revisions=detail.get("revisions") or [],
        runs=[_run_dto(r) for r in (detail.get("runs") or [])],
    )


@router.get("/{source_id}/runs")
async def list_source_runs(source_id: int, limit: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
    """该来源的接入记录（每次勘察/试运行的原始报告都在这里，供人工复核）。"""
    row = await _get_source_or_404(source_id)
    runs = await repo.list_onboarding_runs(source_key=row["key"], limit=limit)
    return {"source_id": source_id, "key": row["key"], "items": [_run_dto(r) for r in runs]}


@router.post("/{source_id}/enable")
async def enable_source(source_id: int, payload: SourceEnableRequest | None = None) -> dict[str, Any]:
    """启用来源（人工确认那一下）。

    **没有指纹一致的通过验收就拒绝**——与 Agent 工具同一道门（`register_source`）。
    凭据按指纹在 `source_onboarding_run` **全表**里找（不设条数窗口，见
    `db/repositories._PASSING_DRY_RUN_SQL`），与列表页的「已验收」徽标是同一条判定——
    2026-10-15 修的正是两者漂开的那次：闸门原来只看「该来源最近 20 条记录」，
    凭据被后来的勘察/失败试运行挤出窗口后，「停用 → 再启用」会被误拒。
    `force=true` 是人工拍板放行，会把「未经验收强制启用」写进变更说明：用了 force 就是
    **明确承担了「未经试运行验证」的风险**，变更历史里查得到。
    """
    row = await _get_source_or_404(source_id)
    payload = payload or SourceEnableRequest()
    result = await register_source(
        row["config"],
        approved=True,
        force=payload.force,
        change_note=payload.change_note
        or (f"页面启用来源「{row['label']}」" + ("（强制放行）" if payload.force else "")),
        changed_by="web",
    )
    if not result.get("ok"):
        # 409：不是「参数写错」，而是「当前状态不允许启用」——正是冲突语义
        raise HTTPException(status_code=409, detail=str(result.get("error") or "启用失败"))
    return {"ok": True, "source_id": source_id, **result}


@router.post("/{source_id}/disable")
async def disable_source(source_id: int) -> dict[str, Any]:
    """停用来源：从抓取来源注册表摘掉；已抓的任务与成果不受影响。"""
    await _get_source_or_404(source_id)
    result = await manage_source("disable", source_id=source_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "停用失败"))
    return {"ok": True, "source_id": source_id, **result}


@router.post("/{source_id}/trial")
async def trial_source(source_id: int, payload: SourceTrialRequest | None = None) -> dict[str, Any]:
    """来源试运行：一次真实的小批量抓取（走完整流水线，停在人工确认闸）。

    与 Agent 工具 `manage_source("run_trial")` 同一道门：**仅 active 来源可用**
    （任务按展示名解析进进程内注册表，未启用/未装载会 409）。触发后可在抓取页看进度。
    """
    await _get_source_or_404(source_id)
    payload = payload or SourceTrialRequest()
    result = await manage_source(
        "run_trial",
        source_id=source_id,
        keywords=payload.keywords,
        max_policies=payload.max_policies,
    )
    if not result.get("ok"):
        # 不是参数错，是「当前状态不允许试运行」——冲突语义
        raise HTTPException(status_code=409, detail=str(result.get("error") or "试运行失败"))
    return {"ok": True, "source_id": source_id, **result}


@router.post("/{source_id}/health")
async def health_source(source_id: int) -> dict[str, Any]:
    """来源健康检查：逐入口真抓第 1 页，报告可达性/HTTP 状态/条目解析数。"""
    await _get_source_or_404(source_id)
    result = await manage_source("health", source_id=source_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "健康检查失败"))
    return {"ok": True, "source_id": source_id, **result}


@router.delete("/{source_id}")
async def delete_source(source_id: int) -> dict[str, Any]:
    """删除来源（级联删版本记录）。内置来源不在表里，因此不会走到这儿。"""
    row = await _get_source_or_404(source_id)
    result = await manage_source("delete", source_id=source_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "删除失败"))
    return {"ok": True, "source_id": source_id, "key": row["key"]}
