"""API 请求 / 响应模型。"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求。thread_id 相同的多条请求共享同一段对话记忆。"""

    message: str = Field(..., description="用户消息")
    thread_id: str = Field(default="default", description="会话 ID")


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
