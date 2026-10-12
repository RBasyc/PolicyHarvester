#!/usr/bin/env bash
# 删除 agent_demo 数据库并重新初始化（危险操作，只影响 agent_demo，不影响同容器的 kos 库）
set -euo pipefail

CONTAINER="${PG_CONTAINER:-server-db-1}"
PGUSER="${PGUSER:-kos}"
DB="${PGDATABASE:-agent_demo}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo ">>> 即将删除数据库 $DB（容器 $CONTAINER）"
docker exec -i "$CONTAINER" psql -U "$PGUSER" -d postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB' AND pid <> pg_backend_pid()" \
    -c "DROP DATABASE IF EXISTS $DB"

exec "$DIR/init.sh"
