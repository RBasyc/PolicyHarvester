"""无头浏览器驱动来源（`engine="browser_driven"`）：持久化的「任意网站」。

与内置伪来源 `BrowserAnySource` 的关系：行为完全继承（发现循环不走 `search`、
`fetch_fulltext` 用无头浏览器重开详情页 + 结构兜底解析），差别只在**身份**——
本类的 key/label/entry_url 来自落库的 `SourceConfig`，因此：

- 来源管理/抓取页来源下拉按 label 展示它（伪来源已从清单撤下，见
  `api/routes/sources.list_sources`）；
- 任务行 `crawl_task.source` 记它的 label，stage 2 `_extract_one` 用
  `get_by_label` 能解析回本类而不是静默回退 gov.cn；
- `entry_url`（= `config.browse.url`）由建任务的服务端逻辑（`services/ingestion`
  .start_ingestion）拷进 `crawl_task.browse_url`，`fetch_node` 现按该列分发到
  发现循环，流水线主体零改动。

`browser_driven = True` 类标记是全链路的判定口径：`start_ingestion` 的浏览器模式、
调度器拒绝、facets 分支都看它，不用猜 engine 字符串。
"""

from __future__ import annotations

from app.sources.browser_any import BrowserAnySource
from app.sources.config import SourceConfig


class BrowserDrivenSource(BrowserAnySource):
    """由 `content_source` 行驱动的无头浏览器来源（每个 active 行一个实例）。"""

    browser_driven = True  # 全链路判定口径（ingestion / scheduler / facets）

    def __init__(self, config: SourceConfig) -> None:
        assert config.engine == "browser_driven" and config.browse is not None
        self.config = config
        # 实例级覆盖类属性（BrowserAnySource 的 key/label 是类属性，见父类 :40-41）：
        # get_by_label 走实例属性读取，任务行的 source label 必须能解析回本类。
        self.key = config.key
        self.label = config.label
        self.entry_url = config.browse.url
        self.fetched_by = f"browser_driven:{config.key}"
