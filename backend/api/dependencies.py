"""Shared FastAPI dependencies for the gateway routes."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException

from backend.config import settings


def require_client_id(x_client_id: Annotated[UUID, Header()]) -> str:
    """Read the anonymous browser client ID from the ``X-Client-Id`` header.

    The ID must be a UUID. A missing or invalid header gives a 422. The ID is
    sent in a header so that it does not appear in URLs and access logs.
    """
    return str(x_client_id)


# Annotated alias so scoped routes can declare ``client_id: ClientId`` and share
# one validation path.
ClientId = Annotated[str, Depends(require_client_id)]


def require_history_enabled() -> None:
    """Turn off the history routes in demo mode, because demo mode stores no history."""
    if settings.demo:
        raise HTTPException(status_code=404, detail="History is not available in the demo")
