"""
Copy the private `client-images` bucket to a private folder, and verify every copy.

Read-only against Storage: it lists and downloads, never uploads, moves or deletes.

Verification, per object:
  * size     the copy's length equals the size Storage records for the object
  * key      for a content-addressed key (services/storage.py), the copy's sha256 matches
             the name; older name-derived keys cannot be checked this way and say so
  * readable the copy opens as an image (Pillow decodes it); anything else is reported
Then, with --expect (the database backup's report), every image the database refers to
must be among the verified copies, so the two backups restore together.

Re-running copies only what is new and re-verifies what is already held.

Credentials are read from an env file (default: HEALTH's .env) for exactly the two values
needed, SUPABASE_URL and SUPABASE_SERVICE_KEY, and never printed. The service key is
full-privilege (Storage has no read-only key); this script only reads with it.

    python scripts/storage_backup.py --out ~/senebiclabs-backups/<date>/images \\
        --expect ~/senebiclabs-backups/<date>/db/db-<stamp>.json

Exit 1 if any object could not be copied or verified, or an expected image is missing.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BUCKET = "client-images"
ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")


def read_env_file(path: Path) -> dict:
    """Only the two keys this script needs; the rest of the file is never read into memory."""
    found = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in ENV_KEYS:
            found[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in ENV_KEYS if not found.get(k)]
    if missing:
        raise SystemExit(f"{path} lacks {', '.join(missing)}")
    return found


def _walk(bucket, prefix=""):
    """Every object under a prefix, with Storage's own metadata. One level per request."""
    offset = 0
    while True:
        page = bucket.list(prefix, {"limit": 100, "offset": offset}) or []
        for entry in page:
            path = f"{prefix}/{entry['name']}" if prefix else entry["name"]
            if entry.get("id") is None:          # a folder
                yield from _walk(bucket, path)
            else:
                yield path, (entry.get("metadata") or {})
        if len(page) < 100:
            return
        offset += 100


def _matches_key(path: str, data: bytes) -> bool | None:
    """True/False for a content-addressed key; None when the key cannot say (older keys were
    derived from the file name and look the same, so a mismatch is not proof of damage)."""
    stem = Path(path).stem
    if len(stem) == 32 and all(ch in "0123456789abcdef" for ch in stem):
        return True if hashlib.sha256(data).hexdigest()[:32] == stem else None
    return None


# The copies are client uploads, so decoding them is parsing untrusted input. Every Pillow
# before 13 has published decoder vulnerabilities, and 13 needs Python 3.11 or later.
MIN_PILLOW = 13


def _readable(data: bytes) -> str | None:
    """None if the bytes decode as an image, else why not. Never decodes with a Pillow that
    has known vulnerabilities: such an object is reported as not checked."""
    try:
        import PIL
        from PIL import Image
    except ImportError:
        return "Pillow not installed: cannot check"
    try:
        major = int(str(PIL.__version__).split(".")[0])
    except ValueError:
        major = 0
    if major < MIN_PILLOW:
        return (f"Pillow {PIL.__version__} has known vulnerabilities, so this was not decoded: "
                f"install Pillow {MIN_PILLOW} or later (needs Python 3.11 or later)")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        return None
    except Exception as exc:
        return f"not a readable image ({type(exc).__name__})"


# Never fill the disk: refuse unless the copy fits with this much to spare.
DISK_MARGIN_BYTES = 1 << 30


class NotEnoughSpace(SystemExit):
    pass


def backup(client, out: Path, margin: int = DISK_MARGIN_BYTES) -> dict:
    bucket = client.storage.from_(BUCKET)
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    # Inventory first: the whole listing, then the space check, then any download.
    inventory = list(_walk(bucket))
    needed = sum(int(m.get("size") or 0) for p, m in inventory if not (out / p).exists())
    free = shutil.disk_usage(out).free
    if needed + margin > free:
        raise NotEnoughSpace(f"Not enough disk: the copy needs {needed} bytes plus a {margin}-byte margin, "
                             f"and {free} are free. Nothing was downloaded.")
    manifest, copied, kept, failed = {}, 0, 0, []
    for path, meta in inventory:
        dest = out / path
        try:
            if dest.exists():
                data = dest.read_bytes()
                kept += 1
            else:
                data = None
                for _ in range(2):                # one retry for a transient failure
                    try:
                        data = bucket.download(path)
                        break
                    except Exception:
                        continue
                if data is None:
                    raise ConnectionError("download failed twice")
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(dest.parent, 0o700)
                dest.write_bytes(data)
                os.chmod(dest, 0o600)
                copied += 1
            source_size = meta.get("size")
            entry = {"bytes": len(data), "source_bytes": source_size, "sha256": hashlib.sha256(data).hexdigest(),
                     "key_matches_content": _matches_key(path, data), "unreadable": _readable(data)}
            manifest[path] = entry
            if source_size is not None and int(source_size) != len(data):
                failed.append({"path": path, "error": f"size {len(data)} != source {source_size}"})
            elif entry["unreadable"]:
                failed.append({"path": path, "error": entry["unreadable"]})
        except Exception as exc:
            failed.append({"path": path, "error": type(exc).__name__})
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    os.chmod(manifest_path, 0o600)
    return {"objects": len(manifest), "copied": copied, "already_held": kept, "failed": failed,
            "key_verified": sum(1 for m in manifest.values() if m["key_matches_content"] is True),
            "key_not_checkable": sum(1 for m in manifest.values() if m["key_matches_content"] is None),
            "bytes": sum(m["bytes"] for m in manifest.values())}


def missing_expected(manifest_paths: set, expected: list[str]) -> list[str]:
    return sorted(set(expected) - manifest_paths)


def expected_references(report_path: Path) -> list[str]:
    """The image references of a VERIFIED database backup. A report from a failed or
    interrupted run (backup_verify names it *.UNVERIFIED.json) is refused: its references
    may be missing or wrong, and an empty list would make the cross-check pass vacuously."""
    report = json.loads(report_path.read_text())
    if report.get("verified") is not True or report.get("status", "verified") != "verified":
        raise ValueError(f"{report_path.name} is not a verified database backup "
                         f"(status: {report.get('status', 'unknown')}); refusing to check against it")
    refs = report.get("image_references")
    if not isinstance(refs, list):
        raise ValueError(f"{report_path.name} has no image references to check against")
    return refs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    ap.add_argument("--expect", type=Path, help="the database backup's report, to cross-check image references")
    args = ap.parse_args(argv)
    refs = None
    if args.expect:
        try:
            refs = expected_references(args.expect.expanduser())
        except ValueError as exc:
            print(f"REFUSED: {exc}. Nothing was copied.")
            return 1
    from supabase import create_client
    env = read_env_file(args.env_file)
    client = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"])
    buckets = sorted(b.name for b in client.storage.list_buckets())
    out = Path(args.out).expanduser()
    result = backup(client, out)
    result["buckets_in_project"] = buckets
    result["taken_at"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if refs is not None:
        result["db_image_references"] = len(refs)
        result["db_references_missing_from_backup"] = missing_expected(
            {p for p, m in json.loads((out / "manifest.json").read_text()).items()}, refs)
    report = out / f"storage-{result['taken_at']}.json"
    report.write_text(json.dumps(result, indent=2))
    os.chmod(report, 0o600)
    print(json.dumps(result, indent=2))
    bad = result["failed"] or result.get("db_references_missing_from_backup")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
