"""
Take a backup of the application database and prove it restores, in one step.

1. Open a read-only, repeatable-read transaction on the source and export its snapshot.
2. pg_dump the `public` schema FROM THAT SNAPSHOT. In the same transaction, record what the
   dump should contain: every table's row count and a checksum of its rows, and an
   inventory of the schema's objects (columns and types, constraints, indexes, functions,
   sequences and their values, triggers, row-level security policies).
3. Restore the dump into a throwaway Postgres of the same major version (Docker, in memory).
4. Take the same counts, checksums and inventory there. Every one must match; otherwise exit 1.
   The restore applies every constraint, so foreign keys are proved satisfied as well.

The source is only ever read. The throwaway database lives in memory and is removed at
the end, however the run ends; a container that could not be removed is reported, never
ignored. The dump is written to --out with owner-only permissions: it holds client and
clinician data, so keep it somewhere private and under your retention policy.

ONLY `db-<stamp>.dump` IS A VERIFIED BACKUP, and only with its `db-<stamp>.json` saying
"status": "verified". A run that fails or is interrupted keeps its evidence as
`db-<stamp>.UNVERIFIED.dump` and `db-<stamp>.UNVERIFIED.json` (status, failed stage, error);
a run killed outright leaves `db-<stamp>.dump.partial`. Nothing is overwritten or deleted.

CREDENTIALS NEVER APPEAR IN ARGUMENTS, ENVIRONMENT OR OUTPUT. The connection string is read
from a file only you can read (mode 600), or from the BACKUP_DATABASE_URL variable:

    install -m 600 /dev/null ~/.config/senebiclabs/backup-db.url
    $EDITOR ~/.config/senebiclabs/backup-db.url        # paste the connection string, save
    python scripts/backup_verify.py --out ~/senebiclabs-backups/<date>/db

Use the database's direct connection, or Supabase's SESSION pooler (port 5432). The
transaction pooler (port 6543) cannot share an exported snapshot. pg_dump gets the password
from a temporary password file (mode 600, mounted read-only into its container), never on a
command line, so it is not visible to `ps`, `docker inspect` or shell history.

Storage objects (the `client-images` bucket) are not in the database; back them up with
scripts/storage_backup.py. Needs Docker; psycopg (requirements-dev.txt).
"""
import argparse
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

DEFAULT_DSN_FILE = Path("~/.config/senebiclabs/backup-db.url").expanduser()

# Objects the application schema refers to that Supabase provides outside `public`. The
# target is a fresh throwaway container, whose empty `public` is replaced by the dump's.
RESTORE_STUBS = """
drop schema if exists public cascade;
create schema if not exists auth;
create or replace function auth.uid() returns uuid language sql as 'select null::uuid';
create or replace function auth.role() returns text language sql as 'select null::text';
create or replace function auth.jwt() returns jsonb language sql as 'select null::jsonb';
create schema if not exists extensions;
do $$ begin create role anon nologin; exception when duplicate_object then null; end $$;
do $$ begin create role authenticated nologin; exception when duplicate_object then null; end $$;
do $$ begin create role service_role nologin; exception when duplicate_object then null; end $$;
"""

# What a schema is made of, as stable, comparable text. Names and definitions only.
INVENTORY = {
    "columns": """select table_name || '.' || column_name || ' ' || data_type || ' null=' || is_nullable
                    || ' default=' || coalesce(column_default, '')
                    from information_schema.columns where table_schema = 'public'""",
    "constraints": """select conrelid::regclass::text || ' ' || conname || ' ' || pg_get_constraintdef(oid)
                        from pg_constraint where connamespace = 'public'::regnamespace""",
    "indexes": "select indexname || ' ' || indexdef from pg_indexes where schemaname = 'public'",
    "functions": """select p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ') '
                      || md5(pg_get_functiondef(p.oid))
                      from pg_proc p where p.pronamespace = 'public'::regnamespace and p.prokind in ('f', 'p')""",
    "triggers": """select c.relname || ' ' || t.tgname from pg_trigger t
                     join pg_class c on c.oid = t.tgrelid
                     where not t.tgisinternal and c.relnamespace = 'public'::regnamespace""",
    "policies": "select tablename || ' ' || policyname || ' ' || cmd from pg_policies where schemaname = 'public'",
    "rls_enabled": """select relname || ' ' || relrowsecurity from pg_class
                        where relnamespace = 'public'::regnamespace and relkind = 'r'""",
    "sequences": """select sequencename || ' last=' || coalesce(last_value::text, 'unset')
                      from pg_sequences where schemaname = 'public'""",
}


