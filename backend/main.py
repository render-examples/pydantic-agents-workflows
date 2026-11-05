"""FastAPI gateway for Ask Render Anything Assistant.

The Q&A pipeline runs in a separate Render Workflows service. This gateway is a
thin client: ``POST /ask`` triggers a workflow run and ``GET /ask/{run_id}``
polls it for the result. It also serves health, stats, and session history.
"""

from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated
from uuid import UUID, uuid4

# Export .env into os.environ before importing the Render SDK. The SDK reads
# RENDER_USE_LOCAL_DEV (and other SDK-only vars) via os.getenv(), but pydantic-settings
# loads .env into Settings only — it never populates os.environ. Without this, local dev
# falls through to https://api.render.com instead of the local dev server on :8120. In
# cloud there is no .env file, so this is a no-op.
from dotenv import load_dotenv

load_dotenv()

import asyncpg
import logfire
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from render import RenderAsync
from render.client.errors import RenderError
from render.client.types import TaskRunStatusValues

# Importing observability configures Logfire + instrumentation at module load.
import backend.observability  # noqa: F401
from backend.api.dependencies import ClientId, require_history_enabled
from backend.api.logs import fetch_logfire_logs
from backend.config import settings
from backend.database import vector_store
from backend.demo import (
    DEPLOY_URL,
    LIMIT_MESSAGE,
    LimiterUnavailable,
    LimitExceeded,
    Limits,
    client_ip,
    get_pool,
    get_remaining,
    release,
    reserve,
)
from backend.models import HealthCheck, QuestionRequest


