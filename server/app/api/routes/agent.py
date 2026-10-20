"""Agent 对话路由。

- POST /api/chat        一次性返回完整回复
- POST /api/chat/stream SSE 流式返回（逐 token）

流式这版与 /api/tasks/{id}/stream、/api/sources/imports/{id}/stream 同一套协议
（`data: {...}\n\n` + 15s 一条 `: ping` 注释行 + 三个反缓冲 header）。
事件：token（模型输出片段）+ tool（工具调用 start/done，普通工具由 `call_tools`
节点发、浏览器工具在工具函数内部自发）；心跳注释行兜住工具执行期的静默。
"""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agent.graph import get_graph
from app.agent.nodes import chat_emit_var
from app.browser import runtime as browser_runtime
from app.browser.context import BrowserRun, browser_run_var
from app.config import get_settings
from app.schemas.chat import ChatRequest, ChatResponse
from app.tracing import isolated_context, trace_config

router = APIRouter(prefix="/api", tags=["agent"])

# 心跳间隔：工具执行期没有 token，靠注释行保活（与 tasks.py 的取值一致）
_STREAM_HEARTBEAT_SECONDS = 15.0


def _chat_config(thread_id: str, browser_enabled: bool = False) -> dict:
    """对话用的 LangGraph config：`thread_id` 记会话 + 把这一轮挂到 Langfuse。

    未启用可观测性时 `trace_config()` 返回 `{}`，`update` 是空操作——这里不写 if。
    `session_id` 用 `thread_id`，于是同一会话的多轮对话在 Langfuse 里归到一个 session。

    浏览器模式下每次工具往返算两步（agent → tools），默认的递归上限 25 会在多步浏览时
    过早中断；按动作预算放宽（至少保留 25）。
    """
    config: dict = {"configurable": {"thread_id": thread_id}}
    config.update(trace_config(name="chat", session_id=thread_id, tags=["chat"]))
    if browser_enabled:
        config["recursion_limit"] = max(
            25, 2 * (get_settings().browser_max_actions + 4)
        )
    return config


def _ensure_browser_available() -> None:
    """开了「浏览网页」但引擎不可用时，在建立 SSE 之前就用 400 说明安装方式。"""
    reason = browser_runtime.unavailable_reason()
    if reason:
        raise HTTPException(status_code=400, detail=reason)


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
    config = _chat_config(req.thread_id, req.browser_enabled)

    run: BrowserRun | None = None
    ctx = isolated_context()
    if req.browser_enabled:
        _ensure_browser_available()
        run = BrowserRun(mode="chat")
        # 在交给图任务的同一个 Context 里 set，工具的协程才能读到这个 run
        ctx.run(browser_run_var.set, run)

    try:
        # 独立执行上下文：避免继承上一条请求残留的 OTel 活动 span（见 tracing.isolated_context）
        result = await asyncio.create_task(
            graph.ainvoke(
                {"messages": [HumanMessage(content=req.message)]},
                config=config,
            ),
            context=ctx,
        )
    finally:
        if run is not None:
            await run.aclose()  # 成功/异常/取消都关闭这个 fresh context

    reply = next(
        m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and m.content
    )
    return ChatResponse(thread_id=req.thread_id, reply=_extract_text(reply.content))


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE 流式对话：每条事件为一行 `data: {...}\\n\\n`。

    事件类型：
    - token:  {"type": "token", "content": "..."}   模型输出片段
    - tool:   {"type": "tool", "name", "detail", "status": "start"|"done", "ok"}  工具调用
    - done:   {"type": "done"}                      正常收尾（客户端取消时不发）
    - error:  {"type": "error", "message": "..."}
    """
    graph = get_graph()
    config = _chat_config(req.thread_id, req.browser_enabled)
    if req.browser_enabled:
        # 在返回 StreamingResponse 之前报 400：SSE 一旦开始，前端只能看到 error 事件，
        # 拿不到「怎么安装」的 HTTP 状态。
        _ensure_browser_available()

    async def event_source() -> AsyncGenerator[str, None]:
        # 图的 astream 由后台 task 推进队列，响应侧只在 `queue.get()` 上做超时——
        # **不能**写成 `asyncio.wait_for(agen.__anext__(), 15)`：超时会把 CancelledError
        # 抛进异步生成器内部，直接打断正在跑的图。
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        run: BrowserRun | None = None
        ctx = isolated_context()
        # 工具事件发射器：普通工具（call_tools 节点）经 chat_emit_var 拿它；浏览器工具
        # 经 BrowserRun.emit 拿它——同一条队列，两种来源（见 app/agent/nodes.py）。
        emit = lambda payload: queue.put_nowait(_sse(payload))
        ctx.run(chat_emit_var.set, emit)
        if req.browser_enabled:
            # emit 是同步回调，流的 pump 与工具在同一条链上
            run = BrowserRun(mode="chat", emit=emit)
            ctx.run(browser_run_var.set, run)

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
        task = asyncio.create_task(pump(), context=ctx)
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
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            if run is not None:
                # 客户端断开 / 停止生成时也要关浏览器 context；shield 让关闭任务在
                # 取消路径上继续跑完（槽位在 close 里归还，不能泄漏）
                try:
                    await asyncio.shield(run.aclose())
                except Exception:  # noqa: BLE001 - 收尾失败不改变已经发生的取消
                    pass

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉反向代理缓冲，token 才能逐个到达
        },
    )