def read_dsn(path: Path) -> str:
    env = os.environ.get("BACKUP_DATABASE_URL")
    if env:
        return env.strip()
    if not path.exists():
        raise SystemExit(f"No connection string: create {path} (mode 600) or set BACKUP_DATABASE_URL.")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SystemExit(f"{path} is readable by others (mode {oct(mode)}); run: chmod 600 {path}")
    return path.read_text().strip()


def _public_target(params: dict) -> str:
    """Where we are connecting, without anything secret, for the report."""
    return f"{params.get('host', '?')}:{params.get('port', '5432')}/{params.get('dbname', '?')} as {params.get('user', '?')}"


def tables(c) -> list[str]:
    return [r[0] for r in c.execute("select tablename from pg_tables where schemaname = 'public' order by 1")]


# Images the database points at in the `client-images` bucket: the stored key, or the path
# inside a stored signed URL. Keys are de-identified digests, so the list holds no content.
# Every object in the `client-images` bucket an item refers to, in each form the platform
# reads (workforce lib/media.ts resolveImageSource, which prefers `_image_key`):
#   content._image_key   the object key, recorded beside the image by the portal upload
#   content.image        a bare object key (`<16 hex>/<32 hex>.<ext>`), or a signed, public
#                        or authenticated storage URL to the bucket, its path possibly %-encoded
# A row with both contributes both: an older URL may name an object the key does not.
# Superseded versions are included, since they are kept on record. Images are the only
# supported media; any other URL (a client's own storage) is not ours to back up.
IMAGE_REFERENCES = r"""
with refs as (
  select content->>'_image_key' as ref
    from public.project_items
   where jsonb_typeof(content) = 'object' and jsonb_typeof(content->'_image_key') = 'string'
  union all
  select case
           when v ~ '^[0-9a-f]{16}/[0-9a-f]{32}\.[a-z0-9]{1,5}$' then v
           else replace(replace(substring(v from '/storage/v1/object/(?:sign|public|authenticated)/client-images/([^?#]+)'),
                                '%2F', '/'), '%2f', '/')
         end
    from (select content->>'image' as v
            from public.project_items
           where jsonb_typeof(content) = 'object' and jsonb_typeof(content->'image') = 'string') i
)
select distinct ref from refs where ref is not null and ref <> ''
"""


def image_references(c) -> list[str]:
    # A database without the items table refers to no images (and is not this app's, or
    # predates it): nothing to cross-check, rather than a failure.
    if c.execute("select to_regclass('public.project_items')").fetchone()[0] is None:
        return []
    return sorted(r[0] for r in c.execute(IMAGE_REFERENCES) if r[0])


# Every session setting that changes how a value prints as text. The source (Supabase) and
# the throwaway restore are different servers with different defaults; a checksum over text
# is only comparable if both print every value the same way, so both sessions are pinned.
SESSION_SETTINGS = {
    "TimeZone": "UTC",                     # timestamptz prints in the session's zone
    "DateStyle": "ISO, YMD",               # date and timestamp layout
    "IntervalStyle": "postgres",           # interval layout
    "extra_float_digits": "1",             # float4/float8: shortest exact form
    "bytea_output": "hex",                 # bytea as \x… rather than escapes
    "lc_monetary": "C",                    # money's currency symbol and separators
    "search_path": "public",               # how regclass and other names are qualified
}


def pin_session(c) -> None:
    for name, value in SESSION_SETTINGS.items():
        c.execute("select set_config(%s, %s, false)", (name, value))


# A table's checksum: each row hashed on its own, the row hashes sorted byte-wise, then hashed
# together. Sorting hashes rather than rows makes the order independent of the database's
# collation (which decides how text sorts, and differs between servers), while every row
# still counts once: a changed, missing, extra or duplicated row changes the result.
CHECKSUM_SQL = """select md5(coalesce(string_agg(h, '' order by h collate "C"), ''))
                    from (select md5(r::text) as h from public."{table}" r) rows"""


