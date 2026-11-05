# Pydantic AI Agents

> A Q&A assistant for the Render docs, built with Pydantic AI agents, Logfire, and Render Workflows

<a href="https://render.com/deploy?repo=https://github.com/render-examples/pydantic-agents-workflows">
  <img src="https://render.com/images/deploy-to-render-button.svg" alt="Deploy to Render" height="32">
</a>

Ask a question about Render, and the app answers from the Render documentation. Every answer goes through a seven-stage LLM pipeline. Logfire traces each run, with the cost, quality score, and duration of every stage.

https://github.com/user-attachments/assets/68c6ace5-b070-4c7c-9ab6-5d4342134956

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Local development](#local-development)
- [Deploy to Render](#deploy-to-render)
- [Demo mode](#demo-mode)
- [Security](#security)
- [Documentation](#documentation)

## Features

- **Hybrid search.** Retrieval combines pgvector similarity with BM25 keyword search, and an LLM expands each question into several queries.
- **Three verification checks.** *Grounding* extracts the claims in each answer and checks them against the retrieved sources. *Accuracy* reviews the answer for factual errors. *Quality* has an OpenAI model and an Anthropic model each rate the answer.
- **Grounded answers.** The assistant answers only from retrieved documentation. The prompt does not tell it to favor Render products.
- **Cost tracking.** The UI shows the cost of each question, per stage.
- **Live progress.** The UI shows each pipeline stage as it runs.
- **Render Workflows.** The pipeline and ingestion run as workflow tasks with retries and timeouts. The accuracy and quality checks run at the same time on separate instances.

The project uses these parts of the [Pydantic](https://pydantic.dev/) stack:

- **[Pydantic AI Agents](https://ai.pydantic.dev/agents/).** Each LLM stage is a `pydantic_ai.Agent` with a typed `output_type`. See [`backend/pipeline/`](./backend/pipeline/).
- **[Pydantic Embedder](https://ai.pydantic.dev/embeddings/).** `pydantic_ai.Embedder` embeds questions and claims. See [`backend/pipeline/embeddings.py`](./backend/pipeline/embeddings.py).
- **[Pydantic](https://docs.pydantic.dev/) and pydantic-settings.** Pydantic models parse LLM output and API requests, and pydantic-settings reads the config in [`backend/config.py`](./backend/config.py).
- **[genai-prices](https://github.com/pydantic/genai-prices).** Model prices come from this registry. The app multiplies them by the token counts from each agent run. See [`backend/prices.py`](./backend/prices.py).
- **[Logfire](https://logfire.pydantic.dev/).** Logfire records traces, custom metrics, and eval scores. It instruments FastAPI, asyncpg, HTTPX, and Pydantic AI automatically. See [`backend/observability.py`](./backend/observability.py).

## Architecture

Only the frontend and the API are public. The API starts Render Workflows runs through the Render SDK and polls them for results. The Workflows service has no public URL.

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
                      ┌─────────────────────▼──────────────────────┐
   Cron (daily) ───►  │  workflows  (Render Workflows · Python)    │
                      │  run_qa_pipeline  ·  ingest_all            │
                      └──────┬──────────────────────────────┬──────┘
                      ┌──────▼──────────────┐  ┌────────────▼───────────────┐
                      │  postgres 18        │  │  external APIs (your keys) │
                      │  pgvector · BM25    │  │  OpenAI · Anthropic        │
                      └─────────────────────┘  │  Logfire                   │
                                               └────────────────────────────┘
```

The API also reads Postgres directly. The pipeline writes stage updates to Postgres, and `GET /ask/{run_id}` reads them back. The `/history`, `/stats`, and `/sessions/{id}/logs` routes also read Postgres. [`render.yaml`](./render.yaml) defines every service in the diagram.

### Pipeline stages

`run_qa_pipeline` runs seven stages, and it calls every stage function inside the orchestrator process. Stages 3, 4, 6, and 7 use agents with the `RenderWorkflows` capability from [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai/blob/main/docs/harness/render-workflows.md). Each model request from those agents runs as a chained task run, such as `answer__model.request`, so each request can retry on its own. Other failures, such as invalid structured output or an error in claim verification, fail the orchestrator task, which then runs again from the start.

| # | Stage | Default model | Runs as |
|---|-------|---------------|---------|
| 1 | Question embedding | `text-embedding-3-small` | orchestrator |
| 2 | Hybrid retrieval with query expansion | `gpt-5.4-nano` | orchestrator |
| 3 | Answer generation | `claude-sonnet-5-5` | chained model request, 3 retries |
| 4 | Claims extraction | `gpt-5.4-mini` | chained model request |
| 5 | Claims verification | `text-embedding-3-small` | orchestrator |
| 6 | Accuracy check | `claude-sonnet-5-5` | chained model request, parallel with 7 |
| 7 | Quality rating | `gpt-5.4-mini` and `claude-sonnet-5-5` | 2 chained model requests, parallel with 6 |

You can change each model in the `pydantic-agents-workflows-pipeline` env group. See [`workflows/app.py`](./workflows/app.py).

Ingestion runs the same way. `ingest_all` runs `ingest_core`, then runs one `ingest_source` task for each source in [`data/sources.py`](./data/sources.py). A daily cron job starts `ingest_all`. The API also refreshes the live sources in its `preDeployCommand`.

### Project structure

```
├── backend/
│   ├── main.py            # FastAPI gateway: starts and polls workflow runs
│   ├── api/               # Route dependencies and the Logfire logs client
│   ├── pipeline/          # Pipeline stages, used by the workflows
│   ├── demo.py            # Demo usage limits
│   ├── database.py        # Postgres and pgvector
│   ├── models.py          # Pydantic models
│   ├── prices.py          # Cost calculation
│   ├── observability.py   # Logfire setup
│   └── config.py          # Settings
├── workflows/
│   ├── app.py             # Workflow task definitions
│   └── trigger_ingest.py  # Cron entry point
├── frontend/              # Next.js UI
├── data/
│   ├── sources.py         # Live source registry
│   ├── curated/           # Hand-written source content
│   ├── embeddings/        # Pre-embedded documentation
│   └── scripts/           # Ingestion scripts
├── docs/                  # Detailed guides
├── tests/                 # pytest suite
└── render.yaml            # Blueprint
```

## Local development

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs Python 3.13
- Node.js 20.9 or later
- Docker, for the local Postgres database
- The [Render CLI](https://render.com/docs/cli), for the local workflow server
- An OpenAI API key and an Anthropic API key
- A Logfire project in the US region. Under **Settings**, create a write token for `LOGFIRE_TOKEN` and a read token for `LOGFIRE_READ_TOKEN`.

### Set up

```bash
make install           # Install Python and Node dependencies
cp .env.example .env   # Then add your keys
make db-start          # Start Postgres with pgvector
make ingest            # Load the documentation. This step takes a while.
```

### Run

`POST /ask` needs a Workflows service. Run one locally with the Render CLI:

```bash
# Terminal 1: local workflow server on port 8120
render workflows dev -- uv run render-workflows workflows.app:app

# Terminal 2: API, pointed at the local workflow server
RENDER_USE_LOCAL_DEV=true WORKFLOW_SLUG=local \
  uv run uvicorn backend.main:app --reload --port 8000

# Terminal 3: frontend
make run-frontend
```

To skip the prefixes, set `RENDER_USE_LOCAL_DEV=true` and `WORKFLOW_SLUG=local` in `.env`. To use a deployed Workflows service instead, set `RENDER_API_KEY` and its real `WORKFLOW_SLUG`. A deployed service writes to its own database.

Open the frontend at http://localhost:3000 and the API docs at http://localhost:8000/docs.

### Refresh one source

After you edit a source in `data/sources.py` or `data/curated/`, reload only that source:

```bash
make add-pricing           # render.com/pricing
make add-ai-agent          # Render Workflows agents tutorial and docs
make add-autoscaling       # render.com/docs/scaling
make add-nodejs            # render.com/docs/deploy-node-express-app
make add-tutorials-index   # render.com/tutorials index
```

### Run the tests

```bash
make test
```

`make test` starts the Docker Compose database and runs `uv run pytest`. The demo limit tests need the database. If you run `uv run pytest` without `TEST_DATABASE_URL`, those tests are skipped.

## Deploy to Render

### 1. Create Logfire tokens

Sign in at [logfire.pydantic.dev](https://logfire.pydantic.dev) and create a project in the US region. Under **Settings**, create a write token and a read token.

### 2. Deploy the Blueprint

<a href="https://render.com/deploy?repo=https://github.com/render-examples/pydantic-agents-workflows">
  <img src="https://render.com/images/deploy-to-render-button.svg" alt="Deploy to Render" height="32">
</a>

Render reads [`render.yaml`](./render.yaml) and creates these resources:

| Resource | Name |
|---|---|
| Postgres 18 with pgvector | `pydantic-agents-workflows-db` |
| Workflows service | `pydantic-agents-workflows` |
| API web service | `pydantic-agents-workflows-api` |
| Daily ingest cron job | `pydantic-agents-workflows-ingest` |
| Frontend static site | `pydantic-agents-workflows-frontend` |

Render asks for these secrets once, at the env group level:

| Variable | Source |
|---|---|
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `LOGFIRE_TOKEN` | Logfire write token |
| `LOGFIRE_READ_TOKEN` | Logfire read token |
| `RENDER_API_KEY` | [Render Account Settings → API Keys](https://dashboard.render.com/settings#api-keys) |

Render also asks for `CORS_ORIGINS` on the API service. The frontend URL does not exist yet, so enter `http://localhost:3000` for now. You replace it in step 3.

The Blueprint sets the other connection values for you: `DATABASE_URL`, `WORKFLOW_SLUG`, and the frontend's `NEXT_PUBLIC_API_URL`.

There are two [env groups](https://render.com/docs/configure-environment-variables#environment-groups):

| Group | Contents | Linked to |
|---|---|---|
| `pydantic-agents-workflows-pipeline` | LLM and Logfire secrets, models, RAG settings, demo settings | API and Workflows service |
| `pydantic-agents-workflows-pipeline-trigger` | `RENDER_API_KEY` | API and ingest cron job |

When you save a change to a group, Render redeploys every service linked to it.

### 3. Allow the frontend to call the API

The API accepts browser requests only from the origins in `CORS_ORIGINS`. Until you set it, the frontend shows "Failed to fetch".

1. In the Dashboard, open `pydantic-agents-workflows-frontend` and copy its URL, for example `https://pydantic-agents-workflows-frontend-ab12.onrender.com`.
2. Open `pydantic-agents-workflows-api` → **Environment**.
3. Set `CORS_ORIGINS` to the frontend URL and save. Render redeploys the API.

Set it on the API service, not in an env group. Only the API reads it. To add a custom domain, list both origins, separated by a comma.

### 4. Load the documentation

The database is empty until ingestion runs. Start it once. After that, the cron job keeps it current.

```bash
render workflows start ingest_all
```

You can also start the `ingest_all` task in the Dashboard with no input.

To test the pipeline without the frontend, open **Workflows service → Tasks** in the Dashboard and start `run_qa_pipeline` with this input:

```
"How do I deploy an AI agent on Render?"
```

> **Note:** The API has no login, so anyone who finds its URL can spend your OpenAI and Anthropic credits. Read [Security](#security) before you share the URL.

## Demo mode

Set `DEMO=true` in the `pydantic-agents-workflows-pipeline` env group to run a public demo with no login. Demo mode limits how many questions visitors can ask, because the demo spends your OpenAI and Anthropic credits. `DEMO` is `false` in `render.yaml`, so a fork has no limits until you turn demo mode on.

`DEMO` also accepts `1`, `yes`, `y`, `t`, and `on`, in any letter case. Any other value turns demo mode off. Check the startup logs of the API and Workflows service for `[startup] DEMO mode enabled`.

When `DEMO=true`, the app changes as follows:

| Capability | In demo mode |
|---|---|
| Ask a question | The limits below apply. Answers are limited to 2,000 tokens. |
| History | The server saves no history. The history panel keeps answers in `sessionStorage`, so they are gone when the tab closes. |
| `/history` and `/sessions/{id}/logs` | Return 404. |
| Logs tab | Uses `GET /ask/{run_id}/logs` for your own runs. Logfire keeps logs for one day. |
| `GET /demo/status` | Returns `{"demo": true, "remaining": N}`, the number of questions the visitor has left. |

The limits count questions per visitor IP address. Postgres stores the counters.

| Env var | Default | Limit |
|---|---|---|
| `DEMO_RATE_LIMIT_PER_MINUTE` | `3` | Questions per minute from one IP address |
| `DEMO_MAX_QUESTIONS_PER_VISITOR` | `10` | Questions per IP address in 24 hours |
| `DEMO_DAILY_QUESTION_LIMIT` | `200` | Questions from all visitors in 24 hours |

A value of `0` turns off that limit. A missing or invalid value uses the default. Only a question that starts a run counts, so polling and logs do not use up questions. When a visitor reaches a limit, the API returns 429 and the UI shows a Deploy to Render button. If the API cannot reach the database, it returns 503 and does not start a run.

To stop a demo quickly, suspend the API service or set `DEMO_DAILY_QUESTION_LIMIT` to `1`.

## Security

- The API has a public `onrender.com` URL and no login. Set `DEMO=true` to limit usage, and set monthly spend limits in the OpenAI and Anthropic consoles.
- `RENDER_API_KEY` can access every service in its workspace. Use a separate workspace for a public deploy.
- Only the browser that started a run can poll it or open its logs. `POST /ask` returns a `progress_token`, and `GET /ask/{run_id}` returns 404 without the matching token.
- Outside demo mode, the browser sends a random UUID in the `X-Client-Id` header, and each browser sees only its own history.
- The API accepts browser requests only from the frontend origin in `CORS_ORIGINS`. The local default is `http://localhost:3000`.
- Postgres accepts no connections from the public internet. To run the ingest scripts from your computer, add your IP address to the database's **Access Control** list.
- Preview environments are off, so pull requests do not deploy copies that use your keys.

## Documentation

- [docs/PIPELINE.md](./docs/PIPELINE.md): the pipeline stages
- [docs/OBSERVABILITY.md](./docs/OBSERVABILITY.md): Logfire instrumentation
- [docs/CONFIGURATION.md](./docs/CONFIGURATION.md): all settings
- [docs/HYBRID_SEARCH.md](./docs/HYBRID_SEARCH.md): hybrid search

External docs: [Logfire](https://logfire.pydantic.dev/docs/), [Pydantic AI](https://ai.pydantic.dev/), and [Render](https://render.com/docs).

## Contributing

Fork the repository, make your change on a branch, add tests, and open a pull request.
