"""可观测性：把对话图与提取流水线接到 Langfuse / Litefuse（**自托管**，见 .env 的 LANGFUSE_*）。

用法——在任何 LangGraph / LangChain 调用点，把 `trace_config(...)` 合进 config 即可：

    config = {"configurable": {"thread_id": thread_id}}
    config.update(trace_config(name="chat", session_id=thread_id))
    await graph.ainvoke(input, config=config)

未启用时 `trace_config()` 返回 `{}`，`config.update({})` 是空操作——**调用点不必写 if**。

降级口径与 MCP / judge 一致：**观测层坏了绝不拖垮业务**。下面任一条件不满足就整体不上报，
对话与抓取照常跑，只在启动日志里说一句（`_handler()` 返回 None）：

1. `LANGFUSE_ENABLED=false`；
2. `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 任一为空；
3. 没装 `langfuse` 包（`uv sync` 没跑）；
4. 初始化本身抛异常（地址写错、包版本不兼容等）。

**自托管**这个前提值得说清楚：trace 只发往 `.env` 里那个地址（本仓默认 `localhost:3000`），
不出本机。别把 `LANGFUSE_BASE_URL` 改成云端地址——那是把对话内容与政策正文送往第三方。
"""

from __future__ import annotations

import contextvars
import inspect
import logging
import os
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# Langfuse Python SDK v3 从这些 metadata 键读走会话 / 用户 / 标签（trace 级的归组维度）。
# 名字写死成常量：写错不会报错，只会静默地「每条 trace 各是一个会话」，很难发现。
_SESSION_KEY = "langfuse_session_id"
_USER_KEY = "langfuse_user_id"
_TAGS_KEY = "langfuse_tags"

# 本仓的 trace 归属：所有对话归一个 user，流水线按任务分会话。二者都不涉及真实账号。
_DEFAULT_USER = "agent-demo"


def _export_endpoint(host: str) -> None:
    """把地址同时写到 LANGFUSE_HOST 与 LANGFUSE_BASE_URL（缺哪个补哪个）。

    SDK 各版本读的变量名不一（官方 LangChain 集成文档用 `LANGFUSE_HOST`，「数据区域」文档
    用 `LANGFUSE_BASE_URL`，本仓 .env 用的也是后者）。补齐另一个名字，可以让「显式传参给
    客户端」与「SDK 自己读环境变量」两条路拿到同一个地址，不至于因为改名而静默断开上报。
    """
    for name in ("LANGFUSE_HOST", "LANGFUSE_BASE_URL"):
        os.environ.setdefault(name, host)


def _init_client(langfuse_cls: Any, public_key: str, secret_key: str, host: str) -> None:
    """构造 SDK 的全局客户端——v3 的 `CallbackHandler()` 不带参数，取的就是这个单例。

    端点参数名各版本不一（`host` / `base_url`），按 `__init__` 的签名挑一个传；一个都挑不到
    就不传，交给 SDK 读环境变量（上面的 `_export_endpoint` 已经补齐）。
    """
    params = inspect.signature(langfuse_cls.__init__).parameters
    kwargs: dict[str, Any] = {"public_key": public_key, "secret_key": secret_key}
    for name in ("host", "base_url"):
        if name in params:
            kwargs[name] = host
            break
    langfuse_cls(**kwargs)


def _handler() -> Any | None:
    """建一次 `CallbackHandler` 并缓存（整个进程复用一个）；不该上报时返回 None。"""
    settings = get_settings()
    if not settings.langfuse_enabled:
        logger.info("可观测性：LANGFUSE_ENABLED=false，不上报 trace")
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.info(
            "可观测性：未配置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY，不上报 trace"
        )
        return None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except ImportError as exc:
        logger.warning(
            "可观测性：未安装 langfuse（%s），不上报 trace——"
            "`cd server && uv add langfuse` 装上即可（`uv sync` 装不上：pyproject 里没声明它）",
            exc,
        )
        return None

    host = settings.langfuse_host
    _export_endpoint(host)
    try:
        _init_client(Langfuse, settings.langfuse_public_key, settings.langfuse_secret_key, host)
        handler = CallbackHandler()
    except Exception:  # noqa: BLE001 - 观测层不能拖垮业务：任何异常都只是「不上报」
        logger.warning("可观测性：Langfuse 初始化失败，本次运行不上报 trace", exc_info=True)
        return None

    logger.info("可观测性：trace 上报到 %s（自托管）", host)
    return handler


