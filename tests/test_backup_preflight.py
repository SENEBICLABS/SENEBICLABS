"""
backup_verify.py's preflight: nothing is dumped unless the source's metadata was read and
the dump, the in-memory restore and the margins certainly fit. Every failure here must stop
before a dump starts: pg_dump is never invoked and nothing appears in the output folder.

The live behaviour against throwaway Postgres is in tests/sql/test_backup_preflight_sql.py.
"""
import json
from unittest.mock import patch

import pytest

from scripts import backup_verify as B

MiB = B.MiB
GiB = 1024 * MiB
DEV = 7


def _meta(version=150008, tables=((10 * MiB, 5 * MiB),)):
    return B.Metadata(version, 200 * MiB,
                      [(f"t{i}", tb, ix, tb + ix) for i, (tb, ix) in enumerate(tables)])


def _machine(free=8 * GiB, mem=4 * GiB, swap=2 * GiB, image=True, docker=True, devices=None):
    device_of = devices or {"out": DEV, "tmp": DEV, "docker:/var/lib/docker": DEV}
    return B.Machine(docker, ["/var/lib/docker"], image if docker else None, mem, swap,
                     {d: free for d in set(device_of.values())}, device_of)


def test_enough_of_everything_passes_with_its_numbers():
    p = B.plan(_meta(), _machine(), margin=1 * GiB, memory_margin=512 * MiB)
    assert p["ok"] and p["refusals"] == []
    assert p["restore_image"] == "postgres:15"
    assert p["dump_upper_bound_bytes"] == 10 * MiB + B.SCHEMA_TEXT_BYTES
    # Requirements on one filesystem are added together, not checked one by one.
    assert p["disk"][str(DEV)]["needed"] == (10 * MiB + B.SCHEMA_TEXT_BYTES + B.REPORT_BYTES
                                             + B.TEMP_BYTES + B.CONTAINER_LAYER_BYTES)


def test_not_enough_disk_is_refused():
    p = B.plan(_meta(tables=((3 * GiB, 0),)), _machine(free=3200 * MiB), margin=1 * GiB, memory_margin=0)
    assert not p["ok"] and any("not enough disk" in r for r in p["refusals"])


def test_each_filesystem_is_checked_on_its_own():
    devices = {"out": 1, "tmp": 2, "docker:/var/lib/docker": 3}
    m = _machine(devices=devices)
    m.free_by_device = {1: 8 * GiB, 2: 8 * GiB, 3: 100 * MiB}          # only Docker's disk is short
    p = B.plan(_meta(), m, margin=1 * GiB, memory_margin=0)
    assert [r for r in p["refusals"] if "filesystem 3" in r] and not [r for r in p["refusals"] if "filesystem 1" in r]


def test_a_missing_restore_image_is_refused_not_downloaded():
    p = B.plan(_meta(version=170002), _machine(image=False), margin=0, memory_margin=0)
    assert not p["ok"]
    assert any("postgres:17 is not on this machine" in r and "docker pull postgres:17" in r for r in p["refusals"])


def test_docker_unavailable_is_refused():
    p = B.plan(_meta(), _machine(docker=False), margin=0, memory_margin=0)
    assert any("Docker is not available" in r for r in p["refusals"])


def test_low_memory_is_refused_and_swap_does_not_count():
    # Plenty of free swap, but the restore would not fit in RAM: refused.
    p = B.plan(_meta(tables=((1 * GiB, 1 * GiB),)), _machine(mem=1800 * MiB, swap=8 * GiB),
               margin=0, memory_margin=512 * MiB)
    assert not p["ok"] and any("not enough RAM" in r and "swap not counted" in r for r in p["refusals"])


def test_memory_is_sized_from_tables_and_indexes_not_the_dump():
    p = B.plan(_meta(tables=((10 * MiB, 900 * MiB),)), _machine(), margin=0, memory_margin=0)
    assert p["memory"]["restore_data_cap_bytes"] >= 910 * MiB
    assert p["dump_upper_bound_bytes"] < 20 * MiB


