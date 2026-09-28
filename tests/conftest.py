#!/usr/bin/env python3
"""测试公共前置。

两件事：

1. config.py 在 import 时就会读取 config.yaml 并要求 bot_token / cookie 非空，
   所以在没有配置文件的机器（CI、新 clone）上，整个测试套件会直接抛 ConfigError。
   这里只在 config.yaml 不存在时生成一份占位配置，绝不覆盖真实配置。
2. 每个用例前重置进程级单例（P115ClientWrapper / 限速器）。pytest-asyncio 会给每个
   用例新建事件循环，而 asyncio 的 Lock/Semaphore 一旦被某个循环用过就不能跨循环
   复用，不重置会得到 "is bound to a different event loop"。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.yaml"

if not CONFIG_FILE.exists():
    CONFIG_FILE.write_text(
        'telegram:\n  bot_token: "123456:test-token"\n'
        'p115:\n  cookie: "UID=1_A1_test; CID=test; SEID=test"\n',
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _reset_async_singletons(monkeypatch):
    from core import ratelimit
    from core.client import P115ClientWrapper

    monkeypatch.setattr(P115ClientWrapper, "_instance", None, raising=False)
    # 让分页/网络相关用例不必真的等 1 QPS
    monkeypatch.setattr(ratelimit, "_instance", ratelimit.RateLimiter(qps=1000.0), raising=False)
    yield
