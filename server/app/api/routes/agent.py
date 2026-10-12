"""Agent 对话路由。

- POST /api/chat        一次性返回完整回复
- POST /api/chat/stream SSE 流式返回（逐 token）
"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agent.graph import get_graph
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["agent"])


def _extract_text(content) -> str:
    """从消息 content 中提取纯文本。

    content 可能是 str，也可能是内容块列表（含 thinking 等块），只保留 text 块。
    """
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """非流式对话：等图执行完毕后返回最终回复。"""
    graph = get_graph()
    config = {"configurable": {"thread_id": req.thread_id}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=req.message)]},
        config=config,
    )
    reply = next(
        m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and m.content
    )
    return ChatResponse(thread_id=req.thread_id, reply=_extract_text(reply.content))


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE 流式对话：每条事件为一行 `data: {...}\\n\\n`。

    事件类型：
    - token:  {"type": "token", "content": "..."}   模型输出片段
    - done:   {"type": "done"}                      流结束
    - error:  {"type": "error", "message": "..."}
    """
    graph = get_graph()
    config = {"configurable": {"thread_id": req.thread_id}}

    async def event_source() -> AsyncGenerator[str, None]:
        try:
            async for chunk, _meta in graph.astream(
                {"messages": [HumanMessage(content=req.message)]},
                config=config,
                stream_mode="messages",
            ):
                # 只推送模型生成的文本片段，跳过 thinking / 工具消息等
                if isinstance(chunk, AIMessageChunk):
                    text = _extract_text(chunk.content)
                    if text:
                        payload = {"type": "token", "content": text}
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - 流内错误以事件形式下发
            payload = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(event_source(), media_type="text/event-stream")
