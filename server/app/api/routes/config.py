"""只读配置端点：把生效的流水线参数透出给前端「高级设置」面板。

修改参数仍走根目录 `.env`（`PIPELINE_*`）+ 重启服务；这里只做展示，不接受写入。
"""

from fastapi import APIRouter

from app.schemas.config import PipelineConfig

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/pipeline", response_model=PipelineConfig)
async def get_pipeline_config() -> PipelineConfig:
    """当前生效的抓取 / 提取流水线参数（进程启动时从 .env 读入）。"""
    return PipelineConfig.from_settings()
