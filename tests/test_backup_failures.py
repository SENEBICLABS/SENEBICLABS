"""
backup_verify.py when things go wrong after the preflight passed: the throwaway restore
container is removed on every path (and a failure to remove it is reported, never hides the
real error), and a dump from a failed or interrupted run can never be mistaken for a verified
backup. Docker, the source and the restore are all faked here; nothing runs or connects.
"""
import json
import os
import signal
import subprocess
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from scripts import backup_verify as B

SECRET = "Sup3r-Secret-Pw"
STAMP_GLOB = "db-*"


def _inventory(**extra):
    inv = {k: [] for k in B.INVENTORY}
    inv.update(extra)
    return inv


EXPECTED = {"counts": {"t": 3}, "checksums": {"t": "abc"}, "inventory": _inventory(indexes=["t_pkey"])}


def _done(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class FakeDocker:
    """Just enough of the docker CLI for RestoreContainer: run, port, rm -f, inspect."""

    def __init__(self, run_rc=0, run_creates=True, run_raises=None, port_ok=True,
                 rm_sticks=False, rm_raises=None):
        self.present = False
        self.calls = []
        self.run_rc, self.run_creates, self.run_raises = run_rc, run_creates, run_raises
        self.port_ok, self.rm_sticks, self.rm_raises = port_ok, rm_sticks, rm_raises

    def __call__(self, cmd, **_k):
        self.calls.append(cmd)
        verb = cmd[1]
        if verb == "run":
            if self.run_creates:
                self.present = True              # created, whether or not it then starts
            if self.run_raises:
                raise self.run_raises
            return _done(self.run_rc, "id\n", "" if self.run_rc == 0 else "port is already allocated")
        if verb == "port":
            return _done(0, "127.0.0.1:55555\n") if self.port_ok else _done(1, "", "no port")
        if verb == "rm":
            if self.rm_raises:
                raise self.rm_raises
            if self.rm_sticks:
                return _done(1, "", "removal of container is already in progress")
            self.present = False
            return _done()
        if verb == "container" and cmd[2] == "inspect":
            return _done(0 if self.present else 1)
        raise AssertionError(f"unexpected docker call {cmd}")

    def removed(self):
        return any(c[1] == "rm" for c in self.calls) and not self.present


# ── RestoreContainer on its own ─────────────────────────────────────────────────

def _rc(docker):
    return B.RestoreContainer("postgres:17", 100, 200, runner=docker)


def test_it_never_pulls_and_listens_only_on_localhost():
    d = FakeDocker()
    with _rc(d) as rc:
        assert rc.port == 55555
    run = d.calls[0]
    assert "--pull=never" in run and "127.0.0.1::5432" in run and "--memory-swap=200" in run
    assert d.removed() and rc.cleanup_error is None


@pytest.mark.parametrize("docker", [
    pytest.param(lambda: FakeDocker(run_rc=125), id="created but did not start"),
    pytest.param(lambda: FakeDocker(port_ok=False), id="port unreadable"),
])
def test_a_startup_failure_still_removes_the_container(docker):
    d = docker()
    with pytest.raises(RuntimeError):
        with _rc(d):
            raise AssertionError("the body must not run")
    assert d.removed()


def test_an_interrupt_during_docker_run_still_removes_the_container():
    d = FakeDocker(run_raises=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        with _rc(d):
            pass
    assert d.removed()


def test_a_container_that_was_never_created_is_not_an_error():
    d = FakeDocker(run_rc=125, run_creates=False)
    rc = _rc(d)
    with pytest.raises(RuntimeError):
        with rc:
            pass
    assert rc.cleanup_error is None


def test_a_failure_in_the_body_removes_the_container_and_is_not_swallowed():
    d = FakeDocker()
    with pytest.raises(ValueError, match="the real error"):
        with _rc(d):
            raise ValueError("the real error")
    assert d.removed()


@pytest.mark.parametrize("docker", [
    pytest.param(lambda: FakeDocker(rm_sticks=True), id="still present after rm"),
    pytest.param(lambda: FakeDocker(rm_raises=FileNotFoundError("docker")), id="docker vanished"),
])
def test_a_failed_removal_is_reported_without_masking_the_real_error(docker, capsys):
    d = docker()
    rc = _rc(d)
    with pytest.raises(ValueError, match="the real error"):
        with rc:
            raise ValueError("the real error")
    assert rc.cleanup_error and rc.name in rc.cleanup_error
    assert "CLEANUP FAILED" in capsys.readouterr().err


def test_readiness_has_a_deadline():
    def never(**_k):
        raise B.psycopg.OperationalError("connection refused")
    rc = _rc(FakeDocker())
    rc.port = 55555
    with patch.object(B.time, "sleep", lambda _s: None):
        with pytest.raises(B.RestoreNotReady, match="did not accept connections"):
            rc.wait_ready(timeout_s=0.05, connect=never)


# ── a whole run, failing at each stage ──────────────────────────────────────────

@pytest.fixture
def dsn_file(tmp_path):
    f = tmp_path / "db.url"
    f.write_text(f"postgresql://postgres:{SECRET}@db.example:5432/postgres")
    f.chmod(0o600)
    return f


def _checked():
    return {"ok": True, "refusals": [], "restore_image": "postgres:17",
            "memory": {"restore_data_cap_bytes": 100, "restore_memory_cap_bytes": 200}}


def _dump_ok(_params, _password, _major, path):
    path.write_bytes(b"PGDMP fake dump")
    return EXPECTED, ["proj/a.png"]


class _Conn:
    def execute(self, *_a):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _run(dsn_file, out, capsys, *, docker=None, dump=_dump_ok, ready=None, restore_rc=0,
         measure=lambda _c: EXPECTED):
    docker = docker or FakeDocker()
    restores = []

    def run(cmd, *a, **k):
        if "pg_restore" in cmd:
            restores.append(cmd)
            return subprocess.CompletedProcess(cmd, restore_rc, b"", b"pg_restore: error: bad archive")
        raise AssertionError(f"nothing else may run: {cmd}")

    class Container(B.RestoreContainer):
        def __init__(self, *a, **k):
            super().__init__(*a, runner=docker, **k)
        wait_ready = ready or (lambda self: None)
    with patch.object(B, "preflight", lambda *_a, **_k: _checked()), \
            patch.object(B, "dump_source", dump), \
            patch.object(B, "RestoreContainer", Container), \
            patch.object(B.subprocess, "run", side_effect=run), \
            patch.object(B.psycopg, "connect", lambda **_k: _Conn()), \
            patch.object(B, "measure", measure):
        rc = B.main(["--dsn-file", str(dsn_file), "--out", str(out)])
    captured = capsys.readouterr()
    return rc, captured.out + captured.err, docker


def _files(out):
    return sorted(p.name for p in out.glob(STAMP_GLOB))


def _report(out, verified):
    pattern = "db-*Z.json" if verified else "db-*.UNVERIFIED.json"
    [path] = out.glob(pattern)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    return json.loads(path.read_text())


def _assert_unverified(out, status, stage):
    names = _files(out)
    assert not [n for n in names if n.endswith("Z.dump") or n.endswith("Z.json")], names
    assert not [n for n in names if n.endswith(".partial")], names
    report = _report(out, verified=False)
    assert report["verified"] is False and report["status"] == status and report["failed_stage"] == stage
    return report


def test_a_verified_run_is_the_only_one_named_as_a_backup(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    rc, output, docker = _run(dsn_file, out, capsys)
    assert rc == 0 and "BACKUP VERIFIED" in output and docker.removed()
    [dump] = out.glob("db-*Z.dump")
    report = _report(out, verified=True)
    assert report["status"] == "verified" and report["dump"] == dump.name and report["cleanup_error"] is None
    assert oct(dump.stat().st_mode & 0o777) == "0o600"
    assert not list(out.glob("*UNVERIFIED*")) and not list(out.glob("*.partial"))


def test_the_dump_is_not_named_final_while_it_is_being_verified(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    seen = []

    def measure(_c):
        seen.extend(_files(out))
        return EXPECTED
    _run(dsn_file, out, capsys, measure=measure)
    assert seen and all(n.endswith(".dump.partial") for n in seen), seen


def test_a_failed_pg_dump_keeps_its_partial_output_as_unverified(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"

    def dump(_p, _pw, _m, path):
        path.write_bytes(b"PGDMP trunc")
        raise RuntimeError(f"pg_dump failed: password {SECRET} rejected")
    rc, output, docker = _run(dsn_file, out, capsys, dump=dump)
    report = _assert_unverified(out, "incomplete", "dump")
    assert rc == 1 and "NOT A VERIFIED BACKUP" in output
    assert (out / report["dump"]).read_bytes() == b"PGDMP trunc"
    assert not docker.calls, "no restore container may start after a failed dump"
    assert SECRET not in output and SECRET not in json.dumps(report)


def test_a_dump_that_never_started_leaves_only_an_unverified_report(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"

    def dump(*_a):
        raise RuntimeError("the source's version changed since the preflight")
    rc, _output, _d = _run(dsn_file, out, capsys, dump=dump)
    report = _assert_unverified(out, "incomplete", "dump")
    assert rc == 1 and report["dump"] is None and all(n.endswith(".UNVERIFIED.json") for n in _files(out))


def test_a_container_that_fails_to_start_is_removed_and_the_dump_unverified(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    rc, output, docker = _run(dsn_file, out, capsys, docker=FakeDocker(run_rc=125))
    report = _assert_unverified(out, "incomplete", "restore_container")
    assert rc == 1 and docker.removed() and "did not start" in report["error"]


def test_a_container_that_never_becomes_ready_is_removed(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"

    def never(self):
        raise B.RestoreNotReady(f"{self.name} did not accept connections within 90s")
    rc, _output, docker = _run(dsn_file, out, capsys, ready=never)
    report = _assert_unverified(out, "incomplete", "restore_ready")
    assert rc == 1 and docker.removed() and "RestoreNotReady" in report["error"]


def test_a_failed_restore_is_removed_and_unverified(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    rc, _output, docker = _run(dsn_file, out, capsys, restore_rc=1)
    report = _assert_unverified(out, "incomplete", "restore")
    assert rc == 1 and docker.removed() and "pg_restore failed" in report["error"]


def test_a_measurement_failure_is_removed_and_unverified(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"

    def measure(_c):
        raise B.psycopg.errors.QueryCanceled("statement timeout")
    rc, _output, docker = _run(dsn_file, out, capsys, measure=measure)
    report = _assert_unverified(out, "incomplete", "measure_restore")
    assert rc == 1 and docker.removed() and "QueryCanceled" in report["error"]


def test_a_verification_mismatch_is_unverified(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    different = {**EXPECTED, "checksums": {"t": "zzz"}}
    rc, output, docker = _run(dsn_file, out, capsys, measure=lambda _c: different)
    report = _assert_unverified(out, "verification_failed", "compare")
    assert rc == 1 and docker.removed() and report["problems"] == {"content_checksums": ["t"]}
    assert report["error"] is None and "NOT A VERIFIED BACKUP" in output


def test_a_cleanup_failure_after_a_failure_reports_both(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    rc, output, docker = _run(dsn_file, out, capsys, docker=FakeDocker(rm_sticks=True), restore_rc=1)
    report = _assert_unverified(out, "incomplete", "restore")
    assert rc == 1 and "pg_restore failed" in report["error"]
    assert report["cleanup_error"] and report["restored_into"]["container"] in report["cleanup_error"]
    assert "CLEANUP FAILED" in output and "NOT REMOVED" in output


def test_a_cleanup_failure_after_success_keeps_the_backup_but_fails_the_run(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    rc, output, _d = _run(dsn_file, out, capsys, docker=FakeDocker(rm_sticks=True))
    report = _report(out, verified=True)
    assert rc == 1 and report["status"] == "verified" and report["cleanup_error"]
    assert "WAS NOT REMOVED" in output


@pytest.mark.parametrize("interrupt", [
    pytest.param(lambda: (_ for _ in ()).throw(KeyboardInterrupt()), id="ctrl-c"),
    pytest.param(lambda: os.kill(os.getpid(), signal.SIGTERM), id="sigterm"),
])
def test_an_interrupted_run_removes_the_container_and_is_unverified(dsn_file, tmp_path, capsys, interrupt):
    out = tmp_path / "out"
    before = signal.getsignal(signal.SIGTERM)

    def measure(_c):
        interrupt()
        return EXPECTED
    rc, _output, docker = _run(dsn_file, out, capsys, measure=measure)
    _assert_unverified(out, "incomplete", "measure_restore")
    assert rc == 130 and docker.removed()
    assert signal.getsignal(signal.SIGTERM) == before


def test_existing_backups_are_never_touched(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    old = {"db-20261001T000000Z.dump": b"old verified", "db-20261001T000000Z.json": b'{"verified": true}',
           "db-20261002T000000Z.UNVERIFIED.dump": b"old evidence"}
    for name, body in old.items():
        (out / name).write_bytes(body)
    _run(dsn_file, out, capsys, restore_rc=1)          # a failed run
    for name, body in old.items():
        assert (out / name).read_bytes() == body


def test_a_name_already_in_use_is_refused_before_dumping(dsn_file, tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    fixed = "20261024T120000Z"
    (out / f"db-{fixed}.dump").write_bytes(b"keep me")
    dumped = []

    def dump(*a):
        dumped.append(a)
        return _dump_ok(*a)

    class Frozen(B.datetime):
        @classmethod
        def now(cls, tz=None):
            return B.datetime(2026, 10, 24, 12, 0, 0, tzinfo=tz)
    with patch.object(B, "datetime", Frozen):
        rc, output, _d = _run(dsn_file, out, capsys, dump=dump)
    assert rc == 1 and not dumped and "nothing is overwritten" in output
    assert (out / f"db-{fixed}.dump").read_bytes() == b"keep me"
