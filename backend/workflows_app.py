"""Render Workflows app and the capability that sends agent model requests to it.

The pipeline agents need the app when they are built, so the app lives in
``backend`` and the ``workflows`` package imports it.
"""

from contextlib import AbstractContextManager, nullcontext
from typing import Any

from logfire.propagate import attach_context, get_context
from pydantic_ai import RunContext
from pydantic_ai.durable_exec import ModelRequestParams
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai_harness import RenderWorkflows
from render import Options, Retry, Workflows

app = Workflows(
    default_retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2.0),
    default_timeout=300,
    default_plan="standard",
)

_MODEL_REQUEST_RETRY = Retry(max_retries=2, wait_duration_ms=1000)

# Key under which the orchestrator's trace context rides in ``RunContext.metadata``.
_TRACE_CONTEXT_KEY = "otel_context"


class TracedRenderWorkflows(RenderWorkflows):
    """``RenderWorkflows`` that keeps chained model requests in the orchestrator's trace.

    A chained task run starts on a fresh instance with no OpenTelemetry context, so
    its model request spans would otherwise open a new trace. Before a request leaves
    the orchestrator, the current trace context is stored in ``RunContext.metadata``,
    which the harness already serializes into the task payload. Inside the chained run
    the context is reattached, so Logfire shows the ``chat`` span under the stage span
    that started it.
    """

    async def wrap_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        handler: Any,
    ) -> ModelResponse:
        if self.in_durable_context:
            carrier = get_context()
            if carrier:
                ctx.metadata = {**(ctx.metadata or {}), _TRACE_CONTEXT_KEY: dict(carrier)}
        return await super().wrap_model_request(ctx, request_context=request_context, handler=handler)

    async def _model_request_operation(self, params: ModelRequestParams) -> ModelResponse:
        with _attached_trace_context(params.run_context):
            return await super()._model_request_operation(params)

    async def _model_request_stream_operation(self, params: ModelRequestParams) -> ModelResponse:
        with _attached_trace_context(params.run_context):
            return await super()._model_request_stream_operation(params)


def _attached_trace_context(ctx: RunContext[Any]) -> AbstractContextManager[None]:
    carrier = (ctx.metadata or {}).get(_TRACE_CONTEXT_KEY)
    if isinstance(carrier, dict) and carrier:
        return attach_context(carrier)
    return nullcontext()


def model_request_workflows(timeout_seconds: int, retry: Retry = _MODEL_REQUEST_RETRY) -> TracedRenderWorkflows:
    """Build a capability that sends each model request of one agent to a chained task run.

    Attach one instance to each agent. Model requests run as chained task runs
    only inside a task that the ``task`` decorator of a ``RenderWorkflows``
    instance on ``app`` registers.
    """
    return TracedRenderWorkflows(app, model_options=Options(retry=retry, timeout_seconds=timeout_seconds))
