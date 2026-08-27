import asyncio

import pytest

from core.ratelimit import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_throttles_to_qps():
    limiter = RateLimiter(qps=10)  # 每 0.1s 放行一次
    start = asyncio.get_event_loop().time()
    for _ in range(3):
        await limiter.acquire()
    elapsed = asyncio.get_event_loop().time() - start
    # 3 次请求，间隔 0.1s，至少 ~0.2s
    assert elapsed >= 0.19


@pytest.mark.asyncio
async def test_rate_limiter_first_call_immediate():
    limiter = RateLimiter(qps=1)
    start = asyncio.get_event_loop().time()
    await limiter.acquire()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 0.3  # 首次立即放行


@pytest.mark.asyncio
async def test_rate_limiter_respects_qps_floor():
    limiter = RateLimiter(qps=0)  # 退化为一个固定间隔
    assert limiter.min_interval > 0
    start = asyncio.get_event_loop().time()
    for _ in range(2):
        await limiter.acquire()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= limiter.min_interval * 1.0
