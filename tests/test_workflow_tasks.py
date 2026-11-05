"""Tests for the tasks that the Workflows app registers."""

import asyncio
import json

import pytest
from pydantic_ai.models.test import TestModel

from backend.models import Claim, Document, EvaluationOutput
from backend.pipeline import accuracy, claims, evaluation, generation, query_expansion
from workflows import app as wf
from workflows.app import app

_AGENT_NAMES = ["answer", "claims", "accuracy", "eval_openai", "eval_anthropic"]

# The tasks RenderWorkflows generates for each agent. Render uses these names to
# store runs, so a change after a harness upgrade must be deliberate.
_GENERATED_SUFFIXES = [
    "model.request",
    "model.request_stream",
    "model.compact_messages",
    "model.cancel_suspended_response",
    "function_toolset__<agent>.call_tool",
    "function_toolset__<agent>.validate_args",
]


def test_app_registers_entry_ingest_and_generated_tasks():
    names = set(app._registry.get_task_names())
    assert names == {
        "run_qa_pipeline",
        "ingest_core",
        "ingest_source",
        "ingest_all",
        *(f"{agent}__{suffix}" for agent in _AGENT_NAMES for suffix in _GENERATED_SUFFIXES),
    }


class FakeTaskContext:
    """Run each chained task in this process and keep its name.

    Arguments and results go through JSON, as they do between Render task runs.
    """

    def __init__(self):
        self.calls = []

    async def run(self, task, *args, **kwargs):
        self.calls.append(task.name)
        args, kwargs = json.loads(json.dumps([args, kwargs]))
        return json.loads(json.dumps(await task.func(self, *args, **kwargs)))


def _stub_model(monkeypatch, agent):
    """Replace the model request of the registered model instance with a TestModel request."""
    monkeypatch.setattr(agent.model, "request", TestModel().request)


_DOC = Document(content="Web services listen on PORT.", source="https://render.com/docs", similarity_score=0.9)


@pytest.mark.parametrize(
    "agent, run, task_name",
    [
        (generation._answer_agent, lambda: generation.generate_answer("Q?", [_DOC]), "answer__model.request"),
        (claims._claims_agent, lambda: claims.extract_claims("A."), "claims__model.request"),
        (accuracy._accuracy_agent, lambda: accuracy.check_accuracy("A.", []), "accuracy__model.request"),
        (evaluation._openai_eval_agent, lambda: evaluation.evaluate_with_openai("Q?", "A.", 1), "eval_openai__model.request"),
        (evaluation._anthropic_eval_agent, lambda: evaluation.evaluate_with_anthropic("Q?", "A.", 1), "eval_anthropic__model.request"),
    ],
)
async def test_heavy_stage_sends_model_request_to_chained_run(monkeypatch, agent, run, task_name):
    _stub_model(monkeypatch, agent)
    ctx = FakeTaskContext()
    with wf.pipeline_workflows.activate(ctx):
        result = await run()
    assert ctx.calls == [task_name]
    assert result["input_tokens"] > 0
    assert result["cost_usd"] > 0


async def test_query_expansion_runs_inline_in_task(monkeypatch):
    _stub_model(monkeypatch, query_expansion._query_expansion_agent)
    ctx = FakeTaskContext()
    with wf.pipeline_workflows.activate(ctx):
        await query_expansion.expand_query("Q?")
    assert ctx.calls == []


async def test_run_qa_pipeline_activates_task_context_and_builds_response(monkeypatch):
    in_task = {}

    async def generate_answer(question, documents):
        in_task["generate"] = wf.pipeline_workflows.in_durable_context
        return {"answer": "Use PORT.", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}

    async def evaluate(question, answer, doc_count):
        output = EvaluationOutput(
            technical_accuracy=80, clarity=80, completeness=80, developer_value=80, overall=80, feedback="ok"
        )
        return {"output": output, "model": "m", "cost_usd": 0.002, "input_tokens": 3, "output_tokens": 2}

    stubs = {
        "embed_question": lambda q: {"embedding": [0.0, 1.0], "tokens": 4, "cost_usd": 0.001},
        "retrieve_documents": lambda e, original_question: {"documents": [_DOC], "cost_usd": 0.0},
        "generate_answer": generate_answer,
        "extract_claims": lambda a: {"claims": ["c"], "input_tokens": 2, "output_tokens": 1, "cost_usd": 0.003},
        "verify_claims": lambda c: {
            "verified_claims": [Claim(claim="c", verified=True)],
            "verification_rate": 1.0,
            "cost_usd": 0.0,
            "tokens_used": 0,
        },
        "check_accuracy": lambda a, c: {"accuracy_score": 90, "input_tokens": 3, "output_tokens": 2, "cost_usd": 0.004},
        "evaluate_with_openai": evaluate,
        "evaluate_with_anthropic": evaluate,
    }

    for name, stub in stubs.items():
        monkeypatch.setattr(wf, name, stub if asyncio.iscoroutinefunction(stub) else _as_async(stub))
    monkeypatch.setattr(wf.vector_store, "initialize", lambda: _done(None))
    monkeypatch.setattr(wf, "_persist_session", lambda response, client_id=None: _done("session-1"))

    result = await wf.run_qa_pipeline.func(FakeTaskContext(), "How do web services bind?")

    assert in_task["generate"] is True
    assert result["answer"] == "Use PORT."
    assert result["accuracy_score"] == 90
    assert result["quality_score"] == pytest.approx(80)
    assert [c["claim"] for c in result["claims"]] == ["c"]
    quality = next(s for s in result["stages"] if s["stage"] == "quality_evaluation")
    assert quality["tokens_used"] == 10
    assert quality["cost_usd"] == pytest.approx(0.004)
    assert result["total_cost"] == pytest.approx(0.001 + 0.01 + 0.003 + 0.004 + 0.004)


async def _done(value):
    return value


def _as_async(fn):
    """Wrap a sync stub so that the orchestrator can await it."""
    return lambda *args, **kwargs: _done(fn(*args, **kwargs))
