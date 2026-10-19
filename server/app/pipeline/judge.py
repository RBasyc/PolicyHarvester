"""入库判别服务（judge · Phase 13）。

检索阶段对候选篇目做「建议 / 不建议入库」的二选一标注：抓取结果里混着大量批复、函等
事务性文件，不值得花大模型提取。判别输入只取**标题**——检索结果里就有，不用先抓全文。
（2026-10-13 双轴矩阵实测：机关/文号一起进 `state` 会带偏该 RL 模型——文号含「函」直接
被判「不入库」，长多机关连排标题也全灭；标题+机关+文号 4/9 → 仅标题 8/9，见
scripts/test_judge.py。）

调用位置：`_search_policies` 在检索循环内**逐页**调用（见 app/pipeline/crawl_graph.py）——
被标注的篇目不占 `pipeline_max_policies` 名额，翻页会继续凑「建议入库」的。
- **手动任务**：标注只落 `policy_document.judge_tagged`，前端确认面板显示「不建议入库」
  标记，**仅提示**，勾不勾仍由用户决定；
- **定时任务**：跳过人工确认，标注为「不入库」的篇目直接剔除（见 app/scheduler.py）。

契约（/v1/systemone）：`questions` 是对象、key 为问题 ID，`type` 支持 choice 等；
`state` 填中文标题，服务端用它做语言路由；判别结果取
`answers.q1.choice` / `answers.q1.probabilities` / `answers.q1.answer_confidence`。

fail-open 是硬约束：判别服务不可达 / 超时 / 响应畸形，**一律按「建议入库」处理并告警**，
绝不阻断抓取主流程（用户已确认的语义）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.prompts import load_prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JudgeVerdict:
    """单篇判别结果。"""

    tagged: bool  # True = 不建议入库
    prob: float | None  # 「不入库」一侧的概率；拿不到为 None


@dataclass
class JudgeBatch:
    """一批候选的判别汇总（key = source_url）。"""

    verdicts: dict[str, JudgeVerdict] = field(default_factory=dict)
    failures: int = 0  # fail-open 的篇数（超时 / HTTP 错 / 响应畸形）


async def judge_rows(rows: list[dict[str, Any]], *, instructions: str | None = None) -> JudgeBatch:
    """批量判别候选篇目。未启用 / 空列表 → 空结果（不发请求）。

    `instructions` 覆盖默认提示词（.env 的 JUDGE_INSTRUCTIONS），供 scripts/test_judge.py
    做提示词变体对比；生产路径不传、走配置。
    并发由 `JUDGE_CONCURRENCY` 封顶、单篇超时 `JUDGE_TIMEOUT_SECONDS`；
    任何异常不抛出：该篇记 `JudgeVerdict(False, None)` 并计入 `failures`。
    """
    settings = get_settings()
    if not settings.judge_enabled or not settings.judge_url or not rows:
        return JudgeBatch()

    semaphore = asyncio.Semaphore(max(1, settings.judge_concurrency))
    batch = JudgeBatch()
    # trust_env=False：内网直连地址不受 HTTP_PROXY 等环境变量劫持
    async with httpx.AsyncClient(
        timeout=settings.judge_timeout_seconds, trust_env=False
    ) as client:

        async def one(row: dict[str, Any]) -> None:
            async with semaphore:
                try:
                    batch.verdicts[row["source_url"]] = await _judge_one(
                        client, row, instructions
                    )
                except Exception as exc:  # noqa: BLE001 - fail-open：判别故障不阻断抓取
                    logger.warning(
                        "判别服务调用失败（%s）——《%s》按建议入库处理", exc, row.get("title")
                    )
                    batch.verdicts[row["source_url"]] = JudgeVerdict(False, None)
                    batch.failures += 1

        await asyncio.gather(*(one(row) for row in rows))

    if batch.failures:
        logger.warning(
            "判别服务本轮 %d/%d 篇失败，均按建议入库处理（fail-open）",
            batch.failures,
            len(rows),
        )
    return batch


async def _judge_one(
    client: httpx.AsyncClient, row: dict[str, Any], instructions: str | None = None
) -> JudgeVerdict:
    """单篇判别。响应畸形（缺 answers.q1 / 选项不在 criteria 里）抛 ValueError，由调用方 fail-open。"""
    settings = get_settings()
    # state 只放标题：机关/文号会带偏判别（双轴矩阵实测，见模块 docstring），且 8/9 的
    # 最优组合里唯一误判方向是「函被判入库」——多展示一行让用户取消，比错杀好文件安全。
    state_text = str(row.get("title") or "").strip()
    payload = {
        "questions": {
            "q1": {
                "type": "choice",
                # 优先级：调用方覆盖 > .env 的 JUDGE_INSTRUCTIONS > prompts/judge.md
                "instructions": instructions
                or settings.judge_instructions
                or load_prompt("judge"),
                "criteria": [settings.judge_keep_choice, settings.judge_drop_choice],
            }
        },
        "state": state_text,  # 中文文本，供服务端做语言路由
    }
    resp = await client.post(settings.judge_url, json=payload)
    resp.raise_for_status()
    answer = ((resp.json() or {}).get("answers") or {}).get("q1")
    if not isinstance(answer, dict):
        raise ValueError("响应缺少 answers.q1")
    choice = str(answer.get("choice") or "").strip()
    if choice not in (settings.judge_keep_choice, settings.judge_drop_choice):
        raise ValueError(f"未知选项：{choice!r}")
    probs = answer.get("probabilities")
    prob = probs.get(settings.judge_drop_choice) if isinstance(probs, dict) else None
    if not isinstance(prob, (int, float)):
        prob = answer.get("answer_confidence")  # 概率缺失时回退整体置信度
    return JudgeVerdict(
        tagged=(choice == settings.judge_drop_choice),
        prob=float(prob) if isinstance(prob, (int, float)) else None,
    )
