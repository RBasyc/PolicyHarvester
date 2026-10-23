#!/usr/bin/env bash
# =============================================================================
#  Agent Demo · 一键启动脚本
#
#    ./start.sh           启动后端（FastAPI :8000）与前端（Vite :5173）
#    ./start.sh check     只做环境自检，不启动任何服务
#    ./start.sh stop      停止本脚本启动的服务（先按 PID，必要时按端口兜底）
#    ./start.sh --help    帮助
#
#  兼容 Windows(Git Bash) / macOS / Linux / WSL。
#  Windows 用户可直接双击根目录的 start.bat（它会转调本脚本）。
#
#  可用环境变量：
#    START_SKIP_INSTALL=1   跳过 uv sync / pnpm install
#    START_AUTO_INIT_DB=0   数据库不存在时不自动执行 database/init.sh
#    NO_COLOR=1             关闭彩色输出
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

RUN_DIR="$ROOT/.run"
SERVER_DIR="$ROOT/server"
WEB_DIR="$ROOT/web"
MCP_DIR="$ROOT/tools/China-Central-Policy-MCP"

PG_CONTAINER="${PG_CONTAINER:-server-db-1}"
PGUSER="${PGUSER:-kos}"
PGDATABASE="${PGDATABASE:-agent_demo}"

WEB_PORT_DEFAULT=5173

# ---------------------------------------------------------------- 输出样式 ---
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'
  RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; RESET=""
fi

step() { printf '%s\n' "${CYAN}▸${RESET} $*"; }
ok()   { printf '%s\n' "${GREEN}✓${RESET} $*"; }
warn() { printf '%s\n' "${YELLOW}!${RESET} $*"; }
err()  { printf '%s\n' "${RED}✗${RESET} $*" >&2; }

die() {
  err "$*"
  printf '\n%s\n' "${DIM}可运行 ./start.sh check 单独做环境自检。${RESET}" >&2
  exit 1
}

have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ 小工具 ---
# 配置文件：顺序必须与 server/app/config.py::_ENV_FILES 一致（后文件覆盖前文件）。
ENV_FILES=("$ROOT/.env" "$ROOT/.env.features")

# 读取配置里某个键的值（两个文件都看，后文件优先；容忍引号、空白、CRLF、行内注释）
# 行内注释口径与 python-dotenv 一致：**未加引号**的值里，空白之后的 `#` 起注释。
# 这一点很要紧：模板里大量写成 `KEY=false   # 说明`，若不剥离注释，
# `BROWSER_ENABLED` 会被读成 `true   # 总开关`，匹配不上 true/1/yes——脚本于是一直
# 走普通 `uv sync`（不装 playwright），而服务端（pydantic-settings）却认为开关是开的。
env_value() {
  local raw="" file value
  for file in "${ENV_FILES[@]}"; do
    [ -f "$file" ] || continue
    value="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$file" 2>/dev/null \
      | tail -n 1 | cut -d= -f2- | tr -d '\r')"
    [ -n "$value" ] && raw="$value"
  done
  [ -n "$raw" ] || return 0
  case "$raw" in
    \"*)
      raw="${raw#\"}"
      raw="${raw%%\"*}"
      ;;
    \'*)
      raw="${raw#\'}"
      raw="${raw%%\'*}"
      ;;
    *)
      raw="${raw%%[[:space:]]#*}"
      ;;
  esac
  printf '%s' "$raw" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

# 该键在 .env 或当前 shell 环境里是否有「像样的」值（非空、非占位符）
has_value() {
  local name="$1" val="${!1:-}"
  [ -z "$val" ] && val="$(env_value "$name")"
  [ -n "$val" ] || return 1
  case "$val" in
    *xxxx*|*XXXX*|*your*|*Your*|*YOUR*|*...*|*占位符*) return 1 ;;
  esac
  return 0
}

