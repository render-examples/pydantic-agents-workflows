"""Tests for the model IDs and the settings each Claude stage sends."""

from dataclasses import dataclass, field

import pytest
from pydantic_ai.models.test import TestModel

from backend.config import Settings
from backend.models import Document
from backend.pipeline import accuracy, evaluation, generation


@dataclass
class RecordingModel(TestModel):
    """TestModel that keeps the model settings of the last request."""

    sent_settings: list = field(default_factory=list)

    async def request(self, messages, model_settings, model_request_parameters):
        self.sent_settings.append(model_settings or {})
        return await super().request(messages, model_settings, model_request_parameters)


def test_default_models_are_current():
    s = Settings(_env_file=None)
    assert s.answer_model == "claude-sonnet-5-5"
    assert s.accuracy_model == "claude-sonnet-5-5"
    assert s.eval_model_anthropic == "claude-sonnet-5-5"
    assert s.query_expansion_model == "gpt-5.4-nano"


async def _run_generation():
    doc = Document(content="Web services listen on PORT.", source="https://render.com/docs", similarity_score=0.9)
    await generation.generate_answer("How do web services bind?", [doc])


async def _run_accuracy():
    await accuracy.check_accuracy("Web services listen on PORT.", [])


async def _run_evaluation():
    await evaluation.evaluate_with_anthropic("Q?", "A.", 1)


@pytest.mark.parametrize(
    "agent, run",
    [
        (generation._answer_agent, _run_generation),
        (accuracy._accuracy_agent, _run_accuracy),
        (evaluation._anthropic_eval_agent, _run_evaluation),
    ],
)
async def test_claude_stages_send_no_temperature_and_no_thinking(agent, run):
    model = RecordingModel()
    with agent.override(model=model):
        await run()
    sent = model.sent_settings[0]
    assert "temperature" not in sent
    assert sent.get("thinking") is False
