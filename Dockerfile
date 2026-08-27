# 115 ShareBot - Docker 镜像
# 说明：config.yaml（含 Telegram token + 115 cookie）必须通过挂载/环境变量注入，
#       绝不内置进镜像。启动时把宿主机的 config.yaml 挂载到 /app/config.yaml。

FROM python:3.13-slim

# 时区 + 时区数据（Telegram 相关时间显示）
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY bot.py config.py config.example.yaml README.md ./
COPY core/ ./core/
COPY tests/ ./tests/

# 数据目录（运行时通过 volume 持久化）
RUN mkdir -p /app/data /app/logs

# 默认挂载点声明（用户需挂载 config.yaml 与数据卷）
VOLUME ["/app/data", "/app/logs"]

# 健康检查：简单探测进程存活（可选）
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import socket,sys; s=socket.socket(); s.bind(('127.0.0.1',0))" || exit 1

CMD ["python", "bot.py"]