"""提示词集中管理：从仓库根 `prompts/*.md` 读取。

提示词原来散在代码里（chat / extract / extract_json_fallback / extract_date / judge 五处），
改一句要翻代码、还容易改漏；集中成 md 文件后，调提示词不用碰 Python。

约定：一个提示词一个文件，**文件名即键**。缺文件直接抛错（RuntimeError 带绝对路径），
不静默回退到空提示词——那会把「提示词没生效」伪装成「模型变笨了」，是最难查的一类问题。
"""

from pathlib import Path

# 仓库根目录（server/app/prompts.py -> 上两级；与 config.py 的 _REPO_ROOT 同一处）
REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"


def load_prompt(name: str) -> str:
    """读 `prompts/<name>.md` 并 strip。

    **故意不加缓存**：调用频率是「每轮对话 / 每篇提取」量级，读一个小文件的开销可忽略，
    换来的是调用点拿到的永远是磁盘上的最新版本（与 `get_model_registry()` 的取舍一致）。

    注意「何时读」由消费方决定，这决定了改完要不要重启：
    - `agent/nodes.py` 在 `call_model` 里读、`pipeline/judge.py` 在判别时读 → 改完**即刻生效**；
    - `pipeline/extract.py` 取的是模块常量（在 import 时读一次）→ 改完**要重启服务**。
    """
    path = PROMPTS_DIR / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"提示词文件缺失或不可读：{path}（{exc}）") from exc
    if not text:
        raise RuntimeError(f"提示词文件是空的：{path}")
    return text