pid_alive() {
  local pid="${1:-}"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

# 监听某端口的进程 PID（可能多个）；拿不到就无输出
port_pids() {
  local port="$1"
  if have lsof; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  elif have netstat; then
    netstat -ano 2>/dev/null \
      | awk -v pat=":$port" '$1 == "TCP" && $4 == "LISTENING" && $2 ~ (pat "$") { print $NF }' \
      | sort -u || true
  fi
}

# 强杀占用某端口的进程（Unix 用 kill，Windows/Git Bash 用 taskkill）
sweep_port() {
  local port="$1" name="$2" pids pid
  pids="$(port_pids "$port")"
  [ -n "$pids" ] || return 0
  warn "端口 $port 仍被占用（PID: $(printf '%s' "$pids" | tr '\n' ' ')），尝试结束该进程（$name）"
  for pid in $pids; do
    if have taskkill; then
      taskkill //F //PID "$pid" >/dev/null 2>&1 || true
    else
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  return 0
}

stop_pid() {  # $1=pid $2=显示名
  local pid="${1:-}" name="${2:-服务}" i=0
  [ -n "$pid" ] || return 0
  pid_alive "$pid" || return 0
  kill -TERM "$pid" 2>/dev/null || true
  while [ "$i" -lt 10 ] && pid_alive "$pid"; do
    sleep 0.3
    i=$((i + 1))
  done
  if pid_alive "$pid"; then
    warn "$name 未响应 TERM，强制结束（PID $pid）"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  return 0
}

# ---------------------------------------------------------------- 环境自检 ---
check_forwards() {
  step "检查基础工具链 ..."

  if ! have uv; then
    err "未找到 uv（Python 包管理器）"
    printf '%s\n' "   安装：https://docs.astral.sh/uv/getting-started/installation/"
    printf '%s\n' "   Windows  PowerShell 一行：irm https://astral.sh/uv/install.ps1 | iex"
    printf '%s\n' "   macOS/Linux 一行：curl -LsSf https://astral.sh/uv/install.sh | sh"
    return 1
  fi
  ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

  if ! have node; then
    err "未找到 node（MCP 政策检索服务需要 Node.js ≥ 18）"
    printf '%s\n' "   安装：https://nodejs.org/"
    return 1
  fi
  local node_ver major
  node_ver="$(node -v 2>/dev/null | sed 's/^v//')"
  major="${node_ver%%.*}"
  if [ -n "$major" ] && [ "$major" -lt 18 ] 2>/dev/null; then
    err "Node.js 版本过低：v$node_ver（需要 ≥ 18）"
    return 1
  fi
  ok "node v$node_ver"

  if ! have pnpm; then
    err "未找到 pnpm（前端包管理器）"
    printf '%s\n' "   任选其一：corepack enable pnpm    /    npm install -g pnpm"
    return 1
  fi
  ok "pnpm $(pnpm --version 2>/dev/null)"
  return 0
}

check_env_file() {
  step "检查 .env / .env.features 配置 ..."

  if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    warn "未找到 .env，已从 .env.example 生成一份静态配置模板：$ROOT/.env"
    err "但它还没有任何 API 认证信息，服务起来也调不通大模型。"
    printf '\n%s\n' "${BOLD}请编辑 .env，填写模型网关后重新运行 ./start.sh：${RESET}"
    printf '%s\n' "  OPENAI_API_KEY=sk-...                        # 网关 API Key"
    printf '%s\n' "  OPENAI_BASE_URL=https://api.deepseek.com     # OpenAI 兼容端点（DeepSeek / Kimi / OpenAI…）"
    printf '\n%s\n\n' "${DIM}协议统一为 OpenAI 兼容：任何 /v1/chat/completions 网关都能接。${RESET}"
    return 1
  fi
  ok ".env 已存在"

  # 功能开关文件：缺失时，若 .env 里还留着旧版开关就只提示迁移，绝不复制模板覆盖
  # （模板里的默认值可能把用户自己设的 false 盖回去）。
  local feature_keys="JUDGE_ENABLED|JUDGE_LAYA_ENABLED|BROWSER_ENABLED|BROWSER_HEADLESS|MCP_POLICY_ENABLED|LANGFUSE_ENABLED"
  if [ ! -f "$ROOT/.env.features" ]; then
    if grep -qE "^[[:space:]]*($feature_keys)[[:space:]]*=" "$ROOT/.env" 2>/dev/null; then
      warn "检测到 .env 里仍是旧版单文件开关（功能开关已支持拆到 .env.features）"
      warn "  现有值继续生效；迁移：把 JUDGE_*/BROWSER_*/MCP_POLICY_ENABLED/LANGFUSE_ENABLED 剪切到 .env.features"
    else
      cp "$ROOT/.env.features.example" "$ROOT/.env.features"
      warn "已生成功能开关模板：$ROOT/.env.features（模板全部注释，默认值 = 内置默认）"
    fi
  else
    ok ".env.features 已存在"
    local key
    for key in JUDGE_ENABLED JUDGE_LAYA_ENABLED BROWSER_ENABLED BROWSER_HEADLESS \
               MCP_POLICY_ENABLED LANGFUSE_ENABLED; do
      if grep -qE "^[[:space:]]*$key[[:space:]]*=" "$ROOT/.env" 2>/dev/null \
         && grep -qE "^[[:space:]]*$key[[:space:]]*=" "$ROOT/.env.features" 2>/dev/null; then
        warn "开关 $key 同时出现在两个文件；生效值来自 .env.features"
      fi
    done
  fi

  if ! has_value OPENAI_API_KEY; then
    err ".env 里没有 OPENAI_API_KEY（OpenAI 兼容网关认证，如 DeepSeek / Kimi / OpenAI）"
    printf '%s\n' "   请编辑 $ROOT/.env 填写后重新运行；占位符（sk-xxxx）不会被当作用心填过。"
    return 1
  fi
  if ! has_value OPENAI_BASE_URL; then
    warn "没有配 OPENAI_BASE_URL：会回落 OpenAI 官方地址；接 DeepSeek/Kimi 等网关请务必配置。"
  fi
  ok "已检测到 LLM 认证配置"
  return 0
}

check_ports() {
  step "检查端口占用 ..."
  local p pids
  for p in "$SERVER_PORT" "$WEB_PORT_DEFAULT"; do
    pids="$(port_pids "$p")"
    if [ -n "$pids" ]; then
      warn "端口 $p 已被占用（PID: $(printf '%s' "$pids" | tr '\n' ' ')）——若启动失败，先执行 ./start.sh stop"
    else
      ok "端口 $p 空闲"
    fi
  done
  return 0
}

check_database() {
  step "检查数据库 ..."

  if ! have docker; then
    warn "未找到 docker：抓取流水线与「成果库」不可用（对话 Agent 仍可正常使用）"
    return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    warn "Docker 未运行：抓取流水线与「成果库」不可用（对话 Agent 仍可正常使用）"
    return 0
  fi
  if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$PG_CONTAINER"; then
    warn "容器 $PG_CONTAINER 未运行：抓取流水线与「成果库」不可用"
    printf '%s\n' "   启动：docker start $PG_CONTAINER"
    return 0
  fi

  if docker exec -i "$PG_CONTAINER" psql -U "$PGUSER" -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='$PGDATABASE'" 2>/dev/null | grep -q 1; then
    ok "数据库 $PGDATABASE 就绪（容器 $PG_CONTAINER）"
    return 0
  fi

  if [ "${START_AUTO_INIT_DB:-1}" != "1" ]; then
    warn "数据库 $PGDATABASE 不存在（已按 START_AUTO_INIT_DB=0 跳过自动初始化）"
    printf '%s\n' "   手动初始化：bash database/init.sh"
    return 0
  fi

  step "数据库 $PGDATABASE 不存在，执行 database/init.sh 初始化（幂等，不影响同容器的其他库）..."
  if bash "$ROOT/database/init.sh"; then
    ok "数据库初始化完成"
  else
    warn "数据库初始化失败：抓取流水线可能不可用，可稍后手动执行 bash database/init.sh"
  fi
  return 0
}

check_ingest_judge() {
  # 入库判别服务在内网，clone 下来的人多半连不上——它 fail-open，不阻断抓取。
  # 总开关（JUDGE_ENABLED）关闭 = laya + 正文预检都不打「不建议入库」标注。
  local master laya url
  master="$(env_value JUDGE_ENABLED | tr '[:upper:]' '[:lower:]')"
  case "$master" in
    false|0|no)
      ok "入库判别总开关已关闭（不产生「不建议入库」标注；正文预检仅回填元数据）"
      return 0
      ;;
  esac

  laya="$(env_value JUDGE_LAYA_ENABLED | tr '[:upper:]' '[:lower:]')"
  case "$laya" in
    false|0|no) return 0 ;;
  esac

  url="$(env_value JUDGE_URL)"
  case "$url" in
    *192.168.*|*10.*|*172.1[6-9].*|*172.2[0-9].*|*172.3[01].*)
      warn "入库判别服务（JUDGE_URL）指向内网地址，外部环境大概率不通；"
      warn "  不通时按 fail-open 处理（全部按「建议入库」），不影响抓取，仅少了这一层筛选。"
      ;;
  esac
  return 0
}

