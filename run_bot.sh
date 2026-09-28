#!/bin/bash
# =====================================================
# 启动 115 ShareBot（后台运行 + 每日日志）
# =====================================================
# 用法：
#   ./run_bot.sh             启动（依赖已装好时最快）
#   ./run_bot.sh --install   先按 requirements.txt 安装/校验依赖再启动
#
# 与旧版的区别：
#   1. 不再硬编码 /usr/local/bin/python3.13（换机器必挂），改用 PYTHON_BIN 或 python3；
#   2. 不再每次启动都 pip install -r requirements.txt（慢，而且会顺手升级依赖，
#      对长跑进程很危险）—— 需要装依赖时显式加 --install；
#   3. 不再传 bot.py 根本不解析的 --instance-name 参数；
#   4. 记录 PID 到 logs/bot.pid，便于停止。
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="venv"
LOG_DIR="logs"
LOG_FILE="$LOG_DIR/bot_$(date +%Y-%m-%d).log"
PID_FILE="$LOG_DIR/bot.pid"

if [ ! -d "$VENV_DIR" ]; then
  echo "[INFO] 未检测到虚拟环境，正在用 $PYTHON_BIN 创建..."
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    echo "[ERROR] 创建虚拟环境失败。可先设置 PYTHON_BIN 指向可用的 python3。"
    exit 1
  fi
fi

VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ] && [ -x "$VENV_DIR/Scripts/python.exe" ]; then
  VENV_PY="$VENV_DIR/Scripts/python.exe"   # Git Bash on Windows
fi

if [ "${1:-}" = "--install" ]; then
  echo "[INFO] 安装/校验依赖（requirements.txt）..."
  "$VENV_PY" -m pip install --upgrade pip
  "$VENV_PY" -m pip install -r requirements.txt
fi

if [ ! -f "config.yaml" ]; then
  echo "[ERROR] 缺少 config.yaml。请先执行："
  echo "  cp config.example.yaml config.yaml"
  echo "  然后编辑 config.yaml 填写 telegram.bot_token 与 p115.cookie"
  exit 1
fi

mkdir -p "$LOG_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "[ERROR] 已有实例在运行（PID $(cat "$PID_FILE")）。先停止：kill $(cat "$PID_FILE")"
  exit 1
fi

echo "[INFO] 后台启动 bot.py，日志写入 $LOG_FILE"
nohup "$VENV_PY" bot.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

echo "[INFO] 已启动（PID $(cat "$PID_FILE")）。"
echo "[INFO] 查看日志：tail -f $LOG_FILE"
echo "[INFO] 停止：kill $(cat "$PID_FILE")"
