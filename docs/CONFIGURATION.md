# Configuration Guide

Complete reference for all configuration options in the Ask Render Anything Assistant.

## Table of Contents

- [Environment Variables](#environment-variables)
- [Security & Deployment Posture](#security--deployment-posture)
- [Pipeline Configuration](#pipeline-configuration)
- [Model Selection](#model-selection)
- [RAG Configuration](#rag-configuration)
- [Performance Tuning](#performance-tuning)

---

## Environment Variables

### Required Variables

These must be set for the application to run:

```bash
# OpenAI API key for embeddings and GPT models
OPENAI_API_KEY=sk-proj-...

# Anthropic API key for Claude models
ANTHROPIC_API_KEY=sk-ant-...

# Logfire token for observability
LOGFIRE_TOKEN=...

# PostgreSQL database URL (with pgvector extension)
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

### Optional Variables

These have sensible defaults but can be customized:

```bash
# Generation Settings
MAX_TOKENS=5000                          # Answer generation token limit

# RAG Settings
RAG_TOP_K=20                             # Number of documents to retrieve
SIMILARITY_THRESHOLD=0.3                 # Minimum cosine similarity floor (0-1)
BM25_WEIGHT=0.4                          # Weight for BM25 in hybrid search (0-1)

# Embedding Settings
EMBEDDING_MODEL=text-embedding-3-small   # OpenAI embedding model
EMBEDDING_DIMENSIONS=1536                # Embedding vector dimensions

# Model Selection
ANSWER_MODEL=claude-sonnet-5-5  # Primary answer generation model
CLAIMS_MODEL=gpt-5.4-mini                # Claims extraction model
ACCURACY_MODEL=claude-sonnet-5-5  # Accuracy checking model
EVAL_MODEL_OPENAI=gpt-5.4-mini           # OpenAI evaluator model
EVAL_MODEL_ANTHROPIC=claude-sonnet-5-5  # Anthropic evaluator model

# Performance Settings
TIMEOUT_SECONDS=30                       # Per-stage timeout
LOG_LEVEL=INFO                           # Logging verbosity (DEBUG, INFO, WARNING, ERROR)

# Frontend Settings (for deployment)
NEXT_PUBLIC_API_URL=http://localhost:8000  # Backend API base origin (no trailing slash)

# Deployment
ENVIRONMENT=production                   # Logfire environment tag; set to `development` locally
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

## Pipeline Configuration

Edit `backend/config.py` to customize pipeline behavior:

### Basic Configuration

```python
class PipelineConfig:
    # Performance tuning
    MAX_TOKENS = 2000                # Output token limit for generation
    TIMEOUT_SECONDS = 30             # Per-stage timeout in seconds
    
    # RAG configuration
    RAG_TOP_K = 10                   # Number of documents to retrieve
    SIMILARITY_THRESHOLD = 0.75      # Minimum similarity score
    BM25_WEIGHT = 0.4                # BM25 weight in hybrid search (0-1)
    EMBEDDING_MODEL = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS = 1536
    
    # Model selection
    ANSWER_MODEL = "claude-sonnet-5-5"
    CLAIMS_MODEL = "gpt-5.4-mini"
    ACCURACY_MODEL = "claude-sonnet-5-5"
    EVAL_MODELS = ["gpt-5.4-mini", "claude-sonnet-5-5"]
```

---

## Model Selection

### Available Models

#### OpenAI Models

```python
# Embedding models
"text-embedding-3-small"    # Recommended, good balance of cost/quality
"text-embedding-3-large"    # Higher quality, more expensive
"text-embedding-ada-002"    # Legacy model, not recommended

# Chat models
"gpt-5.4-mini"              # Latest, recommended
"gpt-4-turbo"               # Previous generation
"gpt-3.5-turbo"             # Cheapest, lower quality
```

#### Anthropic Models

```python
"claude-opus-5-5"             # Most capable, most expensive
"claude-sonnet-5-5"           # Latest Sonnet, best balance (default)
"claude-haiku-5-5"            # Fast and cheap
```

### Model Selection Strategy

**Cost-Optimized:**
```python
ANSWER_MODEL = "claude-haiku-5-5"          # Cheapest Claude
CLAIMS_MODEL = "gpt-3.5-turbo"           # Cheapest OpenAI
ACCURACY_MODEL = "gpt-5.4-mini"          # Balance
EVAL_MODEL_OPENAI = "gpt-3.5-turbo"
EVAL_MODEL_ANTHROPIC = "claude-haiku-5-5"
```

**Quality-Optimized:**
```python
ANSWER_MODEL = "claude-opus-4-8"           # Best Anthropic
CLAIMS_MODEL = "gpt-5.4-mini"            # Best OpenAI for structured output
ACCURACY_MODEL = "claude-sonnet-5-5"
EVAL_MODEL_OPENAI = "gpt-5.4-mini"
EVAL_MODEL_ANTHROPIC = "claude-opus-4-8"
```

**Balanced (Default):**
```python
ANSWER_MODEL = "claude-sonnet-5-5"  # Good balance
CLAIMS_MODEL = "gpt-5.4-mini"                # Fast and cheap
ACCURACY_MODEL = "claude-sonnet-5-5"  # Reliable
EVAL_MODEL_OPENAI = "gpt-5.4-mini"
EVAL_MODEL_ANTHROPIC = "claude-sonnet-5-5"
```

---

## RAG Configuration

### Hybrid Search Parameters

```python
# Number of documents to retrieve
RAG_TOP_K = 10  # Increase for more context, decrease for faster retrieval

# Minimum similarity threshold (0-1)
SIMILARITY_THRESHOLD = 0.75  # Lower = more permissive, higher = more strict

# BM25 weight (0-1)
# 0 = pure semantic search
# 1 = pure BM25 lexical search
# 0.4 = 60% semantic, 40% BM25 (recommended)
BM25_WEIGHT = 0.4

# RRF constant (for combining rankings)
RRF_K = 60  # Lower = more weight to top-ranked items
```

### Embedding Configuration

```python
# Embedding model
EMBEDDING_MODEL = "text-embedding-3-small"

# Embedding dimensions
EMBEDDING_DIMENSIONS = 1536  # Must match model

# Batch size for embedding generation
EMBEDDING_BATCH_SIZE = 100

# Enable embedding caching
ENABLE_EMBEDDING_CACHE = True
```

### Document Chunking

Edit `data/scripts/generate_embeddings.py`:

```python
# Chunk size (characters)
CHUNK_SIZE = 1000  # Larger = more context, fewer chunks

# Chunk overlap (characters)
CHUNK_OVERLAP = 200  # Overlap between chunks for continuity

# Minimum chunk size
MIN_CHUNK_SIZE = 100  # Discard very small chunks
```

---

## Performance Tuning

### Latency Optimization

```python
# Reduce token limits
MAX_TOKENS = 1500  # Down from 2000

# Reduce retrieval count
RAG_TOP_K = 5  # Down from 10

# Use faster models
ANSWER_MODEL = "claude-haiku-5-5"  # Faster than Sonnet

# Disable less critical stages (not recommended)
ENABLE_CLAIMS_VERIFICATION = False
ENABLE_ACCURACY_CHECK = False
```

### Cost Optimization

```python
# Use cheaper models
ANSWER_MODEL = "claude-haiku-5-5"
CLAIMS_MODEL = "gpt-3.5-turbo"
ACCURACY_MODEL = "gpt-5.4-mini"

# Reduce token limits
MAX_TOKENS = 1000
```

### Quality Optimization

```python
# Use best models
ANSWER_MODEL = "claude-opus-4-8"
CLAIMS_MODEL = "gpt-5.4-mini"
ACCURACY_MODEL = "claude-sonnet-5-5"

# Increase token limits
MAX_TOKENS = 3000

# More RAG context
RAG_TOP_K = 15
```

---

## Troubleshooting

### Common Issues

**Pipeline times out:**
```python
# Increase timeout
TIMEOUT_SECONDS = 60

# Or reduce token limit
MAX_TOKENS = 1500
```

**Poor retrieval quality:**
```python
# Improve RAG retrieval
RAG_TOP_K = 15
SIMILARITY_THRESHOLD = 0.70
```

**Costs too high:**
```python
# Use cheaper models
ANSWER_MODEL = "claude-haiku-5-5"
CLAIMS_MODEL = "gpt-3.5-turbo"

# Reduce token limit
MAX_TOKENS = 1000
```

**Low quality scores:**
```python
# Use better models
ANSWER_MODEL = "claude-sonnet-5-5"

# More context
RAG_TOP_K = 15

# More output tokens
MAX_TOKENS = 2500
```

---

## Related Documentation

- [Pipeline Guide](./PIPELINE.md) - Detailed pipeline stages
- [Observability Guide](./OBSERVABILITY.md) - Monitoring and instrumentation
- [Hybrid Search Deep-Dive](./HYBRID_SEARCH.md) - Technical details on retrieval

