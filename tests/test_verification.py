"""Tests for the claim verification stage."""

from backend.pipeline.verification import verify_claims


async def test_verify_claims_with_no_claims_returns_zero_tokens():
    result = await verify_claims([])
    assert result["tokens_used"] == 0
