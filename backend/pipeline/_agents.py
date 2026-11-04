"""Shared constructors for the pipeline's pydantic-ai agents.

Every stage builds its ``Agent`` the same way — wrap a provider-specific model
with the API key from ``settings``, an optional typed ``output_type``, and the
stage instructions. These two helpers remove that repeated boilerplate while
keeping each stage's "this stage is an Agent with a typed output" shape obvious.

This module also holds the Render Workflows ``app`` and the helper that builds
the ``RenderWorkflows`` capability for an agent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai_harness import RenderWorkflows
from render import Options, Retry, Workflows

from backend.config import settings

# The Workflows app lives here because agents receive their RenderWorkflows
# capability at construction, and the capability needs the app.
app = Workflows(
    default_retry=Retry(max_retries=2, wait_duration_ms=1000, backoff_scaling=2.0),
    default_timeout=300,
    default_plan="standard",
)


_MODEL_REQUEST_RETRY = Retry(max_retries=2, wait_duration_ms=1000)


def model_request_workflows(timeout_seconds: int, retry: Retry = _MODEL_REQUEST_RETRY) -> RenderWorkflows:
    """Build a capability that sends each model request of one agent to a chained task run.

    Attach one instance to each agent. The entry task must use the ``task``
    decorator of any ``RenderWorkflows`` instance on ``app``.
    """
    return RenderWorkflows(app, model_options=Options(retry=retry, timeout_seconds=timeout_seconds))


def anthropic_agent(
    name: str,
    model: str,
    instructions: str,
    output_type: Any = str,
    capabilities: Sequence[AbstractCapability] = (),
) -> Agent:
    """Build a Claude-backed ``Agent`` using the configured Anthropic API key.

    Thinking is off, because Claude 5 models think by default and the stages set
    small token limits. Claude 5 models do not accept ``temperature``.

    Render uses ``name`` as the prefix of generated task names. Keep it unique
    and do not change it after deploy.
    """
    return Agent(
        AnthropicModel(model, provider=AnthropicProvider(api_key=settings.anthropic_api_key)),
        name=name,
        output_type=output_type,
        instructions=instructions,
        capabilities=list(capabilities),
        model_settings={"thinking": False},
    )


def openai_agent(
    name: str,
    model: str,
    instructions: str,
    output_type: Any = str,
    capabilities: Sequence[AbstractCapability] = (),
) -> Agent:
    """Build a GPT-backed ``Agent`` using the configured OpenAI API key."""
    return Agent(
        OpenAIChatModel(model, provider=OpenAIProvider(api_key=settings.openai_api_key)),
        name=name,
        output_type=output_type,
        instructions=instructions,
        capabilities=list(capabilities),
    )