check_browser() {
  # 无头浏览器是**可选**能力：任何一项缺失都只提示，绝不阻断启动。
  local enabled
  enabled="$(env_value BROWSER_ENABLED | tr '[:upper:]' '[:lower:]')"
  case "$enabled" in
    true|1|yes) ;;
    *) return 0 ;;
  esac

  if ! ( cd "$SERVER_DIR" && uv run python -c "import playwright" >/dev/null 2>&1 ); then
    warn "BROWSER_ENABLED=true 但后端没装 playwright："
    warn "  运行 (cd server) && uv sync --extra browser 后重试"
    return 0
  fi
  ok "无头浏览器依赖已安装（playwright）"

  # 只查 Chromium 可执行文件在不在（不启动浏览器）；首次安装后约 150MB。
  if ( cd "$SERVER_DIR" && uv run python -c '
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    raise SystemExit(0 if Path(p.chromium.executable_path).exists() else 1)
' >/dev/null 2>&1 ); then
    ok "Chromium 内核就绪"
  else
    warn "未找到 Chromium 内核：运行 (cd server) && uv run playwright install chromium"
    warn "  （浏览器来源与对话里的 browser_* 工具将不可用；其余功能不受影响）"
  fi
  return 0
}

install_deps() {
  if [ "${START_SKIP_INSTALL:-0}" = "1" ]; then
    warn "已跳过依赖安装（START_SKIP_INSTALL=1）"
    return 0
  fi

  local browser_enabled
  browser_enabled="$(env_value BROWSER_ENABLED | tr '[:upper:]' '[:lower:]')"
  case "$browser_enabled" in
    true|1|yes)
      step "同步后端依赖（uv sync --extra browser）..."
      ( cd "$SERVER_DIR" && uv sync --extra browser ) || die "uv sync --extra browser 失败，请检查网络或 uv 环境。"
      ok "后端依赖就绪（含无头浏览器）"
      ;;
    *)
      step "同步后端依赖（uv sync）..."
      ( cd "$SERVER_DIR" && uv sync ) || die "uv sync 失败，请检查网络或 uv 环境。"
      ok "后端依赖就绪"
      ;;
  esac

  step "检查前端依赖 ..."
  if [ -d "$WEB_DIR/node_modules" ] && [ ! "$WEB_DIR/pnpm-lock.yaml" -nt "$WEB_DIR/node_modules" ]; then
    ok "前端依赖已是最新（跳过 pnpm install）"
  else
    ( cd "$WEB_DIR" && pnpm install ) || die "pnpm install 失败，请检查网络或 registry 配置。"
    ok "前端依赖就绪"
  fi
  return 0
}