def measure(c) -> dict:
    """Counts, content checksums and the schema inventory, all from the connection's snapshot,
    with the session pinned so the same data gives the same numbers on any server."""
    pin_session(c)
    out = {"counts": {}, "checksums": {}, "inventory": {}}
    for t in tables(c):
        out["counts"][t] = c.execute(f'select count(*) from public."{t}"').fetchone()[0]
        out["checksums"][t] = c.execute(CHECKSUM_SQL.format(table=t)).fetchone()[0]
    for kind, sql in INVENTORY.items():
        out["inventory"][kind] = sorted(r[0] for r in c.execute(sql))
    return out


def compare(src: dict, dst: dict) -> dict:
    problems = {}
    for t, n in src["counts"].items():
        if dst["counts"].get(t) != n:
            problems.setdefault("row_counts", {})[t] = [n, dst["counts"].get(t)]
        if dst["checksums"].get(t) != src["checksums"][t]:
            problems.setdefault("content_checksums", []).append(t)
    extra = sorted(set(dst["counts"]) - set(src["counts"]))
    if extra:
        problems["unexpected_tables"] = extra
    for kind in INVENTORY:
        missing = sorted(set(src["inventory"][kind]) - set(dst["inventory"][kind]))
        added = sorted(set(dst["inventory"][kind]) - set(src["inventory"][kind]))
        if missing or added:
            problems.setdefault("schema", {})[kind] = {"missing_after_restore": missing,
                                                      "only_after_restore": added}
    return problems


def _docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True)


# ── Preflight ────────────────────────────────────────────────────────────────────
# Before anything is dumped: read only metadata (no application rows), measure this
# machine, and refuse unless the dump, the restore and a margin certainly fit. Every
# number is either measured or a stated upper bound; anything unknown is a refusal.

MiB = 1 << 20
SUPPORTED_MAJORS = range(13, 19)           # the Postgres majors with an official image to match
CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT = "30s"

# The in-memory restore is a throwaway Postgres whose memory is capped (below). These are the
# parts that do not scale with the data, sized from the settings it is started with.
RESTORE_SETTINGS = ["-c", "shared_buffers=64MB", "-c", "maintenance_work_mem=64MB",
                    "-c", "max_wal_size=64MB", "-c", "min_wal_size=32MB",
                    "-c", "fsync=off", "-c", "full_page_writes=off", "-c", "synchronous_commit=off"]
EMPTY_CLUSTER_BYTES = 64 * MiB             # template databases and catalogs of a fresh cluster
WAL_CAP_BYTES = 3 * 64 * MiB               # WAL stays under ~3 x max_wal_size between checkpoints
SERVER_MEMORY_BYTES = 256 * MiB            # shared_buffers, maintenance_work_mem, backends
CONTAINER_LAYER_BYTES = 64 * MiB           # writable layers and logs of the two containers
SCHEMA_TEXT_BYTES = 1 * MiB                # a dump's DDL, functions and table of contents
TEMP_BYTES = 1 * MiB                       # the password file
REPORT_BYTES = 1 * MiB                     # the JSON report beside the dump


class PreflightFailed(Exception):
    """Something needed to prove the backup is safe could not be established."""


@dataclass
class Metadata:
    server_version_num: int
    database_bytes: int
    tables: list = field(default_factory=list)       # (name, table_bytes, index_bytes, total_bytes)

    @property
    def major(self) -> int:
        return self.server_version_num // 10000

    @property
    def public_table_bytes(self) -> int:
        return sum(t[1] for t in self.tables)

    @property
    def public_total_bytes(self) -> int:
        return sum(t[3] for t in self.tables)


@dataclass
class Machine:
    docker_ok: bool
    docker_dirs: list                              # where Docker keeps containers and images
    image_present: bool | None                     # None: could not be determined
    mem_available: int
    swap_free: int
    free_by_device: dict                           # st_dev -> bytes free
    device_of: dict                                # path -> st_dev


METADATA_SQL = {
    "version": "show server_version_num",
    "database": "select pg_database_size(current_database())",
    # Catalog sizes only: how big each table and its indexes are, never what is in them.
    "tables": """select c.relname, pg_table_size(c.oid), pg_indexes_size(c.oid), pg_total_relation_size(c.oid)
                   from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p')
                  order by 1""",
}