@lru_cache(maxsize=1)
def get_render() -> RenderAsync:
    """Lazily build a single async Render client for triggering/polling runs."""
    return RenderAsync(token=settings.render_api_key or None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""

    # Startup
    logfire.info("Starting Ask Render Anything Assistant")
    await vector_store.initialize()
    logfire.info("Application started successfully")

    yield

    # Shutdown
    logfire.info("Shutting down Ask Render Anything Assistant")
    await vector_store.close()
    logfire.info("Application shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Ask Render Anything Assistant",
    description="Production-grade AI pipeline with observable AI using Pydantic AI, Logfire, and Render",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Client-Id"],
    expose_headers=["Retry-After"],
)


def _drop_endpoint_arguments(request, attributes):
    """Do not record endpoint arguments on spans, because they include the client ID."""
    attributes.pop("values", None)
    return attributes


# Instrument FastAPI with Logfire for automatic HTTP tracing
logfire.instrument_fastapi(app, request_attributes_mapper=_drop_endpoint_arguments)


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint."""
    return {
        "name": "Ask Render Anything Assistant",
        "version": "1.0.0",
        "status": "operational"
    }


@app.get("/health", response_model=HealthCheck, tags=["Health"])
async def health_check():
    """Health check endpoint."""

    db_healthy = await vector_store.health_check()

    return HealthCheck(
        status="healthy" if db_healthy else "degraded",
        database_connected=db_healthy,
        logfire_enabled=True
    )


ProgressToken = Annotated[UUID, Query(description="Token returned by POST /ask")]


def _demo_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Demo limits are unavailable. Try again later.")


@app.post("/ask", status_code=202, tags=["Q&A"])
async def ask_question(request: QuestionRequest, http_request: Request):
    """
    Trigger the Q&A pipeline workflow for a question.

    The pipeline runs asynchronously in the Render Workflows service. This
    returns a ``run_id`` and a ``progress_token`` immediately; poll
    ``GET /ask/{run_id}?progress_token=...`` for the result.

    In demo mode, each successful call uses one question from the demo limits.
    """

    if not settings.workflow_slug:
        raise HTTPException(status_code=503, detail="WORKFLOW_SLUG is not configured")

    reservation = None
    if settings.demo:
        try:
            reservation = await reserve(get_pool(), client_ip(http_request), Limits.from_settings())
        except LimitExceeded as e:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(e.retry_after)},
                content={"detail": {"message": LIMIT_MESSAGE, "deploy_url": DEPLOY_URL}},
            )
        except (LimiterUnavailable, asyncpg.PostgresError, OSError) as e:  # Fail closed if the database is down.
            logfire.error("Demo limiter unavailable", error=str(e), exc_info=True)
            raise _demo_unavailable()

    with logfire.span(
        "user_session.qa_request",
        session_id=request.session_id or "anonymous",
        question=request.question[:100],
        question_length=len(request.question),
    ):
        # Opaque token the workflow writes live per-stage progress under. Only
        # the caller receives it, so it also proves ownership of the run.
        progress_token = str(uuid4())
        try:
            task_run = await get_render().workflows.start_task(
                f"{settings.workflow_slug}/run_qa_pipeline",
                {
                    "question": request.question,
                    "session_id": request.session_id,
                    "client_id": request.client_id,
                    "progress_token": progress_token,
                },
            )
            # Without this row the caller cannot poll the run, so treat it as a failed start.
            await vector_store.record_run(progress_token, task_run.id)
        except Exception as e:  # noqa: BLE001 - the caller gets no usable run
            logfire.error("Failed to trigger workflow run", error=str(e), exc_info=True)
            if reservation:
                try:
                    await release(get_pool(), reservation)
                except Exception as release_error:  # noqa: BLE001
                    logfire.error("Failed to release demo question", error=str(release_error))
            raise HTTPException(status_code=502, detail="Failed to start pipeline")

        logfire.info(
            "Triggered Q&A workflow run",
            run_id=task_run.id,
            session_id=request.session_id or "anonymous",
        )
        response = {"run_id": task_run.id, "progress_token": progress_token, "status": "pending"}
        if reservation:
            response["remaining"] = reservation.remaining
        return response


@app.get("/ask/{run_id}", tags=["Q&A"])
async def get_answer(run_id: str, progress_token: ProgressToken):
    """
    Poll a Q&A workflow run.

    ``progress_token`` must be the token that ``POST /ask`` returned for this
    run, otherwise the response is 404. Returns ``{"status": "running"}`` while
    in progress, ``{"status": "done", "result": <AnswerResponse>}`` on success,
    or ``{"status": "failed", "error": ...}`` if the run failed. The cumulative
    per-stage progress so far is returned as ``updates``.
    """

    run = await vector_store.get_run(str(progress_token), run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    updates = run["updates"]

    try:
        details = await get_render().workflows.get_task_run(run_id)
    except RenderError as e:
        logfire.error("Failed to read workflow run", run_id=run_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=404, detail="Run not found")

    status = details.status.value if hasattr(details.status, "value") else details.status

    if status in (TaskRunStatusValues.SUCCEEDED, TaskRunStatusValues.COMPLETED):
        # The orchestrator returns a single AnswerResponse dict; results is a list.
        result = details.results[0] if details.results else None
        return {"status": "done", "result": result, "updates": updates}

    if status in (TaskRunStatusValues.FAILED, TaskRunStatusValues.CANCELED):
        logfire.error(
            "Workflow run did not succeed",
            run_id=run_id,
            status=str(status),
            error=str(getattr(details, "error", None)),
        )
        return {"status": "failed", "error": "Pipeline run failed", "updates": updates}

    return {"status": "running", "updates": updates}


@app.get("/ask/{run_id}/logs", tags=["Observability"])
async def get_run_logs(run_id: str, progress_token: ProgressToken):
    """
    Fetch Logfire logs for a run that the caller started.

    ``progress_token`` must be the token that ``POST /ask`` returned for this
    run. Progress rows expire after one day, so logs are available for one day.
    """
    run = await vector_store.get_run(str(progress_token), run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run["trace_id"]:
        raise HTTPException(status_code=404, detail="Trace ID not available for this run")
    return await fetch_logfire_logs(run["trace_id"])


@app.get("/demo/status", tags=["Demo"])
async def demo_status(request: Request):
    """Report whether demo mode is on and how many questions the caller has left.

    If the database is unavailable, ``remaining`` is null. Demo mode stays on, so
    the frontend still shows the demo badge and keeps history in the browser.
    """
    if not settings.demo:
        return {"demo": False}
    try:
        remaining = await get_remaining(get_pool(), client_ip(request), Limits.from_settings())
    except Exception as e:  # noqa: BLE001 - POST /ask still fails closed
        logfire.error("Demo limiter unavailable", error=str(e), exc_info=True)
        remaining = None
    return {"demo": True, "remaining": remaining}


@app.get("/stats", tags=["Admin"])
async def get_stats():
    """Get database statistics."""

    doc_count = await vector_store.get_document_count()

    return {
        "document_count": doc_count,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "rag_top_k": settings.rag_top_k
    }


@app.get("/history", tags=["Q&A"], dependencies=[Depends(require_history_enabled)])
async def get_history(client_id: ClientId, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    """
    Get recent Q&A sessions for the calling browser client.

    Args:
        client_id: Anonymous browser client ID to scope history to.
        limit: Maximum number of sessions to return (1 to 100, default 20)
    """

    sessions = await vector_store.get_recent_sessions(client_id=client_id, limit=limit)

    return {
        "sessions": sessions,
        "count": len(sessions)
    }


@app.get("/history/{session_id}", tags=["Q&A"], dependencies=[Depends(require_history_enabled)])
async def get_session(session_id: UUID, client_id: ClientId):
    """
    Get a specific Q&A session by ID, scoped to the calling client.

    Args:
        session_id: The UUID of the session
        client_id: Anonymous browser client ID that must own the session
    """

    session = await vector_store.get_session_by_id(str(session_id), client_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return session


@app.delete("/history/{session_id}", tags=["Q&A"], dependencies=[Depends(require_history_enabled)])
async def delete_session(session_id: UUID, client_id: ClientId):
    """
    Delete a specific Q&A session by ID, scoped to the calling client.

    Args:
        session_id: The UUID of the session to delete
        client_id: Anonymous browser client ID that must own the session
    """

    deleted = await vector_store.delete_session(str(session_id), client_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "success": True,
        "message": "Session deleted successfully",
        "session_id": str(session_id)
    }


@app.delete("/history", tags=["Q&A"], dependencies=[Depends(require_history_enabled)])
async def clear_all_history(client_id: ClientId):
    """
    Delete all Q&A sessions owned by the calling client.

    This action cannot be undone.

    Args:
        client_id: Anonymous browser client ID whose history is cleared
    """

    deleted_count = await vector_store.delete_all_sessions(client_id)

    return {
        "success": True,
        "message": f"Deleted {deleted_count} sessions",
        "count": deleted_count
    }


@app.get("/sessions/{session_id}/logs", tags=["Observability"], dependencies=[Depends(require_history_enabled)])
async def get_session_logs(session_id: UUID, client_id: ClientId):
    """
    Fetch Logfire logs for a specific Q&A session, scoped to the calling client.

    Returns detailed observability logs from Logfire for the given session,
    including all spans, traces, and metrics captured during execution.

    Args:
        session_id: The UUID of the session
        client_id: Anonymous browser client ID that must own the session
    """
    session = await vector_store.get_session_by_id(str(session_id), client_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    trace_id = session.get('trace_id')
    if not trace_id:
        raise HTTPException(
            status_code=404,
            detail="Trace ID not available for this session (may be from before trace logging was enabled)"
        )

    return await fetch_logfire_logs(trace_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level=settings.log_level.lower()
    )
