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

# 复制源码（tests/ 只服务于 CI，不进生产镜像）
COPY bot.py config.py config.example.yaml README.md ./
COPY core/ ./core/

# 数据目录（运行时通过 volume 持久化）+ 非 root 运行
RUN useradd --create-home --uid 10001 sharebot \
    && mkdir -p /app/data /app/logs \
    && chown -R sharebot:sharebot /app
USER sharebot

# 默认挂载点声明（用户需挂载 config.yaml 与数据卷）
VOLUME ["/app/data", "/app/logs"]

# 注意：这里原先的 HEALTHCHECK 是假探测（bind 一个随机本地端口，恒成功，测不出 bot
# 是否活着）。本项目没有 HTTP 端点，无法做有意义的健康检查，因此不再声明，靠
# compose 的 restart: unless-stopped / 外部监控告警。
CMD ["python", "bot.py"]