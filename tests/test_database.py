"""Transaction tests for VectorStore and replace_source. Set TEST_DATABASE_URL to run them.

The tests write only rows with a ``test://`` source and delete them after each test.
"""

import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest

from backend import database, ingestion
from backend.config import settings
from backend.database import VectorStore

TEST_SOURCE_PREFIX = "test://"


def _embedding(dims: int = settings.embedding_dimensions) -> list[float]:
    return [0.1] * dims


def _row(source: str, embedding: list[float] | None = None) -> tuple:
    return ("content", source, "title", embedding or _embedding(), None, {})


async def _count(store: VectorStore, source: str) -> int:
    return await store.pool.fetchval("SELECT count(*) FROM documents WHERE source = $1", source)


@pytest.fixture
def database_url(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    monkeypatch.setattr(database.settings, "database_url", url)
    return url


@pytest.fixture
async def db_store(database_url):
    store = VectorStore()
    await store.initialize()
    yield store
    await store.pool.execute("DELETE FROM documents WHERE source LIKE $1", f"{TEST_SOURCE_PREFIX}%")
    await store.close()


@pytest.fixture
def source():
    return f"{TEST_SOURCE_PREFIX}{uuid4()}"


async def test_initialize_runs_concurrently(database_url):
    stores = [VectorStore() for _ in range(3)]
    try:
        await asyncio.gather(*(store.initialize() for store in stores))
    finally:
        await asyncio.gather(*(store.close() for store in stores))


async def test_insert_documents_batch_returns_ids(db_store, source):
    ids = await db_store.insert_documents_batch([_row(source), _row(source)])

    assert len(ids) == 2
    assert await _count(db_store, source) == 2


async def test_insert_documents_batch_rolls_back_on_failure(db_store, source):
    # The second embedding has the wrong number of dimensions, so its insert fails.
    rows = [_row(source), _row(source, embedding=_embedding(3))]

    with pytest.raises(asyncpg.PostgresError):
        await db_store.insert_documents_batch(rows)

    assert await _count(db_store, source) == 0


async def test_replace_source_clears_source_and_legacy_rows(db_store, source, monkeypatch):
    monkeypatch.setattr(ingestion, "vector_store", db_store)
    legacy = f"{source}-legacy"
    other = f"{source}-other"
    await db_store.insert_documents_batch([_row(source), _row(source), _row(legacy), _row(other)])

    doc = {"content": "new", "title": "title", "embedding": _embedding()}
    inserted = await ingestion.replace_source(source, [doc], legacy=(legacy,))

    assert inserted == 1
    assert await _count(db_store, source) == 1
    assert await _count(db_store, legacy) == 0
    assert await _count(db_store, other) == 1
