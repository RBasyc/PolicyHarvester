"""只读配置端点：把生效的流水线参数透出给前端「高级设置」面板。

修改参数仍走根目录 `.env`（`PIPELINE_*`）+ 重启服务；这里只做展示，不接受写入。
"""

from fastapi import APIRouter

from app.browser import runtime as browser_runtime
from app.config import get_settings
from app.schemas.config import FeatureFlags, PipelineConfig

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/pipeline", response_model=PipelineConfig)
async def get_pipeline_config() -> PipelineConfig:
    """当前生效的抓取 / 提取流水线参数（进程启动时从 .env 读入）。"""
    return PipelineConfig.from_settings()


@router.get("/features", response_model=FeatureFlags)
async def get_features() -> FeatureFlags:
    """功能开关（目前只有无头浏览器）。

    `browser_available` = `BROWSER_ENABLED=true` 且装了 playwright；**不启动 Chromium**。
    前端只用它决定是否显示「浏览网页」开关；真正开启浏览时后端还会再校验一次。
    """
    return FeatureFlags(
        browser_available=browser_runtime.available(),
        browser_enabled=get_settings().browser_enabled,
    )
