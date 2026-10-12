"""配置相关 DTO（只读展示，修改仍走根目录 .env + 重启）。"""

from pydantic import BaseModel


class PipelineConfig(BaseModel):
    """抓取 / 提取流水线的生效参数（来自 .env 的 `PIPELINE_*`，只读）。"""

    pipeline_max_policies: int
    pipeline_per_keyword_limit: int
    pipeline_extract_concurrency: int
    pipeline_max_body_chars: int
    pipeline_mcp_timeout: int

    @classmethod
    def from_settings(cls) -> "PipelineConfig":
        from app.config import get_settings

        s = get_settings()
        return cls(
            pipeline_max_policies=s.pipeline_max_policies,
            pipeline_per_keyword_limit=s.pipeline_per_keyword_limit,
            pipeline_extract_concurrency=s.pipeline_extract_concurrency,
            pipeline_max_body_chars=s.pipeline_max_body_chars,
            pipeline_mcp_timeout=s.pipeline_mcp_timeout,
        )
