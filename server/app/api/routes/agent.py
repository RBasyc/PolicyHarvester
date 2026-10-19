"""Agent 对话路由。

- POST /api/chat        一次性返回完整回复
- POST /api/chat/stream SSE 流式返回（逐 token）

流式这版与 /api/tasks/{id}/stream、/api/sources/imports/{id}/stream 同一套协议
（`data: {...}\n\n` + 15s 一条 `: ping` 注释行 + 三个反缓冲 header）。
**只推 token**：工具调用期间不发事件（用户决定，见 TODO Phase 19），前端靠本地
「正在检索政策…」等待态兜住那 10–20 秒静默。
"""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agent.graph import get_graph
from app.schemas.chat import ChatRequest, ChatResponse
from app.tracing import isolated_context, trace_config

router = APIRouter(prefix="/api", tags=["agent"])

# 心跳间隔：工具执行期没有 token，靠注释行保活（与 tasks.py 的取值一致）
_STREAM_HEARTBEAT_SECONDS = 15.0


def _chat_config(thread_id: str) -> dict:
    """对话用的 LangGraph config：`thread_id` 记会话 + 把这一轮挂到 Langfuse。

    未启用可观测性时 `trace_config()` 返回 `{}`，`update` 是空操作——这里不写 if。
    `session_id` 用 `thread_id`，于是同一会话的多轮对话在 Langfuse 里归到一个 session。
    """
    config: dict = {"configurable": {"thread_id": thread_id}}
    config.update(trace_config(name="chat", session_id=thread_id, tags=["chat"]))
    return config


def _sse(payload: dict) -> str:
    """拼一条 SSE 帧：一行 `data: {...}` + 空行（与 tasks.py 的 `_sse` 同款）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
    config = _chat_config(req.thread_id)
    # 独立执行上下文：避免继承上一条请求残留的 OTel 活动 span（见 tracing.isolated_context）
    result = await asyncio.create_task(
        graph.ainvoke(
            {"messages": [HumanMessage(content=req.message)]},
            config=config,
        ),
        context=isolated_context(),
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
    - done:   {"type": "done"}                      正常收尾（客户端取消时不发）
    - error:  {"type": "error", "message": "..."}
    """
    graph = get_graph()
    config = _chat_config(req.thread_id)

    async def event_source() -> AsyncGenerator[str, None]:
        # 图的 astream 由后台 task 推进队列，响应侧只在 `queue.get()` 上做超时——
        # **不能**写成 `asyncio.wait_for(agen.__anext__(), 15)`：超时会把 CancelledError
        # 抛进异步生成器内部，直接打断正在跑的图。
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def pump() -> None:
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
                            await queue.put(_sse({"type": "token", "content": text}))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 流内错误以事件形式下发
                await queue.put(_sse({"type": "error", "message": str(exc)}))
            finally:
                await queue.put(None)  # 哨兵：告诉消费端「图跑完了」

        # 独立执行上下文：避免继承上一条请求残留的 OTel 活动 span（见 tracing.isolated_context）
        task = asyncio.create_task(pump(), context=isolated_context())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=_STREAM_HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # 工具静默期保活
                    continue
                if item is None:
                    break
                yield item
            # done 只在正常收尾时发：**不放在 finally 里**——客户端断开时协程被取消，
            # 取消路径上再 yield 会撞 `RuntimeError: async generator ignored GeneratorExit`
            yield _sse({"type": "done"})
        except asyncio.CancelledError:
            raise  # 用户点了「停止」/ 关了页面：对方已经走了，不发收尾帧
        finally:
            if not task.done():
                task.cancel()  # 连带中止 astream 与在飞的模型请求

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉反向代理缓冲，token 才能逐个到达
        },
    )
