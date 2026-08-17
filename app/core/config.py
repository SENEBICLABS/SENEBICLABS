from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    VERSION: str = "0.1.0"
    DEBUG: bool = False

    BASE_DIR: Path = Path(__file__).resolve().parents[2]
    DATA_DIR: Path = BASE_DIR / "data"
    MODELS_DIR: Path = BASE_DIR / "models"

    # Healthy Respiratory Model artefact directory
    RESPIRATORY_MODEL_DIR: Path = MODELS_DIR / "respiratory_model"

    # HLCA Full — primary reference dataset
    HLCA_FULL_PATH: Path = DATA_DIR / "hlca_full.h5ad"
    HLCA_CORE_PATH: Path = DATA_DIR / "hlca_core.h5ad"

    # Cell-type subset datasets
    CELL_TYPE_FILES: dict = {
        "myeloid":    "lung_tissues_myeloid_cells.h5ad",
        "lymphoid":   "lung_tissues_lymphoid_cells.h5ad",
        "airway":     "lung_tissues_airway_epithelial_cells.h5ad",
        "vascular":   "lung_tissues_vascular_endothelial_cells.h5ad",
        "fibroblast": "lung_tissues_fibroblasts.h5ad",
        "smooth_muscle": "lung_tissues_smooth_muscle_cells.h5ad",
        "b_cells":    "lung_tissues_b_cells.h5ad",
    }

    # Reasoning Engine — required for POST /api/v1/interpret
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
    SELF_URL: str = "https://senebiclabs-api-777437555578.us-central1.run.app"
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
    LS_ACTIVE_WINDOW: int = 5000

    # Portal magic-link signing secret — falls back to the Supabase service key
    PORTAL_SECRET: str | None = None

    # Label Studio integration (annotation surface)
    LS_URL: str | None = None              # e.g. http://localhost:8080
    LS_TOKEN: str | None = None            # Account & Settings → Access Token
    LS_WEBHOOK_SECRET: str | None = None   # shared secret to verify LS → backend webhooks

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()


# Static dataset catalog — used by scripts/download_data.py
# URLs are placeholders; fill in once CellxGene/Zenodo links are confirmed.
DATASET_CATALOG = [
    {
        "id": "hlca_full",
        "name": "HLCA Full (2.28M cells, all conditions)",
        "filename": "hlca_full.h5ad",
        "url": "",
        "size_gb": 20.0,
    },
    {
        "id": "hlca_core",
        "name": "HLCA Core (584K cells, high-quality annotated)",
        "filename": "hlca_core.h5ad",
        "url": "",
        "size_gb": 5.0,
    },
    {
        "id": "hlca_embeddings",
        "name": "HLCA Full Embeddings (pre-computed scVI vectors)",
        "filename": "lung_hlca_full_v1.1_emb.h5ad",
        "url": "",
        "size_gb": 1.2,
    },
    {
        "id": "all_nuclei",
        "name": "Lung Cells All Nuclei (snRNA, 193K cells)",
        "filename": "lung_cells_all_nuclei.h5ad",
        "url": "",
        "size_gb": 2.5,
    },
    {
        "id": "fetal_atac",
        "name": "Fetal Lung ATAC (chromatin accessibility, 101K cells)",
        "filename": "lung_fetal_atac.h5ad",
        "url": "",
        "size_gb": 1.8,
    },
    {
        "id": "fetal_assembled",
        "name": "Fetal Lung Assembled 10 Domains (72K cells)",
        "filename": "lung_fetal_assembled_10domains.h5ad",
        "url": "",
        "size_gb": 1.0,
    },
    {
        "id": "myeloid",
        "name": "Lung Tissues Myeloid Cells (41K cells)",
        "filename": "lung_tissues_myeloid_cells.h5ad",
        "url": "",
        "size_gb": 0.6,
    },
    {
        "id": "lymphoid",
        "name": "Lung Tissues Lymphoid Cells (38K cells)",
        "filename": "lung_tissues_lymphoid_cells.h5ad",
        "url": "",
        "size_gb": 0.6,
    },
    {
        "id": "airway",
        "name": "Lung Tissues Airway Epithelial Cells (30K cells)",
        "filename": "lung_tissues_airway_epithelial_cells.h5ad",
        "url": "",
        "size_gb": 0.5,
    },
    {
        "id": "vascular",
        "name": "Lung Tissues Vascular Endothelial Cells (21K cells)",
        "filename": "lung_tissues_vascular_endothelial_cells.h5ad",
        "url": "",
        "size_gb": 0.4,
    },
    {
        "id": "fibroblast",
        "name": "Lung Tissues Fibroblasts (21K cells)",
        "filename": "lung_tissues_fibroblasts.h5ad",
        "url": "",
        "size_gb": 0.4,
    },
    {
        "id": "smooth_muscle",
        "name": "Lung Tissues Smooth Muscle Cells (5K cells)",
        "filename": "lung_tissues_smooth_muscle_cells.h5ad",
        "url": "",
        "size_gb": 0.1,
    },
    {
        "id": "b_cells",
        "name": "Lung Tissues B Cells (3.7K cells)",
        "filename": "lung_tissues_b_cells.h5ad",
        "url": "",
        "size_gb": 0.1,
    },
    {
        "id": "visium_bronchus",
        "name": "Lung Tissues Visium Bronchus (spatial, 5K spots)",
        "filename": "lung_tissues_visium_bronchus.h5ad",
        "url": "",
        "size_gb": 0.2,
    },
    {
        "id": "visium_parenchyma",
        "name": "Lung Tissues Visium Parenchyma (spatial, 5K spots)",
        "filename": "lung_tissues_visium_parenchyma.h5ad",
        "url": "",
        "size_gb": 0.2,
    },
    {
        "id": "fetal_visium",
        "name": "Fetal Lung Visium Annotated (spatial, 23K spots)",
        "filename": "lung_fetal_visium_annotated.h5ad",
        "url": "",
        "size_gb": 0.4,
    },
    {
        "id": "gene_order",
        "name": "HLCA Gene Order IDs and Symbols (CSV)",
        "filename": "hlca_gene_order_ids_and_symbols.csv",
        "url": "",
        "size_gb": 0.01,
    },
]
