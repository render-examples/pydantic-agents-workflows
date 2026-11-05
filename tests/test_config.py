"""Tests for DEMO flag, limit, and CORS parsing in Settings."""

import pytest

from backend.config import DEMO_MAX_TOKENS, Settings


def make(**env) -> Settings:
    return Settings(_env_file=None, **env)


@pytest.mark.parametrize(
    "value",
    ["true", "TRUE", "True", " tRuE ", "1", "yes", "YES", "y", "t", "on", "On", True, 1],
)
def test_demo_on_values(value):
    assert make(demo=value).demo is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "ture", False, 0])
def test_demo_other_values_are_off(value):
    assert make(demo=value).demo is False


def test_demo_defaults_off():
    assert make().demo is False


def test_limit_invalid_value_uses_default():
    s = make(demo_rate_limit_per_minute="abc", demo_max_questions_per_visitor="-4")
    assert s.demo_rate_limit_per_minute == 3
    assert s.demo_max_questions_per_visitor == 10


def test_limit_zero_is_kept():
    assert make(demo_daily_question_limit="0").demo_daily_question_limit == 0


def test_demo_profile_keeps_sonnet_and_caps_tokens():
    s = make(demo="true", max_tokens="9000")
    assert s.answer_model == "claude-sonnet-5-5"
    assert s.accuracy_model == "claude-sonnet-5-5"
    assert s.eval_model_anthropic == "claude-sonnet-5-5"
    assert s.max_tokens == DEMO_MAX_TOKENS == 2000


def test_normal_mode_keeps_models():
    s = make(answer_model="claude-sonnet-4-6")
    assert s.answer_model == "claude-sonnet-4-6"


def test_cors_bare_host_gets_https():
    s = make(cors_origins="my-frontend.onrender.com")
    assert s.cors_origins == ["https://my-frontend.onrender.com"]


def test_cors_comma_list_and_json_list():
    assert make(cors_origins="http://localhost:3000, a.example.com").cors_origins == [
        "http://localhost:3000",
        "https://a.example.com",
    ]
    assert make(cors_origins='["http://localhost:3000"]').cors_origins == ["http://localhost:3000"]


def test_cors_default_is_local_frontend():
    assert make().cors_origins == ["http://localhost:3000"]