@pytest.mark.parametrize("version", [90624, 120005, 190000, 0])
def test_an_unknown_or_unsupported_version_is_refused(version):
    p = B.plan(_meta(version=version), _machine(), margin=0, memory_margin=0)
    assert any("unknown or unsupported PostgreSQL version" in r for r in p["refusals"])


def test_no_metadata_is_refused():
    assert B.plan(None, _machine(), 0, 0)["ok"] is False


# ── every failure stops before the dump ─────────────────────────────────────────

SECRET = "Sup3r-Secret-Pw"


@pytest.fixture
def dsn_file(tmp_path):
    f = tmp_path / "db.url"
    f.write_text(f"postgresql://postgres:{SECRET}@db.example:5432/postgres")
    f.chmod(0o600)
    return f


def _run_main(dsn_file, out, capsys, **patches):
    dumped = []
    real_run = B.subprocess.run

    def watch(cmd, *a, **k):
        if "pg_dump" in cmd or "pg_restore" in cmd:
            dumped.append(cmd)
            raise AssertionError("a dump or restore started")
        return real_run(cmd, *a, **k)
    with patch.object(B.subprocess, "run", side_effect=watch), \
            patch.multiple(B, **patches):
        rc = B.main(["--dsn-file", str(dsn_file), "--out", str(out)])
    output = capsys.readouterr().out
    return rc, output, dumped


def _fails(exc):
    def f(*_a, **_k):
        raise exc
    return f


def test_a_connection_failure_stops_before_the_dump(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    rc, output, dumped = _run_main(dsn_file, out, capsys, connect_source=_fails(
        B.psycopg.OperationalError(f'connection to server failed: password "{SECRET}" rejected')))
    assert rc == 1 and not dumped and not out.exists()
    assert "could not connect" in output and SECRET not in output


def test_a_metadata_failure_stops_before_the_dump(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"

    class Conn:
        def close(self):
            pass
    rc, output, dumped = _run_main(dsn_file, out, capsys, connect_source=lambda *_a, **_k: Conn(),
                                   read_metadata=_fails(B.psycopg.errors.QueryCanceled("statement timeout")))
    assert rc == 1 and not dumped and not out.exists()
    assert "could not read the source's metadata" in output


@pytest.mark.parametrize("machine,why", [
    (lambda: _machine(free=10 * MiB), "not enough disk"),
    (lambda: _machine(image=False), "is not on this machine"),
    (lambda: _machine(mem=100 * MiB), "not enough RAM"),
    (lambda: _machine(docker=False), "Docker is not available"),
])
def test_an_insufficient_machine_stops_before_the_dump(dsn_file, tmp_path, capsys, machine, why):
    out = tmp_path / "out"

    class Conn:
        def close(self):
            pass
    rc, output, dumped = _run_main(dsn_file, out, capsys, connect_source=lambda *_a, **_k: Conn(),
                                   read_metadata=lambda _c: _meta(),
                                   probe_machine=lambda *_a, **_k: machine())
    assert rc == 1 and not dumped and not out.exists()
    assert why in output and "nothing was dumped" in output
    assert SECRET not in output


def test_preflight_only_dumps_nothing_even_when_everything_fits(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"

    class Conn:
        def close(self):
            pass
    with patch.object(B, "connect_source", lambda *_a, **_k: Conn()), \
            patch.object(B, "read_metadata", lambda _c: _meta()), \
            patch.object(B, "probe_machine", lambda *_a, **_k: _machine()), \
            patch.object(B.subprocess, "run", side_effect=AssertionError("nothing may run")):
        rc = B.main(["--dsn-file", str(dsn_file), "--out", str(out), "--preflight-only"])
    output = capsys.readouterr().out
    assert rc == 0 and "PREFLIGHT PASSED" in output and not out.exists()
    assert json.loads(output.split("PREFLIGHT PASSED")[0])["preflight"]["ok"] is True