def connect_source(params: dict, password: str):
    """A connection that cannot write and cannot hang: a connect timeout, a statement
    timeout, and every transaction read-only."""
    conn = psycopg.connect(**params, password=password, connect_timeout=CONNECT_TIMEOUT_S)
    conn.execute(f"set statement_timeout = '{STATEMENT_TIMEOUT}'")
    conn.execute("set default_transaction_read_only = on")
    conn.commit()
    return conn


def read_metadata(conn) -> Metadata:
    """Version, database size and per-table sizes, in one read-only transaction."""
    conn.execute("set transaction read only")
    try:
        version = int(conn.execute(METADATA_SQL["version"]).fetchone()[0])
        database = int(conn.execute(METADATA_SQL["database"]).fetchone()[0])
        tables = [(r[0], int(r[1]), int(r[2]), int(r[3])) for r in conn.execute(METADATA_SQL["tables"])]
    finally:
        conn.rollback()
    return Metadata(version, database, tables)


def _device(path: Path) -> int:
    p = Path(path)
    while not p.exists():
        p = p.parent
    return os.stat(p).st_dev


def probe_machine(out: Path, tmpdir: Path, image: str | None) -> Machine:
    docker_ok, docker_dirs, image_present = True, [], None
    try:
        info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
        docker_dirs.append(Path(info.get("DockerRootDir") or "/var/lib/docker"))
        # With the containerd image store, images live under containerd's own directory.
        if any("containerd" in str(v) for pair in (info.get("DriverStatus") or []) for v in pair):
            docker_dirs.append(Path("/var/lib/containerd"))
    except Exception:
        docker_ok = False
    if docker_ok and image:
        r = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
        image_present = r.returncode == 0
    meminfo = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        meminfo[k] = int(v.split()[0]) * 1024
    paths = {"out": out, "tmp": tmpdir, **{f"docker:{d}": d for d in docker_dirs}}
    device_of, free = {}, {}
    for label, p in paths.items():
        dev = _device(p)
        device_of[label] = dev
        st = os.statvfs(p if Path(p).exists() else Path(p).parent)
        free[dev] = st.f_bavail * st.f_frsize
    return Machine(docker_ok, docker_dirs, image_present, meminfo.get("MemAvailable", 0),
                   meminfo.get("SwapFree", 0), free, device_of)


def plan(meta: Metadata | None, machine: Machine, margin: int, memory_margin: int) -> dict:
    """Whether the backup certainly fits, with every number behind the answer."""
    refusals = []
    if meta is None:
        return {"ok": False, "refusals": ["no metadata: the source could not be measured"]}
    if meta.major not in SUPPORTED_MAJORS:
        refusals.append(f"unknown or unsupported PostgreSQL version {meta.server_version_num}: "
                        "no matching restore image can be chosen")
    image = f"postgres:{meta.major}"
    if not machine.docker_ok:
        refusals.append("Docker is not available")
    elif machine.image_present is None:
        refusals.append(f"could not tell whether {image} is present")
    elif not machine.image_present:
        refusals.append(f"{image} is not on this machine. Its size cannot be known in advance, so it is "
                        f"not downloaded here: pull it yourself (docker pull {image}) once there is room, "
                        "then run the preflight again")

    # Disk, per filesystem: each requirement charged to the device it lands on.
    # The dump's upper bound is the tables' on-disk size: a custom-format dump holds each
    # row's data once, compressed, without the page and tuple overhead or the indexes.
    dump_bound = meta.public_table_bytes + SCHEMA_TEXT_BYTES
    needs = {}
    def charge(label, n):
        needs[machine.device_of[label]] = needs.get(machine.device_of[label], 0) + n
    charge("out", dump_bound + REPORT_BYTES)
    charge("tmp", TEMP_BYTES)
    docker_label = next((k for k in machine.device_of if k.startswith("docker:")), None)
    if docker_label:
        charge(docker_label, CONTAINER_LAYER_BYTES)
    disk = {}
    for dev, need in needs.items():
        have = machine.free_by_device.get(dev, 0)
        disk[str(dev)] = {"needed": need, "margin": margin, "free": have}
        if need + margin > have:
            refusals.append(f"not enough disk on filesystem {dev}: needs {need} bytes plus a {margin}-byte "
                            f"margin, {have} free")

    # Memory: the restored data lives in RAM (tmpfs), with its WAL and the server's own
    # memory. Bounded by the source's tables AND indexes, not by the compressed dump.
    # Swap is not counted: it may be full, and a restore that swaps is not one to rely on.
    restore_data = meta.public_total_bytes + EMPTY_CLUSTER_BYTES + WAL_CAP_BYTES
    restore_memory = restore_data + SERVER_MEMORY_BYTES
    if restore_memory + memory_margin > machine.mem_available:
        refusals.append(f"not enough RAM for an in-memory restore: needs {restore_memory} bytes plus a "
                        f"{memory_margin}-byte margin, {machine.mem_available} available (swap not counted)")
    return {
        "ok": not refusals, "refusals": refusals,
        "source": {"postgres": meta.server_version_num, "database_bytes": meta.database_bytes,
                   "public_tables": len(meta.tables), "public_table_bytes": meta.public_table_bytes,
                   "public_total_bytes": meta.public_total_bytes},
        "restore_image": image,
        "dump_upper_bound_bytes": dump_bound,
        "disk": disk,
        "memory": {"restore_data_cap_bytes": restore_data, "restore_memory_cap_bytes": restore_memory,
                   "margin": memory_margin, "available": machine.mem_available, "swap_free": machine.swap_free},
    }


