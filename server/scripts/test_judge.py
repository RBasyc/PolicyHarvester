"""入库判别服务（judge）连通性 + 提示词调优测试。

直接运行：uv run python scripts/test_judge.py

验证内容：
1. Settings 的 JUDGE_* 配置生效（打印 url / enabled / timeout / concurrency）
2. 契约验证：/v1/systemone 接受 state=中文字符串、answers.q1.choice 可解析（failures=0 即通）
3. **双轴变体矩阵**：提示词写法 × state 组成（标题+机关+文号 / 标题+机关 / 仅标题）。
   样例含 2026-10-13 线上误判的 5 篇真实标题（意见/通知被标注「不建议入库」），以及
   「同标题、文号带函字 vs 不带」的对照组——直接验证「文号里的『函』把小模型带偏」假设
   （state 是 标题+机关+文号 拼接、文号在末尾，文种直判规则里 函 → 不入库）。
   该日两轮共 16 组合的冠军：**「疑问句+枚举」× 仅标题 = 8/9**（唯一误判方向安全：函被判
   入库只是多展示一行），已固化为 config 默认与 judge.py 的 state 拼接（仅标题）。
   复跑可验证服务确定性（同输入同输出）；换新措辞时以此为基准打分
4. fail-open 语义：判别不可达时不抛异常、全部按「建议入库」处理（tagged=False）

不需要数据库，也不需要 MCP。
"""

import asyncio
import sys
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.pipeline.judge import judge_rows

# 检索阶段即可得的字段（标题 + 发文机关 + 文号）；expected=True 表示期望判为「不入库」。
# 2026-10-13 线上误判的 5 篇（标题取自用户反馈原文；当时任务已清空、真实文号取不回，
# 按各部门发文字号惯例拟了合理值——对照组会用「同标题仅文号不同」隔离文号的影响）。
SAMPLES = [
    # --- 事务性文件：期望「不入库」 ---
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-pifu.htm",
        "title": "国务院关于某某事项的批复",
        "agency": "国务院",
        "doc_no": "国函〔2026〕1号",
        "expect": True,
    },
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-han.htm",
        "title": "国务院办公厅关于同意某某实施方案的函",
        "agency": "国务院办公厅",
        "doc_no": "国办函〔2026〕12号",
        "expect": True,
    },
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-renmian.htm",
        "title": "国务院任免国家工作人员",
        "agency": "国务院",
        "doc_no": None,
        "expect": True,
    },
    # --- 线上误判的 5 篇真实标题：期望「入库」 ---
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-xianyu.htm",
        "title": "商务部等9部门关于进一步激发下沉市场活力 活跃县域消费的意见",
        "agency": "商务部",
        "doc_no": "商办流通发〔2026〕210号",
        "expect": False,
    },
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-qiche.htm",
        "title": "商务部等9部门关于培育壮大汽车后市场消费若干措施的通知",
        "agency": "商务部",
        "doc_no": "商办消费发〔2026〕98号",
        "expect": False,
    },
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-budongchan.htm",
        "title": (
            "自然资源部办公厅 公安部办公厅 国家税务总局办公厅 国家市场监督管理总局办公厅 "
            "国家金融监督管理总局办公厅关于做好企业购置不动产转移登记“高效办成一件事”的通知"
        ),
        "agency": "自然资源部办公厅",
        "doc_no": "自然资办函〔2026〕45号",
        "expect": False,
    },
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-fuwumaoyi.htm",
        "title": "商务部 市场监管总局关于印发《服务贸易标准化工作行动计划（2026—2030年）》的通知",
        "agency": "商务部",
        "doc_no": "商服贸函〔2026〕321号",
        "expect": False,
    },
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-fanbuzhengdang.htm",
        "title": "市场监管总局关于进一步贯彻实施《中华人民共和国反不正当竞争法》的通知",
        "agency": "国家市场监督管理总局",
        "doc_no": "国市监竞发〔2026〕30号",
        "expect": False,
    },
    # --- 对照组：与 demo-qiche 同标题，仅文号带「函」——两行判定若不同即文号污染 ---
    {
        "source_url": "https://www.gov.cn/zhengce/zhengceku/demo-qiche-han-docno.htm",
        "title": "商务部等9部门关于培育壮大汽车后市场消费若干措施的通知",
        "agency": "商务部",
        "doc_no": "商办建函〔2026〕77号",
        "expect": False,
    },
]

