"""Shared FastAPI dependencies for the gateway routes."""

from typing import Annotated

from fastapi import Depends, HTTPException


def require_client_id(client_id: str) -> str:
    """Require a non-empty ``client_id`` query parameter.

    Every session-scoped route takes an anonymous browser ``client_id`` and must
    reject requests that omit it, so ownership checks can't be silently bypassed.
    Declaring ``client_id: str`` keeps it a required query parameter (a missing
    value yields FastAPI's 422); this guard additionally rejects a present-but-empty
    value (``?client_id=`` yields a 400).
    """
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id is required")
    return client_id


# Annotated alias so scoped routes can declare ``client_id: ClientId`` and share
# one validation path.
ClientId = Annotated[str, Depends(require_client_id)]
