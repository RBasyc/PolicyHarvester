"""抓取源注册表：按 key / 展示 label 找适配器，未知 label 回退 gov.cn 默认源。

注册表里有**两类**来源，都在同一个 `_registry` 里按 key 索引：

1. **内置源**（代码写的，如 `GovCnSource`）——启动时 `register()` 一次；
2. **配置化源**（`HtmlListSource`，由接入 Skill 生成配置后落库、启动/注册时装载）
   ——见 `register_configured` / `load_configured_from_db`。

两类对流水线是**同一种东西**：流水线只认 `get_by_label(task_row["source"])`，拿到适配器就
照常检索 / 预检 / 抽取。这正是「新增网站逐渐成为可配置的接入任务」而非「每次写爬虫」的机制。

注意：流水线的抓取任务以 `label`（展示名）为外键解析来源，因此
**配置化来源的 label 必须唯一**；`register_configured` 会拒绝与已有 label 冲突的配置。
"""

from __future__ import annotations

import logging

from app.sources.base import PolicySource
from app.sources.gov_cn import GovCnSource

logger = logging.getLogger(__name__)

_registry: dict[str, PolicySource] = {}
# 配置化来源的 key（「停用/删除来源」时只允许移除这一类，内置源不能卸）
_configured: set[str] = set()


def register(source: PolicySource) -> None:
    """注册内置源（覆盖同 key 的旧实例）。"""
    _registry[source.key] = source


def unregister(key: str) -> bool:
    """注销一个**配置化**来源；返回是否真的移除了。

    内置源拒绝注销（`False`）——它们的代码就在仓库里，卸掉只会让默认源解析失败。
    """
    if key not in _configured:
        logger.warning("拒绝注销非配置化来源：%s", key)
        return False
    _configured.discard(key)
    return _registry.pop(key, None) is not None


def get(key: str) -> PolicySource | None:
    return _registry.get(key)


def get_default() -> PolicySource:
    return _registry[GovCnSource.key]


def get_by_label(label: str | None) -> PolicySource:
    """按任务行上的自由文本来源名解析适配器；不认识的名字告警并回退默认源。"""
    for source in _registry.values():
        if source.label == label:
            return source
    if label:
        logger.warning("未知抓取源「%s」，回退默认源 %s", label, get_default().label)
    return get_default()


def all_sources() -> list[PolicySource]:
    return list(_registry.values())


def configured_sources() -> list[PolicySource]:
    """只列配置化来源（接入报告与来源管理用，不含 gov.cn）。"""
    return [source for key, source in _registry.items() if key in _configured]


def register_configured(config, *, replace: bool = False) -> PolicySource:
    """把一份 `SourceConfig` 装进注册表，返回 `HtmlListSource` 实例。

    `replace=False` 时，key 或 label 撞车一律拒绝（抛 `ValueError`）——抓取任务以 label
    为外键解析来源，同名会静默抓错站点，必须挡在注册这一步。重新注册同一份配置的
    新版请显式 `replace=True`。
    """
    from app.sources.config import validate_source_config  # 局部导入：config 反向依赖 net
    from app.sources.html_source import HtmlListSource

    problems = validate_source_config(config)
    if problems:
        raise ValueError("配置不可执行：" + "；".join(problems))

    existing = _registry.get(config.key)
    if existing is not None and not replace:
        raise ValueError(f"来源 key「{config.key}」已被占用（{existing.label}）")
    for key, source in _registry.items():
        if source.label == config.label and key != config.key:
            raise ValueError(f"来源名「{config.label}」与已有来源（{key}）冲突，请换一个展示名")

    source = HtmlListSource(config)
    _registry[config.key] = source
    _configured.add(config.key)
    logger.info("已注册配置化来源：%s（%s，%d 个入口）", config.label, config.key, len(config.entrypoints))
    return source


def register_configs(configs, *, replace: bool = True) -> tuple[list[str], list[str]]:
    """批量注册（启动装载用）；返回 `(成功 key 列表, 失败说明列表)`。

    单份配置坏掉不该拖垮启动——收集错误继续装其余的（与 MCP 加载失败降级的口径一致）。
    """
    ok: list[str] = []
    failed: list[str] = []
    for config in configs:
        try:
            register_configured(config, replace=replace)
            ok.append(config.key)
        except Exception as exc:  # noqa: BLE001 - 配置坏掉的形态很多，统一降级为一条说明
            failed.append(f"{getattr(config, 'key', '?')}: {exc}")
    if failed:
        logger.warning("有 %d 份来源配置未能装载：%s", len(failed), "；".join(failed))
    return ok, failed


async def load_configured_from_db() -> tuple[list[str], list[str]]:
    """从 `content_source` 表装载 `active` 来源（lifespan 启动时调用）。

    局部导入 repositories / config：repositories 依赖 psycopg 与 pool，顶层导入会让
    `sources` 包在纯离线场景（解析测试）也被拖上数据库依赖。
    """
    from app.db import repositories as repo
    from app.sources.config import config_from_row

    try:
        rows = await repo.list_content_sources(status="active")
    except Exception as exc:  # noqa: BLE001 - 数据库不可用时服务器仍要能启动
        logger.warning("读取来源配置失败（数据库不可用？）：%s", exc)
        return [], [f"数据库读取失败：{exc}"]

    configs = []
    failed: list[str] = []
    for row in rows:
        try:
            configs.append(config_from_row(row))
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{row.get('key', '?')}: 配置反序列化失败：{exc}")
    ok, more_failed = register_configs(configs, replace=True)
    if ok:
        logger.info("已装载 %d 个配置化来源：%s", len(ok), "、".join(ok))
    return ok, failed + more_failed


register(GovCnSource())
