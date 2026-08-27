#!/bin/bash

# =====================================================
# 完全隐藏后台运行 Bot 并每日生成日志 (Linux 版)
# =====================================================

VENV_DIR="venv"

# 获取当前日期，格式 YYYY-MM-DD
YEAR=$(date +%Y)
MONTH=$(date +%m)
DAY=$(date +%d)
LOG_DIR="logs" # 明确定义日志目录
LOG_FILE="${LOG_DIR}/bot_${YEAR}-${MONTH}-${DAY}.log"

echo "[INFO] 日志文件将保存到 logs/${LOG_FILE}"

# 检查虚拟环境
if [ ! -d "$VENV_DIR" ]; then
    echo "[INFO] 未检测到虚拟环境，正在创建 '$VENV_DIR'..."
    # 确保系统安装了 python3，用于创建虚拟环境
    /usr/local/bin/python3.13 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then # 检查上一条命令是否成功执行
        echo "[ERROR] 无法创建虚拟环境。请确保 'python3' 命令可用。"
        exit 1
    fi
fi

# 激活虚拟环境
echo "[INFO] 激活虚拟环境 '$VENV_DIR'..."
# 'source' 或 '.' 命令用于在当前 shell 中执行脚本，从而激活虚拟环境
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
    echo "[ERROR] 无法激活虚拟环境。请检查路径或文件是否存在。"
    exit 1
fi

# 安装或更新依赖
echo "[INFO] 正在安装/更新 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "[ERROR] 无法安装 Python 依赖。请检查网络或pip配置。"
    exit 1
fi

# 确保 logs 目录存在
mkdir -p logs

# 检查配置文件
if [ ! -f "config.yaml" ]; then
    echo "[ERROR] 缺少 config.yaml。请先执行："
    echo "  cp config.example.yaml config.yaml"
    echo "  然后编辑 config.yaml 填写 telegram.bot_token 与 p115.cookie"
    exit 1
fi
echo "[INFO] 已找到配置文件 config.yaml"

echo "[INFO] 正在后台运行 bot.py，并将输出写入 logs/${LOG_FILE}..."
# 后台运行 bot.py，并将所有输出重定向到日志文件
# 'nohup' 确保脚本在用户退出终端后继续运行
# 'python' 会指向虚拟环境中的 python 解释器
# '>' 重定向标准输出到文件
# '2>&1' 重定向标准错误到标准输出（即也写入日志文件）
# '&' 将整个命令放到后台执行
BOT_IDENTIFIER="--instance-name sharebot" # 为这个Bot定义一个唯一标识符
nohup python bot.py $BOT_IDENTIFIER >> "$LOG_FILE" 2>&1 &
# 或者如果你使用的是虚拟环境的python，修改为：
# ./venv/bin/python bot.py $BOT_IDENTIFIER >> "$LOG_FILE" 2>&1 &

echo "[INFO] bot.py 已尝试后台启动。您可以使用 'tail -f logs/${LOG_FILE}' 查看实时日志。"
echo "[INFO] 您可以使用 'ps aux | grep bot.py' 或 'pgrep -f bot.py' 检查进程是否正在运行。"

# 注意：这里我们没有使用 deactivate。
# 因为 bot.py 进程已经在后台启动，它将独立于当前 shell 运行。
# 如果在这里调用 deactivate，只会影响当前 shell 的环境，而不会影响后台运行的 bot 进程。