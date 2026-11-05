"""Render Workflows app and the capability that sends agent model requests to it.

The pipeline agents need the app when they are built, so the app lives in
``backend`` and the ``workflows`` package imports it.
"""

from pydantic_ai_harness import RenderWorkflows
from render import Options, Retry, Workflows

app = Workflows(
    default_retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2.0),
    default_timeout=300,
    default_plan="standard",
)

_MODEL_REQUEST_RETRY = Retry(max_retries=2, wait_duration_ms=1000)


def model_request_workflows(timeout_seconds: int, retry: Retry = _MODEL_REQUEST_RETRY) -> RenderWorkflows:
    """Build a capability that sends each model request of one agent to a chained task run.

    Attach one instance to each agent. Model requests run as chained task runs
    only inside a task that the ``task`` decorator of a ``RenderWorkflows``
    instance on ``app`` registers.
    """
    return RenderWorkflows(app, model_options=Options(retry=retry, timeout_seconds=timeout_seconds))
