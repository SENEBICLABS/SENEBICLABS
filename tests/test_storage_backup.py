"""
scripts/storage_backup.py against an in-memory bucket: it walks folders, copies only what
is new, never writes to Storage, and verifies every copy (size against the source's record,
bytes against a content-addressed name, decodes as an image), reporting what it cannot.
"""
import hashlib
import json
import struct
import sys
import types
import zlib
from unittest.mock import patch

import pytest

from scripts import storage_backup as S


def _png(shade):
    """A valid 4x4 grey PNG, built without Pillow (the 3.10 CI job has none)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\x00" + bytes([shade, shade, shade]) * 4 for _ in range(4))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _safe_pillow():
    try:
        import PIL
        return int(PIL.__version__.split(".")[0]) >= S.MIN_PILLOW
    except Exception:
        return False


needs_safe_pillow = pytest.mark.skipif(not _safe_pillow(), reason="needs Pillow 13 or later (Python 3.11 or later)")


class _Bucket:
    def __init__(self, objects, broken=(), sizes=None):
        self.objects, self.broken, self.sizes = objects, set(broken), sizes or {}
        self.downloads = self.writes = 0

    def list(self, prefix, opts):
        names = {}
        for path, data in self.objects.items():
            if prefix and not path.startswith(prefix + "/"):
                continue
            rest = path[len(prefix) + 1:] if prefix else path
            head = rest.split("/", 1)[0]
            if "/" in rest:
                names[head] = {"name": head, "id": None}
            else:
                names[head] = {"name": head, "id": "x",
                               "metadata": {"size": self.sizes.get(path, len(data))}}
        page = sorted(names.values(), key=lambda e: e["name"])
        return page[opts["offset"]: opts["offset"] + opts["limit"]]

    def download(self, path):
        if path in self.broken:
            raise ConnectionError("reset")
        self.downloads += 1
        return self.objects[path]

    def upload(self, *_a, **_k):           # must never be called
        self.writes += 1


class _Client:
    def __init__(self, bucket):
        self.storage = type("S", (), {"from_": lambda _self, _name: bucket})()


def _key(prefix, data):
    return f"{prefix}/{hashlib.sha256(data).hexdigest()[:32]}.png"


@needs_safe_pillow
def test_copies_once_verifies_and_never_writes(tmp_path):
    a, b = _png(10), _png(200)
    bucket = _Bucket({_key("c1", a): a, _key("c2", b): b})
    first = S.backup(_Client(bucket), tmp_path, margin=0)   # the disk guard has its own test
    assert (first["objects"], first["copied"], first["failed"], first["key_verified"]) == (2, 2, [], 2)
    again = S.backup(_Client(bucket), tmp_path, margin=0)   # the disk guard has its own test
    assert (again["copied"], again["already_held"], again["failed"]) == (0, 2, [])
    assert bucket.downloads == 2 and bucket.writes == 0


@needs_safe_pillow
def test_every_unverifiable_object_is_reported(tmp_path):
    good, corrupt = _png(1), b"\x89PNG not really"
    sized = _png(2)
    bucket = _Bucket({_key("c", good): good, "c/legacyname0000000000000000000000.png": corrupt,
                      _key("c", sized): sized, "c/broken.png": b"y"},
                     broken={"c/broken.png"}, sizes={_key("c", sized): len(sized) + 7})
    out = S.backup(_Client(bucket), tmp_path, margin=0)   # the disk guard has its own test
    errors = {f["path"]: f["error"] for f in out["failed"]}
    assert errors["c/broken.png"] == "ConnectionError"
    assert errors["c/legacyname0000000000000000000000.png"].startswith("not a readable image")
    assert errors[_key("c", sized)].startswith("size ")
    assert _key("c", good) not in errors


def test_the_env_file_yields_only_the_two_keys(tmp_path):
    f = tmp_path / ".env"
    f.write_text("ANTHROPIC_API_KEY=nope\nSUPABASE_URL=https://p.supabase.co\nSUPABASE_SERVICE_KEY='k'\n")
    assert S.read_env_file(f) == {"SUPABASE_URL": "https://p.supabase.co", "SUPABASE_SERVICE_KEY": "k"}


def test_database_references_missing_from_the_backup_are_named():
    assert S.missing_expected({"a/1.png", "a/2.png"}, ["a/1.png", "a/3.png"]) == ["a/3.png"]


def test_it_refuses_to_start_when_the_copy_would_not_fit(tmp_path):
    a = _png(3)
    bucket = _Bucket({_key("c", a): a}, sizes={_key("c", a): 10 ** 15})
    import pytest
    with pytest.raises(S.NotEnoughSpace, match="Nothing was downloaded"):
        S.backup(_Client(bucket), tmp_path)
    assert bucket.downloads == 0


@pytest.mark.parametrize("report", [
    {"verified": False, "status": "verification_failed", "image_references": ["p/a.png"]},
    {"verified": False, "status": "incomplete", "image_references": None},
    {"verified": True, "status": "incomplete", "image_references": []},
])
def test_an_unverified_database_report_is_refused_not_trusted(tmp_path, report):
    f = tmp_path / "db-x.UNVERIFIED.json"
    f.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="not a verified database backup"):
        S.expected_references(f)
    # main refuses before it would even connect to storage.
    assert S.main(["--out", str(tmp_path / "images"), "--expect", str(f)]) == 1
    assert not (tmp_path / "images").exists()


def test_a_verified_database_report_yields_its_references(tmp_path):
    f = tmp_path / "db-x.json"
    f.write_text(json.dumps({"verified": True, "status": "verified", "image_references": ["p/a.png"]}))
    assert S.expected_references(f) == ["p/a.png"]
    # Reports written before the status field existed still count when verified.
    f.write_text(json.dumps({"verified": True, "image_references": []}))
    assert S.expected_references(f) == []


def _fake_pillow(version):
    pil = types.ModuleType("PIL"); pil.__version__ = version
    image = types.ModuleType("PIL.Image")
    image.open = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("a vulnerable Pillow must never decode"))
    pil.Image = image
    return {"PIL": pil, "PIL.Image": image}


@pytest.mark.parametrize("version", ["12.2.0", "12.3.0", "9.5.0"])
def test_a_vulnerable_pillow_never_decodes_a_client_image(version):
    with patch.dict(sys.modules, _fake_pillow(version)):
        why = S._readable(_png(10))
    assert why and "known vulnerabilities" in why and "Pillow 13" in why


def test_without_pillow_the_object_is_reported_unchecked():
    with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
        assert S._readable(_png(10)) == "Pillow not installed: cannot check"


@needs_safe_pillow
def test_a_safe_pillow_decodes_a_real_png_and_rejects_junk():
    assert S._readable(_png(10)) is None
    assert "not a readable image" in S._readable(b"not an image")
