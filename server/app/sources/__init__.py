"""抓取源适配层（`PolicySource` 及其注册表）。

把站点专属知识（gov.cn 的检索参数、过滤规则、字段映射）从流水线抽到各
`PolicySource` 子类里，流水线只做编排（预算 / 翻页 / 判别 / 预检 / 落库）。

**两类来源并存**：

- 内置源（代码）：`GovCnSource`；
- 配置化源（数据）：`HtmlListSource` + 一份 `SourceConfig`，由网站来源接入 Skill
  勘察 → 生成配置 → 试运行验收 → 注册而来，**新增站点原则上不改代码**。

配套的取数与解析能力：

- `net`      受控 HTTP 抓取（SSRF 校验 / 逐跳复验 / robots / 限频）；
- `htmlparse` 迷你 DOM + CSS 选择器子集 + 中文站点编码嗅探（纯标准库）；
- `config`   `SourceConfig` 配置 schema 与可执行性校验。
"""

from app.sources.base import FulltextResult, PolicyItem, PolicySource, SourceError
from app.sources.config import SourceConfig, config_from_row, validate_source_config
from app.sources.html_source import HtmlListSource
from app.sources.registry import (
    all_sources,
    configured_sources,
    get,
    get_by_label,
    get_default,
    load_configured_from_db,
    register,
    register_configs,
    register_configured,
    unregister,
)

__all__ = [
    "FulltextResult",
    "HtmlListSource",
    "PolicyItem",
    "PolicySource",
    "SourceConfig",
    "SourceError",
    "all_sources",
    "config_from_row",
    "configured_sources",
    "get",
    "get_by_label",
    "get_default",
    "load_configured_from_db",
    "register",
    "register_configs",
    "register_configured",
    "unregister",
    "validate_source_config",
]