# 提示词变体：None = 用 config 默认（2026-10-13 双轮矩阵冠军「疑问句+枚举」，8/9）
VARIANTS: dict[str, str | None] = {
    "基线（默认=疑问句+枚举）": None,
    "文种直判（旧默认，仅对照）": (
        "根据文件标题中的文种判断：批复、函、任免、公告、纪要 → 不入库；"
        "条例、办法、规定、意见、规划、通知 → 入库。"
    ),
    "文种直判+文号澄清": (
        "根据文件标题末尾的文种判断，发文字号只是编号、其中的字不算文种："
        "批复、函、任免、公告、纪要 → 不入库；条例、办法、规定、意见、规划、通知 → 入库。"
    ),
    "疑问句+枚举": (
        "这篇文件属于哪一类？批复、函、人事任免、公告、会议纪要等事务性或礼节性文件"
        "选「不入库」；条例、办法、规定、规划、意见等具有长期执行效力的政策文件选「入库」。"
    ),
    "叙述式长指令（仅对照）": (
        "你要判断这篇政策文件是否值得入库归档。批复、函、人事任免、公告、会议纪要等"
        "事务性或礼节性文件不值得入库；行政法规、部门规章、规范性文件以及具有长期执行"
        "效力的政策文件值得入库。请从给定选项中选择。"
    ),
}

# state 组成轴：_judge_one 现在只用 title 拼 state，置 None 即剔除该字段
# （「标题+机关+文号」是 2026-10-13 之前的线上旧口径，留作对照轴）
COMPOSITIONS: dict[str, tuple[str, ...]] = {
    "标题+机关+文号（旧线上口径）": ("title", "agency", "doc_no"),
    "标题+机关": ("title", "agency"),
    "仅标题（线上现状）": ("title",),
}


def _compose(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """按组成轴裁剪样例行（source_url 永远保留——判别结果以它为 key）。"""
    return [
        {k: row.get(k) for k in keys} | {"source_url": row["source_url"]} for row in rows
    ]


async def run_combo(
    prompt_name: str, instructions: str | None, comp_name: str, keys: tuple[str, ...]
) -> tuple[int, int, int]:
    """跑一个「提示词 × 组成」组合，返回 (正确数, 样例数, 失败数)；逐行打印结论。"""
    rows = _compose(
        [{k: v for k, v in s.items() if k != "expect"} for s in SAMPLES], keys
    )
    batch = await judge_rows(rows, instructions=instructions)
    correct = 0
    for sample in SAMPLES:
        verdict = batch.verdicts.get(sample["source_url"])
        if verdict is None:
            print(f"  - {sample['title'][:24]:<24} → 未判别")
            continue
        ok = verdict.tagged == sample["expect"]
        correct += ok
        mark = "✓" if ok else "✗"
        label = "不入库" if verdict.tagged else "入库"
        expect_label = "不入库" if sample["expect"] else "入库"
        print(
            f"  {mark} {sample['title'][:24]:<24} → {label}（期望 {expect_label}，prob={verdict.prob}）"
        )
    return correct, len(SAMPLES), batch.failures


async def main() -> int:
    settings = get_settings()

    print("=== 1. JUDGE_* 配置 ===")
    print(f"  enabled     = {settings.judge_enabled}")
    print(f"  url         = {settings.judge_url}")
    print(f"  timeout     = {settings.judge_timeout_seconds}s")
    print(f"  concurrency = {settings.judge_concurrency}")
    print(f"  criteria    = {settings.judge_keep_choice} / {settings.judge_drop_choice}")
    if not settings.judge_enabled:
        print("WARN: JUDGE_ENABLED=false，judge_rows 会直接返回空批（不发请求）")

    print(f"\n=== 2. 双轴变体矩阵（{len(VARIANTS)} 提示词 × {len(COMPOSITIONS)} 组成 × {len(SAMPLES)} 样例） ===")
    scores: list[tuple[str, int, int]] = []
    for comp_name, keys in COMPOSITIONS.items():
        for prompt_name, instructions in VARIANTS.items():
            print(f"\n--- [{prompt_name}] × state=[{comp_name}] ---")
            correct, total, failures = await run_combo(prompt_name, instructions, comp_name, keys)
            scores.append((f"{prompt_name} × {comp_name}", correct, failures))
            print(f"  得分 {correct}/{total}" + (f"，失败 {failures} 篇（fail-open）" if failures else ""))

    print("\n=== 3. 结论 ===")
    ranked = sorted(scores, key=lambda s: s[1], reverse=True)
    for name, correct, failures in ranked:
        print(f"  {correct}/{len(SAMPLES)}  {name}" + (f"（失败 {failures}）" if failures else ""))
    winners = [n for n, c, f in ranked if c == len(SAMPLES)]
    if winners:
        print(f"\n  推荐组合：「{winners[0]}」——提示词写进 .env 的 JUDGE_INSTRUCTIONS，"
              "state 组成同步改 judge.py 的拼接（改后重启服务）。")
    else:
        best = ranked[0]
        print(
            f"\n  没有满分组合（最高 {best[1]}/{len(SAMPLES)}「{best[0]}」）——服务对该任务区分度有限，"
            "可考虑：① 继续在 .env 里调 JUDGE_INSTRUCTIONS 措辞；② 或临时 JUDGE_ENABLED=false"
            "（判别先只做提示/不剔除），等判别服务侧改进后再启用。"
        )

    print("\nOK: 入库判别服务测试结束")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
