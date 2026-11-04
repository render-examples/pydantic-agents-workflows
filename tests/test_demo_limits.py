"""Tests for the DEMO usage limits stored in Postgres."""

import asyncio

import pytest

from backend.demo import LimitExceeded, Limits, get_remaining, release, reserve

IP = "203.0.113.7"


async def ask(pool, n, limits, ip=IP, now=1000.0):
    results = []
    for i in range(n):
        results.append(await reserve(pool, ip, limits, now=now + i * 61))
    return results


async def test_visitor_limit_allows_ten_then_blocks(pool):
    limits = Limits(per_minute=3, per_visitor=10, daily=200)
    reservations = await ask(pool, 10, limits)
    assert [r.remaining for r in reservations][-1] == 0
    with pytest.raises(LimitExceeded) as exc:
        await reserve(pool, IP, limits, now=1000.0 + 10 * 61)
    assert exc.value.retry_after > 0


async def test_blocked_request_does_not_use_a_question(pool):
    limits = Limits(per_minute=0, per_visitor=2, daily=0)
    await ask(pool, 2, limits)
    for _ in range(3):
        with pytest.raises(LimitExceeded):
            await reserve(pool, IP, limits, now=5000.0)
    assert await get_remaining(pool, IP, limits, now=5000.0) == 0


async def test_per_minute_limit(pool):
    limits = Limits(per_minute=3, per_visitor=10, daily=200)
    for _ in range(3):
        await reserve(pool, IP, limits, now=60.0)
    with pytest.raises(LimitExceeded) as exc:
        await reserve(pool, IP, limits, now=61.0)
    assert 1 <= exc.value.retry_after <= 60
    # Next minute window is open again.
    await reserve(pool, IP, limits, now=120.0)


async def test_global_daily_limit_across_visitors(pool):
    limits = Limits(per_minute=0, per_visitor=0, daily=3)
    for i in range(3):
        await reserve(pool, f"198.51.100.{i}", limits, now=10.0)
    with pytest.raises(LimitExceeded):
        await reserve(pool, "198.51.100.99", limits, now=10.0)


async def test_release_gives_question_back(pool):
    limits = Limits(per_minute=3, per_visitor=10, daily=200)
    r = await reserve(pool, IP, limits, now=10.0)
    assert r.remaining == 9
    await release(pool, r)
    assert await get_remaining(pool, IP, limits, now=10.0) == 10


async def test_zero_disables_every_limit(pool):
    limits = Limits(per_minute=0, per_visitor=0, daily=0)
    for _ in range(50):
        r = await reserve(pool, IP, limits, now=10.0)
    assert r.remaining is None


async def test_ip_is_hashed_in_keys(pool):
    await reserve(pool, IP, Limits(per_minute=3, per_visitor=10, daily=200), now=10.0)
    keys = [row["key"] for row in await pool.fetch("SELECT key FROM demo_counters")]
    assert keys and not any(IP in k for k in keys)


async def test_concurrent_requests_get_different_counts(pool):
    limits = Limits(per_minute=0, per_visitor=10, daily=0)
    results = await asyncio.gather(
        reserve(pool, IP, limits, now=10.0), reserve(pool, IP, limits, now=10.0)
    )
    assert sorted(r.remaining for r in results) == [8, 9]


async def test_old_windows_are_deleted(pool):
    limits = Limits(per_minute=3, per_visitor=10, daily=200)
    await reserve(pool, IP, limits, now=10.0)
    await reserve(pool, IP, limits, now=10.0 + 86_400)
    keys = [row["key"] for row in await pool.fetch("SELECT key FROM demo_counters")]
    assert len(keys) == 3 and all(k.endswith(":1") for k in keys if ":day:" in k)