def _scrub(text: str, password: str) -> str:
    return text.replace(password, "***") if password else text


def preflight(params: dict, password: str, out: Path, tmpdir: Path, margin: int, memory_margin: int) -> dict:
    """Measure the source (metadata only) and this machine. Never raises; a failure is a
    refusal with a reason that names no secret."""
    try:
        conn = connect_source(params, password)
    except Exception as exc:
        return {"ok": False, "refusals": [f"could not connect to the source ({type(exc).__name__}: "
                                          f"{_scrub(str(exc), password).splitlines()[0][:200]})"]}
    try:
        meta = read_metadata(conn)
    except Exception as exc:
        return {"ok": False, "refusals": [f"could not read the source's metadata ({type(exc).__name__}: "
                                          f"{_scrub(str(exc), password).splitlines()[0][:200]})"]}
    finally:
        conn.close()
    image = f"postgres:{meta.major}" if meta.major in SUPPORTED_MAJORS else None
    try:
        machine = probe_machine(out, tmpdir, image)
    except Exception as exc:
        return {"ok": False, "refusals": [f"could not measure this machine ({type(exc).__name__})"]}
    return plan(meta, machine, margin, memory_margin)


# ── The restore container ────────────────────────────────────────────────────────

class Interrupted(BaseException):
    """SIGTERM, raised so the run ends through the same cleanup and naming as Ctrl-C."""


def _interrupt(signum, _frame):
    raise Interrupted(f"stopped by signal {signum}")


class RestoreNotReady(RuntimeError):
    """The throwaway Postgres never accepted connections."""


