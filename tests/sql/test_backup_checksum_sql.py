"""
backup_verify.py's content checksum, across databases that hold the same data but differ in
everything a server may differ in: collation (how text sorts), time zone, date, interval,
float, bytea and money output. The production source and the throwaway restore are such a
pair, so the checksum must agree across them, and still catch every real difference.

Each "false mismatch" case first shows the previous checksum (rows ordered by their text,
under the database's collation and the session's own settings) disagreeing on identical data,
then shows the current one agreeing.
"""
import uuid

import psycopg
import pytest

from conftest import DSN
from scripts import backup_verify as B

# The checksum as it was before this fix, kept here only to prove the regression.
OLD_CHECKSUM = """select md5(coalesce(string_agg(r::text, chr(10) order by r::text), ''))
                    from public."{table}" r"""

SCHEMA = """
create table samples (
  id integer primary key, label text, at timestamptz, day date, span interval,
  ratio float8, blob bytea, price money, amount numeric, doc jsonb, note text
);
create sequence counter;
-- No key to sort by first: the order of these rows is decided by collation alone.
create table words (w text);
"""
ROWS = """
insert into samples values
  (1, 'apple',  '2026-10-24 09:15:00.123456+00', '2026-01-02', '1 day 02:03:04', 0.1,  '\\x00ff', 12.5, 1.50, '{"b": 1, "a": [1, 2]}', null),
  (2, 'Banana', '2026-10-24 23:59:59+03',        '2026-12-31', '-3 hours',       1e-7, '\\x',     0,    0,    '{}',                      ''),
  (3, 'cherry', '1999-01-01 00:00:00+00',        '1999-01-01', '1 mon',          3.14159265358979, null, 9999.99, 123456789.123, 'null', 'ünïcödé'),
  (4, 'Ápple',  null,                            null,         null,             null, null, null, null, null, 'tab\tline');
select setval('counter', 42);
insert into words values ('apple'), ('Banana'), ('Ápple'), ('banana'), ('_under'), ('Zebra');
"""


def _admin():
    return psycopg.connect(DSN, autocommit=True)


def _db(locale="C", **settings):
    """A fresh database with the given collation and default session settings."""
    name = f"ck_{uuid.uuid4().hex[:10]}"
    with _admin() as a:
        a.execute(f"create database {name} template template0 locale '{locale}' encoding 'UTF8'")
        for k, v in settings.items():
            a.execute(f"alter database {name} set {k} = '{v}'")
    return name


def _conn(name):
    return psycopg.connect(psycopg.conninfo.make_conninfo(DSN, dbname=name), autocommit=True)


def _drop(*names):
    with _admin() as a:
        for n in names:
            a.execute(f"drop database if exists {n} with (force)")


def _filled(locale="C", **settings):
    name = _db(locale, **settings)
    with _conn(name) as c:
        c.execute(SCHEMA)
        c.execute(ROWS)
    return name


def _old(name):
    with _conn(name) as c:                          # the database's own defaults, as before
        return {t: c.execute(OLD_CHECKSUM.format(table=t)).fetchone()[0] for t in ("samples", "words")}


def _measure(name):
    with _conn(name) as c:
        return B.measure(c)


@pytest.fixture
def pair(templates):
    made = []

    def make(a=None, b=None):
        x, y = _filled(**(a or {})), _filled(**(b or {}))
        made.extend([x, y])
        return x, y
    yield make
    _drop(*made)


# ── identical data, different servers: must agree ──────────────────────────────

DIFFERENT_SERVERS = {
    "collation only": ({"locale": "C"}, {"locale": "en_US.utf8"}),
    "time zone only": ({"TimeZone": "UTC"}, {"TimeZone": "Asia/Kolkata"}),
    "every session default": (
        {"locale": "C", "TimeZone": "UTC"},
        {"locale": "en_US.utf8", "TimeZone": "America/Los_Angeles", "DateStyle": "SQL, DMY",
         "IntervalStyle": "sql_standard", "extra_float_digits": "0", "bytea_output": "escape",
         "lc_monetary": "en_US.utf8"}),
}


