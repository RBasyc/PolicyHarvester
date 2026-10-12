"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent import tools as tools_registry
from app.agent.graph import get_graph
from app.agent.mcp import load_mcp_tools
from app.api.routes import agent, health
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 先加载 MCP 工具（失败时内部已降级为空列表），再预编译图，
    # 保证图里的 ToolNode / bind_tools 拿到完整工具表
    tools_registry.MCP_TOOLS.extend(await load_mcp_tools())
    get_graph()
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Agent Demo", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(agent.router)

    return app


app = create_app()
