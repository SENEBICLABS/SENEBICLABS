from pathlib import Path
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
    ADMIN_EMAIL: str = "godwinyampoi449@gmail.com"

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
    # Where Label Studio sends annotation webhooks. Defaults to SELF_URL's /ls/webhook.
    LS_CALLBACK_URL: str | None = None
    # Clinicians per item is OUR quality decision, not the client's. This default
    # applies to every new project; the operator can tune it per project.
    DEFAULT_REVIEWERS_PER_ITEM: int = 3
    # Cap items per /ingest call so one monster request fails cleanly (a clear 413)
    # instead of dropping at Cloud Run's payload limit. Bulk = many calls, or a manifest.
    MAX_ITEMS_PER_INGEST: int = 5000
    # Manifest (bulk) ingestion samples a bounded review set from the client's data,
    # so clinician load and Label Studio stay steady no matter how large the source is.
    DEFAULT_SAMPLE_SIZE: int = 1000
    MAX_SAMPLE_SIZE: int = 10000
    # Stop streaming a manifest after this many lines so the worker can't run forever on
    # a pathologically huge file; we sample from what we read. Re-fire a stalled manifest
    # ingest after this many minutes (safety net for a missed background trigger).
    MAX_MANIFEST_LINES: int = 5_000_000
    MANIFEST_STALE_MINUTES: int = 10
    # Rolling window (backpressure): the most tasks we keep ACTIVE in Label Studio at once
    # (queued or in review). The full backlog lives in our DB; /sync-pending tops LS up to
    # this many and refills as clinicians finish, so LS load stays flat no matter how large
    # the job is. Exhaustive ("all") ingestion relies on this to not overwhelm Label Studio.
    # NOTE: counted PER PROJECT, so the load on Label Studio is this times the number
    # of clients running at once. At 5000 three concurrent clients could put 15000
    # active tasks on one 2-vCPU box; 1500 keeps the realistic ceiling near 4500 with
    # no effect on throughput, because the backlog waits in our own database and the
    # window refills as clinicians finish.
    LS_ACTIVE_WINDOW: int = 1500

    # Portal magic-link signing secret — falls back to the Supabase service key
    PORTAL_SECRET: str | None = None

    # Label Studio integration (annotation surface)
    LS_URL: str | None = None              # e.g. http://localhost:8080
    LS_TOKEN: str | None = None            # Account & Settings → Access Token
    LS_WEBHOOK_SECRET: str | None = None   # shared secret to verify LS → backend webhooks

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
