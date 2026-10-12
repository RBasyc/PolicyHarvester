"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent import tools as tools_registry
from app.agent.graph import get_graph
from app.agent.mcp import load_mcp_tools
from app.api.routes import agent, health, policies, tasks
from app.config import get_settings
from app.db.pool import close_pool, open_pool
from app.pipeline.crawl_graph import shutdown_pipelines


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 数据库连接池（连不上只告警，不阻断启动）
    await open_pool()
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

    return app


app = create_app()
