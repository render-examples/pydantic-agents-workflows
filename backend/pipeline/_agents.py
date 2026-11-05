"""Shared constructors for the pipeline's pydantic-ai agents.

Every stage builds its ``Agent`` the same way — wrap a provider-specific model
with the API key from ``settings``, an optional typed ``output_type``, and the
stage instructions. These two helpers remove that repeated boilerplate while
keeping each stage's "this stage is an Agent with a typed output" shape obvious.
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

from backend.config import settings


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
