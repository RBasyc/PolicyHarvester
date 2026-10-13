"""配置相关 DTO（只读展示，修改仍走根目录 .env + 重启）。"""

from pydantic import BaseModel


class ModelOption(BaseModel):
    """「高级设置 → 提取模型」下拉框的一项（不含密钥等敏感信息）。"""

    id: str
    label: str


class PipelineConfig(BaseModel):
    """抓取 / 提取流水线的生效参数（来自 .env 的 `PIPELINE_*`，只读）。"""

    pipeline_max_policies: int
    pipeline_per_keyword_limit: int
    pipeline_extract_concurrency: int
    pipeline_max_body_chars: int
    pipeline_mcp_timeout: int
    # 提取模型注册表（.env 的 PIPELINE_MODELS）+ 默认模型 id（列表第一项，null 覆盖时用它）
    models: list[ModelOption]
    default_model: str

    @classmethod
    def from_settings(cls) -> "PipelineConfig":
        from app.config import get_model_registry, get_settings

        s = get_settings()
        registry = get_model_registry()
        return cls(
            pipeline_max_policies=s.pipeline_max_policies,
            pipeline_per_keyword_limit=s.pipeline_per_keyword_limit,
            pipeline_extract_concurrency=s.pipeline_extract_concurrency,
            pipeline_max_body_chars=s.pipeline_max_body_chars,
            pipeline_mcp_timeout=s.pipeline_mcp_timeout,
            models=[ModelOption(id=e.id, label=e.label) for e in registry],
            default_model=registry[0].id,
        )
