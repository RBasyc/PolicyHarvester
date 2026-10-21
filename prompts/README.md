# prompts/

LLM 提示词集中放这里，一个提示词一个 `.md`，**文件名即键**（由 `server/app/prompts.py`
的 `load_prompt(name)` 读取，路径 = 仓库根 `prompts/<name>.md`）。

| 文件 | 用在哪 | 什么时候读 |
| --- | --- | --- |
| `chat.md` | 对话 Agent 的系统提示（`app/agent/nodes.py`） | 每次调用模型时 → **改完即刻生效** |
| `chat_browser.md` | 开启「浏览网页」时追加到对话系统提示（`app/agent/nodes.py`） | 每次调用模型时 → **改完即刻生效** |
| `browser_browse.md` | 流水线「任意网站（无头浏览器）」发现循环的系统提示（`app/pipeline/browser_discovery.py`） | 每次发现运行时 → **改完即刻生效** |
| `judge.md` | 入库判别服务的判别指令（`app/pipeline/judge.py`） | 每次判别时 → **改完即刻生效**（但会被 `.env` 的 `JUDGE_INSTRUCTIONS` 覆盖） |
| `extract.md` | 正文提取的系统提示（`app/pipeline/extract.py`） | 模块常量，import 时读一次 → **改完要重启服务** |
| `extract_json_fallback.md` | 提取回退到纯文本 JSON 时追加的指令 | 同上 → **改完要重启服务** |
| `extract_fields.md` | 结构解析拿不到发布日期 / 发文机关 / 文号时的模型兜底抽取（`app/pipeline/extract.py::extract_policy_fields`） | 同上 → **改完要重启服务** |

两点注意：

1. **缺文件会直接报错**（`RuntimeError: 提示词文件缺失或不可读：…`），不会静默用空提示词——
   这是有意的，见 `server/app/prompts.py` 的模块注释。
2. 提示词里的**具体文案**改了不用改代码；但新增一个 `.md` 需要在消费点加一行 `load_prompt(...)`。
