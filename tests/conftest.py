"""Test setup: give Settings the env vars it requires before any backend import."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("LOGFIRE_TOKEN", "test")
os.environ.setdefault("LOGFIRE_SEND_TO_LOGFIRE", "false")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("WORKFLOW_SLUG", "test-workflow")

import asyncpg
import pytest

from backend.database import DEMO_COUNTERS_SCHEMA


@pytest.fixture
async def pool():
    """A pool for the demo limit tests. Skip the test if TEST_DATABASE_URL is not set."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    await pool.execute(DEMO_COUNTERS_SCHEMA)
    await pool.execute("TRUNCATE demo_counters")
    yield pool
    await pool.execute("TRUNCATE demo_counters")
    await pool.close()