ensure_mcp_build() {
  step "检查 MCP 政策检索服务 ..."
  if [ -f "$MCP_DIR/build/index.js" ]; then
    ok "构建产物就绪（tools/China-Central-Policy-MCP/build/index.js）"
    return 0
  fi

  warn "缺少构建产物 build/index.js（仓库里本应已提交）"
  if [ "${START_SKIP_INSTALL:-0}" = "1" ] || ! have npm; then
    warn "已跳过构建：对话 Agent 将降级为仅本地工具，抓取流水线不可用。"
    return 0
  fi

  step "构建 MCP（npm install && npm run build）..."
  if ( cd "$MCP_DIR" && npm install && npm run build ); then
    ok "MCP 构建完成"
  else
    warn "MCP 构建失败：服务仍可启动，但对话 Agent 只能用本地工具、抓取流水线不可用。"
  fi
  return 0
}

# $1 = start | check：start 模式下自检不过直接中止；check 模式只报告
run_checks() {
  local mode="${1:-start}" failed=0

  check_forwards  || failed=1
  check_env_file  || failed=1
  check_ports
  check_database
  check_ingest_judge
  # start 模式放到 install_deps 之后查（见 do_start），避免首次安装前误报
  if [ "$mode" = "check" ]; then
    check_browser
  fi

  printf '\n'
  if [ "$failed" = "1" ]; then
    if [ "$mode" = "check" ]; then
      err "环境自检未通过：请按上面的提示处理。"
      exit 1
    fi
    die "环境自检未通过，未启动服务。请按上面的提示处理后重试。"
  fi
  ok "环境自检通过"
  return 0
}

# ------------------------------------------------------------------ 启停 ---
shutdown_services() {
  stop_pid "${SERVER_PID:-}" "后端"
  stop_pid "${WEB_PID:-}" "前端"
  rm -f "$RUN_DIR/server.pid" "$RUN_DIR/web.pid" 2>/dev/null || true
}

on_interrupt() {
  trap - INT TERM
  printf '\n'
  warn "收到退出信号，正在停止服务 ..."
  shutdown_services
  ok "已全部停止"
  exit 0
}

