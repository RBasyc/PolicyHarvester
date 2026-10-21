"""单篇政策的结构化提取：正文 → 「事实 / 信息 / 知识」三区。

模型由「高级设置 → 提取模型」按任务选择（`crawl_task.pipeline_model` 存注册表 id，
`app/config.py::get_model_registry()` **热读** `.env` 的 `PIPELINE_MODELS`——改完无需重启）。
注册表统一走 OpenAI 兼容协议（一条 ChatOpenAI 代码路径）：不填 `base_url`/`api_key_env`
的项沿用 .env 的 OPENAI_BASE_URL / OPENAI_API_KEY 网关；自带端点的项（跨网关，如
Kimi 的 `https://api.moonshot.cn/v1`）用它们覆盖。
注册表项默认 `thinking=false`——提取只要结构化结果，不开思考（又快又省）。

两条提取路径：
1. 工具调用拿严格 schema —— 但**不强制** `tool_choice`（见 `_extract_via_tool`）；
2. 模型没调工具时回退到「要求输出 JSON」的纯文本提示 + 宽松解析。

两条都失败则抛异常，由流水线把该篇标记为 `failed`（不影响其他篇）。

另有一个**按需**的小入口 `extract_policy_fields()`：结构解析没拿到发布日期 / 发文机关 /
文号时，流水线单独调它从页面文本里补提（见 `crawl_graph._preflight_bodies` 与
`_backfill_model_fields`）。它不属于上面的三区提取，失败也不影响提取结果；补到的字段分别带
`publish_date_source` / `agency_source` / `doc_no_source` = `'model'` 标记，前端显示「模型提取」。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.agent.nodes import _build_llm
from app.config import ModelRegistryEntry, get_settings, resolve_model_api_key, resolve_model_entry
from app.prompts import load_prompt
from app.sources.util import parse_date

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
    """一条沉淀下来的知识点。

    存库键沿用 `term` / `desc`（见 database/schema.sql 的 `knowledge_concepts`），
    语义是「标题 / 说明」——不是「术语 / 术语解释」。
    """

    term: str = Field(description="知识点标题：一句陈述句，说清一条可供行动参考的结论，不要写术语名")
    desc: str = Field(description="该知识点的依据与做法：结合原文的数字、时限、条件，说明我们能据此做什么")


class KnowledgePayload(BaseModel):
    """知识区：可沉淀的知识点 + 适用领域 + 行动建议。

    存库键沿用 `concepts` / `fields` / `extension`（见 database/schema.sql），
    语义分别对应「知识点 / 适用领域 / 行动建议」。
    """

    concepts: list[ConceptPayload] = Field(description="2-4 条可沉淀的知识点，每条是一句可执行的判断，不要名词解释")
    fields: list[str] = Field(description="3-5 个适用领域标签：这些知识在哪些业务场景下用得上，如「产业政策」「人工智能」")
    extension: str = Field(description="1-2 句行动建议：我们该做什么、要避开什么（窗口期、合规红线、资源投向）")


class ExtractionPayload(BaseModel):
    """一篇政策的完整提取结果。"""

    facts: list[FactPayload] = Field(description="6-10 条事实，覆盖时间、机构、动作、数字指标")
    info: InfoPayload
    knowledge: KnowledgePayload


# 提示词本体在仓库根 prompts/extract.md（见 app/prompts.py）。
# 这里是**模块常量**：import 时读一次，改 md 后要重启服务。
SYSTEM_PROMPT = load_prompt("extract")

# 回退路径（纯文本 JSON）时追加的指令，同样来自 prompts/。
JSON_FALLBACK_INSTRUCTION = load_prompt("extract_json_fallback")

# 元信息兜底抽取的提示词（结构解析拿不到日期/机构/文号时才调用），同样是 import 时读一次。
FIELDS_PROMPT = load_prompt("extract_fields")

# 模型兜底日期的合理区间：发布日期不可能在未来，太早的日期几乎都是页面其他内容
# （版权年、相关链接）被误读。区间外一律当没抽到，不写库。
_MIN_MODEL_DATE_YEAR = 1990

# 机构/文号的长度上限：超长值基本是把整行标签或正文句子误当成了字段。
_MAX_AGENCY_CHARS = 100
_MAX_DOC_NO_CHARS = 60

# gov.cn / 政务模板的零宽字符（前端复制、页面源码里都常见）。
_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"
_STRIP_ZERO_WIDTH = str.maketrans("", "", _ZERO_WIDTH)

# 模型偶尔把「没有」直接写成这些值；它们不是字段内容。
_PLACEHOLDERS = {"无", "暂无", "未知", "null", "none", "n/a", "-", "—", "－"}


@dataclass
class PolicyFields:
    """模型从页面文本补提出的元信息；没抽到的字段保持 None。"""

    publish_date: date | None = None
    agency: str | None = None
    doc_no: str | None = None


def _clean_text(value: Any, limit: int) -> str | None:
    """去零宽、压空白、限长；非字符串或空串返回 None。"""
    if not isinstance(value, str):
        return None
    text = " ".join(value.translate(_STRIP_ZERO_WIDTH).split())
    if not text or len(text) > limit:
        return None
    return text


def _valid_agency(value: Any) -> str | None:
    """发文机关的轻校验：中文机关名、不是标签串，长度合理。"""
    text = _clean_text(value, _MAX_AGENCY_CHARS)
    if text is None or text.lower() in _PLACEHOLDERS:
        return None
    if re.match(r"^(来源|转载来源|涉及机构|主题分类|索引号)[:：]", text):
        return None
    if not re.search(r"[\u4e00-\u9fff]", text):
        return None
    return text


def _valid_doc_no(value: Any) -> str | None:
    """文号的轻校验：必须含数字，且像「XX〔年〕号 / 第…号」而不是索引号或日期。"""
    text = _clean_text(value, _MAX_DOC_NO_CHARS)
    if text is None or text.lower() in _PLACEHOLDERS:
        return None
    if "索引号" in text or "生成日期" in text:
        return None
    if not re.search(r"\d", text):
        return None
    if not re.search(r"[〔\[（(]|第.{0,8}号|号$", text):
        return None
    return text


def _build_extract_llm(entry: ModelRegistryEntry) -> Any:
    """按注册表项构建提取用 LLM（统一 OpenAI 兼容协议，认证/base_url 见 `_build_llm`）。

    项里配了 `api_key_env` 但取不到值时抛 RuntimeError（单篇记 failed，文案直达用户）。
    `thinking` 默认关（False → 不传 reasoning_effort，交给服务端默认）；
    项里写 true 才传 `reasoning_effort="high"` 显式开思考。
    """
    api_key = resolve_model_api_key(entry) if entry.api_key_env else None
    if entry.api_key_env and not api_key:
        raise RuntimeError(
            f"模型「{entry.label}」缺少 API Key：环境变量 {entry.api_key_env} 未配置（根目录 .env）"
        )
    return _build_llm(
        model=entry.model,
        max_tokens=entry.max_tokens,
        thinking=True if entry.thinking else None,
        base_url=entry.base_url,
        api_key=api_key,
    )


async def extract_policy(
    *,
    title: str,
    body: str,
    url: str | None = None,
    max_body_chars: int | None = None,
    model_id: str | None = None,
    config: RunnableConfig | None = None,
) -> tuple[ExtractionPayload, str]:
    """提取一篇政策的「事实/信息/知识」。

    `max_body_chars` / `model_id` 均为任务级覆盖（页面高级设置）；不传时用 .env 的全局默认
    （`model_id=None` → 注册表第一项）。返回 `(结果, 使用的模型名)`；失败时抛异常。

    `config` 是 LangChain 的 run config（可观测性用，见 `app/tracing.py`）：**显式往下传**
    而不是让模型自己继承——调用方（`_extract_one`）传进来一个挂了 Langfuse callback 的 config，
    这里再原样递给 `llm.ainvoke`，这篇提取才会和它的两次尝试（工具 / JSON 回退）归在同一条
    trace 下。不传 = 不上报（`None` 对 LangChain 就是「没有 config」）。
    """
    settings = get_settings()
    limit = max_body_chars if max_body_chars else settings.pipeline_max_body_chars
    truncated = body.strip()
    if len(truncated) > limit:
        truncated = truncated[:limit] + "\n…（正文过长已截断）"

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

    entry = resolve_model_entry(model_id)
    llm = _build_extract_llm(entry)
    try:
        result = await _extract_via_tool(llm, messages, config=config)
        return result, entry.model
    except Exception as exc:  # noqa: BLE001 - 回退到纯文本 JSON 路径
        logger.warning("工具调用结构化输出失败（%s），回退到 JSON 文本解析", exc)

    payload = await _extract_via_json(llm, messages, config=config)
    return payload, entry.model


async def extract_policy_fields(
    *,
    title: str,
    text: str,
    url: str | None = None,
    fields: list[str] | None = None,
    max_text_chars: int | None = None,
    model_id: str | None = None,
    config: RunnableConfig | None = None,
) -> PolicyFields:
    """结构解析没拿到发布日期 / 发文机关 / 文号时的**兜底**：让模型从页面文本里补提。

    为什么要单独一次调用：这些元信息常写在正文容器之外的页头/元信息块，正文 `body` 里
    根本没有；而正文提取的提示词只关心三区内容，不应为了几个字段把整页噪声塞进去。
    只在缺字段时调用（见 `crawl_graph._preflight_bodies` / `_backfill_model_fields`），
    三个字段一次调用补全，失败按「没抽到」处理——补不齐不能拖垮已经成功的提取。

    `fields` 指定本次要补的字段（`publish_date` / `agency` / `doc_no`）；为空表示不需要
    调用，直接返回空结果。调用方只会把它真正缺失的字段传进来。

    防幻觉是硬约束：提示词要求只认页面明确写出的字段，不许拿成文/施行日期、来源、
    索引号顶替，也不许推测；返回后再做轻校验（日期合理区间、机构/文号长度与形态）。
    """
    wanted = [name for name in (fields or []) if name in {"publish_date", "agency", "doc_no"}]
    if not wanted:
        return PolicyFields()

    settings = get_settings()
    limit = max_text_chars or settings.pipeline_max_body_chars
    truncated = (text or "").strip()
    if not truncated:
        return PolicyFields()
    if len(truncated) > limit:
        truncated = truncated[:limit] + "\n…（页面文本过长已截断）"

    field_labels = {"publish_date": "发布日期", "agency": "发文机关", "doc_no": "文号"}
    messages = [
        SystemMessage(content=FIELDS_PROMPT),
        HumanMessage(
            content=(
                f"政策标题：{title}\n"
                f"原文链接：{url or '（无）'}\n"
                f"本次只需补提：{'、'.join(field_labels[name] for name in wanted)}\n\n"
                f"页面文本：\n{truncated}"
            )
        ),
    ]

    try:
        entry = resolve_model_entry(model_id)
        response = await _build_extract_llm(entry).ainvoke(messages, config=config)
        content = (
            response.content
            if isinstance(response.content, str)
            else _blocks_to_text(response.content)
        )
        data = _loads_lenient(content)
    except Exception as exc:  # noqa: BLE001 - 元信息兜底失败不能影响提取结果
        logger.warning("模型补提政策元信息失败（%s），按未抽到处理", exc)
        return PolicyFields()

    result = PolicyFields()
    if "publish_date" in wanted:
        parsed = parse_date(data.get("publish_date"))
        # 模型给出的日期必须落在合理区间内；否则当作没抽到，不写库。
        if parsed is not None and date(_MIN_MODEL_DATE_YEAR, 1, 1) <= parsed <= date.today():
            result.publish_date = parsed
        elif parsed is not None:
            logger.warning("模型补提的发布日期不合理（%s），已丢弃", parsed)
    if "agency" in wanted:
        result.agency = _valid_agency(data.get("agency"))
    if "doc_no" in wanted:
        result.doc_no = _valid_doc_no(data.get("doc_no"))
    return result


async def _extract_via_tool(
    llm: Any, messages: list, *, config: RunnableConfig | None = None
) -> ExtractionPayload:
    """首选路径：把 schema 绑成一个工具，让模型自己决定调用。

    刻意**不**走 `with_structured_output`——它会强制 `tool_choice={"type": "function", ...}`，
    而部分网关（开思考的推理模型，历史上有 Kimi anthropic 端点 400 实测）会拒绝
    「思考开启 + 指定工具」的组合。不指定 `tool_choice` 时模型照样会调用该工具，
    真没调再回退纯文本 JSON，所以这里不必强制。
    """
    response = await llm.bind_tools([ExtractionPayload]).ainvoke(messages, config=config)
    for call in response.tool_calls or []:
        if call.get("name") == ExtractionPayload.__name__:
            return ExtractionPayload.model_validate(call["args"])
    raise ValueError("模型没有返回工具调用")


async def _extract_via_json(
    llm: Any, messages: list, *, config: RunnableConfig | None = None
) -> ExtractionPayload:
    """回退路径：让模型直接吐 JSON，再宽松解析。"""
    schema_hint = json.dumps(
        ExtractionPayload.model_json_schema(), ensure_ascii=False, indent=2
    )
    instructed = [
        *messages,
        HumanMessage(content=f"{JSON_FALLBACK_INSTRUCTION}\n{schema_hint}"),
    ]
    response = await llm.ainvoke(instructed, config=config)
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
