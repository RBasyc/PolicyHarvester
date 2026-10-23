"""API 请求 / 响应模型。"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求。thread_id 相同的多条请求共享同一段对话记忆。"""

    message: str = Field(..., description="用户消息")
    thread_id: str = Field(default="default", description="会话 ID")
    # 浏览器工具不再按请求开关：引擎可用（BROWSER_ENABLED=true 且装了 playwright）时
    # 每轮都挂上 browser_*；不可用时自然没有工具。浏览器会话仍按请求创建、请求结束即关闭，
    # 不进 InMemorySaver，也不跨 thread_id 复用。


class ChatUsage(BaseModel):
    """一次模型调用的用量（网关返回什么算什么；拿不到时整体为 None）。"""

    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
    # 非流式对话只做一次最终模型调用；流式对话逐次通过 `usage` SSE 事件上报
    usage: ChatUsage | None = None
