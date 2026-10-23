"""判别总开关与双 env 配置的离线测试（不连库、不调模型、不联网）。

直接运行：uv run python scripts/test_feature_switches.py

覆盖：
1. `.env` / `.env.features` 合并顺序：后文件覆盖前文件，缺失文件跳过
2. `PIPELINE_MODELS` 热读（改文件后下一次调用即生效）
3. 总开关关闭：不调用 laya、不写 judge_tagged，但正文预检仍回填日期/机关/文号
4. 总开关开、laya 子开关关：不发 laya 请求，正文预检照常打标
5. 总开关开、laya 开：被 laya 标记者不再进入正文预检
"""

import asyncio
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.pipeline import crawl_graph, judge
from app.pipeline.extract import PolicyFields
from app.sources.base import FulltextResult

_PASS = True

GOOD_BODY = "这是一段用于测试的正文内容，长度足够通过预检。" * 30
THIN_BODY = "短。"


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS
    _PASS &= ok
    print(f"  {'PASS' if ok else 'FAIL'}: {name}" + (f"（{detail}）" if detail and not ok else ""))


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        judge_enabled=True,
        judge_laya_enabled=True,
        judge_url="http://127.0.0.1:1",
        judge_concurrency=1,
        judge_timeout_seconds=1,
        judge_instructions="",
        judge_keep_choice="入库",
        judge_drop_choice="不入库",
        pipeline_min_body_chars=200,
        pipeline_min_sent_enders=5,
        pipeline_preflight_concurrency=1,
        pipeline_max_body_chars=12000,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeSource:
    fulltext_date_is_publish = True

    def __init__(self, body: str) -> None:
        self.body = body

    def fulltext_available(self) -> bool:
        return True

    async def fetch_fulltext(self, url: str, timeout: float = 0) -> FulltextResult:
        return FulltextResult(
            body=self.body,
            title="测试文件",
            issuer="测试机关",
            doc_no="测〔2026〕1号",
            date=date(2026, 1, 2),
            date_source="label",
            image_count=0,
        )


async def _fake_extract(**_kwargs) -> PolicyFields:
    return PolicyFields()


def _item(url: str = "https://example.gov.cn/a.html") -> dict:
    return {"url": url, "title": "测试文件", "agency": None, "doc_no": None}


async def _run_preflight(settings, body: str = GOOD_BODY, item: dict | None = None) -> tuple[dict, dict]:
    """在受控的 settings / judge / 模型兜底下跑一轮 _judge_and_preflight。"""
    item = item or _item()
    stats: dict[str, int] = {}
    saved = (
        crawl_graph.get_settings,
        judge.get_settings,
        crawl_graph.extract_policy_fields,
    )
    crawl_graph.get_settings = lambda: settings
    judge.get_settings = lambda: settings
    crawl_graph.extract_policy_fields = _fake_extract
    try:
        await crawl_graph._judge_and_preflight(
            [item], FakeSource(body), 5, stats, overrides={"pipeline_max_body_chars": 12000}
        )
    finally:
        (
            crawl_graph.get_settings,
            judge.get_settings,
            crawl_graph.extract_policy_fields,
        ) = saved
    return item, stats


def test_env_files() -> None:
    print("=== 1. 两个 env 文件的合并顺序 ===")
    with tempfile.TemporaryDirectory() as tmp:
        static = Path(tmp) / ".env"
        features = Path(tmp) / ".env.features"
        static.write_text("FOO=from_static\nSHARED=static\n", encoding="utf-8")
        features.write_text("SHARED=features\n", encoding="utf-8")

        saved = config._ENV_FILES
        config._ENV_FILES = (static, features)
        try:
            merged = config._env_file_values()
        finally:
            config._ENV_FILES = saved

        check("后文件覆盖前文件", merged.get("SHARED") == "features", str(merged.get("SHARED")))
        check("前文件独有的键保留", merged.get("FOO") == "from_static")

        config._ENV_FILES = (static, Path(tmp) / "missing.env")
        try:
            merged = config._env_file_values()
        finally:
            config._ENV_FILES = saved
        check("缺失文件跳过", merged.get("FOO") == "from_static")

    check(
        "真实文件顺序 = .env → .env.features",
        [p.name for p in config._ENV_FILES] == [".env", ".env.features"],
        str([p.name for p in config._ENV_FILES]),
    )
    env_file = config.Settings.model_config.get("env_file")
    check(
        "Settings.env_file 与 _ENV_FILES 一致",
        [Path(p).name for p in env_file] == [".env", ".env.features"],
        str(env_file),
    )


