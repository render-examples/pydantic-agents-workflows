"""Usage limits for the public demo (DEMO=true).

Each question increments three fixed-window counters in Render Key Value: the
visitor's minute, the visitor's day, and the global day. The visitor key holds a
SHA-256 hash of the IP address, so Key Value never stores the raw address.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Request
from redis.asyncio import Redis

from backend.config import settings

MINUTE = 60
DAY = 86_400

LIMIT_MESSAGE = "Demo limit reached. Deploy your own copy to keep going."
DEPLOY_URL = "https://render.com/deploy?repo=https://github.com/render-examples/pydantic-agents-workflows"


class LimitExceeded(Exception):
    """A demo limit is used up. ``retry_after`` is in seconds."""

    def __init__(self, retry_after: int):
        super().__init__(LIMIT_MESSAGE)
        self.retry_after = retry_after


class LimiterUnavailable(Exception):
    """Key Value is not configured or not reachable."""


@dataclass(frozen=True)
class Limits:
    per_minute: int
    per_visitor: int
    daily: int

    @classmethod
    def from_settings(cls) -> Limits:
        return cls(
            per_minute=settings.demo_rate_limit_per_minute,
            per_visitor=settings.demo_max_questions_per_visitor,
            daily=settings.demo_daily_question_limit,
        )


@dataclass(frozen=True)
class Reservation:
    """The counter keys one question incremented, so it can be given back."""

    keys: tuple[str, ...]
    remaining: int | None


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    """Return the shared Key Value client. Raise if REDIS_URL is not set."""
    if not settings.redis_url:
        raise LimiterUnavailable("REDIS_URL is not set")
    return Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)


def client_ip(request: Request) -> str:
    """Return the visitor's IP address.

    Render's edge (Cloudflare) sets CF-Connecting-IP and replaces any value the
    client sends. True-Client-IP and X-Forwarded-For are not used, because a
    client can set them.
    """
    value = request.headers.get("cf-connecting-ip")
    if value:
        return value.strip()
    return request.client.host if request.client else "unknown"


def _keys(ip: str, now: float) -> tuple[str, str, str]:
    visitor = hashlib.sha256(ip.encode()).hexdigest()
    minute = int(now // MINUTE)
    day = int(now // DAY)
    return (
        f"demo:visitor:{visitor}:minute:{minute}",
        f"demo:visitor:{visitor}:day:{day}",
        f"demo:all:day:{day}",
    )


def _remaining(limits: Limits, visitor_day: int, all_day: int) -> int | None:
    left = [
        limit - used
        for limit, used in ((limits.per_visitor, visitor_day), (limits.daily, all_day))
        if limit > 0
    ]
    return max(0, min(left)) if left else None


def _seconds_to_window_end(now: float, window: int) -> int:
    return max(1, math.ceil(window - (now % window)))


async def reserve(redis: Redis, ip: str, limits: Limits, now: float | None = None) -> Reservation:
    """Use one question for ``ip``. Raise ``LimitExceeded`` if any limit is used up."""
    now = time.time() if now is None else now
    minute_key, visitor_key, all_key = keys = _keys(ip, now)

    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(minute_key).expire(minute_key, MINUTE)
        pipe.incr(visitor_key).expire(visitor_key, DAY)
        pipe.incr(all_key).expire(all_key, DAY)
        minute_count, _, visitor_count, _, all_count, _ = await pipe.execute()

    retry_after = 0
    if 0 < limits.per_minute < minute_count:
        retry_after = max(retry_after, _seconds_to_window_end(now, MINUTE))
    if 0 < limits.per_visitor < visitor_count or 0 < limits.daily < all_count:
        retry_after = max(retry_after, _seconds_to_window_end(now, DAY))

    reservation = Reservation(keys=keys, remaining=_remaining(limits, visitor_count, all_count))
    if retry_after:
        await release(redis, reservation)
        raise LimitExceeded(retry_after)
    return reservation


async def release(redis: Redis, reservation: Reservation) -> None:
    """Give back a question, for example when the pipeline run did not start."""
    async with redis.pipeline(transaction=True) as pipe:
        for key in reservation.keys:
            pipe.decr(key)
        await pipe.execute()


async def get_remaining(redis: Redis, ip: str, limits: Limits, now: float | None = None) -> int | None:
    """Return how many questions ``ip`` can still ask today, without using one."""
    now = time.time() if now is None else now
    _, visitor_key, all_key = _keys(ip, now)
    visitor_count, all_count = await redis.mget(visitor_key, all_key)
    return _remaining(limits, int(visitor_count or 0), int(all_count or 0))
