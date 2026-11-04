"""Tests for the gateway's ownership checks, input validation, and DEMO mode."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError
from render.client.errors import RenderError

from backend import main
from backend.config import settings
from backend.demo import LimiterUnavailable

CLIENT = str(uuid4())
QUESTION = {"question": "How do I deploy a web service?", "client_id": CLIENT}


class FakeWorkflows:
    def __init__(self):
        self.started = []
        self.fail_start = False
        self.run = SimpleNamespace(status="running", results=[], error=None)

    async def start_task(self, slug, payload):
        if self.fail_start == "network":
            raise ConnectionError("connection refused")
        if self.fail_start:
            raise RenderError("upstream says: secret internal detail")
        self.started.append(payload)
        return SimpleNamespace(id=f"trn-{len(self.started)}")

    async def get_task_run(self, run_id):
        return self.run


class FakeStore:
    def __init__(self):
        self.runs = {}

    async def record_run(self, token, run_id):
        self.runs[token] = {"run_id": run_id, "updates": [], "trace_id": "a" * 32}

    async def get_run(self, token, run_id):
        row = self.runs.get(token)
        return row if row and row["run_id"] == run_id else None

    async def get_recent_sessions(self, client_id, limit=20):
        return [{"id": "x", "client_id": client_id}]

    async def get_session_by_id(self, session_id, client_id):
        return None

    async def delete_session(self, session_id, client_id):
        return False

    async def delete_all_sessions(self, client_id):
        return 0


@pytest.fixture
def workflows(monkeypatch):
    wf = FakeWorkflows()
    monkeypatch.setattr(main, "get_render", lambda: SimpleNamespace(workflows=wf))
    return wf


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(main, "vector_store", s)
    return s


@pytest.fixture
def redis(monkeypatch):
    r = FakeAsyncRedis()
    monkeypatch.setattr(main, "get_redis", lambda: r)
    return r


@pytest.fixture
def demo(monkeypatch, redis):
    monkeypatch.setattr(settings, "demo", True)
    monkeypatch.setattr(settings, "demo_rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "demo_max_questions_per_visitor", 2)
    monkeypatch.setattr(settings, "demo_daily_question_limit", 200)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Run ownership (finding 2) ---------------------------------------------


async def test_poll_requires_progress_token(client, workflows, store):
    r = await client.get("/ask/trn-1")
    assert r.status_code == 422


async def test_poll_with_wrong_token_is_404(client, workflows, store):
    start = (await client.post("/ask", json=QUESTION)).json()
    r = await client.get(f"/ask/{start['run_id']}", params={"progress_token": str(uuid4())})
    assert r.status_code == 404


async def test_poll_with_token_for_other_run_is_404(client, workflows, store):
    a = (await client.post("/ask", json=QUESTION)).json()
    b = (await client.post("/ask", json=QUESTION)).json()
    r = await client.get(f"/ask/{b['run_id']}", params={"progress_token": a["progress_token"]})
    assert r.status_code == 404


async def test_poll_with_own_token_works(client, workflows, store):
    start = (await client.post("/ask", json=QUESTION)).json()
    r = await client.get(
        f"/ask/{start['run_id']}", params={"progress_token": start["progress_token"]}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "running"


async def test_logs_with_wrong_token_is_404(client, workflows, store):
    start = (await client.post("/ask", json=QUESTION)).json()
    r = await client.get(
        f"/ask/{start['run_id']}/logs", params={"progress_token": str(uuid4())}
    )
    assert r.status_code == 404


# --- Error text (finding 7) ------------------------------------------------


async def test_start_failure_hides_upstream_text(client, workflows, store):
    workflows.fail_start = True
    r = await client.post("/ask", json=QUESTION)
    assert r.status_code == 502
    assert "secret" not in r.text


async def test_failed_run_hides_upstream_text(client, workflows, store):
    start = (await client.post("/ask", json=QUESTION)).json()
    workflows.run = SimpleNamespace(status="failed", results=[], error="Traceback secret")
    r = await client.get(
        f"/ask/{start['run_id']}", params={"progress_token": start["progress_token"]}
    )
    assert r.json()["status"] == "failed"
    assert "secret" not in r.text


# --- ID validation and header (finding 8) ----------------------------------


async def test_ask_rejects_non_uuid_client_id(client, workflows, store):
    r = await client.post("/ask", json={**QUESTION, "client_id": "not-a-uuid"})
    assert r.status_code == 422


async def test_history_reads_client_id_header(client, store):
    r = await client.get("/history", headers={"X-Client-Id": CLIENT})
    assert r.status_code == 200
    assert r.json()["sessions"][0]["client_id"] == CLIENT


async def test_history_ignores_query_client_id(client, store):
    r = await client.get("/history", params={"client_id": CLIENT})
    assert r.status_code == 422


async def test_history_rejects_non_uuid_header(client, store):
    r = await client.get("/history", headers={"X-Client-Id": "abc"})
    assert r.status_code == 422


async def test_session_route_rejects_non_uuid_session_id(client, store):
    r = await client.get("/history/not-a-uuid", headers={"X-Client-Id": CLIENT})
    assert r.status_code == 422


# --- limit bound (finding 11) ----------------------------------------------


@pytest.mark.parametrize("limit", [-1, 0, 101])
async def test_history_limit_bounds(client, store, limit):
    r = await client.get(
        "/history", params={"limit": limit}, headers={"X-Client-Id": CLIENT}
    )
    assert r.status_code == 422


# --- CORS (finding 5) ------------------------------------------------------


async def test_cors_allows_client_id_header(client):
    r = await client.options(
        "/history",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-client-id",
        },
    )
    assert r.status_code == 200
    assert "x-client-id" in r.headers["access-control-allow-headers"].lower()


async def test_cors_rejects_other_origin(client):
    r = await client.options(
        "/history",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers


# --- DEMO mode -------------------------------------------------------------


async def test_demo_status_when_off(client):
    r = await client.get("/demo/status")
    assert r.json() == {"demo": False}


async def test_demo_limit_returns_429_with_retry_after(client, workflows, store, demo):
    first = await client.post("/ask", json=QUESTION)
    assert first.json()["remaining"] == 1
    await client.post("/ask", json=QUESTION)
    r = await client.post("/ask", json=QUESTION)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "Deploy your own copy" in r.json()["detail"]["message"]
    assert len(workflows.started) == 2


async def test_demo_polling_does_not_use_questions(client, workflows, store, demo):
    start = (await client.post("/ask", json=QUESTION)).json()
    for _ in range(5):
        await client.get(
            f"/ask/{start['run_id']}", params={"progress_token": start["progress_token"]}
        )
    assert (await client.get("/demo/status")).json() == {"demo": True, "remaining": 1}


async def test_demo_start_failure_gives_question_back(client, workflows, store, demo):
    workflows.fail_start = True
    await client.post("/ask", json=QUESTION)
    assert (await client.get("/demo/status")).json()["remaining"] == 2


async def test_demo_network_failure_gives_question_back(client, workflows, store, demo):
    workflows.fail_start = "network"
    r = await client.post("/ask", json=QUESTION)
    assert r.status_code == 502
    assert (await client.get("/demo/status")).json()["remaining"] == 2


async def test_demo_without_key_value_returns_503(client, workflows, store, demo, monkeypatch):
    def broken():
        raise LimiterUnavailable()

    monkeypatch.setattr(main, "get_redis", broken)
    r = await client.post("/ask", json=QUESTION)
    assert r.status_code == 503
    assert workflows.started == []


async def test_demo_key_value_error_returns_503(client, workflows, store, demo, monkeypatch):
    async def broken(*args, **kwargs):
        raise RedisConnectionError("connection refused")

    monkeypatch.setattr(main, "reserve", broken)
    r = await client.post("/ask", json=QUESTION)
    assert r.status_code == 503
    assert workflows.started == []


async def test_demo_status_without_key_value_stays_on(client, demo, monkeypatch):
    def broken():
        raise LimiterUnavailable()

    monkeypatch.setattr(main, "get_redis", broken)
    r = await client.get("/demo/status")
    assert r.status_code == 200
    assert r.json() == {"demo": True, "remaining": None}


async def test_demo_uses_cf_connecting_ip_not_forwarded_for(client, workflows, store, demo):
    for i in range(2):
        await client.post(
            "/ask",
            json=QUESTION,
            headers={"CF-Connecting-IP": "203.0.113.1", "X-Forwarded-For": f"10.0.0.{i}"},
        )
    r = await client.post(
        "/ask",
        json=QUESTION,
        headers={"CF-Connecting-IP": "203.0.113.1", "X-Forwarded-For": "10.0.0.99"},
    )
    assert r.status_code == 429


async def test_demo_history_routes_are_off(client, store, demo):
    h = {"X-Client-Id": CLIENT}
    assert (await client.get("/history", headers=h)).status_code == 404
    sid = str(uuid4())
    assert (await client.get(f"/history/{sid}", headers=h)).status_code == 404
    assert (await client.delete(f"/history/{sid}", headers=h)).status_code == 404
    assert (await client.delete("/history", headers=h)).status_code == 404
    assert (await client.get(f"/sessions/{sid}/logs", headers=h)).status_code == 404


# --- Final review fixes ----------------------------------------------------


async def test_demo_ignores_true_client_ip(client, workflows, store, demo):
    # Without CF-Connecting-IP, a client-set True-Client-IP must not make a new visitor.
    for i in range(2):
        await client.post("/ask", json=QUESTION, headers={"True-Client-IP": f"198.51.100.{i}"})
    r = await client.post("/ask", json=QUESTION, headers={"True-Client-IP": "198.51.100.99"})
    assert r.status_code == 429


async def test_demo_record_run_failure_gives_question_back(client, workflows, store, demo):
    async def broken(token, run_id):
        raise RuntimeError("database down")

    store.record_run = broken
    r = await client.post("/ask", json=QUESTION)
    assert r.status_code == 502
    assert (await client.get("/demo/status")).json()["remaining"] == 2


async def test_ask_span_does_not_record_client_id(client, workflows, store, capfire):
    await client.post("/ask", json=QUESTION)
    await client.get("/history", headers={"X-Client-Id": CLIENT})
    spans = capfire.exporter.exported_spans_as_dict()
    assert spans
    leaks = [(s["name"], k) for s in spans for k, v in s["attributes"].items() if CLIENT in str(v)]
    assert leaks == []
