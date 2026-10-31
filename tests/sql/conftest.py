"""
A real Postgres for the native task store's SQL, built from both repos' own schema files.

These tests prove what an in-memory fake cannot: that the answer, the completion and the
event really commit together or not at all, that the reviewer ceiling holds when several
clinicians submit to one task at the same moment, and that only one worker can process a
task's events at a time.

They never touch a real database. They need SQLTEST_DSN pointing at a disposable server
(the admin database of it), and skip otherwise, so the hermetic suite stays hermetic:

    docker run -d --rm --name senebi-sqltest -e POSTGRES_PASSWORD=test \
        -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:15
    SQLTEST_DSN=postgresql://postgres:test@127.0.0.1:55432/postgres \
        PYTHONPATH=. pytest tests/sql

The workforce repo's schema is read from WORKFORCE_REPO (default: the sibling checkout).
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

# The shared test helpers (_fakedb) live one folder up.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("SQLTEST_DSN")
HEALTH = Path(__file__).resolve().parents[2]
WORKFORCE = Path(os.environ.get(
    "WORKFORCE_REPO",
    HEALTH.parent / "workforce" / "senebiclabs-workforce-platform"))

# Migrations that need a Postgres extension this disposable server does not have, so they
# cannot be part of a template. 011 schedules the lease sweep through pg_cron and is written
# to refuse rather than half-apply, which is itself asserted in
# tests/sql/test_reservation_sweep_sql.py. Everything the application reads is in 010.
NEEDS_EXTENSIONS = {"011_schedule_reservation_sweep.sql"}

TEMPLATE_BEFORE = "senebi_tpl_before_native"   # everything except the native store
TEMPLATE_BEFORE_008 = "senebi_tpl_before_008"   # the native store, Label Studio not yet removed
TEMPLATE = "senebi_tpl_native"                 # everything

if not DSN:
    pytest.skip("SQLTEST_DSN not set: these tests need a disposable Postgres", allow_module_level=True)

# Stand-ins for what Supabase provides and the schema files assume.
_SUPABASE_STUBS = """
create schema if not exists auth;
create or replace function auth.uid() returns uuid language sql as 'select null::uuid';
do $$ begin create role anon; exception when duplicate_object then null; end $$;
do $$ begin create role authenticated; exception when duplicate_object then null; end $$;
-- Migration 009 grants EXECUTE on its functions to service_role explicitly, so the role
-- has to exist here too. Without it the whole suite errored, and only passed at all
-- because the throwaway e2e stack happens to create the role in the same container.
do $$ begin create role service_role nologin bypassrls; exception when duplicate_object then null; end $$;
"""


def _schema_files(native: bool, upto: str | None = None) -> list[Path]:
    files = [HEALTH / "supabase_schema.sql"]
    files += sorted((HEALTH / "migrations").glob("00[2-4]_*.sql"))
    files += [WORKFORCE / "database.sql"]
    files += sorted(p for p in (WORKFORCE / "migrations").glob("*.sql"))
    if not native:
        files = [f for f in files if f.name != "021_pool_project.sql"]
    else:
        # Every HEALTH migration from the native store on, in order.
        files += sorted(f for f in (HEALTH / "migrations").glob("*.sql")
                        if f.name >= "005" and f.name not in NEEDS_EXTENSIONS
                        and (upto is None or f.name < upto))
    return files


def _dsn_for(db: str) -> str:
    return psycopg.conninfo.make_conninfo(DSN, dbname=db)


def _admin():
    return psycopg.connect(DSN, autocommit=True)


def _build(name: str, native: bool, upto: str | None = None) -> None:
    with _admin() as a:
        a.execute(f'drop database if exists "{name}"')
        a.execute(f'create database "{name}"')
    with psycopg.connect(_dsn_for(name), autocommit=True) as c:
        c.execute(_SUPABASE_STUBS)
        for f in _schema_files(native, upto):
            c.execute(f.read_text())


@pytest.fixture(scope="session")
def templates():
    if not (WORKFORCE / "database.sql").exists():
        pytest.skip(f"workforce repo not found at {WORKFORCE}")
    _build(TEMPLATE_BEFORE, native=False)
    _build(TEMPLATE_BEFORE_008, native=True, upto="008")
    _build(TEMPLATE, native=True)
    yield
    with _admin() as a:
        for t in (TEMPLATE, TEMPLATE_BEFORE_008, TEMPLATE_BEFORE):
            a.execute(f'drop database if exists "{t}"')


def _clone(template: str) -> str:
    name = f"t_{uuid.uuid4().hex[:12]}"
    with _admin() as a:
        a.execute(f'create database "{name}" template "{template}"')
    return name


def _drop(name: str) -> None:
    with _admin() as a:
        a.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture
def dsn(templates):
    """A fresh database with the full schema, native store included."""
    name = _clone(TEMPLATE)
    yield _dsn_for(name)
    _drop(name)


@pytest.fixture
def dsn_before_native(templates):
    """A fresh database as production is today: no native store yet."""
    name = _clone(TEMPLATE_BEFORE)
    yield _dsn_for(name)
    _drop(name)


@pytest.fixture
def dsn_before_008(templates):
    """A fresh database with the native store, where Label Studio work can still exist."""
    name = _clone(TEMPLATE_BEFORE_008)
    yield _dsn_for(name)
    _drop(name)


@pytest.fixture
def db(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        yield c


def apply_native(conn) -> None:
    """The native-pilot release's migrations: workforce 021, then HEALTH 005 to 007."""
    conn.execute((WORKFORCE / "migrations" / "021_pool_project.sql").read_text())
    for f in sorted((HEALTH / "migrations").glob("*.sql")):
        if "005" <= f.name < "008":
            conn.execute(f.read_text())
