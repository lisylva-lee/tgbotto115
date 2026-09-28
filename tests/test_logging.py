#!/usr/bin/env python3
"""回归测试：凭据不得进入日志。

两条独立的泄露路径：

1. httpx 在 INFO 级记录完整请求 URL，而 Telegram Bot API 的 URL 形如
   https://api.telegram.org/bot<TOKEN>/getMe —— 根日志为 INFO 时 token 直接进日志文件
   （run_bot.sh 会把 stdout/stderr 落盘）。对策：把这些 logger 压到 WARNING。
2. 压级别挡不住"异常信息自带 token"：PTB 在 token 无效时抛
   InvalidToken: The token <id>:<secret> was rejected by the server.
   经 telegram.ext 记录或作为未处理异常打印都会落盘。对策：RedactSecretsFilter 兜底脱敏。
"""

import io
import logging

import bot

FAKE_TOKEN = "123456:AAHtestTESTtestTESTtestTESTtest"


def test_noisy_loggers_are_silenced():
    for name in bot.NOISY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING, name


def test_redact_masks_bot_token():
    out = bot.redact("InvalidToken: The token " + FAKE_TOKEN + " was rejected by the server.")
    assert FAKE_TOKEN not in out
    assert bot.REDACTED in out


def test_redact_masks_115_cookie_values():
    out = bot.redact("cookie: UID=12345_A1_abcdef; CID=xyz; SEID=abc")
    assert "12345_A1_abcdef" not in out
    assert "UID=<REDACTED>" in out


def test_redact_filter_rewrites_exception_traceback():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(bot.RedactSecretsFilter())

    log = logging.getLogger("tgbotto115.redact.test")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)

    try:
        raise RuntimeError("The token " + FAKE_TOKEN + " was rejected by the server.")
    except RuntimeError:
        log.exception("启动失败")

    out = stream.getvalue()
    assert FAKE_TOKEN not in out
    assert bot.REDACTED in out
    assert "启动失败" in out
