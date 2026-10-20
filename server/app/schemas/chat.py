"""API 请求 / 响应模型。"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求。thread_id 相同的多条请求共享同一段对话记忆。"""

    message: str = Field(..., description="用户消息")
    thread_id: str = Field(default="default", description="会话 ID")
    # 是否给这一轮挂上无头浏览器工具（前端「浏览网页」开关）。浏览器会话按请求创建、
    # 请求结束即关闭，不进 InMemorySaver，也不跨 thread_id 复用。
    browser_enabled: bool = Field(
        default=False, description="启用无头浏览器工具（需 BROWSER_ENABLED=true）"
    )


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
