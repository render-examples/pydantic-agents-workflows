"""Usage limits for the public demo (DEMO=true).

Each question increments three fixed-window counters in the Postgres
``demo_counters`` table: the visitor's minute, the visitor's day, and the global
day. The visitor key holds a SHA-256 hash of the IP address, so the database
never stores the raw address.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass

import asyncpg
from fastapi import Request

from backend.config import settings
from backend.database import vector_store

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
    """The database pool is not ready."""


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


def get_pool() -> asyncpg.Pool:
    """Return the shared database pool. Raise if it is not initialized."""
    if vector_store.pool is None:
        raise LimiterUnavailable("Database pool is not initialized")
    return vector_store.pool


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


async def reserve(pool: asyncpg.Pool, ip: str, limits: Limits, now: float | None = None) -> Reservation:
    """Use one question for ``ip``. Raise ``LimitExceeded`` if any limit is used up."""
    now = time.time() if now is None else now
    minute_key, visitor_key, all_key = keys = _keys(ip, now)
    minute_end = (now // MINUTE + 1) * MINUTE
    day_end = (now // DAY + 1) * DAY

    # Postgres locks each row until commit, so concurrent requests get different
    # counts. Every statement locks rows in key order, so two requests cannot deadlock.
    ends = {minute_key: minute_end, visitor_key: day_end, all_key: day_end}
    ordered = sorted(keys)

    async with pool.acquire() as conn, conn.transaction():
        # Remove counters from past windows. Skip rows that another request is deleting.
        await conn.execute(
            """
            DELETE FROM demo_counters WHERE key IN (
                SELECT key FROM demo_counters WHERE expires_at < to_timestamp($1)
                ORDER BY key FOR UPDATE SKIP LOCKED
            )
            """,
            now,
        )
        rows = await conn.fetch(
            """
            INSERT INTO demo_counters (key, count, expires_at)
            SELECT k, 1, to_timestamp(e)
            FROM unnest($1::text[], $2::float8[]) AS t(k, e)
            ON CONFLICT (key) DO UPDATE SET count = demo_counters.count + 1
            RETURNING key, count
            """,
            ordered,
            [ends[k] for k in ordered],
        )
        counts = {row["key"]: row["count"] for row in rows}  # RETURNING does not keep input order.
        minute_count, visitor_count, all_count = (counts[k] for k in keys)

        retry_after = 0
        if 0 < limits.per_minute < minute_count:
            retry_after = max(retry_after, _seconds_to_window_end(now, MINUTE))
        if 0 < limits.per_visitor < visitor_count or 0 < limits.daily < all_count:
            retry_after = max(retry_after, _seconds_to_window_end(now, DAY))
        if retry_after:
            # Raise before commit, so the rollback removes the increments and
            # other requests never see the counts of a blocked request.
            raise LimitExceeded(retry_after)

    return Reservation(keys=keys, remaining=_remaining(limits, visitor_count, all_count))


async def release(pool: asyncpg.Pool, reservation: Reservation) -> None:
    """Give back a question, for example when the pipeline run did not start."""
    await pool.execute(
        """
        UPDATE demo_counters d SET count = d.count - 1
        FROM (
            SELECT key FROM demo_counters WHERE key = ANY($1::text[])
            ORDER BY key FOR UPDATE
        ) locked
        WHERE d.key = locked.key
        """,
        list(reservation.keys),
    )


async def get_remaining(pool: asyncpg.Pool, ip: str, limits: Limits, now: float | None = None) -> int | None:
    """Return how many questions ``ip`` can still ask today, without using one."""
    now = time.time() if now is None else now
    _, visitor_key, all_key = _keys(ip, now)
    rows = await pool.fetch(
        "SELECT key, count FROM demo_counters WHERE key = ANY($1::text[])",
        [visitor_key, all_key],
    )
    counts = {row["key"]: row["count"] for row in rows}
    return _remaining(limits, counts.get(visitor_key, 0), counts.get(all_key, 0))
