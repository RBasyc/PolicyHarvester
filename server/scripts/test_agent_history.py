"""对话历史配对校验的离线测试（不连数据库、不调模型）。

直接运行：uv run python scripts/test_agent_history.py

覆盖 `app/agent/history.py::sanitize_tool_pairs`：
1. 完整的 assistant tool_calls + ToolMessage → 原样保留
2. 缺一条工具结果的半轮 → 整轮丢弃（DeepSeek 400 的根因）
3. 孤立 ToolMessage → 丢弃
4. 没有 tool_calls 的普通消息 → 原样保留
5. 并行调用只有部分结果 → 整轮丢弃
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.history import sanitize_tool_pairs


def _ai(*ids: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "get_current_time", "args": {}, "id": call_id} for call_id in ids
        ],
    )


def check(name: str, got: list, want: list) -> bool:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")
    if not ok:
        print(f"    got : {[type(m).__name__ for m in got]}")
        print(f"    want: {[type(m).__name__ for m in want]}")
    return ok


def main() -> int:
    print("=== 1. 完整配对 ===")
    ai = _ai("call_1")
    tool = ToolMessage(content="ok", tool_call_id="call_1")
    human = HumanMessage(content="你好")
    passed = check("保留完整半轮", sanitize_tool_pairs([human, ai, tool]), [human, ai, tool])

    print("=== 2. 缺工具结果 ===")
    passed &= check("丢弃悬空半轮", sanitize_tool_pairs([human, ai]), [human])

    print("=== 3. 孤立 ToolMessage ===")
    passed &= check("丢弃孤立工具结果", sanitize_tool_pairs([human, tool]), [human])

    print("=== 4. 普通消息 ===")
    plain = [human, AIMessage(content="hi")]
    passed &= check("普通对话原样保留", sanitize_tool_pairs(plain), plain)

    print("=== 5. 并行调用只回来一条 ===")
    ai2 = _ai("call_a", "call_b")
    tool_a = ToolMessage(content="a", tool_call_id="call_a")
    passed &= check("两条调用一条结果 → 整轮丢弃", sanitize_tool_pairs([human, ai2, tool_a]), [human])

    print("\n结果：", "全部通过" if passed else "有失败项")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
