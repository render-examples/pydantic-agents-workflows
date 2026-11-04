"""Test setup: give Settings the env vars it requires before any backend import."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("LOGFIRE_TOKEN", "test")
os.environ.setdefault("LOGFIRE_SEND_TO_LOGFIRE", "false")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("WORKFLOW_SLUG", "test-workflow")
