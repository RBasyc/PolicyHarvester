"""Agent Demo 后端应用包。

这里除了标记包，还做一件**必须在任何事件循环创建之前完成**的全局设置（见下），
因此放在包初始化中——它保证先于 `app` 的任何子模块被导入。
"""

from __future__ import annotations

import asyncio
import sys

# psycopg 的**异步**模式不能用 Windows 默认的 ProactorEventLoop：
# 它会直接抛 `InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode`。
# 必须在事件循环被创建之前切到 SelectorEventLoop，而创建循环的是 uvicorn / asyncio.run()，
# 都在本项目控制之外 —— 所以只能在包导入时设置。
#
# 已实测（Windows 11 / Python 3.12 / psycopg 3.3）：SelectorEventLoop 下
#   - psycopg 异步连接池可正常连接 agent_demo；
#   - MCP 的 node stdio 子进程也照常工作（不依赖 Proactor）。
if sys.platform == "win32":  # pragma: no cover - 仅在 Windows 生效
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
