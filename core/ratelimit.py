#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/ratelimit.py - 全局限速器（RateLimiter）

防止对 115 API 的高频请求触发风控导致 cookie 失效。
所有 115 请求（经 P115ClientWrapper.run_blocking_io）统一按 QPS 节流。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """基于固定最小间隔的全局限速器。

    保证任意两次 `acquire()` 之间的时间间隔 >= min_interval（= 1 / qps）。
    首次调用立即放行；并发调用通过 asyncio.Lock 排队。
    """

    def __init__(self, qps: float = 1.0):
        self._lock = asyncio.Lock()
        self._last_ts: float = 0.0
        self._qps = max(float(qps), 0.0)
        # 固定间隔 = 1/QPS；QPS<=0 时退化为 1 秒
        self.min_interval = (1.0 / self._qps) if self._qps > 0 else 1.0

    async def acquire(self) -> None:
        """等待直到允许发起下一次请求。"""
        async with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_ts = time.monotonic()

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        return None


# 全局单例（应用级 QPS=1），由 client 层注入实际 qps
_instance: Optional[RateLimiter] = None


def get_rate_limiter(qps: float = 1.0) -> RateLimiter:
    """返回全局限速器单例（首次按 qps 初始化）。"""
    global _instance
    if _instance is None:
        _instance = RateLimiter(qps)
    return _instance


def reset_rate_limiter() -> None:
    """重置单例（测试用）。"""
    global _instance
    _instance = None