# 单例：`_handler()` 的构造有网络客户端与后台上报线程，别每次调用都建一个。
_handler_cached: Any | None = None
_handler_built = False


def _get_handler() -> Any | None:
    global _handler_cached, _handler_built
    if not _handler_built:
        _handler_cached = _handler()
        _handler_built = True
    return _handler_cached


def isolated_context() -> contextvars.Context:
    """每次图执行用一个新的执行上下文，抹掉上一条请求残留的 OTel 活动 span。

    **为什么需要**：Langfuse v5 的 `CallbackHandler` 在 run 开始时把根 span 挂进当前
    OTel 上下文，结束时再 detach。LangGraph 会在内部复制 contextvars 执行节点，异步
    路径上 detach 可能落在另一个上下文里而失败（SDK 自己也把这种情况当作可忽略）。
    失败后，那个活动 span 就留在**发起图执行的执行上下文**里；长驻的 FastAPI 进程
    下一条请求会把新根 span 挂到它上面——父 span 从未导出，于是这条 trace 没有真正
    的根，`traces_scalar` 不收它，界面上表现为「只有第一次对话能记录」。

    **做法**：复制当前上下文（业务用的其他 contextvar 保持不变），只把 OTel 的
    contextvar 换成空上下文——新 run 的 `trace.get_current_span()` 就是 INVALID，
    `start_observation` 自然会开一条新 trace。

    用法（调用点）：

        task = asyncio.create_task(run(...), context=isolated_context())

    未安装 `opentelemetry`（未装 langfuse）时等同于普通复制，不影响业务。
    """
    ctx = contextvars.copy_context()
    try:
        from opentelemetry import context as otel_context
    except ImportError:  # 没装观测依赖：没有 span 可残留
        return ctx
    ctx.run(otel_context.attach, otel_context.Context())
    return ctx


def trace_config(
    *,
    name: str | None = None,
    session_id: str | None = None,
    user_id: str | None = _DEFAULT_USER,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """拼一段可直接 `config.update(...)` 的 LangChain / LangGraph config。

    - `name`       这次 run 的名字，也就是 Langfuse 里的 **trace 名**（如 `chat`、`提取：…`）；
    - `session_id` 归组维度：同一个会话 / 同一抓取任务的多次 run 收在一个 session 下；
    - `tags`       便于筛选，如 `["chat"]` / `["pipeline", "extract"]`；
    - `metadata`   附加到 trace 上的自定义键值（如 `{"task_id": 12, "policy_id": 34}`），
                   在 Langfuse 界面里可直接看到 —— 用来把 trace 和库里的行对上号。

    **未启用时返回 `{}`**，因此调用点可以无条件 `config.update(trace_config(...))`。
    """
    handler = _get_handler()
    if handler is None:
        return {}

    meta: dict[str, Any] = dict(metadata or {})
    if session_id:
        meta[_SESSION_KEY] = session_id
    if user_id:
        meta[_USER_KEY] = user_id
    if tags:
        meta[_TAGS_KEY] = list(tags)

    config: dict[str, Any] = {"callbacks": [handler]}
    if name:
        config["run_name"] = name
    if meta:
        config["metadata"] = meta
    return config


def flush_tracing() -> None:
    """关停时把缓冲区里的 trace 发完（SDK 是后台批量上报，不 flush 会丢最后几条）。"""
    if _get_handler() is None:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:  # noqa: BLE001 - 关停路径上出问题也不该影响退出
        logger.warning("可观测性：flush 失败（不影响业务）", exc_info=True)
