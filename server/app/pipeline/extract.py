"""单篇政策的结构化提取：正文 → 「事实 / 信息 / 知识」三区。

复用 `app/agent/nodes.py` 的 `_build_llm()`（沿用同一份模型配置，不引入新模型/依赖）。

两条路径：
1. `with_structured_output` —— 走工具调用拿严格 schema（首选）；
2. 网关不支持工具调用时回退到「要求输出 JSON」的纯文本提示 + 宽松解析。

两条都失败则抛异常，由流水线把该篇标记为 `failed`（不影响其他篇）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agent.nodes import _build_llm
from app.config import get_settings

logger = logging.getLogger(__name__)


class FactPayload(BaseModel):
    """一条事实明细。"""

    label: str = Field(description="维度标签，只能取：时间 / 发文机构 / 涉及机构 / 政策动作 / 数字指标 / 资金来源 / 适用对象")
    content: str = Field(description="该维度下的具体事实，一句话，必须来自原文")


class InfoPayload(BaseModel):
    """信息区：摘要 + 要点。"""

    summary: str = Field(description="2-3 句话的整体摘要，说清文件要解决什么问题、怎么解决")
    points: list[str] = Field(description="4-6 条要点，每条一句话，覆盖主要举措")


class ConceptPayload(BaseModel):
    """一个背景概念。"""

    term: str = Field(description="政策中出现、但一般读者可能不熟的术语")
    desc: str = Field(description="用一句话解释这个术语")


class KnowledgePayload(BaseModel):
    """知识区：背景概念 + 相关领域 + 延伸解释。"""

    concepts: list[ConceptPayload] = Field(description="2-4 个背景概念")
    fields: list[str] = Field(description="3-5 个相关领域标签，如「产业政策」「人工智能」")
    extension: str = Field(description="1-2 句延伸解释：该政策在政策脉络中的位置与信号")


class ExtractionPayload(BaseModel):
    """一篇政策的完整提取结果。"""

    facts: list[FactPayload] = Field(description="6-10 条事实，覆盖时间、机构、动作、数字指标")
    info: InfoPayload
    knowledge: KnowledgePayload


SYSTEM_PROMPT = """你是政策文件结构化提取助手。用户会给你一篇国务院/部委政策文件的标题与正文，
你要把它拆成「事实 Facts / 信息 Information / 知识 Knowledge」三部分。

要求：
- 全部用简体中文；只依据原文，不得推测或编造原文没有的内容；
- 事实（facts）是可核验的硬信息：时间、发文机构、涉及机构、政策动作、数字指标等，逐条列明；
- 信息（info）是对文件的归纳：摘要 + 要点；
- 知识（knowledge）是背景补充：术语解释、相关领域、该文件在政策脉络中的位置；
- 正文可能被截断，若某一部分在给定正文里找不到依据，就给出更少但更可靠的条目，不要编。
"""


async def extract_policy(*, title: str, body: str, url: str | None = None) -> tuple[ExtractionPayload, str]:
    """提取一篇政策的「事实/信息/知识」。

    返回 `(结果, 使用的模型名)`；失败时抛异常。
    """
    settings = get_settings()
    truncated = body.strip()
    if len(truncated) > settings.pipeline_max_body_chars:
        truncated = truncated[: settings.pipeline_max_body_chars] + "\n…（正文过长已截断）"

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"政策标题：{title}\n"
                f"原文链接：{url or '（无）'}\n\n"
                f"政策正文：\n{truncated}"
            )
        ),
    ]

    llm = _build_llm()
    try:
        structured = llm.with_structured_output(ExtractionPayload)
        result = await structured.ainvoke(messages)
        if isinstance(result, ExtractionPayload):
            return result, settings.model_name
        # 个别网关会把结构化结果退化成 dict
        return ExtractionPayload.model_validate(result), settings.model_name
    except Exception as exc:  # noqa: BLE001 - 回退到纯文本 JSON 路径
        logger.warning("结构化输出失败（%s），回退到 JSON 文本解析", exc)

    payload = await _extract_via_json(llm, messages)
    return payload, settings.model_name


async def _extract_via_json(llm: Any, messages: list) -> ExtractionPayload:
    """回退路径：让模型直接吐 JSON，再宽松解析。"""
    schema_hint = json.dumps(
        ExtractionPayload.model_json_schema(), ensure_ascii=False, indent=2
    )
    instructed = [
        *messages,
        HumanMessage(
            content=(
                "不要解释、不要 Markdown 代码块，只输出一个 JSON 对象，"
                f"严格符合下面的 JSON Schema：\n{schema_hint}"
            )
        ),
    ]
    response = await llm.ainvoke(instructed)
    text = response.content if isinstance(response.content, str) else _blocks_to_text(response.content)
    return ExtractionPayload.model_validate(_loads_lenient(text))


def _blocks_to_text(content: Any) -> str:
    """把内容块列表里的 text 块拼起来（跳过 thinking 等块）。"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _loads_lenient(text: str) -> dict:
    """从模型输出里把 JSON 抠出来：容忍 ```json 围栏与前后废话。"""
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"模型输出里找不到 JSON 对象：{text[:200]}")
    return json.loads(cleaned[start : end + 1])