class RestoreContainer:
    """A throwaway Postgres for the restore, removed however the run ends.

    Created with --pull=never (an image is never downloaded here; the preflight requires it
    to be present), bound only to 127.0.0.1 on a port Docker picks, its data in RAM under the
    preflight's caps, and no swap. Leaving the `with` block removes it, whether the block
    succeeded, failed or was interrupted, and then checks it is really gone. A container that
    could not be removed is reported in `cleanup_error` and printed, never raised: removal
    must not hide why the run failed."""

    def __init__(self, image: str, data_cap: int, memory_cap: int, settings=RESTORE_SETTINGS, runner=None):
        self.image, self.data_cap, self.memory_cap, self.settings = image, data_cap, memory_cap, list(settings)
        self.name = f"senebi-restore-{uuid.uuid4().hex[:8]}"
        self.port: int | None = None
        self.cleanup_error: str | None = None
        self.created = False
        self._run = runner or (lambda cmd, **k: subprocess.run(cmd, capture_output=True, text=True, **k))

    def __enter__(self):
        # Docker can create a container and still fail to start it, and an interrupt can land
        # while `docker run` is still working, so assume one may exist from here on.
        self.created = True
        try:
            r = self._run(["docker", "run", "-d", "--rm", "--pull=never", "--name", self.name,
                           "-e", "POSTGRES_PASSWORD=restore", "-p", "127.0.0.1::5432",
                           "--tmpfs", f"/var/lib/postgresql/data:size={self.data_cap}",
                           f"--memory={self.memory_cap}", f"--memory-swap={self.memory_cap}",
                           self.image, "postgres", *self.settings])
            if r.returncode != 0:
                raise RuntimeError(f"the restore container did not start: {(r.stderr or '').strip()[-300:]}")
            p = self._run(["docker", "port", self.name, "5432/tcp"])
            if p.returncode != 0 or ":" not in (p.stdout or ""):
                raise RuntimeError("could not read the restore container's port")
            self.port = int(p.stdout.strip().splitlines()[0].rsplit(":", 1)[1])
            return self
        except BaseException:
            self.cleanup_error = self.remove()
            self._report_cleanup()
            raise

    def __exit__(self, exc_type, exc, tb):
        self.cleanup_error = self.remove()
        self._report_cleanup()
        return False                         # never swallow the block's own exception

    def remove(self) -> str | None:
        """Remove the container; None when it is gone, otherwise why it may not be."""
        if not self.created:
            return None
        try:
            r = self._run(["docker", "rm", "-f", self.name])
            still = self._run(["docker", "container", "inspect", self.name]).returncode == 0
        except Exception as exc:
            return f"could not run docker to remove {self.name}: {type(exc).__name__}"
        if still:
            return (f"container {self.name} is still present after `docker rm -f` "
                    f"({(r.stderr or '').strip()[-200:] or 'no error given'}); it may hold restored "
                    f"data in memory. Remove it: docker rm -f {self.name}")
        return None

    def _report_cleanup(self):
        if self.cleanup_error:
            print(f"CLEANUP FAILED: {self.cleanup_error}", file=sys.stderr)

    def target(self) -> dict:
        return dict(host="127.0.0.1", port=self.port, user="postgres", password="restore", dbname="postgres")

    def wait_ready(self, timeout_s: float = 90, connect=None) -> None:
        """Until the server accepts a connection and the stubs are in place, or RestoreNotReady."""
        connect = connect or psycopg.connect
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            try:
                with connect(**self.target(), autocommit=True, connect_timeout=2) as t:
                    t.execute(RESTORE_STUBS)
                return
            except psycopg.OperationalError as exc:
                last = exc
                time.sleep(1)
        raise RestoreNotReady(f"{self.name} did not accept connections within {timeout_s:.0f}s "
                              f"({type(last).__name__ if last else 'no attempt'})")


# ── Naming: a dump is only ever called a backup once it is verified ──────────────
# While running:          db-<stamp>.dump.partial
# Verified:               db-<stamp>.dump               + db-<stamp>.json            ("verified": true)
# Failed or interrupted:  db-<stamp>.UNVERIFIED.dump    + db-<stamp>.UNVERIFIED.json (stage, error)
# A run killed outright leaves the .partial name, which is never mistaken for a backup either.
# Nothing is overwritten: an existing name is an error.

def run_files(out: Path, stamp: str) -> dict:
    return {"partial": out / f"db-{stamp}.dump.partial",
            "dump": out / f"db-{stamp}.dump", "report": out / f"db-{stamp}.json",
            "unverified_dump": out / f"db-{stamp}.UNVERIFIED.dump",
            "unverified_report": out / f"db-{stamp}.UNVERIFIED.json"}


def _move(src: Path, dst: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"{dst.name} already exists; not overwriting it")
    os.rename(src, dst)


def finish(files: dict, report: dict, verified: bool) -> Path | None:
    """Name the run's dump and write its report, verified or not. Returns the dump's final path."""
    dump_dst = files["dump"] if verified else files["unverified_dump"]
    report_dst = files["report"] if verified else files["unverified_report"]
    final_dump = None
    if files["partial"].exists():
        os.chmod(files["partial"], 0o600)
        _move(files["partial"], dump_dst)
        final_dump = dump_dst
    if report_dst.exists():
        raise FileExistsError(f"{report_dst.name} already exists; not overwriting it")
    report = {**report, "verified": verified, "dump": final_dump.name if final_dump else None}
    report_dst.write_text(json.dumps(report, indent=2))
    os.chmod(report_dst, 0o600)
    return final_dump


def _close_source(src) -> None:
    """End the read-only source transaction without letting a broken connection's error
    replace the one that actually stopped the run."""
    for step in (src.rollback, src.close):
        try:
            step()
        except Exception as exc:
            print(f"(closing the source connection: {type(exc).__name__}; ignored)", file=sys.stderr)


