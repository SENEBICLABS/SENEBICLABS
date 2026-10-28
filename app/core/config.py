from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    VERSION: str = "0.1.0"
    DEBUG: bool = False

    BASE_DIR: Path = Path(__file__).resolve().parents[2]
    DATA_DIR: Path = BASE_DIR / "data"
    MODELS_DIR: Path = BASE_DIR / "models"

    ANTHROPIC_API_KEY: str | None = None

    # Supabase — required for expert applications and waitlist storage
    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_KEY: str | None = None

    # Email — Resend (https://resend.com)
    RESEND_API_KEY: str | None = None
    FROM_EMAIL: str = "Senebiclabs <noreply@senebiclabs.com>"
    ADMIN_EMAIL: str = "support@senebiclabs.com"

    # CORS — comma-separated list of allowed origins; defaults to all in dev
    CORS_ORIGINS: str = "*"

    # Admin API key — required for approve/reject endpoints
    ADMIN_API_KEY: str | None = None

    # Analyse access token — gates the /analyse research interface
    ANALYSE_ACCESS_TOKEN: str | None = None

    # Public site URL — used to build customer portal magic-link emails
    SITE_URL: str = "https://senebiclabs.com"
    # The API's own public URL, used to self-trigger the background /sync-pending run.
    # The custom domain, not the run.app address, which carries the GCP project number.
    SELF_URL: str = "https://api.senebiclabs.com"
    # Clinicians per item is OUR quality decision, not the client's. This default
    # applies to every new project; the operator can tune it per project.
    DEFAULT_REVIEWERS_PER_ITEM: int = 3
    # Cap items per /ingest call so one monster request fails cleanly (a clear 413)
    # instead of dropping at Cloud Run's payload limit. Bulk = many calls, or a manifest.
    MAX_ITEMS_PER_INGEST: int = 5000
    # Manifest (bulk) ingestion samples a bounded review set from the client's data,
    # so clinician load stays steady no matter how large the source is.
    DEFAULT_SAMPLE_SIZE: int = 1000
    MAX_SAMPLE_SIZE: int = 10000
    # Stop streaming a manifest after this many lines so the worker can't run forever on
    # a pathologically huge file; we sample from what we read. Re-fire a stalled manifest
    # ingest after this many minutes (safety net for a missed background trigger).
    MAX_MANIFEST_LINES: int = 5_000_000
    MANIFEST_STALE_MINUTES: int = 10
    # Portal magic-link signing secret — falls back to the Supabase service key
    PORTAL_SECRET: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v):
        """Trim whitespace off every setting as it is read.

        Secrets reach production by being pasted into a hosting dashboard, where a
        trailing newline is invisible and survives the paste. httpx then refuses to
        build a header containing it and raises before any request is sent, which
        surfaces as a connection error blaming the host rather than the value. That
        cost an evening once; one strip here closes it for every secret at once.
        """
        return v.strip() if isinstance(v, str) else v

    # Settings this code no longer reads (the retired LS_* keys, say) may still sit in a
    # .env file; ignore them rather than refuse to start.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
