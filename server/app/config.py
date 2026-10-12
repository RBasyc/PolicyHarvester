"""配置管理：从环境变量 / .env 文件读取配置。"""

from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 先把 .env 加载进 os.environ：
# anthropic SDK 会自动读取 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL 等变量，
# 而 pydantic-settings 只把 .env 读进 Settings 对象，不会写回环境。
load_dotenv()


class Settings(BaseSettings):
    """应用全局配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
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

    # --- MCP: chuance-policy（川策智行 · 权威政策检索，stdio/uvx 启动，无需 API Key）---
    mcp_chuance_enabled: bool = True
    mcp_chuance_command: str = "uvx"
    mcp_chuance_args: list[str] = [
        "--from",
        "git+https://github.com/wenyi3370-lgtm/chuance-policy-mcp.git",
        "chuance-policy-mcp",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
