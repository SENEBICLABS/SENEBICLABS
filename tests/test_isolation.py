"""
Slice #2 ISOLATION GATE. Both gates must run green before any real client X-ray touches
the system. This talks to the live Supabase project (storage + DB) and cleans up after
itself. Run: PYTHONPATH=. python3 tests/test_isolation.py

Gate A (storage): client A cannot read client B's object.
    - a private-bucket object is NOT world-readable (public URL -> non-200)
    - the owner's signed URL DOES work (-> 200)
    - clients live under separate, de-identified prefixes

Gate B (access scoping): a work code cannot reach an unassigned project by passing its id.
    - unassigned clinician -> work_brief raises 403
    - once assigned (project_clinicians) -> no longer 403
"""
import os
import struct
import zlib

import httpx
from fastapi import HTTPException

from app.services import storage
from app.services.supabase_client import get_client
from app.api.v1.project import work_brief

SCRATCH = os.environ.get("SCRATCH", "/tmp")
# Set API_BASE_URL to prove Gate B against the DEPLOYED instance over HTTP (the prod
# re-verify step), e.g. API_BASE_URL=https://senebiclabs-api-xxxxx.run.app. When unset,
# Gate B exercises the in-process function locally.
API_BASE = os.environ.get("API_BASE_URL", "").rstrip("/")


def _png(path: str, seed: int) -> str:
    """Write a tiny valid 1x1 PNG (demo pixels only — no real X-rays in tests)."""
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00" + bytes((seed % 256, (seed * 2) % 256, (seed * 3) % 256))
    idat = zlib.compress(raw)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return path


def gate_a_storage(db):
    client_a = storage.client_id_for("clientA@example.com")
    client_b = storage.client_id_for("clientB@example.com")
    assert client_a != client_b, "clients must have distinct prefixes"

    storage.ensure_bucket(db)
    pa = _png(os.path.join(SCRATCH, "a.png"), 11)
    pb = _png(os.path.join(SCRATCH, "b.png"), 22)
    key_a = storage.upload_image(client_a, pa, db)
    key_b = storage.upload_image(client_b, pb, db)
    try:
        assert key_a.startswith(client_a + "/"), "A's object not under A's prefix"
        assert key_b.startswith(client_b + "/"), "B's object not under B's prefix"
        assert not key_b.startswith(client_a + "/"), "B's object leaked into A's namespace"

        # A cannot read B's object without a signed URL: B's raw object is not world-readable.
        pub_b = storage.public_url(key_b, db)
        r = httpx.get(pub_b, timeout=20)
        assert r.status_code != 200, f"PRIVATE FAIL: B's object is world-readable ({r.status_code})"

        # The owner's signed URL does work.
        sig_b = storage.signed_url(key_b, 300, db)
        r2 = httpx.get(sig_b, timeout=20)
        assert r2.status_code == 200, f"owner signed URL should serve the object, got {r2.status_code}"
        print("  gate A: PASS (private object not world-readable; owner signed URL works; prefixes isolated)")
    finally:
        for k in (key_a, key_b):
            try:
                db.storage.from_(storage.BUCKET).remove([k])
            except Exception:
                pass


def _brief_status(project_id, code) -> int:
    """Status of GET work/brief — against the deployed API if API_BASE_URL is set, else
    the in-process function. Same DB either way, so DB-created fixtures are visible to both."""
    if API_BASE:
        r = httpx.get(f"{API_BASE}/api/v1/project/work/brief",
                      params={"project_id": project_id}, headers={"X-Work-Code": code}, timeout=30)
        return r.status_code
    try:
        work_brief(project_id, x_work_code=code)
        return 200
    except HTTPException as e:
        return e.status_code


def gate_b_scoping(db):
    import secrets
    code = "wc_" + secrets.token_urlsafe(6)
    cli = db.table("clinicians").insert(
        {"name": "GATE TEST clinician", "email": f"gate+{code}@example.com", "access_code": code}
    ).execute().data[0]
    proj = db.table("project_submissions").insert(
        {"name": "GATE TEST", "company": "GATE TEST project", "email": "gate@example.com",
         "description": "isolation gate test", "task_type": "xray_classification", "status": "new"}
    ).execute().data[0]
    cid, pid = cli["id"], proj["id"]

    target = f"deployed {API_BASE}" if API_BASE else "local (in-process)"
    assigned = False
    try:
        # NEGATIVE: unassigned clinician must be denied.
        assert _brief_status(pid, code) == 403, "unassigned clinician was NOT denied (403 expected)"

        # POSITIVE: assign, then the same clinician must no longer be denied.
        try:
            db.table("project_clinicians").insert({"project_id": pid, "clinician_id": cid}).execute()
            assigned = True
        except Exception as exc:
            raise SystemExit(
                "MIGRATION REQUIRED: could not write project_clinicians "
                f"({exc}).\nApply supabase_schema.sql in Supabase (project_clinicians table "
                "+ the eval_config column), then re-run this test."
            )
        assert _brief_status(pid, code) != 403, "assigned clinician was still denied — scoping check is broken"
        print(f"  gate B: PASS (unassigned -> 403; assigned -> allowed) [{target}]")
    finally:
        try:
            if assigned:
                db.table("project_clinicians").delete().eq("project_id", pid).eq("clinician_id", cid).execute()
            db.table("project_submissions").delete().eq("id", pid).execute()
            db.table("clinicians").delete().eq("id", cid).execute()
        except Exception as exc:
            print(f"  (cleanup warning: {exc})")


if __name__ == "__main__":
    db = get_client()
    if db is None:
        raise SystemExit("Supabase not configured — cannot run the isolation gate.")
    gate_a_storage(db)
    gate_b_scoping(db)
    print("PASS: tests/test_isolation.py (both isolation gates green)")
