"""配置管理：从环境变量 / .env 文件读取配置。"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

from dotenv import dotenv_values, load_dotenv
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.prompts import REPO_ROOT

logger = logging.getLogger(__name__)

# 仓库根目录：真身在 app/prompts.py（提示词目录也要用它），这里保留私有别名，少改下面的调用点
_REPO_ROOT = REPO_ROOT

# 真实 shell 环境的快照（**必须**在 load_dotenv 之前拍）：load_dotenv 不覆盖已有变量，
# 启动后 os.environ 里已分不清「用户在 shell 里设的」和「从 .env 灌进来的」；
# 提取模型注册表要热读 .env（改完不重启），优先级需要还原成：真实环境变量 > 当前 .env。
_REAL_ENV = dict(os.environ)

# .env 统一放在仓库根目录（与 web/、database/ 平级），与启动时的工作目录无关。
# 先把 .env 加载进 os.environ：
# anthropic SDK 会自动读取 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL 等变量，
# 而 pydantic-settings 只把 .env 读进 Settings 对象，不会写回环境。
load_dotenv(_REPO_ROOT / ".env")

# China-Central-Policy-MCP 本地构建产物（tools/ 下克隆并自带 build/）
_DEFAULT_POLICY_MCP_ENTRY = str(
    _REPO_ROOT / "tools" / "China-Central-Policy-MCP" / "build" / "index.js"
)


class Settings(BaseSettings):
    """应用全局配置。"""

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    anthropic_api_key: str = ""
    anthropic_auth_token: str = ""  # Bearer token 认证（如 Kimi 等兼容网关）
    anthropic_base_url: str | None = None  # 可选：自定义 API 网关
    model_name: str = "claude-opus-5-5"
    max_tokens: int = 16000
    # 提取模型注册表（PIPELINE_MODELS）不走 Settings：它要**热读** .env（改完不重启），
    # 见 get_model_registry() / _live_env()。

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]

    # --- Database: agent_demo（docker 容器 server-db-1，见 database/README.md）---
    database_url: str = "postgresql://kos:kos@localhost:5433/agent_demo"
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    # --- 抓取 / 提取流水线（Phase 4）---
    pipeline_max_policies: int = 10  # 单次任务最多处理多少篇（demo 控时长）
    pipeline_per_keyword_limit: int = 10  # 每个关键词向 MCP 请求多少条
    pipeline_extract_concurrency: int = 1  # 并发提取的篇数（1 = 顺序执行，日志与 stage 语义最清晰）
    pipeline_max_body_chars: int = 12000  # 送进大模型的正文上限（超出截断）
    pipeline_mcp_timeout: int = 30  # 单次 MCP 调用超时秒数（检索/取全文）
    pipeline_max_search_calls: int = 12  # 检索扇出上限：keyword × 发布机构 × 文种 组合最多打多少次 MCP（防组合爆炸）
    # 正文预检（laya 判别之后的第二道筛，≤0 = 关闭对应信号；改 .env 需重启，与 JUDGE_* 同）：
    # 图片版文件抓回来的是页面壳（CSS/JS 残渣，CJK 可达 555 但句末标点 0）、薄壳转发通知
    # 只有百余字——laya 只看标题拦不住。阈值由 scripts/test_ingest_filter.py 用真实链接校准
    # （2026-10-13：好例 cjk≥1062 / 句末≥29，坏例 cjk≤642 / 句末≤1，空档巨大）。
    pipeline_min_body_chars: int = 200  # 清洗后正文 CJK 字数下限
    pipeline_min_sent_enders: int = 5  # 清洗后正文句末标点（。！？；）数下限
    pipeline_preflight_concurrency: int = 4  # 正文预检并发抓全文的篇数（信号量封顶）

    # --- MCP: central-policy（China-Central-Policy-MCP · 国务院政策检索，node stdio 启动，无需 API Key）---
    mcp_policy_enabled: bool = True
    mcp_policy_command: str = "node"
    mcp_policy_args: list[str] = [_DEFAULT_POLICY_MCP_ENTRY]

    # --- 入库判别服务（judge · Phase 13）：检索阶段对候选篇目（仅标题）做「建议/不建议入库」标注 ---
    # 注意：这组走 Settings（进程启动时读取），改 .env 需重启服务（与 PIPELINE_MODELS 的热读不同）。
    # 服务不可达 / 超时 → fail-open：全部按「建议入库」处理，只在服务端日志告警，绝不阻断抓取主流程。
    judge_enabled: bool = True
    judge_url: str = "http://192.168.16.201:8084/v1/systemone"
    judge_timeout_seconds: int = 5  # 单篇判别请求超时秒数
    judge_concurrency: int = 4  # 并发判别篇数（信号量封顶）
    # 提示词写法与 state 组成是双轴矩阵实测出来的（scripts/test_judge.py，2026-10-13 两轮
    # 共 16 组合）：「疑问句+枚举」× state=仅标题 8/9，且唯一误判方向安全（函被判入库，
    # 只是在确认面板多一行）；旧的「文种直判」对长标题/含函文号只有 4/9——文号里的
    # 「函」会把模型带偏。该 RL 模型对措辞极敏感，改前先跑一遍矩阵，别凭语感改。
    # 默认文案现在住在 prompts/judge.md（留空 = 用它）；这里填了才覆盖它。
    judge_instructions: str = ""
    judge_keep_choice: str = "入库"  # criteria[0]：判到它 → 正常入库
    judge_drop_choice: str = "不入库"  # criteria[1]：判到它 → 打「不建议入库」标记

    # --- 网站来源接入（Phase 14）：受控抓取（app/sources/net.py）与来源勘察 ---
    # 这组走 Settings（进程启动时读取），改 .env 需重启（与 PIPELINE_MODELS 的热读不同）。
    source_fetch_timeout: float = 20.0  # 单次 HTTP 请求超时秒数（含详情页与附件）
    source_fetch_max_bytes: int = 4_000_000  # 单个响应体字节上限，超出截断（防超大文件撑爆内存）
    source_fetch_max_redirects: int = 5  # 重定向跳数上限（每一跳都要过一遍安全复验）
    source_fetch_concurrency: int = 4  # 全局同时在飞的请求数上限（信号量）
    source_request_interval_ms: int = 800  # 同一主机两次请求的最小间隔（礼貌抓取；0 = 不限）
    source_respect_robots: bool = True  # 是否遵守 robots.txt（用户要求遵守站点访问许可）
    source_user_agent: str = "AgentDemo-SourceOnboarding/1.0 (+policy ingestion; contact: local demo)"
    # 允许访问私网 / 本机地址。**默认 false**：抓取目标来自用户提交的 URL，必须防 SSRF。
    # 只在本机联调或确需抓受信内网站点时打开（会同时影响 check_url 与逐跳复验）。
    source_allow_private_hosts: bool = False
    source_list_cache_ttl: float = 300.0  # 列表页缓存存活秒数（同一次检索内避免重复取同一页）
    # 试运行 / 附件归档产物的落盘目录（相对仓库根；留空 = 不落盘，只在库里记 URL 与哈希）
    source_artifact_dir: str = "server/.artifacts"
    source_max_artifacts_per_run: int = 20  # 单次试运行最多归档多少个附件（控体积与时长）

    # --- 无头浏览器抓取（Phase 24）：Playwright + Chromium，可选依赖 `browser` -----------
    # 未安装 Playwright 时服务照常启动：`BROWSER_ENABLED=true` 但包缺失只会让浏览器来源
    # 从下拉框消失（GET /api/config/features 报 browser_available=false）。
    browser_enabled: bool = False  # 总开关；安装后还要显式置 true
    browser_headless: bool = True  # 调试时可设 false 看到 Chromium 窗口（服务器上别关）
    browser_max_sessions: int = 2  # 同时打开的 BrowserContext 上限（交互会话 + 取全文共用）
    browser_max_actions: int = 20  # 单次运行的动作预算（点击/输入/滚动/抽取都算）
    browser_max_pages: int = 10  # 单次运行最多打开多少个页面
    browser_max_extracts: int = 10  # 单次运行最多抽取多少篇正文
    browser_timeout_seconds: int = 300  # 单次运行的总时长上限（超时后工具拒绝继续操作）
    browser_navigation_timeout_ms: int = 30_000  # 单次导航超时（毫秒）
    # 发现循环用的模型名；留空 = 对话模型 MODEL_NAME。**必须支持工具调用**，
    # 所以这里只换模型名、不接 PIPELINE_MODELS 的自定义端点（那个注册表是给提取用的）。
    browser_model: str | None = None
    # 可选域名白名单（空 = 任意公网站点）。环境变量写法为 JSON 数组：
    # BROWSER_ALLOWED_DOMAINS=["www.news.cn","gov.cn"]；命中域及其子域才允许访问。
    browser_allowed_domains: list[str] = []

    # --- 可观测性：Langfuse / Litefuse（Phase 21）---
    # **自托管实例**（本仓默认 http://localhost:3000），trace 只发往这里，不出本机；
    # Litefuse 与 Langfuse 的 SDK 兼容，所以沿用 LANGFUSE_* 这套变量名。
    # 本组走 Settings（进程启动时读取），改 .env **需重启**（与 PIPELINE_MODELS 的热读不同）。
    # 降级口径与 MCP / judge 一致：**观测层坏了绝不拖垮业务**——下面任一条件不满足就整体
    # 不挂载（对话与抓取照常跑），只在启动日志里说一句：
    #   ① LANGFUSE_ENABLED=false；② PUBLIC_KEY / SECRET_KEY 任一为空；③ 没装 langfuse 包。
    langfuse_enabled: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # 服务地址。**两个变量名都收**：官方 LangChain 集成文档用 LANGFUSE_HOST，
    # 「数据区域」文档用 LANGFUSE_BASE_URL（本仓 .env 用的就是后者）——SDK 究竟认哪个
    # 随版本变，这里先归一成一个字段，由 app/tracing.py 统一喂给 SDK。
    langfuse_host: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ---------- 提取模型注册表（「高级设置 → 提取模型」下拉框数据源） ----------


class ModelRegistryEntry(BaseModel):
    """`PIPELINE_MODELS` JSON 数组里的一项（全部走 **Anthropic 兼容协议**，统一 ChatAnthropic）。

    - `id`         稳定标识，落库到 `crawl_task.pipeline_model` / `schedule_job.pipeline_model`；
    - `model`      发往 API 的模型名（也会写进 extraction.model 做溯源）；
    - `base_url`   Anthropic 兼容端点；**不填 = 复用 ANTHROPIC_BASE_URL 网关**（如 Kimi）。
                   DeepSeek 填 https://api.deepseek.com/anthropic；
    - `api_key_env` 从哪个环境变量取 key 做 x-api-key 认证；**不填 = 复用 ANTHROPIC_*
                   网关认证**（AUTH_TOKEN Bearer / ANTHROPIC_API_KEY）；
    - `max_tokens` 不填用 .env 的 MAX_TOKENS；
    - `thinking`   是否保留思考模式，**默认 False（关）**——提取只要结构化结果，思考又慢
                   又烧 token；需要时在项里写 "thinking": true 交给服务端默认。
    """

    id: str
    label: str
    model: str
    base_url: str | None = None  # 不填 = 复用 ANTHROPIC_BASE_URL 网关
    api_key_env: str | None = None  # 不填 = 复用 ANTHROPIC_* 网关认证；DeepSeek 用 DEEPSEEK_API_KEY
    max_tokens: int | None = None  # 不填用 .env 的 MAX_TOKENS
    thinking: bool = False  # 默认关思考：thinking={"type": "disabled"}（Anthropic 协议统一参数）


def _live_env(name: str) -> str:
    """热读一个配置项：真实 shell 环境变量 > **当前** .env 文件 > 启动时灌进 os.environ 的旧值。

    供 `PIPELINE_MODELS` 注册表及其 key 使用——改完 .env **无需重启**，下一次读取即生效。
    （直接读 os.environ 不行：它混着启动时从旧 .env 灌入的值，改文件后不会跟着变。）
    """
    fresh = dotenv_values(_REPO_ROOT / ".env")
    value = _REAL_ENV.get(name) or fresh.get(name) or os.environ.get(name) or ""
    return value.strip()


def _default_registry() -> list[ModelRegistryEntry]:
    """未配置 PIPELINE_MODELS 时的兜底：单模型，行为与历史版本完全一致。"""
    s = get_settings()
    return [
        ModelRegistryEntry(
            id="default", label=f"默认（{s.model_name}）", model=s.model_name
        )
    ]


def get_model_registry() -> list[ModelRegistryEntry]:
    """解析 `PIPELINE_MODELS`（**每次调用都热读 .env**，改完无需重启服务）。

    解析失败 / 为空时告警并回退单模型，不让整个服务起不来。故意不加缓存——
    调用频率是「每次建任务/每篇提取」量级，读一个小文件的开销可以忽略。
    """
    raw = _live_env("PIPELINE_MODELS")
    if not raw:
        return _default_registry()
    try:
        entries = [ModelRegistryEntry.model_validate(item) for item in json.loads(raw)]
        # 按 id 保序去重（同一个 id 出现两次时下拉框会两个都选中）
        seen: set[str] = set()
        unique = [e for e in entries if not (e.id in seen or seen.add(e.id))]
        if unique:
            return unique
        logger.warning("PIPELINE_MODELS 是空数组，回退默认模型注册表")
    except Exception as exc:  # noqa: BLE001 - 配置写错不该让服务起不来
        logger.warning("PIPELINE_MODELS 解析失败（%s），回退默认模型注册表", exc)
    return _default_registry()


def resolve_model_entry(model_id: str | None) -> ModelRegistryEntry:
    """按 id 取注册表项；None / 找不到（.env 后来改过）时回退第一项（= 默认模型）。"""
    registry = get_model_registry()
    if model_id:
        for entry in registry:
            if entry.id == model_id:
                return entry
        logger.warning(
            "未知的提取模型 id：%s（PIPELINE_MODELS 改过？），回退默认 %s", model_id, registry[0].id
        )
    return registry[0]


def resolve_model_api_key(entry: ModelRegistryEntry) -> str:
    """注册表项自带 key 时从 `api_key_env` 取（同样热读：换 key 改 .env 即生效，不用重启）。"""
    return _live_env(entry.api_key_env or "DEEPSEEK_API_KEY")
