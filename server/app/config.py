"""配置管理：从环境变量 / .env 文件读取配置。"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录（server/app/config.py -> 上三级）
_REPO_ROOT = Path(__file__).resolve().parents[2]

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

    # --- MCP: central-policy（China-Central-Policy-MCP · 国务院政策检索，node stdio 启动，无需 API Key）---
    mcp_policy_enabled: bool = True
    mcp_policy_command: str = "node"
    mcp_policy_args: list[str] = [_DEFAULT_POLICY_MCP_ENTRY]


@lru_cache
def get_settings() -> Settings:
    return Settings()
