# Configuration Guide

This guide lists the settings for the Ask Render Anything Assistant. All settings are environment variables. `backend/config.py` defines them and their defaults. For local development, set them in `.env`.

## Table of Contents

- [Environment Variables](#environment-variables)
- [Security & Deployment Posture](#security--deployment-posture)
- [Model Selection](#model-selection)
- [Retrieval](#retrieval)
- [Embeddings and Chunking](#embeddings-and-chunking)
- [Timeouts](#timeouts)
- [Troubleshooting](#troubleshooting)

---

## Environment Variables

### Required Variables

The application does not start without these:

```bash
OPENAI_API_KEY=sk-proj-...   # Embeddings and the OpenAI models
ANTHROPIC_API_KEY=sk-ant-... # The Anthropic models
LOGFIRE_TOKEN=...            # Logfire write token
DATABASE_URL=postgresql://user:password@host:5432/dbname  # PostgreSQL with pgvector
```

The API service also needs `RENDER_API_KEY` and `WORKFLOW_SLUG` to start and poll workflow runs. For local development, see the README.

### Optional Variables

These have defaults:

```bash
# Generation
MAX_TOKENS=5000                    # Answer token limit. Demo mode lowers it to 2000.

# Retrieval
RAG_TOP_K=20                       # Maximum number of documents to retrieve
RELEVANCE_CUTOFF_FRACTION=0.75     # Keep a document only if its score is at least this fraction of the best score
SIMILARITY_THRESHOLD=0.3           # Minimum cosine similarity for a retrieved document
VERIFICATION_THRESHOLD=0.30        # Minimum cosine similarity for a claim verification passage

# Embeddings
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536

# Models
ANSWER_MODEL=claude-sonnet-5-5
CLAIMS_MODEL=gpt-5.4-mini
ACCURACY_MODEL=claude-sonnet-5-5
EVAL_MODEL_OPENAI=gpt-5.4-mini
EVAL_MODEL_ANTHROPIC=claude-sonnet-5-5
QUERY_EXPANSION_MODEL=gpt-5.4-nano

# Logging and Logfire
LOG_LEVEL=INFO                     # DEBUG, INFO, WARNING, or ERROR
ENVIRONMENT=production             # Logfire environment tag. Set to `development` locally.
LOGFIRE_READ_TOKEN=                # Read token for the session logs view
LOGFIRE_API_BASE=https://logfire-us.pydantic.dev  # Use https://logfire-eu.pydantic.dev for EU projects
LOGFIRE_QUERY_WINDOW_DAYS=30       # How many days back the session logs query searches

# API
CORS_ORIGINS=http://localhost:3000 # Frontend origins, as a comma-separated list or a JSON list

# Demo mode (see the README "Demo mode" section)
DEMO=false
DEMO_RATE_LIMIT_PER_MINUTE=3
DEMO_MAX_QUESTIONS_PER_VISITOR=10
DEMO_DAILY_QUESTION_LIMIT=200      # Set a limit to 0 to turn it off

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000  # Backend API origin, with no trailing slash
```

---

## Security & Deployment Posture

There is no login on any endpoint. Read these points before you share a deployed URL:

- `POST /ask` costs money. Each call runs the full pipeline on your OpenAI and Anthropic
  keys, and anyone who finds the API URL can call it. Set `DEMO=true` to turn on usage limits
  (see the README "Demo mode" section), and set spend limits in the OpenAI and Anthropic consoles.
- History is scoped by an anonymous `client_id`, a random UUID in the browser's `localStorage`.
  The browser sends it in the `X-Client-Id` header. The API rejects a value that is not a UUID.
  Anyone who has a visitor's `client_id` can read that visitor's history.
- Only the browser that started a run can poll it. `POST /ask` returns a `progress_token`, and
  `GET /ask/{run_id}` and `GET /ask/{run_id}/logs` return 404 without the matching token.
- `CORS_ORIGINS` defaults to `http://localhost:3000`. On Render, set it on the
  `pydantic-agents-workflows-api` service to the frontend URL. The value is a comma-separated
  list, and a bare host gets an `https://` prefix.
- Secrets live only in environment variables. `render.yaml` marks all secrets `sync: false`,
  so Render prompts for them on first apply.

Set `ENVIRONMENT` so Logfire groups traces correctly.

---

## Model Selection

Each model setting uses a fixed provider. Set it to a model ID from that provider.

| Setting | Stage | Provider |
| --- | --- | --- |
| `ANSWER_MODEL` | Answer generation | Anthropic |
| `ACCURACY_MODEL` | Technical accuracy check | Anthropic |
| `EVAL_MODEL_ANTHROPIC` | Quality evaluation | Anthropic |
| `CLAIMS_MODEL` | Claims extraction and claim verification | OpenAI |
| `EVAL_MODEL_OPENAI` | Quality evaluation | OpenAI |
| `QUERY_EXPANSION_MODEL` | Query expansion | OpenAI |

To lower cost, use smaller models:

```bash
ANSWER_MODEL=claude-haiku-5-5
ACCURACY_MODEL=claude-haiku-5-5
EVAL_MODEL_ANTHROPIC=claude-haiku-5-5
CLAIMS_MODEL=gpt-5.4-nano
EVAL_MODEL_OPENAI=gpt-5.4-nano
```

To raise quality, use larger models:

```bash
ANSWER_MODEL=claude-opus-5-5
ACCURACY_MODEL=claude-opus-5-5
EVAL_MODEL_ANTHROPIC=claude-opus-5-5
```

The pipeline calculates cost with the `genai-prices` package. If the package does not know a model ID, the pipeline uses a fallback rate of $3 per million input tokens and $15 per million output tokens.

---

## Retrieval

Retrieval combines vector search and full-text search. See [Hybrid Search](./HYBRID_SEARCH.md) for the details.

- `RAG_TOP_K` sets the maximum number of documents.
- `RELEVANCE_CUTOFF_FRACTION` removes documents that score well below the best match. Raise it toward 0.8 to get fewer sources. Lower it toward 0.6 to get more.
- `SIMILARITY_THRESHOLD` removes any document below this cosine similarity, even when the best match is weak.

Two retrieval values are set in code, not in the environment:

- The full-text search weight is `bm25_weight=0.4` in `backend/pipeline/retrieval.py`.
- The Reciprocal Rank Fusion constant is `rrf_k = 60` in `VectorStore.hybrid_search` in `backend/database.py`.

---

## Embeddings and Chunking

The `documents.embedding` column is `vector(1536)`, and `data/scripts/generate_embeddings.py` always uses `text-embedding-3-small` with 1536 dimensions. Keep the default `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS`. To change them, also change the column in `backend/database.py` and the script, then ingest all documents again.

`data/scripts/chunking.py` splits documents into chunks:

- `MAX_CHUNK_CHARS = 2000` sets the maximum chunk size. The splitter breaks at paragraph boundaries.
- `MIN_CHUNK_CHARS = 100` sets the minimum size. The splitter discards shorter content.
- Curated markdown pages get one chunk per `##` or `###` section.

Chunks do not overlap.

---

## Timeouts

Timeouts are set in code. Each pipeline stage sets the timeout for its model requests with `model_request_workflows(timeout_seconds=...)`:

| Stage | Timeout |
| --- | --- |
| Answer generation | 120 seconds |
| Technical accuracy check | 90 seconds |
| Claims extraction | 60 seconds |
| Quality evaluation | 60 seconds |

The `run_qa_pipeline` task in `workflows/app.py` has a timeout of 600 seconds.

---

## Troubleshooting

**Answers are slow or time out:** Lower `MAX_TOKENS`, or use a smaller `ANSWER_MODEL`.

**Answers miss relevant documentation:** Lower `RELEVANCE_CUTOFF_FRACTION` or `SIMILARITY_THRESHOLD`.

**Answers cite unrelated sources:** Raise `RELEVANCE_CUTOFF_FRACTION`.

**Costs are too high:** Use the smaller models in [Model Selection](#model-selection), and lower `MAX_TOKENS`.

---

## Related Documentation

- [Pipeline Guide](./PIPELINE.md) - Pipeline stages
- [Observability Guide](./OBSERVABILITY.md) - Monitoring and instrumentation
- [Hybrid Search](./HYBRID_SEARCH.md) - Retrieval details