def dump_source(params: dict, password: str, major: int, dump_path: Path) -> tuple[dict, list]:
    """Measure the source and pg_dump it from one read-only snapshot into dump_path."""
    with tempfile.TemporaryDirectory(prefix="senebi-backup-") as private:
        os.chmod(private, 0o700)
        passfile = Path(private) / "pgpass"
        # host:port:db:user:password, colons and backslashes escaped as libpq requires.
        esc = lambda v: str(v).replace("\\", "\\\\").replace(":", "\\:")   # noqa: E731
        passfile.write_text(":".join(esc(params.get(k, "*")) for k in ("host", "port", "dbname", "user"))
                            + ":" + esc(password) + "\n")
        # libpq refuses a password file anyone else can read; the container reads it as root.
        os.chmod(passfile, 0o600)

        src = connect_source(params, password)
        try:
            src.execute("set transaction isolation level repeatable read, read only")
            # Counting and hashing every row takes longer than a metadata read.
            src.execute("set local statement_timeout = '15min'")
            if int(src.execute("show server_version_num").fetchone()[0]) // 10000 != major:
                raise RuntimeError("the source's version changed since the preflight")
            snapshot = src.execute("select pg_export_snapshot()").fetchone()[0]
            expected = measure(src)
            references = image_references(src)
            # Non-secret connection settings as environment; the password only in the file.
            env_args = [a for k, v in (("PGHOST", params.get("host")), ("PGPORT", params.get("port")),
                                       ("PGDATABASE", params.get("dbname")), ("PGUSER", params.get("user")),
                                       ("PGSSLMODE", params.get("sslmode", "require")),
                                       ("PGCONNECT_TIMEOUT", str(CONNECT_TIMEOUT_S)))
                        if v for a in ("-e", f"{k}={v}")]
            with open(dump_path, "wb") as f:
                os.chmod(dump_path, 0o600)
                proc = subprocess.run(
                    ["docker", "run", "--rm", "--pull=never", "--network", "host",
                     "-v", f"{passfile}:/pgpass:ro", "-e", "PGPASSFILE=/pgpass", *env_args, f"postgres:{major}",
                     "pg_dump", f"--snapshot={snapshot}", "--schema=public", "--lock-wait-timeout=30s",
                     "--no-owner", "--no-privileges", "--format=custom"],
                    stdout=f, stderr=subprocess.PIPE)
            if proc.returncode != 0:
                raise RuntimeError("pg_dump failed: " + _scrub(proc.stderr.decode()[-500:], password))
            return expected, references
        finally:
            _close_source(src)


