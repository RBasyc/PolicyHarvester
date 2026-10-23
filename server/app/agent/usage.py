"""从模型回复里提取本轮用量：token 数、缓存命中、思考 token。

不同 OpenAI 兼容网关把用量塞在不同位置，这里统一收敛成一个扁平字典：

- LangChain 标准字段 `usage_metadata`（`input_tokens` / `output_tokens` /
  `input_token_details.cache_read` / `output_token_details.reasoning`）；
- OpenAI 原始 `response_metadata.token_usage`（`prompt_tokens` / `completion_tokens` /
  `prompt_tokens_details.cached_tokens`）；
- DeepSeek 的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`；
- 部分网关直接给顶层 `cached_tokens`。

**拿不到就不编**：一项都没有时返回 `None`，调用方不发 `usage` 事件——前端那行小字
只显示次数与耗时，不会出现一串 0。
"""

from __future__ import annotations

from typing import Any


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _first_int(*values: Any) -> int:
    """取第一个非零的整数值（0 / None 跳过）。"""
    for value in values:
        number = _as_int(value)
        if number:
            return number
    return 0


def extract_usage(message: Any) -> dict[str, Any] | None:
    """`AIMessage` / `AIMessageChunk` → 扁平用量字典；网关没返回用量时 `None`。"""
    usage = getattr(message, "usage_metadata", None) or {}
    meta = getattr(message, "response_metadata", None) or {}
    raw = meta.get("token_usage") or meta.get("usage") or {}

    input_details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}
    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}

    input_tokens = _first_int(usage.get("input_tokens"), raw.get("prompt_tokens"))
    output_tokens = _first_int(usage.get("output_tokens"), raw.get("completion_tokens"))
    if not input_tokens and not output_tokens:
        return None

    cached_tokens = _first_int(
        input_details.get("cache_read"),
        raw.get("prompt_cache_hit_tokens"),  # DeepSeek
        prompt_details.get("cached_tokens"),  # OpenAI / 多数兼容网关
        raw.get("cached_tokens"),
    )
    # 缓存命中数不可能超过输入总数；个别网关把「未命中」也塞进来时防一手
    cached_tokens = min(cached_tokens, input_tokens)

    reasoning_tokens = _first_int(
        output_details.get("reasoning"),
        completion_details.get("reasoning_tokens"),
    )

    model = (meta.get("model_name") or meta.get("model") or "") or None
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
    }
