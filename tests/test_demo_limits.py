"""Tests for the DEMO usage limits stored in Key Value."""

import pytest
from fakeredis import FakeAsyncRedis

from backend.demo import LimitExceeded, Limits, get_remaining, release, reserve

IP = "203.0.113.7"


@pytest.fixture
def redis():
    return FakeAsyncRedis()


async def ask(redis, n, limits, ip=IP, now=1000.0):
    results = []
    for i in range(n):
        results.append(await reserve(redis, ip, limits, now=now + i * 61))
    return results


async def test_visitor_limit_allows_ten_then_blocks(redis):
    limits = Limits(per_minute=3, per_visitor=10, daily=200)
    reservations = await ask(redis, 10, limits)
    assert [r.remaining for r in reservations][-1] == 0
    with pytest.raises(LimitExceeded) as exc:
        await reserve(redis, IP, limits, now=1000.0 + 10 * 61)
    assert exc.value.retry_after > 0


async def test_blocked_request_does_not_use_a_question(redis):
    limits = Limits(per_minute=0, per_visitor=2, daily=0)
    await ask(redis, 2, limits)
    for _ in range(3):
        with pytest.raises(LimitExceeded):
            await reserve(redis, IP, limits, now=5000.0)
    assert await get_remaining(redis, IP, limits, now=5000.0) == 0


async def test_per_minute_limit(redis):
    limits = Limits(per_minute=3, per_visitor=10, daily=200)
    for _ in range(3):
        await reserve(redis, IP, limits, now=60.0)
    with pytest.raises(LimitExceeded) as exc:
        await reserve(redis, IP, limits, now=61.0)
    assert 1 <= exc.value.retry_after <= 60
    # Next minute window is open again.
    await reserve(redis, IP, limits, now=120.0)


async def test_global_daily_limit_across_visitors(redis):
    limits = Limits(per_minute=0, per_visitor=0, daily=3)
    for i in range(3):
        await reserve(redis, f"198.51.100.{i}", limits, now=10.0)
    with pytest.raises(LimitExceeded):
        await reserve(redis, "198.51.100.99", limits, now=10.0)


async def test_release_gives_question_back(redis):
    limits = Limits(per_minute=3, per_visitor=10, daily=200)
    r = await reserve(redis, IP, limits, now=10.0)
    assert r.remaining == 9
    await release(redis, r)
    assert await get_remaining(redis, IP, limits, now=10.0) == 10


async def test_zero_disables_every_limit(redis):
    limits = Limits(per_minute=0, per_visitor=0, daily=0)
    for _ in range(50):
        r = await reserve(redis, IP, limits, now=10.0)
    assert r.remaining is None


async def test_ip_is_hashed_in_keys(redis):
    await reserve(redis, IP, Limits(per_minute=3, per_visitor=10, daily=200), now=10.0)
    keys = [k.decode() for k in await redis.keys("*")]
    assert keys and not any(IP in k for k in keys)
