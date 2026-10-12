"""psycopg3 异步连接池（进程级单例）。

连接池在 FastAPI `lifespan` 中 open/close（见 `app/main.py`）；
业务代码统一通过 `get_pool()` 取用，不自行建连。

约定：
- `row_factory=dict_row` —— 查询结果直接是 `dict`，便于直接喂给 pydantic 模型；
- `autocommit=True` —— 单条读写无需显式提交；需要原子性的多步操作
  用 `async with conn.transaction():` 显式开事务（autocommit 下依然有效）。

Windows 注意：psycopg 异步模式不支持默认的 ProactorEventLoop，事件循环策略在
`app/__init__.py` 导入时已切到 SelectorEventLoop —— 那必须在建循环之前发生，
详见该文件注释。
"""

from __future__ import annotations

import logging

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

logger = logging.getLogger(__name__)

# 启动时等待首个连接的秒数：连不上不要卡住整个服务启动
_STARTUP_WAIT_SECONDS = 5.0

_pool: AsyncConnectionPool | None = None
# 上次 open 失败的原因。未就绪时用它拼出**可操作**的错误，而不是让调用方
# 撞上 `PoolClosed` 那种把真实原因盖掉的报错。
_pool_error: str | None = None


def pool_error() -> str | None:
    """连接池未就绪的原因；就绪时返回 None。"""
    if _pool is None or _pool.closed:
        return _pool_error or "数据库连接池尚未初始化"
    return None


def get_pool() -> AsyncConnectionPool:
    """返回进程级连接池；未就绪则抛 `RuntimeError`。

    这里**不能**把已关闭的池透出去：psycopg_pool 在 `open(wait=True)` 超时后会把池关掉，
    若仍返回它，调用方只会看到 `PoolClosed: the pool 'pool-1' is already closed`，
    真正的失败原因（连不上、库不存在、认证失败……）就被掩盖了。
    """
    if _pool is None or _pool.closed:
        raise RuntimeError(pool_error() or "数据库连接池尚未初始化")
    return _pool


async def _discard_pool() -> None:
    """关闭并丢弃当前池实例（清理失败残留 / 便于重新 open）。"""
    global _pool
    if _pool is None:
        return
    pool, _pool = _pool, None
    try:
        await pool.close()
    except Exception:  # noqa: BLE001 - 清理失败不影响后续重建
        logger.debug("关闭连接池时出错", exc_info=True)


async def open_pool() -> None:
    """打开连接池。

    连不上时只告警不抛错——数据库不可用不应该让整个服务起不来
    （与 MCP 加载失败降级的行为保持一致）。失败后**丢弃**该池，
    之后任何取用都会得到 `RuntimeError`（原因可读），而不是 `PoolClosed`。
    """
    global _pool, _pool_error

    if _pool is not None and not _pool.closed:
        return
    await _discard_pool()

    settings = get_settings()
    pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        open=False,  # 显式指定，避免 psycopg_pool 在异步上下文下的隐式 open 警告
        kwargs={
            "row_factory": dict_row,
            "autocommit": True,
            "connect_timeout": int(_STARTUP_WAIT_SECONDS),
        },
    )
    _pool = pool

    try:
        # wait=True：等到 min_size 条连接真正建好（失败即抛，不会留下半开的池）
        await pool.open(wait=True, timeout=_STARTUP_WAIT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - 启动期降级，具体错误记录后丢弃
        _pool_error = (
            f"数据库连接池未能就绪（{settings.database_url}）：{exc}"
        )
        logger.warning("%s", _pool_error)
        await _discard_pool()
        return

    _pool_error = None
    logger.info("数据库连接池已就绪：%s", settings.database_url)


async def close_pool() -> None:
    """关闭连接池并释放单例（便于测试重复 open/close）。"""
    global _pool_error
    await _discard_pool()
    _pool_error = None
