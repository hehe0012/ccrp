#!/usr/bin/env bash
set -euo pipefail

# One-click launcher for the server-side ccrp HTTP proxy.
# The SSH reverse tunnel itself is created by `ccrp.py up` on the local machine.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
CCRP_SCRIPT="$PROJECT_ROOT/ccrp.py"
PYTHON="${PYTHON:-python3}"
SESSION="${CCRP_TMUX_SESSION:-ccrp-server}"
CONFIG="${CCRP_CONFIG:-}"
MODE="start"
HEALTH_URL=""

usage() {
  cat <<'EOF'
Usage:
  start-ccrp-server.sh [options]

Start the server-side ccrp HTTP reverse proxy. Run this script on the server.
The local SSH reverse tunnel must still be started separately with `ccrp.py up`.

Options:
  -c, --config PATH       Server config path
  --python PATH           Python executable (default: $PYTHON or python3)
  --session NAME          tmux session name (default: ccrp-server)
  --foreground            Run in the current terminal instead of tmux
  --restart               Stop the existing tmux session before starting
  --stop                  Stop the tmux server session
  --status                Show whether the tmux session is running
  --logs                  Show the last 100 lines of server output
  --health URL            Check a health URL after starting, for example
                          http://127.0.0.1:8080/__ccrp/health
  -h, --help              Show this help

Config search order when --config is omitted:
  1. CCRP_CONFIG environment variable
  2. ./ccrp.config.json
  3. ~/.config/ccrp/config.json

Examples:
  ./scripts/start-ccrp-server.sh
  ./scripts/start-ccrp-server.sh -c ~/.config/ccrp/config.json
  ./scripts/start-ccrp-server.sh --restart --health http://127.0.0.1:8080/__ccrp/health
  ./scripts/start-ccrp-server.sh --logs
  ./scripts/start-ccrp-server.sh --stop
EOF
}

fail() {
  echo "错误：$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--config)
      [[ $# -ge 2 ]] || fail "--config 缺少参数"
      CONFIG="$2"
      shift 2
      ;;
    --python)
      [[ $# -ge 2 ]] || fail "--python 缺少参数"
      PYTHON="$2"
      shift 2
      ;;
    --session)
      [[ $# -ge 2 ]] || fail "--session 缺少参数"
      SESSION="$2"
      shift 2
      ;;
    --foreground)
      MODE="foreground"
      shift
      ;;
    --restart)
      MODE="restart"
      shift
      ;;
    --stop)
      MODE="stop"
      shift
      ;;
    --status)
      MODE="status"
      shift
      ;;
    --logs)
      MODE="logs"
      shift
      ;;
    --health)
      [[ $# -ge 2 ]] || fail "--health 缺少参数"
      HEALTH_URL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

command -v "$PYTHON" >/dev/null 2>&1 || fail "找不到 Python：$PYTHON"
[[ -f "$CCRP_SCRIPT" ]] || fail "找不到 ccrp.py：$CCRP_SCRIPT"

if [[ -z "$CONFIG" ]]; then
  if [[ -f "$PROJECT_ROOT/ccrp.config.json" ]]; then
    CONFIG="$PROJECT_ROOT/ccrp.config.json"
  elif [[ -f "$HOME/.config/ccrp/config.json" ]]; then
    CONFIG="$HOME/.config/ccrp/config.json"
  else
    fail "找不到配置文件。请使用 --config 指定，或先运行 install-server。"
  fi
fi

if [[ "$CONFIG" != /* ]]; then
  CONFIG="$PWD/$CONFIG"
fi
[[ -f "$CONFIG" ]] || fail "配置文件不存在：$CONFIG"

# Keep tmux session names simple and predictable.
[[ "$SESSION" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "tmux session 名称只能包含字母、数字、点、下划线和短横线"

has_session() {
  tmux has-session -t "$SESSION" 2>/dev/null
}

show_status() {
  if has_session; then
    echo "运行中：tmux session $SESSION"
    tmux list-windows -t "$SESSION"
    exit 0
  fi
  echo "未运行：tmux session $SESSION"
  exit 1
}

show_logs() {
  has_session || fail "tmux session 不存在：$SESSION"
  tmux capture-pane -pt "$SESSION" -S -100
}

stop_server() {
  if has_session; then
    tmux kill-session -t "$SESSION"
    echo "已停止：$SESSION"
  else
    echo "未发现运行中的 tmux session：$SESSION"
  fi
}

if [[ "$MODE" == "status" ]]; then
  show_status
fi
if [[ "$MODE" == "logs" ]]; then
  show_logs
fi
if [[ "$MODE" == "stop" ]]; then
  stop_server
  exit 0
fi

if [[ "$MODE" == "restart" ]] && has_session; then
  stop_server
fi

if [[ "$MODE" != "foreground" ]] && has_session; then
  echo "已经在运行：tmux session $SESSION"
  echo "查看日志：$0 --logs"
  echo "进入会话：tmux attach -t $SESSION"
  exit 0
fi

if [[ "$MODE" == "foreground" ]]; then
  echo "启动服务器端 ccrp（前台模式）"
  echo "配置：$CONFIG"
  exec "$PYTHON" "$CCRP_SCRIPT" server --config "$CONFIG"
fi

command -v tmux >/dev/null 2>&1 || fail "找不到 tmux。请安装 tmux，或使用 --foreground 前台运行。"

# Quote each argument before passing the command string to tmux's shell.
quote_arg() { printf '%q' "$1"; }
command_string="$(quote_arg "$PYTHON") $(quote_arg "$CCRP_SCRIPT") server --config $(quote_arg "$CONFIG")"
tmux_command="cd $(quote_arg "$PROJECT_ROOT") && exec $command_string"
tmux new-session -d -s "$SESSION" "$tmux_command"

echo "服务器端 ccrp 已启动"
echo "配置：$CONFIG"
echo "tmux 会话：$SESSION"
echo "查看日志：$0 --logs"
echo "进入会话：tmux attach -t $SESSION"
echo "停止服务：$0 --stop"

if [[ -n "$HEALTH_URL" ]]; then
  command -v curl >/dev/null 2>&1 || fail "指定了 --health，但找不到 curl"
  echo "等待健康检查：$HEALTH_URL"
  for _ in $(seq 1 10); do
    if curl --fail --silent --show-error --max-time 3 "$HEALTH_URL"; then
      echo
      exit 0
    fi
    sleep 1
  done
  echo "健康检查失败，最近的服务器日志：" >&2
  tmux capture-pane -pt "$SESSION" -S -30 >&2 || true
  exit 3
fi