def verify_restore(dump_path: Path, major: int, data_cap: int, memory_cap: int, stages: list, facts: dict) -> dict:
    """Restore the dump into a throwaway container and measure it. The container is removed
    however this ends; `facts` records it, and any failure to remove it, even when this raises."""
    rc = RestoreContainer(f"postgres:{major}", data_cap, memory_cap)
    facts.update(container=rc.name, data_cap_bytes=data_cap, memory_cap_bytes=memory_cap)
    try:
        stages.append("restore_container")
        with rc:
            stages.append("restore_ready")
            rc.wait_ready()
            stages.append("restore")
            with open(dump_path, "rb") as f:
                proc = subprocess.run(
                    ["docker", "exec", "-i", rc.name, "pg_restore", "--no-owner", "--no-privileges",
                     "--exit-on-error", "--dbname=postgres", "--username=postgres"],
                    stdin=f, capture_output=True)
            if proc.returncode != 0:
                raise RuntimeError("pg_restore failed: " + proc.stderr.decode()[-800:])
            stages.append("measure_restore")
            with psycopg.connect(**rc.target(), autocommit=True) as t:
                t.execute("set default_transaction_read_only = on")
                restored = measure(t)
    finally:
        facts["cleanup_error"] = rc.cleanup_error
    return restored


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn-file", type=Path, default=DEFAULT_DSN_FILE,
                    help="file holding the connection string (mode 600)")
    ap.add_argument("--out", required=True, help="private folder for the dump and its report")
    ap.add_argument("--preflight-only", action="store_true",
                    help="measure (metadata only) and report whether the backup would fit; dump nothing")
    ap.add_argument("--margin-mb", type=int, default=1024, help="disk to leave free on every filesystem used")
    ap.add_argument("--memory-margin-mb", type=int, default=512, help="RAM to leave free during the restore")
    args = ap.parse_args(argv)

    params = conninfo_to_dict(read_dsn(args.dsn_file.expanduser()))
    password = params.pop("password", None) or ""
    params.setdefault("port", "5432")
    target_desc = _public_target(params)

    out = Path(args.out).expanduser()
    tmpdir = Path(tempfile.gettempdir())

    # Nothing is written, and nothing beyond catalog metadata is read, until this passes.
    checked = preflight(params, password, out, tmpdir, args.margin_mb * MiB, args.memory_margin_mb * MiB)
    print(json.dumps({"preflight": checked}, indent=2))
    if not checked["ok"]:
        print("PREFLIGHT REFUSED: nothing was dumped.")
        return 1
    if args.preflight_only:
        print("PREFLIGHT PASSED: the backup fits. Nothing was dumped (--preflight-only).")
        return 0
    major = int(checked["restore_image"].split(":")[1])

    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    files = run_files(out, stamp)
    taken = [p.name for p in files.values() if p.exists()]
    if taken:
        print(f"REFUSED: {', '.join(taken)} already exist in {out}; nothing is overwritten. Nothing was dumped.")
        return 1

    report = {"taken_at": stamp, "source": target_desc, "postgres_major": major, "preflight": checked}
    stages: list[str] = []
    facts: dict = {}
    expected = references = restored = None
    error: BaseException | None = None
    problems: dict = {}
    previous_term = signal.signal(signal.SIGTERM, _interrupt)
    try:
        stages.append("dump")
        expected, references = dump_source(params, password, major, files["partial"])
        stages.append("hash_dump")
        report["dump_sha256"] = hashlib.sha256(files["partial"].read_bytes()).hexdigest()
        report["dump_bytes"] = files["partial"].stat().st_size
        restored = verify_restore(files["partial"], major, checked["memory"]["restore_data_cap_bytes"],
                                  checked["memory"]["restore_memory_cap_bytes"], stages, facts)
        stages.append("compare")
        problems = compare(expected, restored)
    except BaseException as exc:          # interrupts too: the evidence is still named for what it is
        error = exc
    finally:
        signal.signal(signal.SIGTERM, previous_term)

    verified = error is None and not problems
    cleanup_error = facts.get("cleanup_error")
    report.update(
        status="verified" if verified else ("verification_failed" if error is None else "incomplete"),
        failed_stage=None if verified else stages[-1],
        error=None if error is None else f"{type(error).__name__}: {_scrub(str(error), password)[-800:]}",
        problems=problems,
        restored_into=facts or None, cleanup_error=cleanup_error,
        tables=len(expected["counts"]) if expected else None,
        rows=sum(expected["counts"].values()) if expected else None,
        objects={k: len(v) for k, v in expected["inventory"].items()} if expected else None,
        image_references=references,
        counts=expected["counts"] if expected else None,
        checksums=expected["checksums"] if expected else None)
    try:
        final = finish(files, report, verified)
    except Exception as exc:
        # Naming the evidence failed; say so without hiding why the run itself ended.
        print(f"COULD NOT NAME THE RUN'S FILES ({type(exc).__name__}: {exc}). Whatever is in {out} "
              f"for {stamp} is NOT a verified backup.", file=sys.stderr)
        final = None
        verified = False

    shown = {k: v for k, v in report.items() if k not in ("counts", "checksums", "image_references", "preflight")}
    shown["dump"] = final.name if final else None
    shown["image_references"] = len(references) if references is not None else None
    print(json.dumps(shown, indent=2))
    if verified and not cleanup_error:
        print(f"BACKUP VERIFIED: {final.name}")
        return 0
    if verified:
        print(f"BACKUP VERIFIED: {final.name}, BUT THE RESTORE CONTAINER WAS NOT REMOVED: {cleanup_error}")
        return 1
    print(f"NOT A VERIFIED BACKUP ({report['status']}"
          + (f" at {report['failed_stage']}" if report["failed_stage"] else "") + "). "
          + (f"Evidence kept as {final.name} and its .UNVERIFIED.json." if final
             else "No dump was written; the report is the .UNVERIFIED.json."))
    if cleanup_error:
        print(f"ALSO, THE RESTORE CONTAINER WAS NOT REMOVED: {cleanup_error}")
    return 130 if isinstance(error, (KeyboardInterrupt, Interrupted)) else 1

if __name__ == "__main__":
    sys.exit(main())