def test_hot_read() -> None:
    print("=== 2. PIPELINE_MODELS 热读 ===")
    if "PIPELINE_MODELS" in config._REAL_ENV:
        print("  SKIP: 真实 shell 环境里设了 PIPELINE_MODELS，热读优先级会盖过文件")
        return
    with tempfile.TemporaryDirectory() as tmp:
        static = Path(tmp) / ".env"
        static.write_text('PIPELINE_MODELS=[{"id":"one","label":"one","model":"one"}]\n', encoding="utf-8")
        saved = config._ENV_FILES
        config._ENV_FILES = (static,)
        try:
            first = config._live_env("PIPELINE_MODELS")
            static.write_text('PIPELINE_MODELS=[{"id":"two","label":"two","model":"two"}]\n', encoding="utf-8")
            second = config._live_env("PIPELINE_MODELS")
        finally:
            config._ENV_FILES = saved
        check("改文件后下一次读取即生效", '"one"' in first and '"two"' in second)


def test_master_off() -> None:
    print("=== 3. 总开关关闭 ===")
    calls = {"laya": 0}

    async def fake_judge_rows(rows, **_kwargs):
        calls["laya"] += 1
        return judge.JudgeBatch()

    saved = judge.judge_rows
    judge.judge_rows = fake_judge_rows
    try:
        item, stats = asyncio.run(_run_preflight(_settings(judge_enabled=False)))
    finally:
        judge.judge_rows = saved

    check("不调用 laya", calls["laya"] == 0, f"{calls['laya']} 次")
    check("不写 judge_tagged", "judge_tagged" not in item)
    check("回填发布日期", item.get("date") == "2026-01-02", str(item.get("date")))
    check("回填发文机关", item.get("agency") == "测试机关", str(item.get("agency")))
    check("回填文号", item.get("doc_no") == "测〔2026〕1号", str(item.get("doc_no")))
    check("标记 tagging_disabled", stats.get("tagging_disabled") == 1, str(stats))
    check("不计 body_rejected", "body_rejected" not in stats, str(stats))
    check("统计预检篇数", stats.get("preflight_checked") == 1, str(stats))


def test_laya_off() -> None:
    print("=== 4. 总开关开、laya 子开关关 ===")
    item, stats = asyncio.run(
        _run_preflight(_settings(judge_laya_enabled=False), body=THIN_BODY)
    )
    check("正文不达标照常打标", item.get("judge_tagged") is True)
    check("计入 body_rejected", stats.get("body_rejected") == 1, str(stats))
    check("未标记 tagging_disabled", "tagging_disabled" not in stats, str(stats))


def test_laya_tagged_skips_preflight() -> None:
    print("=== 5. laya 标记者不进入正文预检 ===")
    tagged_url = "https://example.gov.cn/tagged.html"
    kept_url = "https://example.gov.cn/kept.html"
    seen: list[str] = []

    async def fake_judge_rows(rows, **_kwargs):
        return judge.JudgeBatch(verdicts={tagged_url: judge.JudgeVerdict(True, 0.9)})

    async def fake_preflight(_source, items, *_args, **_kwargs):
        seen.extend(it["url"] for it in items)

    saved_rows, saved_preflight = judge.judge_rows, crawl_graph._preflight_bodies
    judge.judge_rows = fake_judge_rows
    crawl_graph._preflight_bodies = fake_preflight
    settings = _settings()
    saved_get = crawl_graph.get_settings
    crawl_graph.get_settings = lambda: settings
    try:
        asyncio.run(
            crawl_graph._judge_and_preflight(
                [_item(tagged_url), _item(kept_url)], FakeSource(GOOD_BODY), 5, {}
            )
        )
    finally:
        judge.judge_rows = saved_rows
        crawl_graph._preflight_bodies = saved_preflight
        crawl_graph.get_settings = saved_get

    check("只预检未标记者", seen == [kept_url], str(seen))


def main() -> int:
    test_env_files()
    test_hot_read()
    test_master_off()
    test_laya_off()
    test_laya_tagged_skips_preflight()
    print("\n结果：", "全部通过" if _PASS else "有失败项")
    return 0 if _PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
