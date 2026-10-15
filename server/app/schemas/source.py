"""来源接入相关 DTO（Phase 15）：页面内「导入网址」入口与来源管理。

两件事共用这一组 DTO：

- **导入作业**：页面上传一个栏目地址 → 后台依次跑「勘察 → 内容发现 → 生成配置 →
  试运行验收 → 注册草案」，进度用 SSE 推（`ImportPhase` 就是进度条上的阶段名）。
  作业状态**只活在进程内**（见 `app/services/source_import.py`），持久留痕在
  `source_onboarding_run`（每次勘察/试运行各一行）；服务重启丢的只是进度条。
- **来源清单**：抓取页的来源下拉读 `SourceDTO` 列表，`builtin` 标出内置的 gov.cn，
  `verified` 标出「存在指纹一致的通过验收」。

`report` 字段直接透传 `dry_run_ingestion` 的验收报告（`criteria` / `details` /
`untested` / `coverage_limits`），**不做裁剪**——「这次没验证什么」必须原样到页面。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.sources.config import SourceStatus

# 导入作业在**进程内**的状态机：queued → running → done / error / cancelled
# （queued = 批量导入时并发已满，在队列里等待空位；单个导入不会进入该状态）
ImportJobStatus = Literal["queued", "running", "done", "error", "cancelled"]
# 作业阶段（与 Skill 的阶段 1/2/4/5/6 对齐），finished 只用于收尾那一帧
ImportPhase = Literal["inspect", "discover", "generate", "dry_run", "register", "finished"]
# 失败的性质：failed = 配置/站点结构不符；blocked = 站点或环境不允许（不绕过访问控制）
ImportErrorKind = Literal["failed", "blocked"]


class SourceImportRequest(BaseModel):
    """发起一次接入作业。

    `url` 是**栏目/入口页**地址（列出政策条目的那一页），不是门户首页——首页当然也能跑，
    只是要靠 `discover` 往下找栏目，多绕一层。地址在入队前先过 `net.check_url`
    （公网 http/https、非内网、非云元数据），不通过直接 400，不建作业。

    `base_config` + `candidate` 是**候选重跑**用的：带上一次生成的配置与想换掉的选择器组合，
    跳过勘察与内容发现，直接重新生成配置并试运行（复用同一条 SSE 与同一份报告结构）。
    """

    url: str = Field(..., min_length=1, max_length=2000, description="栏目/入口页地址")
    target: str | None = Field(None, max_length=200, description="这次要采集什么（可选，进勘察报告）")
    keywords: list[str] = Field(default_factory=list, description="试抓关键词；留空 = 不按关键词过滤")
    limit: int = Field(3, ge=1, le=10, description="试抓篇数")
    pages: int = Field(1, ge=1, le=5, description="每个入口试抓前几页")
    base_config: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class SourceImportLogLine(BaseModel):
    """一条进度日志（与抓取任务的 task_log 同观感）。"""

    seq: int
    phase: ImportPhase
    level: Literal["info", "warn", "error"]
    message: str
    created_at: str


class SourceImportJobDTO(BaseModel):
    """作业快照：SSE 每个事件推一份，`GET /api/sources/import/{job_id}` 也返回它。"""

    job_id: str
    url: str
    status: ImportJobStatus
    phase: ImportPhase
    phase_text: str
    logs: list[SourceImportLogLine] = Field(default_factory=list)
    config: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    report: dict[str, Any] | None = None  # dry_run_ingestion 的验收报告全文
    # 早期阶段的原始报告（勘察 = `inspect` / 内容发现 = `discover`）。作业在这个阶段就
    # 受阻时，页面靠它说明「站点怎么说的」（robots、允许域名、范围外链接…）。
    stage_reports: dict[str, Any] = Field(default_factory=dict)
    registered: dict[str, Any] | None = None  # register_source 的返回（source_id / status / version）
    error: str | None = None
    error_kind: ImportErrorKind | None = None
    started_at: str
    finished_at: str | None = None


class SourceImportCreated(BaseModel):
    """`POST /api/sources/import` 的响应：只回作业号，进度走 SSE。"""

    job_id: str
    stream: str = Field(..., description="该作业的 SSE 地址")


# ---------- 批量导入（Phase 16） ----------


class SourceBatchImportItem(BaseModel):
    """批量导入中**一条地址**的立即处理结果。

    `accepted=False` 表示没建出作业（地址非法 / 被拒绝），原因见 `error`；
    `accepted=True` 时 `job_id` 一定存在，进度走该作业的 SSE 或批量聚合 SSE。
    """

    url: str
    job_id: str | None = None
    accepted: bool
    error: str | None = None


class SourceBatchImportRequest(BaseModel):
    """批量导入：多个栏目地址 + 一组共享试抓参数，服务端排队逐个跑。

    单个作业的行为与 `POST /import` 完全一致（同一套六阶段、同一道注册闸）；
    区别在于并发已满时**排队**（queued）而不是 429，且批内不受 5 秒冷却限制。
    """

    urls: list[str] = Field(..., min_length=1, max_length=20, description="每行一个栏目/入口页地址")
    target: str | None = Field(None, max_length=200, description="这次要采集什么（可选，进勘察报告）")
    keywords: list[str] = Field(default_factory=list, description="试抓关键词；留空 = 不按关键词过滤")
    limit: int = Field(3, ge=1, le=10, description="试抓篇数")
    pages: int = Field(1, ge=1, le=5, description="每个入口试抓前几页")

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("urls")
    @classmethod
    def _clean_urls(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            url = (item or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(url)
        if not out:
            raise ValueError("没有可用地址（空行/重复行已去除）")
        return out


class SourceBatchImportCreated(BaseModel):
    """`POST /api/sources/import/batch` 的响应：逐条立即结果 + 聚合 SSE 地址。

    `jobs` 保持请求行序（去重后），前端按行对齐显示；`accepted`/`rejected`
    是给状态行汇总用的拆分视图。
    """

    batch_id: str
    stream: str = Field(..., description="该批作业的聚合 SSE 地址")
    jobs: list[SourceBatchImportItem] = Field(default_factory=list)
    accepted: list[str] = Field(default_factory=list)
    rejected: list[SourceBatchImportItem] = Field(default_factory=list)


class SourceJobDTO(BaseModel):
    """进程内导入作业的行快照（`GET /api/sources/import` 列表用）。

    不含日志/报告等大字段——那些走单作业 `GET /import/{job_id}`；
    进程重启或超窗清理后作业会从列表消失，持久留痕在 `source_onboarding_run`。
    """

    job_id: str
    url: str
    batch_id: str | None = None
    status: ImportJobStatus
    phase: ImportPhase
    phase_text: str
    error: str | None = None
    error_kind: ImportErrorKind | None = None
    started_at: str
    finished_at: str | None = None


class SourceBatchChildDTO(BaseModel):
    """批量聚合 SSE 事件里的子作业视图：队列位置只在排队时给出。"""

    job_id: str
    url: str
    status: ImportJobStatus
    phase: ImportPhase
    phase_text: str
    error: str | None = None
    error_kind: ImportErrorKind | None = None
    position: int | None = None  # 排队中的序号（1 起）；不在队列则为 None
    # 进度快照已被内存边界清出 JOBS（按终态对待；持久记录见 source_onboarding_run）。
    # 必须显式声明：Pydantic v2 默认丢弃模型未声明的键，漏了这里前端就永远收不到 true。
    gone: bool = False
    started_at: str
    finished_at: str | None = None


class SourceBatchSnapshot(BaseModel):
    """一次批量导入的聚合快照（batch SSE 的 `phase`/`done` 事件体）。"""

    batch_id: str
    queue: list[SourceBatchChildDTO] = Field(default_factory=list)
    running_count: int = 0
    pending_count: int = 0
    all_terminal: bool = False


class SourceDTO(BaseModel):
    """来源清单的一项（抓取页的来源下拉 + 来源管理列表共用）。

    `id` 为 `None` 表示**内置来源**（gov.cn），它不在 `content_source` 表里，只能选不能改。
    `verified` = 存在指纹一致的通过试运行；`active` 才能被正式抓取任务选中。
    `entrypoints` 是入口名列表——同时就是「发布机构」筛选的可选值（配置化来源没有
    gov.cn 那样的机构 facet，口径就是入口名）。`supports_doc_types` 为 False 时
    前端必须隐藏「文种」筛选，否则会筛出一个静默的空结果。
    """

    id: int | None = None
    key: str
    label: str
    level: str
    organization: str | None = None
    status: SourceStatus
    builtin: bool = False
    version: int = 1
    verified: bool = False
    entrypoints: list[str] = Field(default_factory=list)
    supports_doc_types: bool = False
    notes: str | None = None


class SourceListResponse(BaseModel):
    """来源下拉的数据源：`default_label` 是内置来源的 label，前端校验当前选中项用。"""

    items: list[SourceDTO] = Field(default_factory=list)
    default_label: str


class SourceDetailDTO(BaseModel):
    """来源详情：配置 + 版本历史 + 最近几次接入记录（验收报告在 `runs[].report`）。"""

    source: SourceDTO
    config: dict[str, Any]
    revisions: list[dict[str, Any]] = Field(default_factory=list)
    runs: list[dict[str, Any]] = Field(default_factory=list)


class SourceEnableRequest(BaseModel):
    """启用来源（人工确认那一下）。

    `force=True` 是**人工拍板放行**：没有指纹一致的通过验收也允许启用，但服务端会把
    「未经验收强制启用」写进变更说明（与 Agent 工具 `register_source(force=True)` 同一段代码）。
    """

    force: bool = False
    change_note: str | None = Field(None, max_length=500)


class SourceTrialRequest(BaseModel):
    """来源试运行：真实的小批量抓取（走完整流水线，停在人工确认闸）。

    仅对已启用（active）来源可用——任务走「来源展示名 → 进程内注册表」解析，
    未装载/未启用会 409。触发后可在抓取页看进度与确认。
    """

    keywords: list[str] = Field(default_factory=list)
    max_policies: int = Field(3, ge=1, le=5)

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


