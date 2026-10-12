#!/usr/bin/env bash
# 初始化 Agent Demo 数据库：创建库（如不存在）-> 建表 -> 灌入演示数据
# 使用本机 docker 容器 server-db-1（pgvector/pgvector:pg16），不触碰容器内已有的 kos 库。
#
# 用法：
#   database/init.sh            # 初始化（幂等，可重复执行）
#   PG_CONTAINER=xxx database/init.sh   # 指定其他容器
#
# 重置（删库重建）：database/reset.sh
set -euo pipefail

CONTAINER="${PG_CONTAINER:-server-db-1}"
PGUSER="${PGUSER:-kos}"
DB="${PGDATABASE:-agent_demo}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo ">>> 目标容器: $CONTAINER, 数据库: $DB"

# 1. 创建数据库（幂等）
if ! docker exec -i "$CONTAINER" psql -U "$PGUSER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
    echo ">>> 创建数据库 $DB ..."
    docker exec -i "$CONTAINER" psql -U "$PGUSER" -d postgres \
        -c "CREATE DATABASE $DB"
else
    echo ">>> 数据库 $DB 已存在，跳过创建"
fi

# 2. 建表
echo ">>> 执行 schema.sql ..."
docker exec -i "$CONTAINER" psql -U "$PGUSER" -d "$DB" -v ON_ERROR_STOP=1 < "$DIR/schema.sql"

# 3. 演示数据
echo ">>> 执行 seed.sql ..."
docker exec -i "$CONTAINER" psql -U "$PGUSER" -d "$DB" -v ON_ERROR_STOP=1 < "$DIR/seed.sql"

echo ">>> 完成。连接串: postgresql://$PGUSER@localhost:5433/$DB"
