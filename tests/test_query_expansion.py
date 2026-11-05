"""Tests for the query expansion fallback."""

import pytest
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior

from backend.pipeline import query_expansion


def _fail_with(monkeypatch, error):
    async def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(query_expansion._query_expansion_agent, "run", run)


@pytest.mark.parametrize(
    "error",
    [
        ModelAPIError(model_name="gpt", message="connection reset"),
        UnexpectedModelBehavior("output was truncated"),
    ],
)
async def test_agent_error_falls_back_to_original_question(monkeypatch, error):
    _fail_with(monkeypatch, error)
    assert await query_expansion.expand_query("Q?") == (["Q?"], 0.0)


async def test_other_errors_are_not_caught(monkeypatch):
    _fail_with(monkeypatch, TypeError("bug"))
    with pytest.raises(TypeError):
        await query_expansion.expand_query("Q?")
