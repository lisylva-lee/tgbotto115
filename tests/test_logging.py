#!/usr/bin/env python3
"""回归测试：第三方库不得把 bot token 写进日志。

PTB 通过 httpx 访问 Telegram Bot API，而 httpx 在 INFO 级记录
    HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getMe ...
run_bot.sh 会把 stdout/stderr 落到 logs/bot_*.log，所以根日志一旦是 INFO，
token 就会明文进日志文件。bot.py 通过压掉这些 logger 解决，这里把它钉住。
"""

import logging

import bot


def test_noisy_loggers_are_silenced():
    for name in bot.NOISY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING, name


def test_log_format_has_no_token_placeholder():
    assert "token" not in bot.LOG_FORMAT.lower()
