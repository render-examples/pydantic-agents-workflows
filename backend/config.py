"""Configuration settings for the Ask Render Anything Assistant."""

import json
from typing import Annotated, Any

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Max embedding dimensions supported per OpenAI model. Used to reject an
# embedding_model/embedding_dimensions mismatch at startup (a wrong pairing
# otherwise surfaces only as opaque API errors at query time). Unknown models
# pass through so new models work without a config change.
KNOWN_EMBEDDING_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


# Demo profile (DEMO=true): lower answer token limit.
DEMO_MAX_TOKENS = 2000

# DEMO values that turn on demo mode, in any letter case. Same set as Pydantic's bool parser.
TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})


class Settings(BaseSettings):
    """Application settings."""

    # extra="ignore": tolerate SDK-only env vars that live in the shared .env but aren't
    # Settings fields (e.g. RENDER_USE_LOCAL_DEV / RENDER_LOCAL_DEV_URL for local dev, and the
    # platform-injected RENDER_SDK_MODE / RENDER_SDK_SOCKET_PATH). Without this, pydantic-settings
    # defaults to extra="forbid" and crashes on startup when those keys appear in the .env file.
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # API Keys
    openai_api_key: str
    anthropic_api_key: str
    logfire_token: str
    logfire_read_token: str = ""  # Optional: for fetching logs via API
    # Logfire Query API base URL. Use https://logfire-eu.pydantic.dev for EU-region projects.
    logfire_api_base: str = "https://logfire-us.pydantic.dev"
    # How far back the session-logs query scopes its time window. The trace_id WHERE
    # clause already pins results to one trace; this window just needs to comfortably
    # contain it. 30 (not 7) so logs stay fetchable for sessions older than a week.
    logfire_query_window_days: int = 30

    # Database
    database_url: str

    # Render Workflows (gateway -> workflow service)
    render_api_key: str = ""  # Required to trigger/poll workflow runs
    workflow_slug: str = ""  # e.g. "pydantic-agents-workflows" (from the Workflow's Dashboard page)

    # Pipeline Configuration
    max_tokens: int = 5000  # Answer generation budget. Claude 5 models count about 30% more tokens than Claude 4.

    # RAG Configuration
    rag_top_k: int = 20  # Hard ceiling / backstop on retrieved docs. The adaptive relative
    # cutoff (see _apply_relative_cutoff) is what trims the tail per question; this just caps it.
    # Adaptive relevance cutoff: keep a doc only if its cosine similarity is >= this fraction
    # of the BEST match in the result set. Anchoring to the top match self-tunes per question —
    # a strong topic (best ~0.65) gates high and drops its tail, a weak-but-valid one keeps its
    # cluster. Raise toward 0.8 for fewer/tighter sources, lower toward 0.6 to be more inclusive.
    relevance_cutoff_fraction: float = 0.75
    # Hard floor beneath the relative cutoff: a doc is never returned below this cosine, so even
    # a question whose best match is weak can't admit sub-threshold noise. Gates the final set.
    similarity_threshold: float = 0.3
    # Similarity threshold for claim verification (lowered to catch explicit facts)
    verification_threshold: float = 0.30
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Model Selection
    answer_model: str = "claude-sonnet-5-5"
    claims_model: str = "gpt-5.4-mini"
    accuracy_model: str = "claude-sonnet-5-5"
    eval_model_openai: str = "gpt-5.4-mini"
    eval_model_anthropic: str = "claude-sonnet-5-5"
    query_expansion_model: str = "gpt-5.4-nano"

    # Performance
    log_level: str = "INFO"
    # Deployment environment tag sent to Logfire (traces are grouped by it).
    # Defaults to "production"; set ENVIRONMENT=development locally so dev traces
    # aren't mixed into the production view.
    environment: str = "production"

    # CORS. Origins the browser may call the API from. Accepts a JSON list or a
    # comma-separated string. A bare host gets an `https://` prefix.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # Public demo mode. Off by default; see the README "Demo mode" section.
    demo: bool = False
    demo_rate_limit_per_minute: int = 3
    demo_max_questions_per_visitor: int = 10
    demo_daily_question_limit: int = 200

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            v = json.loads(v) if v.startswith("[") else v.split(",")
        if isinstance(v, list):
            origins = [str(o).strip().rstrip("/") for o in v if str(o).strip()]
            return [o if "://" in o else f"https://{o}" for o in origins]
        return v

    @field_validator("demo", mode="before")
    @classmethod
    def _parse_demo(cls, v: Any) -> bool:
        """Turn on demo mode for a value in TRUE_VALUES. Any other value turns it off."""
        return str(v).strip().lower() in TRUE_VALUES

    @field_validator(
        "demo_rate_limit_per_minute",
        "demo_max_questions_per_visitor",
        "demo_daily_question_limit",
        mode="before",
    )
    @classmethod
    def _parse_limit(cls, v: Any, info: ValidationInfo) -> int:
        """Use the default for a missing or invalid limit. `0` turns the limit off."""
        try:
            value = int(str(v).strip())
        except ValueError:
            value = -1
        return value if value >= 0 else cls.model_fields[info.field_name].default

    @model_validator(mode="after")
    def _apply_demo_profile(self) -> "Settings":
        """In demo mode, use a lower answer token limit."""
        if self.demo:
            self.max_tokens = min(self.max_tokens, DEMO_MAX_TOKENS)
        return self

    @model_validator(mode="after")
    def _validate_embedding_dimensions(self) -> "Settings":
        """Reject an embedding_dimensions value the configured model can't produce."""
        max_dims = KNOWN_EMBEDDING_DIMS.get(self.embedding_model)
        if max_dims is not None and not (0 < self.embedding_dimensions <= max_dims):
            raise ValueError(
                f"embedding_dimensions={self.embedding_dimensions} is invalid for "
                f"embedding_model='{self.embedding_model}' (must be 1..{max_dims})"
            )
        return self


class PipelineConfig:
    """Static pipeline configuration constants."""

    # Stage names for tracing
    STAGE_EMBEDDING = "question_embedding"
    STAGE_RETRIEVAL = "rag_retrieval"
    STAGE_GENERATION = "answer_generation"
    STAGE_CLAIMS = "claims_extraction"
    STAGE_VERIFICATION = "claims_verification"
    STAGE_ACCURACY = "technical_accuracy"
    STAGE_EVALUATION = "quality_evaluation"


# Global settings instance
settings = Settings()
