"""
Application configuration — 12-factor compliant.

All secrets and environment-specific values are loaded from environment
variables. No defaults are provided for sensitive values to prevent
accidental exposure in non-production environments.

FedRAMP note: Configuration is externalized per NIST 800-53 CM-6
(Configuration Settings). No credentials are hardcoded.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ---------- Application ----------
    app_name: str = "fedramp-ingestion-core"
    app_version: str = "0.1.0"
    debug: bool = False

    # ---------- Database ----------
    # Connection string for async PostgreSQL via psycopg (psycopg3)
    database_url: str = Field(
        default="postgresql+psycopg://fedramp:fedramp@localhost:5432/fedramp",
        description="Async PostgreSQL connection string",
    )
    # Synchronous URL used by Alembic migrations
    database_url_sync: str = Field(
        default="postgresql+psycopg://fedramp:fedramp@localhost:5432/fedramp",
        description="Sync PostgreSQL connection string (Alembic)",
    )
    database_connect_timeout_seconds: int = Field(
        default=3,
        ge=1,
        le=60,
        description="DB connection timeout (seconds) for fast-fail in local dev",
    )

    # ---------- AWS ----------
    # Default external ID for AssumeRole — should be set per-tenant
    aws_default_external_id: str = ""
    # STS session duration in seconds (max 1 hour for SecurityAudit)
    aws_session_duration: int = 3600
    # Credential source (optional)
    # These are read so local dev can put AWS_* vars in .env without crashing.
    # boto3 will still read AWS_* from the environment as its primary mechanism.
    aws_default_region: str = "us-east-1"
    aws_access_key_id: SecretStr | None = Field(default=None, description="AWS access key ID (dev only)")
    aws_secret_access_key: SecretStr | None = Field(default=None, description="AWS secret access key (dev only)")
    aws_session_token: SecretStr | None = Field(default=None, description="AWS session token (if using STS/SSO)")

    # ---------- OpenAI (Assessment AI) ----------
    openai_api_key: str = Field(default="", description="OpenAI API key for narrative generation")
    openai_model: str = Field(default="gpt-5.2", description="OpenAI model name (e.g. gpt-5.2, gpt-4.1)")
    openai_timeout_seconds: int = Field(default=120, description="HTTP timeout for OpenAI API calls")
    openai_max_tokens: int = Field(default=4096, description="Maximum tokens for narrative generation response")
    openai_image_model: str = Field(
        default="gpt-image-1",
        description="OpenAI image model for diagram generation (must be image-capable in your account)",
    )
    openai_image_size: str = Field(
        default="1536x1024",
        description="Image size for OpenAI image generation (e.g. 1024x1024, 1536x1024, 1024x1536)",
    )

    # ---------- OpenAI (Embeddings / RAG) ----------
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI embedding model for document search (e.g. text-embedding-3-small)",
    )

    # ---------- LlamaCloud (Template Parsing) ----------
    llama_cloud_api_key: str = Field(default="", description="LlamaCloud API key for LlamaParse")
    llama_cloud_parse_timeout_seconds: int = Field(default=600, description="Timeout for LlamaParse (seconds)")

    # ---------- FedRAMP (Org / Inheritance) ----------
    fedramp_inherited_auth_name: str = Field(
        default="",
        description="Name of underlying FedRAMP authorization to reference (e.g., AGENCYAMAZONEW)",
    )
    fedramp_inherited_auth_date: str = Field(
        default="",
        description="Date of underlying FedRAMP authorization (MM/DD/YYYY)",
    )

    # ---------- Assessment AI (Tools) ----------
    assessment_ai_max_tool_calls: int = Field(
        default=20,
        description="Maximum number of evidence tool calls per narrative generation",
    )
    assessment_ai_max_assessor_summary_items: int = Field(
        default=8,
        description="Maximum number of high-level assessor summary bullets to store/show in Agent Trace",
    )
    assessment_ai_default_sample_limit: int = Field(
        default=10,
        description="Default cap for sample records returned by evidence tools",
    )
    assessment_ai_enabled_tools: str = Field(
        default="",
        description="Optional comma-separated allowlist of tool names; empty means all tools enabled",
    )

    # ---------- Logging ----------
    log_level: str = "INFO"
    log_format: str = "json"

    # ---------- External vulnerability intelligence (NVD) ----------
    nvd_api_key: str = Field(default="", description="Optional NVD API key for higher rate limits")

    # Ignore unknown env vars so adding AWS_* or other runtime vars doesn't break startup.
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

