"""对话历史的前向校验：保证发给模型的工具调用是**成对**的。

为什么需要这一层
----------------

OpenAI 兼容网关（尤其 DeepSeek）对工具调用协议是硬校验：一条 assistant 消息里
每个 `tool_calls[].id` 后面都必须紧跟一条 `tool_call_id` 相同的 `ToolMessage`，
否则整次请求 400：

    An assistant message with 'tool_calls' must be followed by tool messages
    responding to each 'tool_call_id'. (insufficient tool messages ...)

本项目有一条会制造「半轮」的路径：用户点「停止」/关页面时，SSE 协程取消，
[`api/routes/agent.py`](../api/routes/agent.py) 的 finally 会 `task.cancel()`。
LangGraph 按 superstep 提交 checkpoint——agent 节点返回的 `AIMessage(tool_calls)`
已经落盘，tools 节点还没提交 `ToolMessage` 就被打断了。下一条消息仍用同一个
`thread_id` 时，LangGraph 不会把挂起的 tools 任务补跑（带新 input 的调用按新回合
处理），于是这条孤立的工具调用会一直被带进以后每次请求，线程彻底「中毒」。

因此 `call_model` 在拼请求前先跑一遍 `sanitize_tool_pairs`：

- assistant 的 `tool_calls` **全部**有应答 → 原样保留；
- 缺任何一条应答（或调用缺 id，无法核对）→ 整轮丢弃（连同已拿到的部分结果）——
  宁可不带这半轮，也不伪造一条「工具执行成功/失败」的结果；
- 前面没有 assistant 工具调用的 `ToolMessage` → 丢弃。

校验只作用于**本次请求的视图**，不改 checkpointer 里的历史；代价是 O(n)，对话量级
下可忽略。日志里会留下丢弃条数，便于排查。
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

logger = logging.getLogger(__name__)


def sanitize_tool_pairs(messages: list[AnyMessage]) -> list[AnyMessage]:
    """返回可安全发给模型的副本：assistant 工具调用与 ToolMessage 一一配对。

    `ToolNode` 的结果总是紧跟在触发它的 assistant 消息之后，所以这里只认**紧邻**
    的一段 `ToolMessage`。被丢弃的半轮会记一条 warning，但不抛错——对话要能继续。
    """
    out: list[AnyMessage] = []
    dropped_turns = 0
    dropped_orphans = 0
    i = 0
    while i < len(messages):
        msg = messages[i]

        if isinstance(msg, AIMessage) and msg.tool_calls:
            required: list[str] = []
            for call in msg.tool_calls:
                call_id = call.get("id")
                if not call_id:  # 没有 id 就无法核对配对；这半轮不能发
                    required = []
                    break
                required.append(call_id)

            j = i + 1
            answers: dict[str, ToolMessage] = {}
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                answers.setdefault(messages[j].tool_call_id, messages[j])
                j += 1

            if required and set(required) <= set(answers):
                out.append(msg)
                # 按 tool_calls 顺序输出，一个 id 一条；多出来的结果不带走
                out.extend(answers[call_id] for call_id in dict.fromkeys(required))
            else:
                dropped_turns += 1
            i = j
            continue

        if isinstance(msg, ToolMessage):
            # 没有紧邻的 assistant 工具调用：单独一条 tool 消息同样会被网关拒绝
            dropped_orphans += 1
            i += 1
            continue

        out.append(msg)
        i += 1

    if dropped_turns or dropped_orphans:
        logger.warning(
            "对话历史含悬空的工具调用：丢弃 %d 个半轮、%d 条孤立工具结果"
            "（常见于用户中断/连接断开；见 app/agent/history.py）",
            dropped_turns,
            dropped_orphans,
        )
    return out
