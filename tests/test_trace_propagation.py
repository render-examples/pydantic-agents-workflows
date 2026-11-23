"""The orchestrator's trace must continue inside chained model request runs."""

import json

import logfire
import pytest
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.models.test import TestModel

from backend.models import Document
from backend.pipeline import generation
from workflows import app as wf


class DetachedTaskContext:
    """Run each chained task with no inherited OpenTelemetry context.

    A real chained task run starts on a fresh instance, so nothing from the
    orchestrator's context is present unless the payload carries it.
    """

    async def run(self, task, *args, **kwargs):
        args, kwargs = json.loads(json.dumps([args, kwargs]))
        token = otel_context.attach(otel_context.Context())
        try:
            return json.loads(json.dumps(await task.func(self, *args, **kwargs)))
        finally:
            otel_context.detach(token)


@pytest.fixture
def exported_spans():
    exporter = InMemorySpanExporter()
    provider = logfire.DEFAULT_LOGFIRE_INSTANCE.config.get_tracer_provider().provider
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


async def test_chained_model_request_span_stays_in_orchestrator_trace(monkeypatch, exported_spans):
    test_model = TestModel()

    async def provider_request(*args, **kwargs):
        # Stands in for the provider HTTP call the chained run makes on its own instance.
        with logfire.span("provider request"):
            return await test_model.request(*args, **kwargs)

    monkeypatch.setattr(generation._answer_agent.model, "request", provider_request)
    doc = Document(content="Web services listen on PORT.", source="https://render.com/docs", similarity_score=0.9)

    with logfire.span("qa_pipeline") as pipeline_span, wf.pipeline_workflows.activate(DetachedTaskContext()):
        await generation.generate_answer("Q?", [doc])

    pipeline_trace_id = pipeline_span.context.trace_id
    worker_spans = [s for s in exported_spans.get_finished_spans() if s.name == "provider request"]
    assert worker_spans, "the chained run made no provider request"
    assert {s.context.trace_id for s in worker_spans} == {pipeline_trace_id}
    assert all(s.parent is not None for s in worker_spans)
