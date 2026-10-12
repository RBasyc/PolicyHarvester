"""数据库连通性 + seed 数据读取测试。

直接运行：uv run python scripts/test_db.py

验证内容（对应 TODO.md Phase 1 验收）：
1. 连接 agent_demo 库（DATABASE_URL）
2. 读出 crawl_task 的 seed 任务（进度统计与 stage）
3. 读出视图 v_policy_results 的 seed 政策行（四态状态）
4. 顺带验证 task_log 与 extraction + fact_item 明细（抽屉三栏数据源）

前置：docker 容器 server-db-1 运行中，且已执行 database/init.sh。
"""

import asyncio
import sys
from pathlib import Path

# 直接从 scripts/ 运行时把 server/ 根目录加入 sys.path，以便导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import repositories as repo
from app.db.pool import close_pool, open_pool, pool_error

_LABELS = {
    "extracted": "已提取",
    "extracting": "提取中",
    "pending": "未提取",
    "failed": "提取失败",
}


def _print_pool_help(reason: str) -> None:
    print(f"FAIL: {reason}")
    print("请依次确认：")
    print("  1) docker 容器 server-db-1 运行中：docker ps")
    print("  2) 已执行 bash database/init.sh（建库建表 + 种子数据）")
    print("  3) server/.env 的 DATABASE_URL 指向该容器（默认 localhost:5433/agent_demo）")


async def main() -> int:
    await open_pool()
    try:
        reason = pool_error()
        if reason is not None:
            # 连接池没起来就别往下走了——否则只会撞上一堆无意义的报错
            _print_pool_help(reason)
            return 1

        print("=== 1. crawl_task（当前任务）===")
        task = await repo.fetch_current_task()
        if task is None:
            print("FAIL: crawl_task 无数据 —— 请先执行 bash database/init.sh")
            return 1
        print(
            f"  id={task['id']} status={task['status']} "
            f"进度 {task['extracted_count']}/{task['total_count']} · 失败 {task['failed_count']}"
        )
        print(f"  stage: {task['stage']}")
        print(f"  keywords: {task['keywords']}")

        print("\n=== 2. v_policy_results（抓取结果）===")
        rows = await repo.fetch_task_policies(task["id"])
        if not rows:
            print("FAIL: v_policy_results 无数据")
            return 1
        for row in rows:
            label = _LABELS.get(row["status"], row["status"])
            print(f"  [{label}] {row['publish_date']}  {row['title']}")

        print("\n=== 3. task_log（日志流）===")
        for log in await repo.fetch_task_logs(task["id"]):
            print(f"  [{log['created_at']:%H:%M:%S}] ({log['tone']}) {log['message']}")

        print("\n=== 4. extraction + fact_item（抽屉三栏）===")
        detail = await repo.fetch_extraction_detail(rows[0]["id"])
        extraction = detail["extraction"] if detail else None
        if extraction is None:
            print(f"  第 1 篇（policy_id={rows[0]['id']}）尚无提取结果")
        else:
            print(f"  状态={extraction['status']} 模型={extraction['model']}")
            print(f"  事实 {len(detail['facts'])} 条 / 信息要点 {len(extraction['info_points'])} 条 "
                  f"/ 知识概念 {len(extraction['knowledge_concepts'])} 条")
            print(f"  摘要: {(extraction['info_summary'] or '')[:60]} …")
    finally:
        await close_pool()

    print("\nOK: 数据库读写测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