@pytest.mark.parametrize("case", list(DIFFERENT_SERVERS))
def test_identical_data_agrees_where_the_old_checksum_did_not(pair, case):
    a, b = pair(*DIFFERENT_SERVERS[case])
    assert _old(a) != _old(b), "the old checksum should have produced a false mismatch here"
    ma, mb = _measure(a), _measure(b)
    assert ma["checksums"] == mb["checksums"]
    assert B.compare(ma, mb) == {}


def test_the_pinned_session_holds_whatever_the_database_defaults_are(pair):
    a, b = pair(*DIFFERENT_SERVERS["every session default"])
    for name in (a, b):
        with _conn(name) as c:
            B.pin_session(c)
            for setting, value in B.SESSION_SETTINGS.items():
                assert c.execute("select current_setting(%s)", (setting,)).fetchone()[0] == value


# ── real differences: must still fail ───────────────────────────────────────────

DATA_CHANGES = {
    "a changed value": "update samples set label = 'apples' where id = 1",
    "a missing row": "delete from samples where id = 3",
    "an extra row": "insert into samples (id, label) values (9, 'extra')",
    "null became empty text": "update samples set note = '' where id = 1",
    "one microsecond later": "update samples set at = at + interval '1 microsecond' where id = 1",
    "a float in its last digit": "update samples set ratio = 0.1 + 1e-16 where id = 1",
    "a jsonb key renamed": """update samples set doc = '{"c": 1, "a": [1, 2]}' where id = 1""",
}


@pytest.mark.parametrize("change", list(DATA_CHANGES))
def test_a_real_data_difference_still_fails(pair, change):
    a, b = pair(*DIFFERENT_SERVERS["every session default"])
    with _conn(b) as c:
        c.execute(DATA_CHANGES[change])
    problems = B.compare(_measure(a), _measure(b))
    assert "content_checksums" in problems and "samples" in problems["content_checksums"]


def test_a_duplicated_row_still_fails(pair):
    """Rows are a multiset: two identical rows are not one row."""
    a, b = pair()
    for name, copies in ((a, 1), (b, 2)):
        with _conn(name) as c:
            c.execute("create table plain (v text)")
            for _ in range(copies):
                c.execute("insert into plain values ('same')")
    problems = B.compare(_measure(a), _measure(b))
    assert "plain" in problems["content_checksums"] and problems["row_counts"]["plain"] == [1, 2]


def test_rows_swapped_between_tables_still_fail(pair):
    a, b = pair()
    with _conn(b) as c:
        c.execute("create table other (like samples)")
        c.execute("insert into other select * from samples where id = 4")
        c.execute("delete from samples where id = 4")
    with _conn(a) as c:
        c.execute("create table other (like samples)")
    problems = B.compare(_measure(a), _measure(b))
    assert set(problems["content_checksums"]) == {"samples", "other"}


SCHEMA_CHANGES = {
    "indexes": "create index samples_label on samples (label)",
    "constraints": "alter table samples add constraint price_positive check (amount >= 0)",
    "functions": "create function f() returns int language sql as 'select 1'",
    "triggers": """create function t() returns trigger language plpgsql as 'begin return new; end';
                   create trigger tr before insert on samples for each row execute function t()""",
    "policies": "create policy p on samples using (true)",
    "rls_enabled": "alter table samples enable row level security",
    "sequences": "select setval('counter', 43)",
}


@pytest.mark.parametrize("kind", list(SCHEMA_CHANGES))
def test_a_schema_difference_still_fails(pair, kind):
    a, b = pair(*DIFFERENT_SERVERS["every session default"])
    with _conn(b) as c:
        c.execute(SCHEMA_CHANGES[kind])
    problems = B.compare(_measure(a), _measure(b))
    assert kind in problems.get("schema", {}), problems


def test_a_function_body_change_is_caught_even_with_the_same_signature(pair):
    a, b = pair()
    with _conn(a) as c:
        c.execute("create function g() returns int language sql as 'select 1'")
    with _conn(b) as c:
        c.execute("create function g() returns int language sql as 'select 2'")
    assert "functions" in B.compare(_measure(a), _measure(b))["schema"]
