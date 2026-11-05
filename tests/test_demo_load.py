"""Load tests for the demo limits.

Each test sends a burst of concurrent POST /ask requests through the real route
and the real Postgres counters. The Workflows client is a stub, so no test
starts a pipeline run or calls an LLM. Set TEST_DATABASE_URL to run them.
"""

import asyncio
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest

from backend import main
from backend.config import settings

QUESTION = {"question": "How do I deploy a web service?"}


class StubWorkflows:
    def __init__(self, fail_every: int = 0):
        self.started = 0
        self.attempts = 0
        self.fail_every = fail_every

    async def start_task(self, slug, payload):
        self.attempts += 1
        if self.fail_every and self.attempts % self.fail_every == 0:
            raise ConnectionError("connection refused")
        self.started += 1
        await asyncio.sleep(0.01)  # Keep the request open while other requests reserve.
        return SimpleNamespace(id=f"trn-{self.attempts}")


class StubStore:
    async def record_run(self, token, run_id):
        pass


@pytest.fixture
def run_burst(monkeypatch, pool):
    """Return a function that sets the limits, sends a burst, and returns the status codes."""

    async def run(ips, per_minute=0, per_visitor=0, daily=0, workflows=None):
        workflows = workflows or StubWorkflows()
        monkeypatch.setattr(main, "get_pool", lambda: pool)
        monkeypatch.setattr(main, "get_render", lambda: SimpleNamespace(workflows=workflows))
        monkeypatch.setattr(main, "vector_store", StubStore())
        monkeypatch.setattr(settings, "demo", True)
        monkeypatch.setattr(settings, "demo_rate_limit_per_minute", per_minute)
        monkeypatch.setattr(settings, "demo_max_questions_per_visitor", per_visitor)
        monkeypatch.setattr(settings, "demo_daily_question_limit", daily)

        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(*(
                client.post("/ask", json=QUESTION, headers={"CF-Connecting-IP": ip}) for ip in ips
            ))
        return Counter(r.status_code for r in responses), responses, workflows

    return run


async def _count(pool, key_part: str) -> int:
    return await pool.fetchval(
        "SELECT COALESCE(SUM(count), 0) FROM demo_counters WHERE key LIKE $1", f"%{key_part}%"
    )


async def test_one_visitor_burst_gets_only_the_per_minute_limit(run_burst, pool):
    codes, _, workflows = await run_burst(["203.0.113.1"] * 50, per_minute=3, per_visitor=10)
    assert codes == {202: 3, 429: 47}
    assert workflows.started == 3
    # Blocked requests give their question back, so the counters show only the accepted ones.
    assert await _count(pool, ":minute:") == 3
    assert await _count(pool, "demo:all:") == 3


async def test_minute_limit_burst_gets_a_minute_retry_after(run_burst):
    _, responses, _ = await run_burst(["203.0.113.1"] * 50, per_minute=3, per_visitor=10)
    assert all(int(r.headers["retry-after"]) <= 60 for r in responses if r.status_code == 429)


async def test_one_visitor_burst_does_not_block_other_visitors(run_burst):
    attacker = "203.0.113.1"
    # Send one request from another visitor after every 20 attacker requests.
    ips = [ip for i in range(20) for ip in [attacker] * 20 + [f"198.51.100.{i}"]]
    _, responses, _ = await run_burst(ips, per_minute=3, per_visitor=10, daily=200)
    others = [r.status_code for ip, r in zip(ips, responses) if ip != attacker]
    assert others == [202] * 20


async def test_one_visitor_burst_gets_only_the_per_visitor_limit(run_burst):
    codes, responses, _ = await run_burst(["203.0.113.1"] * 50, per_visitor=10)
    assert codes == {202: 10, 429: 40}
    remaining = sorted(r.json()["remaining"] for r in responses if r.status_code == 202)
    assert remaining == list(range(10))


async def test_many_visitors_burst_gets_only_the_daily_limit(run_burst, pool):
    ips = [f"198.51.{i // 256}.{i % 256}" for i in range(150) for _ in range(2)]
    codes, _, workflows = await run_burst(ips, per_visitor=10, daily=200)
    assert codes == {202: 200, 429: 100}
    assert workflows.started == 200
    assert await _count(pool, "demo:all:") == 200


async def test_failed_starts_under_load_give_questions_back(run_burst, pool):
    workflows = StubWorkflows(fail_every=3)
    codes, _, _ = await run_burst(["203.0.113.1"] * 30, per_visitor=100, workflows=workflows)
    assert codes == {202: 20, 502: 10}
    assert await _count(pool, "demo:all:") == 20
