"""FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent import tools as tools_registry
from app.agent.graph import get_graph
from app.agent.mcp import load_mcp_tools
from app.api.routes import agent, config, filters, health, policies, tasks
from app.config import get_settings
from app.db import repositories as repo
from app.db.pool import close_pool, open_pool
from app.pipeline.crawl_graph import shutdown_pipelines

logger = logging.getLogger(__name__)

# 上个进程被强杀（Ctrl-C 两次 / 崩溃）时，残留的 pending/running 任务没有对应的
# asyncio 流水线了；启动时统一落成 failed，否则页面上会永远停在「运行中」且无法重新开始。
_STALE_TASK_REASON = "服务重启导致任务中断，请重新开始抓取"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 数据库连接池（连不上只告警，不阻断启动）
    await open_pool()
    # 收尾上次进程遗留的僵尸任务（库不可用时跳过，与 open_pool 的「只告警」保持一致）
    try:
        stale = await repo.fail_stale_tasks(_STALE_TASK_REASON)
        if stale:
            logger.info("启动收尾：任务 %s 上次未跑完，已标记为 failed", stale)
    except Exception:  # noqa: BLE001 - 数据库不可用不应阻断服务启动
        logger.warning("启动收尾失败：无法清理残留任务", exc_info=True)
    # 先加载 MCP 工具（失败时内部已降级为空列表），再预编译图，
    # 保证图里的 ToolNode / bind_tools 拿到完整工具表
    tools_registry.MCP_TOOLS.extend(await load_mcp_tools())
    get_graph()
    yield
    # 关停顺序：先取消在跑的抓取流水线，再关连接池（否则流水线会用到已关闭的池）
    await shutdown_pipelines()
    await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Agent Demo", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """把 FastAPI 默认的 422 错误列表压成 `{"detail": "..."}`（契约见 PLAN.md 第三节）。"""
        errors = exc.errors()
        if not errors:
            return JSONResponse(status_code=422, content={"detail": "请求参数校验失败"})
        first = errors[0]
        loc = ".".join(
            str(part) for part in first.get("loc", ()) if part not in ("body", "query", "path")
        )
        msg = first.get("msg", "请求参数校验失败").removeprefix("Value error, ")
        return JSONResponse(
            status_code=422,
            content={"detail": f"{loc}: {msg}" if loc else msg},
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(agent.router)
    app.include_router(tasks.router)
    app.include_router(policies.router)
    app.include_router(filters.router)
    app.include_router(config.router)

    return app


app = create_app()