do_start() {
  run_checks start

  install_deps
  ensure_mcp_build
  check_browser

  mkdir -p "$RUN_DIR"
  trap on_interrupt INT TERM

  printf '\n'
  step "启动后端：uv run python main.py（端口 ${SERVER_PORT}）"
  ( cd "$SERVER_DIR" && exec uv run python main.py ) &
  SERVER_PID=$!
  printf '%s' "$SERVER_PID" > "$RUN_DIR/server.pid"

  step "启动前端：pnpm dev（端口 ${WEB_PORT_DEFAULT}）"
  ( cd "$WEB_DIR" && exec npm run dev -- --host 0.0.0.0 ) &
  WEB_PID=$!
  printf '%s' "$WEB_PID" > "$RUN_DIR/web.pid"

  sleep 3
  if ! pid_alive "$SERVER_PID"; then
    printf '\n'
    err "后端启动失败（进程已退出），请看上方日志。"
    shutdown_services
    exit 1
  fi
  if ! pid_alive "$WEB_PID"; then
    printf '\n'
    err "前端启动失败（进程已退出），请看上方日志。"
    shutdown_services
    exit 1
  fi

  printf '\n%s\n' "${GREEN}${BOLD}  服务已启动${RESET}"
  printf '%s\n'   "  ${DIM}后端${RESET}  FastAPI + Swagger  ${CYAN}http://localhost:${SERVER_PORT}/docs${RESET}"
  printf '%s\n'   "  ${DIM}前端${RESET}  Vue 3 应用         ${CYAN}http://localhost:${WEB_PORT_DEFAULT}${RESET} ${DIM}(端口被占用时 Vite 会自动换端口，以日志为准)${RESET}"
  printf '%s\n'   "  ${DIM}停止${RESET}  在此按 ${BOLD}Ctrl+C${RESET}，或另开终端执行 ${BOLD}./start.sh stop${RESET}"
  printf '\n'

  # 任一服务退出即收尾（不用 wait -n，macOS 自带 bash 3.2 不支持）
  while pid_alive "$SERVER_PID" && pid_alive "$WEB_PID"; do
    sleep 0.5
  done

  printf '\n'
  pid_alive "$SERVER_PID" || err "后端已退出（PID $SERVER_PID）"
  pid_alive "$WEB_PID"    || err "前端已退出（PID $WEB_PID）"
  warn "正在停止其余服务 ..."
  shutdown_services
  exit 1
}

do_stop() {
  local had_pid=0

  if [ -f "$RUN_DIR/server.pid" ] || [ -f "$RUN_DIR/web.pid" ]; then
    had_pid=1
    stop_pid "$(cat "$RUN_DIR/server.pid" 2>/dev/null || true)" "后端"
    stop_pid "$(cat "$RUN_DIR/web.pid" 2>/dev/null || true)"    "前端"
    rm -f "$RUN_DIR/server.pid" "$RUN_DIR/web.pid" 2>/dev/null || true
  fi

  sleep 1
  sweep_port "$SERVER_PORT" "后端"
  sweep_port "$WEB_PORT_DEFAULT" "前端"

  if [ "$had_pid" = "1" ]; then
    ok "已停止"
  else
    warn "没有找到本脚本记录的 PID（.run/*.pid 不存在），上面按端口做了兜底清理。"
  fi
  return 0
}

# ------------------------------------------------------------------ 入口 ---
usage() {
  printf '%s\n' "${BOLD}Agent Demo · 一键启动${RESET}"
  printf '\n%s\n' "${BOLD}用法${RESET}"
  printf '%s\n' "  ./start.sh           启动后端 + 前端（默认）"
  printf '%s\n' "  ./start.sh check     只做环境自检，不启动服务"
  printf '%s\n' "  ./start.sh stop      停止本脚本启动的服务"
  printf '%s\n' "  ./start.sh --help    显示本帮助"
  printf '\n%s\n' "${BOLD}环境变量${RESET}"
  printf '%s\n' "  START_SKIP_INSTALL=1   跳过 uv sync / pnpm install"
  printf '%s\n' "  START_AUTO_INIT_DB=0   数据库不存在时不自动执行 database/init.sh"
  printf '%s\n' "  NO_COLOR=1             关闭彩色输出"
  printf '\n%s\n' "${BOLD}Windows${RESET}"
  printf '%s\n' "  直接双击根目录的 start.bat 即可（它会转调本脚本）。"
  printf '\n'
}

print_banner() {
  printf '\n%s\n'   "${BOLD}  Agent Demo${RESET} ${DIM}· 政策文件事实提取${RESET}"
  printf '%s\n\n'   "${DIM}  ────────────────────────────────────────────────${RESET}"
}

main() {
  print_banner

  # 后端端口以 .env 里的 PORT 为准
  SERVER_PORT="$(env_value PORT)"
  SERVER_PORT="${SERVER_PORT:-8000}"

  case "${1:-start}" in
    start)              do_start ;;
    check)              run_checks check ;;
    stop)               do_stop ;;
    -h|--help|help)     usage ;;
    *)                  err "未知参数：$1"; printf '\n'; usage; exit 2 ;;
  esac
}

main "$@"
