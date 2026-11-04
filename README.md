# Pydantic AI Agents

> Render Developer Q&A Assistant showcasing observable AI with Pydantic AI Agents, Pydantic Embedder, Logfire, and Render Workflows

<a href="https://render.com/deploy?repo=https://github.com/render-examples/pydantic-agents-workflows">
  <img src="https://render.com/images/deploy-to-render-button.svg" alt="Deploy to Render" height="32">
</a>

Intelligent question-answering system that demonstrates real-world AI observability patterns. This example project shows how to build, instrument, and monitor a multi-stage LLM pipeline with full cost tracking, quality evaluation, and performance monitoring.


https://github.com/user-attachments/assets/68c6ace5-b070-4c7c-9ab6-5d4342134956

## Table of Contents

- [What This App Does](#what-this-app-does)
- [What This Demonstrates](#what-this-demonstrates)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Deploy to Render](#deploy-to-render)
- [Demo mode](#demo-mode)
- [Security](#security)
- [Example Metrics](#example-metrics)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## What This App Does

This is an **AI-powered Q&A assistant for Render documentation**. Users can ask questions about Render's platform, and the app provides accurate, well-researched answers backed by the official documentation.

### User Experience

1. **Ask a question** - "How do I deploy a Node.js app on Render?" or "What database plans are available?"
2. **Watch the pipeline** - Track progress as the run moves through 7 stages (embedding → retrieval → generation → verification)
3. **Get accurate answers** - Receive detailed responses with sources from Render docs
4. **Quality guaranteed** - Every answer is verified for accuracy and rated by dual AI evaluators

### Key Features

- **Hybrid search** - Combines semantic understanding with keyword matching for better retrieval
- **Three verification capabilities** - Each answer goes through *Grounding* (extract claims, verify them against the retrieved sources), *Accuracy* (a factual-correctness review), and *Quality* (a dual-model developer-experience rating) — distinct checks, not redundant ones
- **Neutral, grounded answers** - The assistant answers only from retrieved documentation, with no product-favorable steering in the prompt; relevant Render docs surface through retrieval, not by being force-sold
- **Cost tracking** - See exactly how much each question costs to answer
- **Parallel fan-out** - The pipeline runs on [Render Workflows](https://render.com/docs/workflows), fanning out the Accuracy + dual-model Quality checks across instances so they execute concurrently

---

## What This Demonstrates

### Render Capabilities

- **Render Workflows** - The Q&A pipeline and ingestion run as durable workflow tasks with per-task retries, timeouts, and cross-instance parallel fan-out
- **PostgreSQL with pgvector + full-text** - Managed hybrid search database
- **Web Service + Static Site** - FastAPI gateway + Next.js frontend
- **Cron Jobs** - Scheduled ingestion refresh that triggers the workflow fan-out
- **Blueprint deploy + env groups** - `render.yaml` provisions everything; shared config lives in two env groups

### Logfire Features

- **LLM Traces** - Complete visibility into every AI call (OpenAI + Anthropic auto-instrumented)
- **HTTP Tracing** - FastAPI auto-instrumentation for request/response tracking
- **Database Monitoring** - AsyncPG auto-instrumentation for query performance
- **Cost Tracking** - Per-stage and per-execution cost attribution with custom metrics
- **Multi-Model Evals** - Dual-rater quality assessment (OpenAI + Anthropic)
- **Session Tracking** - End-to-end user journey with distributed tracing
- **Custom Metrics** - Business-specific metrics (cost, quality, accuracy)
- **SQL Queries** - Custom analytics on AI performance

### Pydantic Stack

This project is built end-to-end on the [Pydantic](https://pydantic.dev/) ecosystem:

- **[Pydantic AI Agents](https://ai.pydantic.dev/agents/)** — every pipeline stage (generation, claims extraction, accuracy check, dual-rater evaluation) is a `pydantic_ai.Agent` with a typed `output_type`. Multi-provider orchestration (Claude + GPT) runs through `OpenAIProvider` / `AnthropicProvider` in a single pipeline. See [`backend/pipeline/`](./backend/pipeline/).
- **[Pydantic Embedder](https://ai.pydantic.dev/embeddings/)** — `pydantic_ai.Embedder` with `OpenAIEmbeddingModel` powers question embedding (`embed_query`) and batch claim embedding (`embed_documents`) for verification. Auto-instrumented by `logfire.instrument_pydantic_ai()`. See [`backend/pipeline/embeddings.py`](./backend/pipeline/embeddings.py) and [`backend/pipeline/verification.py`](./backend/pipeline/verification.py).
- **[Pydantic Models](https://docs.pydantic.dev/)** — Claims, accuracy scores, eval dimensions, and pipeline state are parsed directly into Pydantic models (e.g. `ClaimsOutput`, `EvaluationOutput`). `pydantic-settings` manages config in [`backend/config.py`](./backend/config.py).
- **[Pydantic GenAI Prices](https://github.com/pydantic/genai-prices)** — model pricing is loaded dynamically from the `pydantic/genai-prices` registry, then combined with per-agent token counts from `result.usage()` to produce per-stage cost attribution. See [`backend/prices.py`](./backend/prices.py).
- **[Logfire](https://logfire.pydantic.dev/)** — distributed traces, custom metrics, dual-model evals, and cost attribution. Auto-instruments FastAPI, AsyncPG, HTTPX, and Pydantic AI. See [`backend/observability.py`](./backend/observability.py).

---

## Architecture

Only the static site and the `api` gateway are public. The gateway triggers **Render Workflows** runs over the Render SDK and polls them for results; the workflows service is private and never HTTP-facing. Both talk to one managed Postgres, and both call out to your LLM providers with your own API keys.

```
                      ┌────────────────────────────────────────────┐
      Internet  ───►  │  frontend  (Next.js · Render static site)  │
                      └─────────────────────┬──────────────────────┘
                                            │ HTTPS
                      ┌─────────────────────▼──────────────────────┐
                      │  api  (FastAPI · public web service)       │
                      │  POST /ask  ·  GET /ask/{run_id}           │
                      └─────────────────────┬──────────────────────┘
                                            │ Render SDK
                                            │ start_task · get_task_run
                      ┌─────────────────────▼──────────────────────┐
   Cron (daily) ───►  │  workflows  (private · Python 3.13)        │
                      │  run_qa_pipeline  ·  ingest_all            │
                      └──────┬──────────────────────────────┬──────┘
                      ┌──────▼──────────────┐  ┌────────────▼───────────────┐
                      │  postgres           │  │  external APIs (your keys) │
                      │  (Render Managed 18)│  │  OpenAI · Anthropic        │
                      │  pgvector · BM25    │  │  Logfire (traces + evals)  │
                      └─────────────────────┘  └────────────────────────────┘
```

The `api` service also reads Postgres directly — live run progress (the orchestrator writes
stage updates there, and `GET /ask/{run_id}` reads them back), plus `/history`, `/stats`, and
`/sessions/{id}/logs`. [`render.yaml`](./render.yaml) defines every service in the diagram.

### Pipeline stages

`run_qa_pipeline` orchestrates seven stages, promoting only the expensive ones to their own tasks:

| # | Stage | Model | Execution |
|---|-------|-------|-----------|
| 1 | Question embedding | OpenAI embeddings | in-process |
| 2 | Hybrid retrieval (pgvector + BM25, LLM multi-query expansion) | `gpt-5.4-nano` | in-process |
| 3 | Answer generation | Claude | subtask (3 retries) |
| 4 | Claims extraction | GPT | subtask |
| 5 | Claims verification (RAG) | OpenAI embeddings | subtask |
| 6 | Factual-grounding check | Claude | subtask, parallel with 7 |
| 7 | Dual-model quality rating | OpenAI + Anthropic | 2 subtasks, parallel with 6 |

Ingestion runs the same way: `ingest_all` → `ingest_core`, then `ingest_source` fanned out over
the source registry in [`data/sources.py`](./data/sources.py). A daily cron job triggers it, and
the `api` service re-seeds pre-embedded pages in its `preDeployCommand`.

> **Why hybrid?** Workflows aren't HTTP-facing, so a client (the gateway) triggers tasks
> via the SDK and reads run status. Stages 1 and 2 are cheap/data-dependent and stay
> in-process on the orchestrator; only the heavy, independently-retryable LLM stages are
> promoted to their own tasks. Stages 6 + 7 run as three concurrent subtasks on separate
> instances. See [`workflows/app.py`](./workflows/app.py).

### Project Structure

```
render-qa-assistant/
├── backend/
│   ├── main.py                    # FastAPI gateway (triggers + polls workflow runs)
│   ├── api/
│   │   └── logs.py                # Logfire logs API endpoint
│   ├── pipeline/                  # 7-stage pipeline implementation (reused by workflows)
│   ├── ingestion.py               # Shared embed + replace-by-source helpers
│   ├── models.py                  # Pydantic models
│   ├── database.py                # PostgreSQL + pgvector
│   ├── observability.py           # Logfire configuration
│   └── config.py                  # Settings management
├── workflows/                     # Render Workflows service
│   ├── app.py                     # Workflows() instance + all @app.task defs
│   ├── serialization.py           # JSON boundary helpers (model_dump/model_validate)
│   └── trigger_ingest.py          # Cron entrypoint → start_task("…/ingest_all")
├── frontend/
│   ├── src/                       # Next.js + TypeScript UI
│   └── package.json
├── data/
│   ├── embeddings/                # Pre-embedded documentation
│   ├── curated/                   # Hand-curated source content (markdown)
│   ├── sources.py                 # Live-source registry (build strategies + metadata)
│   └── scripts/                   # Data ingestion scripts
├── docs/
│   ├── PIPELINE.md                # Detailed pipeline guide
│   ├── OBSERVABILITY.md           # Logfire instrumentation guide
│   ├── CONFIGURATION.md           # Configuration reference
│   └── HYBRID_SEARCH.md           # Hybrid search deep-dive
├── pyproject.toml                 # Python dependencies (uv)
├── uv.lock                        # Locked dependency versions
├── .python-version                # Pins Python to 3.13
├── render.yaml                    # Infrastructure as code
├── .env.example                   # Environment variables template
└── README.md                      # This file
```

---

## Quick Start

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (manages Python 3.13 automatically)
- Node.js 18+
- PostgreSQL 16+ (with pgvector extension >= 0.5.0, for the HNSW vector index)
- OpenAI API key
- Anthropic API key
- **Logfire account** — sign in at [logfire.pydantic.dev](https://logfire.pydantic.dev), create a project (US region), then:
  1. **Settings → Write Tokens** → create a token → `LOGFIRE_TOKEN` in `.env`
  2. **Settings → Read Tokens** → create a token → `LOGFIRE_READ_TOKEN` in `.env`
  3. View traces in the **Live** panel under your project

### Local Development (with Make)

```bash
# 1. Install everything (uv installs Python 3.13 automatically)
make install

# 2. Set up .env file (copy from example and fill in your keys)
cp .env.example .env

# 3. Start database
make db-start

# 4. Load documentation (this step might take a while!)
make ingest

# 5. Run backend (in one terminal)
make run-backend

# 6. Run frontend (in another terminal)
make run-frontend
```

> **Asking questions locally requires a deployed Workflow for now.** `POST /ask` delegates to a
> Workflows service; with nothing to delegate to it returns `503 WORKFLOW_SLUG is not configured`.
> But you can run the rest of the stack locally — no Render cloud resources, no API key — with a local dev server:
>
> ```bash
> # Terminal 1 — local workflow dev server (loads .env, listens on :8120)
> render workflows dev -- uv run render-workflows workflows.app:app
>
> # Terminal 2 — gateway pointed at the local dev server
> RENDER_USE_LOCAL_DEV=true WORKFLOW_SLUG=local \
>   uv run uvicorn backend.main:app --reload --port 8000
>
> # Terminal 3 — frontend
> cd frontend && npm run dev
> ```
>
> `RENDER_USE_LOCAL_DEV=true` points the SDK at `http://localhost:8120` instead of Render's cloud
> (no token needed); `WORKFLOW_SLUG=local` just satisfies the gateway's guard. Set both in `.env`
> to skip the prefixes. The workflow runs against your local `DATABASE_URL`, so **History**
> populates normally. *(Alternatively, set `RENDER_API_KEY` + the real `WORKFLOW_SLUG` to target a
> deployed cloud Workflows service, which writes to its own database.)*

> **Local config → deployed env groups.** Locally every process reads one `.env` (copied from
> [`.env.example`](./.env.example)). On deploy that same config splits into the two Render env
> groups in [`render.yaml`](./render.yaml) — see [Deploy → Environment groups](#environment-groups).
> Local-only knobs (`RENDER_USE_LOCAL_DEV`, the Docker `DATABASE_URL`) aren't in any group; in the
> cloud the SDK uses the platform socket and `DATABASE_URL` is injected from the database.

`make ingest` runs the full pipeline: bulk doc embeddings, plus the curated "special pages" that get explicit-injection into RAG context (pricing, AI agent, autoscaling, Node.js). These live sources are defined in the [`data/sources.py`](./data/sources.py) registry and ingested through the shared build → embed → replace-by-source helpers. To re-load just one of those after editing its registry entry (or curated content), use the per-target shortcuts:

```bash
make add-pricing      # render.com/pricing tables
make add-ai-agent     # render.com/tutorials/agents-on-render-workflows (AI agents → Render Workflows)
make add-autoscaling  # render.com/docs/scaling
make add-nodejs       # render.com/docs/deploy-node-express-app
```

**Access locally:**

- Frontend: http://localhost:3000
- API docs: http://localhost:8000/docs
- Logfire: https://logfire.pydantic.dev

---

## Deploy to Render

### 1. Set up a Logfire account.

Before clicking the deploy button, sign in at [logfire.pydantic.dev](https://logfire.pydantic.dev), create a project (US region), and generate two tokens:

- **Preferences → Write Tokens** → create token → save as `LOGFIRE_TOKEN`
- **Preferences → Read Tokens** → create token → save as `LOGFIRE_READ_TOKEN`

You'll paste both into the Render Dashboard in step 2.

### 2. One-click deploy

<a href="https://render.com/deploy?repo=https://github.com/render-examples/pydantic-agents-workflows">
  <img src="https://render.com/images/deploy-to-render-button.svg" alt="Deploy to Render" height="32">
</a>

Render reads [`render.yaml`](./render.yaml) and provisions:

- PostgreSQL 18 database with pgvector (`pydantic-agents-workflows-db`)
- Workflows service (`pydantic-agents-workflows`) that runs the Q&A pipeline and ingestion tasks
- API gateway web service (`pydantic-agents-workflows-api`, FastAPI + Logfire)
- Ingestion refresh cron (`pydantic-agents-workflows-ingest`, triggers the workflow daily)
- Frontend static site (`pydantic-agents-workflows-frontend`, Next.js)
- Key Value instance (`pydantic-agents-workflows-kv`, free plan) for the demo usage limits. The API connects to it only when `DEMO=true`.
- Two **environment groups** that hold all shared config (see below)

On **Apply**, Render prompts once for the secret values in the env groups. You fill these at
the **group** level, not per service:

| Variable | Source |
|---|---|
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `LOGFIRE_TOKEN` | Logfire write token from step 1 |
| `LOGFIRE_READ_TOKEN` | Logfire read token from step 1 |
| `RENDER_API_KEY` | [Render Account Settings → API Keys](https://dashboard.render.com/settings#api-keys) |

The gateway and the cron get `WORKFLOW_SLUG` from the Workflows service, and every service
that needs `DATABASE_URL` gets it from the database. You do not set either one.

#### Environment groups

`render.yaml` defines two reusable [env groups](https://render.com/docs/configure-environment-variables#environment-groups)
so config lives in one place instead of being duplicated across services:

| Group | Contents | Linked to |
|---|---|---|
| **`pydantic-agents-workflows-pipeline`** | LLM/Logfire secrets + all pipeline, RAG, and model config (~20 vars) | API gateway **and** the Workflows service |
| **`pydantic-agents-workflows-pipeline-trigger`** | `RENDER_API_KEY` | API gateway **and** the ingest cron |

The gateway and the Workflows service both run `backend.config.Settings`, so both link
`pydantic-agents-workflows-pipeline` and get the same config.

> Edit a group under **Dashboard → Env Groups → `<group>`**. Saving re-deploys every service
> linked to it.

### 3. Wire the frontend to the backend

After the gateway web service deploys, copy its public URL from the service's Dashboard page and set it as
the `NEXT_PUBLIC_API_URL` env var on the **frontend** service, then redeploy the frontend so the
value takes effect. For this deploy that's:

```
NEXT_PUBLIC_API_URL=https://pydantic-agents-workflows-api.onrender.com
```

Use the **base origin only** — no trailing slash and no `/api` path (the frontend appends
`/ask`, `/health`, etc. itself). If your service name isn't globally unique, Render adds a random
suffix (`…-api-xxxx.onrender.com`), so always copy the exact URL shown in the Dashboard.

Click **Save and rebuild**.

### 4. Seed the corpus, then done

The Workflows service has no documents until ingestion runs. Trigger it once to seed the DB
(the cron will keep it fresh afterward):

```bash
render workflows start ingest_all   # or start the ingest_all task in the Dashboard with no payload
```

### 5. (Optional) Smoke-test the pipeline from the Dashboard

Once the corpus is seeded, you can run the Q&A pipeline directly in the [Render dashboard](https://dashboard.render.com) — no frontend needed. In the
**Workflows service → Tasks**, start the **`run_qa_pipeline`** task with this input:

```
"How do I deploy an AI agent on Render?"
```

> **Note:** The API has no login, so anyone who finds its URL can spend your OpenAI and
> Anthropic credits. Read [Security](#security) before you share the URL.

## Demo mode

Set `DEMO=true` to run a public demo with no login. The demo runs on your OpenAI and Anthropic keys, so it limits how many questions visitors can ask.

`DEMO` also accepts `1`, `yes`, `y`, `t`, and `on`, in any letter case. Any other value turns demo mode off.

`DEMO` is `false` in `render.yaml`. A fork deploys the full app with no limits until you set `DEMO=true` in the `pydantic-agents-workflows-pipeline` env group. Both the API and the Workflows service read the flag from that group.

When `DEMO=true`, the app makes these changes:

| Capability | Demo | Change |
|---|---|---|
| Ask a question | On, with limits | The limits below apply. Answers are limited to 2,000 tokens. |
| Save history to Postgres | Off | The workflow does not save sessions. |
| History panel | On, in the browser only | Answers are kept in `sessionStorage` and are gone when the tab closes. |
| `/history` routes | Off | `GET /history` returns an empty list. The other history routes return 404. |
| Logfire logs | On, for your own runs | The Logs tab uses `GET /ask/{run_id}/logs`. Logs are available for one day. |
| `/stats` | On | No change. It returns no visitor data. |

The limits count questions by visitor IP address, and the counters are stored in Key Value:

| Env var | Default | Limit |
|---|---|---|
| `DEMO_RATE_LIMIT_PER_MINUTE` | `3` | Questions per minute from one IP address. |
| `DEMO_MAX_QUESTIONS_PER_VISITOR` | `10` | Questions per IP address in 24 hours. |
| `DEMO_DAILY_QUESTION_LIMIT` | `200` | Questions from all visitors in 24 hours. |

A value of `0` turns off that limit. A missing or invalid value uses the default. Only a question that starts a pipeline run counts. Polling for the answer, loading history, and opening logs do not count. When a limit is reached, the API returns 429 and the UI shows a Deploy to Render button. If the API cannot reach Key Value in demo mode, it returns 503 and does not start a run.

On Render, `render.yaml` sets `REDIS_URL` on the API from the Key Value instance. To run demo mode locally, start Key Value with `docker compose up keyvalue` and set `REDIS_URL=redis://localhost:6379` in `.env`.

Visitors cannot see each other's history, and the demo stores no history on the server. The free Key Value plan does not persist data, so the counters reset if the instance restarts.

To stop a demo quickly, suspend the API service in the Dashboard or set `DEMO_DAILY_QUESTION_LIMIT` to `1`.

## Security

- The API's `onrender.com` URL is public and has no login, so anyone who finds it spends your OpenAI and Anthropic credits. Set `DEMO=true` to cap usage, and set monthly spend limits in the OpenAI and Anthropic consoles.
- `RENDER_API_KEY` grants access to every service in its workspace. Use a separate workspace for a public deploy.
- The API accepts browser requests only from the frontend. `render.yaml` sets `CORS_ORIGINS` to the frontend's host. For local development the default is `http://localhost:3000`.
- The database and Key Value instance accept no connections from the public internet. To run the ingest scripts from your computer, add your IP address to the database's **Access Control** list in the Dashboard.
- Preview environments are off, so pull requests do not deploy copies that use your keys.
- Only the browser that started a run can poll it or open its logs. `POST /ask` returns a `progress_token`, and `GET /ask/{run_id}` returns 404 without the matching token.

## Documentation

### Core Guides

- **[docs/PIPELINE.md](./docs/PIPELINE.md)** - Detailed breakdown of the 7-stage pipeline
- **[docs/OBSERVABILITY.md](./docs/OBSERVABILITY.md)** - Comprehensive Logfire instrumentation guide
- **[docs/CONFIGURATION.md](./docs/CONFIGURATION.md)** - All configuration options and tuning
- **[docs/HYBRID_SEARCH.md](./docs/HYBRID_SEARCH.md)** - Technical deep-dive on hybrid search

### External Resources

- **Logfire Documentation:** https://docs.pydantic.dev/logfire/
- **Pydantic AI Documentation:** https://ai.pydantic.dev/
- **Render Documentation:** https://docs.render.com/

---

## Contributing

This is a demo project, but improvements are welcome!

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

---

## License

MIT License - see LICENSE file for details

---

## Acknowledgments

Built to showcase:

- **Logfire** by Pydantic - AI observability platform
- **Render** - Modern cloud platform
- **Pydantic AI** - Type-safe AI agent framework
- **OpenAI & Anthropic** - LLM providers

---

**Ready to build observable AI?** Fork this repo and deploy to Render to get started!
