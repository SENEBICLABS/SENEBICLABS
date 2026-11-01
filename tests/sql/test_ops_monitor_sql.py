"""
The in-database operations monitor, against a real Postgres: migrations 012 and 013.

What an in-memory fake cannot show, and what this file is for: that the operator key is sent
in a request header and is never written into the monitor's own record, that a reply which
says 503 is recorded as the monitor working rather than the monitor failing, that a failed
probe does not move the timestamp which says the monitor is alive, that two ticks at the
same moment do not both collect the same reply, and that a key which is not the service key
can neither call the tick nor read its record.

`pg_net` and `supabase_vault` are not installable on this disposable server, so the tests
below create stand-ins for the small part of each that 012 touches: `net.http_get` writing
to a request queue, `net._http_response` to read replies from, and `vault.decrypted_secrets`
to read a secret from. That is a stub of the *interface*, so these tests prove the
migration's own logic and prove nothing about pg_net. Two things are therefore checked
against the real thing rather than here: the column list of `net._http_response` and the
signature of `net.http_get` (supabase.github.io/pg_net, checked 2026-11-01), and the fact
that 013 refuses outright when either extension is absent, which the last tests assert.

013 is not in the template (see conftest.NEEDS_EXTENSIONS) because it needs pg_cron and
pg_net. Everything the application reads is in 012.
"""
import json
import pathlib
import re
import uuid

import psycopg
import pytest

HEALTH = pathlib.Path(__file__).resolve().parents[2]
M012 = HEALTH / "migrations" / "012_ops_monitor.sql"
M013 = HEALTH / "migrations" / "013_schedule_ops_monitor.sql"

KEY = "op_test_key_that_must_never_be_written_down"
URL = "http://api.test/api/v1/project/admin/ops-health"

# What `ops-health` really answers, trimmed to the shape 012 reads: a top-level status and a
# list of checks each with a name and a status.
OK_BODY = json.dumps({"status": "ok", "checked_at": "2026-11-01T00:00:00+00:00",
                      "checks": [{"name": "database_latency_ms", "status": "ok"}]})
ALERT_BODY = json.dumps({"status": "alert", "checks": [
    {"name": "database_latency_ms", "status": "ok"},
    {"name": "scoring_events_failed", "status": "alert"},
    {"name": "deliveries_failing", "status": "warn"}]})


# ── the stand-ins ───────────────────────────────────────────────────────────────

NET_STUB = """
create schema if not exists net;

-- The columns pg_net really has (supabase.github.io/pg_net). Only `id`, `status_code`,
-- `timed_out`, `error_msg` and `content` are read by 012; the rest are here so a test
-- cannot pass against a shape the real extension does not have.
create unlogged table net._http_response (
  id            bigint,
  status_code   integer,
  content_type  text,
  headers       jsonb,
  content       text,
  timed_out     boolean,
  error_msg     text,
  created       timestamptz not null default now()
);

-- Where pg_net parks a request, headers included, until its worker sends it. Kept here
-- because it is the one place this monitor's key ever appears, and a test asserts that is
-- the only place.
create unlogged table net.http_request_queue (
  id                   bigserial primary key,
  method               text not null,
  url                  text not null,
  headers              jsonb,
  body                 bytea,
  timeout_milliseconds integer not null
);

create function net.http_get(url text, params jsonb default '{}'::jsonb,
                             headers jsonb default '{}'::jsonb,
                             timeout_milliseconds int default 5000) returns bigint
language sql as $$
  insert into net.http_request_queue (method, url, headers, timeout_milliseconds)
       values ('GET', url, headers, timeout_milliseconds)
    returning id;
$$;
"""

VAULT_STUB = """
create schema if not exists vault;
create table vault.secrets (
  id uuid primary key default gen_random_uuid(),
  name text unique, description text default '', secret text not null);
create view vault.decrypted_secrets as
  select id, name, description, secret, secret as decrypted_secret from vault.secrets;
create function vault.create_secret(new_secret text, new_name text default null,
                                    new_description text default '') returns uuid
language sql as $$
  insert into vault.secrets (name, description, secret)
       values (new_name, new_description, new_secret)
    returning id;
$$;
"""


def _arm(db, *, net=True, vault=True, secret=KEY, enabled=True):
    """A database where the monitor can actually run, or deliberately cannot."""
    if net:
        db.execute(NET_STUB)
    if vault:
        db.execute(VAULT_STUB)
        if secret is not None:
            db.execute("select vault.create_secret(%s, 'ops_monitor_admin_key', 'test')",
                       (secret,))
    db.execute("update ops_monitor_state set url = %s, enabled = %s where name = 'ops-health'",
               (URL, enabled))


def _state(db):
    row = db.execute(
        "select enabled, pending_request_id, pending_since, last_run_at, last_success_at,"
        " last_status_code, last_reported_status, last_failing_checks, last_failure_at,"
        " last_error, consecutive_failures, runs"
        " from ops_monitor_state where name = 'ops-health'").fetchone()
    return dict(zip(("enabled", "pending", "pending_since", "last_run_at", "last_success_at",
                     "code", "reported", "failing", "last_failure_at", "error",
                     "failures", "runs"), row))


def _tick(db):
    return db.execute("select ops_monitor_tick()").fetchone()[0]


def _reply(db, request_id, *, status_code=None, content=None,
           timed_out=None, error_msg=None):
    """Stand in for pg_net's worker having sent the request and got an answer."""
    db.execute("insert into net._http_response (id, status_code, content, timed_out, error_msg)"
               " values (%s, %s, %s, %s, %s)",
               (request_id, status_code, content, timed_out, error_msg))


def _queue(db):
    return db.execute("select url, headers, timeout_milliseconds from net.http_request_queue"
                      " order by id desc limit 1").fetchone()


# ── 012 applies to a plain Postgres and leaves the monitor off ─────────────────

def test_the_row_is_seeded_but_switched_off(db):
    """The default that keeps applying this migration from raising an alarm about itself."""
    s = _state(db)
    assert s["enabled"] is False
    assert s["runs"] == 0 and s["last_run_at"] is None and s["last_success_at"] is None
    assert db.execute("select url, secret_name from ops_monitor_state"
                      " where name = 'ops-health'").fetchone() == (
        "https://api.senebiclabs.com/api/v1/project/admin/ops-health", "ops_monitor_admin_key")


def test_reapplying_012_does_not_overwrite_an_operators_url_or_flag(db):
    """Safe to re-run, as the header promises, including after activation."""
    _arm(db)
    db.execute("update ops_monitor_state set url = 'http://moved.test/x'")
    db.execute(M012.read_text())
    s = db.execute("select url, enabled from ops_monitor_state").fetchone()
    assert s == ("http://moved.test/x", True), "re-running the migration reset configuration"


def test_a_switched_off_monitor_does_nothing_at_all(db):
    _arm(db, enabled=False)
    assert _tick(db) == "skipped: disabled"
    assert _state(db)["runs"] == 0
    assert db.execute("select count(*) from net.http_request_queue").fetchone()[0] == 0


# ── the first tick sends a request, and the key goes nowhere else ──────────────

def test_the_first_tick_sends_the_probe_with_the_key_in_a_header(db):
    _arm(db)
    assert _tick(db).startswith("nothing pending; sent ")

    url, headers, timeout = _queue(db)
    assert url == URL
    assert headers == {"X-Admin-Key": KEY}, "the key must be sent as the admin header"
    assert timeout == 10000

    s = _state(db)
    assert s["pending"] is not None and s["pending_since"] is not None
    assert s["last_run_at"] is not None and s["runs"] == 1
    # A request issued is not an answer received: the heartbeat stays empty until something
    # replies, which is the whole reason the staleness bound is read from last_success_at.
    assert s["last_success_at"] is None


def test_the_key_is_never_written_into_the_monitors_own_record(db):
    """The invariant that lets `ops-health` read this row and hand it to a monitor.

    Every text column of the row, after a full cycle, scanned for the secret. `ops-health`
    serves `last_error` and `last_reported_status` in its body, so a key that leaked into
    either would leave the database in an HTTP response.

    Every branch that writes `last_error`, not only one, because a leak added to a branch
    this test did not visit would otherwise go unnoticed.

    What actually keeps the collect branches safe today is structural rather than careful:
    the secret is read *after* the collect, so `v_secret` is still null in every branch that
    records a reply, and a leak written into one of them would record a null rather than a
    key. This test is the guard on a refactor that moves that read earlier, which is the one
    change that would turn those messages into a credential in an HTTP response.
    """
    _arm(db)
    for reply in (dict(status_code=403, content="forbidden"),      # the named-key branch
                  dict(status_code=502, content="bad gateway"),    # the generic branch
                  dict(timed_out=True),
                  dict(error_msg="Could not resolve host: api.test"),
                  dict(status_code=200, content=OK_BODY),
                  dict(status_code=503, content=ALERT_BODY)):
        _tick(db)
        _reply(db, _state(db)["pending"], **reply)
        _tick(db)
        row = db.execute("select to_jsonb(t) from ops_monitor_state t").fetchone()[0]
        assert KEY not in json.dumps(row), f"the key reached the record after {reply}: {row}"

    # The lost-reply branch writes its own message with the key still in scope, and the
    # two preconditions write theirs before the key is read at all.
    _tick(db)
    db.execute("update ops_monitor_state set pending_since = now() - interval '11 minutes'")
    _tick(db)
    row = db.execute("select to_jsonb(t) from ops_monitor_state t").fetchone()[0]
    assert KEY not in json.dumps(row), f"the key reached the record: {row}"
    # And the one place it legitimately appears is pg_net's queue, which is the trade 013
    # documents rather than a leak this migration can avoid.
    assert KEY in json.dumps(db.execute(
        "select coalesce(jsonb_agg(headers), '[]') from net.http_request_queue").fetchone()[0])


# ── collecting a reply ─────────────────────────────────────────────────────────

def test_a_200_is_recorded_as_the_heartbeat(db):
    _arm(db)
    _tick(db)
    rid = _state(db)["pending"]
    _reply(db, rid, status_code=200, content=OK_BODY)

    assert _tick(db).startswith("answered 200: ok; sent ")
    s = _state(db)
    assert s["last_success_at"] is not None
    assert s["code"] == 200 and s["reported"] == "ok"
    assert s["failing"] is None, "a healthy body has no failing checks to name"
    assert s["failures"] == 0 and s["error"] is None
    assert s["runs"] == 2
    # Collected replies are not left lying about: the body of every probe would otherwise
    # sit in an unlogged table for six hours with no reader.
    assert db.execute("select count(*) from net._http_response").fetchone()[0] == 0


def test_a_503_is_the_monitor_working_and_names_the_failing_checks(db):
    """The classification the whole design turns on.

    `ops-health` answers 503 whenever any check is in alert. If the monitor counted that as
    a failed probe it would mark itself failing whenever anything else failed, the endpoint
    would answer 503 about the monitor, and that 503 would keep the monitor failing after
    the original fault was fixed. A probe succeeds when it reaches the endpoint; what the
    endpoint said is recorded, not judged.
    """
    _arm(db)
    _tick(db)
    rid = _state(db)["pending"]
    _reply(db, rid, status_code=503, content=ALERT_BODY)

    assert _tick(db).startswith("answered 503: alert; sent ")
    s = _state(db)
    assert s["last_success_at"] is not None, "a 503 still means the endpoint answered"
    assert s["failures"] == 0 and s["error"] is None
    assert s["code"] == 503 and s["reported"] == "alert"
    assert s["failing"] == ["deliveries_failing", "scoring_events_failed"], \
        "the names of what was failing, in a stable order, and nothing else"


@pytest.mark.parametrize("label, code, content", [
    ("an unrelated marketing page", 200, "<html>Sign in to our site</html>"),
    ("a proxy error page", 200, "<html>502 Bad Gateway</html>"),
    ("malformed JSON", 200, '{"status": "ok", "checks": ['),
    ("JSON with no checks", 200, '{"status": "ok"}'),
    ("checks that is not an array", 200, '{"status": "ok", "checks": "none"}'),
    ("an empty body", 200, ""),
    # The route answers 503 with this when it cannot reach the database, and a load
    # balancer answers 503 with its own page. Neither is ops-health reporting on anything.
    ("the route's own database-unavailable 503", 503, '{"detail": "Database unavailable."}'),
])
def test_an_answer_that_is_not_an_ops_health_body_is_not_a_heartbeat(db, label, code, content):
    """What a status code does and does not prove, which this originally got wrong.

    This test replaces one that asserted the opposite: that any 200 was a heartbeat and an
    unparseable body merely left the verdict unknown. Pointed at a marketing page, that gave
    a monitor which was permanently green and had never spoken to this platform once. A
    status code proves something at the configured URL replied; the body is what proves it
    was `ops-health`.
    """
    _arm(db)
    _tick(db)
    _reply(db, _state(db)["pending"], status_code=code, content=content)

    assert _tick(db).startswith(f"failed: HTTP {code} but not an ops-health body"), label
    s = _state(db)
    assert s["last_success_at"] is None, f"{label} was recorded as a heartbeat"
    assert s["failures"] == 1 and s["code"] == code
    assert "not with an ops-health body" in s["error"]
    assert "url column" in s["error"], "the note should say where to look first"


@pytest.mark.parametrize("code", [200, 201, 503])
def test_a_real_ops_health_body_is_a_heartbeat_whatever_the_status(db, code):
    """The deliberate 503 behaviour, kept: `ops-health` answers 503 whenever anything is in
    alert, and that is the endpoint working. Validating the body is what makes the 503 case
    mean "ops-health said so" rather than "something on that port said so"."""
    _arm(db)
    _tick(db)
    _reply(db, _state(db)["pending"], status_code=code,
           content=ALERT_BODY if code == 503 else OK_BODY)

    assert _tick(db).startswith(f"answered {code}")
    s = _state(db)
    assert s["last_success_at"] is not None and s["failures"] == 0 and s["error"] is None
    assert s["reported"] == ("alert" if code == 503 else "ok")


@pytest.mark.parametrize("reply, expect_return, expect_error", [
    (dict(timed_out=True), "failed: timed out", "timed out"),
    (dict(error_msg="Could not resolve host: api.test"), "failed: no response",
     "Could not resolve host"),
    (dict(status_code=403, content="forbidden"), "failed: HTTP 403", "revoked or replaced"),
    (dict(status_code=401, content=""), "failed: HTTP 401", "revoked or replaced"),
    (dict(status_code=502, content="bad gateway"), "failed: HTTP 502", "answered HTTP 502"),
])
def test_each_way_a_probe_can_fail_is_recorded_as_itself(db, reply, expect_return, expect_error):
    _arm(db)
    _tick(db)
    _reply(db, _state(db)["pending"], **reply)

    assert _tick(db).startswith(expect_return + "; sent ")
    s = _state(db)
    assert s["failures"] == 1
    assert expect_error in s["error"], s["error"]
    assert s["last_failure_at"] is not None
    assert s["last_success_at"] is None, "a failure must not look like an answer"


def test_a_failure_does_not_move_the_timestamp_that_says_the_monitor_is_alive(db):
    """The same invariant the lease sweep has, and for the same reason: the heartbeat is
    only worth reading if nothing but a real answer can refresh it."""
    _arm(db)
    _tick(db)
    _reply(db, _state(db)["pending"], status_code=200, content=OK_BODY)
    _tick(db)
    alive = _state(db)["last_success_at"]
    assert alive is not None

    for code in (500, 502):
        _reply(db, _state(db)["pending"], status_code=code, content="x")
        _tick(db)

    s = _state(db)
    assert s["last_success_at"] == alive, "a failing probe refreshed the heartbeat"
    assert s["failures"] == 2


def test_failures_accumulate_and_one_answer_clears_them(db):
    """How the retry story reads in the record: the count is what escalates a warning into
    an alert in ops_health.py, so it has to climb while it fails and reset when it works."""
    _arm(db)
    seen = []
    for _ in range(4):
        _tick(db)
        _reply(db, _state(db)["pending"], status_code=502, content="bad gateway")
        seen.append(_state(db)["failures"])
    _tick(db)
    assert seen == [0, 1, 2, 3], seen          # the first tick had nothing to collect yet

    _reply(db, _state(db)["pending"], status_code=200, content=OK_BODY)
    _tick(db)
    s = _state(db)
    assert s["failures"] == 0 and s["error"] is None
    assert s["last_success_at"] is not None
    assert s["last_failure_at"] is not None, "the history of the failure is not erased"


# ── a reply that never comes ───────────────────────────────────────────────────

def test_a_reply_that_has_not_arrived_yet_is_waited_for_rather_than_failed(db):
    """Run twice in quick succession by hand, the reply is simply not back yet. Calling that
    a failure would make a manual check look like an outage."""
    _arm(db)
    _tick(db)
    rid = _state(db)["pending"]
    assert _tick(db) == "waiting for the previous reply"
    s = _state(db)
    assert s["pending"] == rid, "the pending request was replaced and orphaned"
    assert s["failures"] == 0 and s["runs"] == 1


def test_a_reply_that_never_arrived_is_a_failure_once_it_is_old_enough(db):
    """pg_net's worker not running at all, which is otherwise indistinguishable from the
    monitor never having been asked to do anything."""
    _arm(db)
    _tick(db)
    db.execute("update ops_monitor_state set pending_since = now() - interval '11 minutes'")

    assert _tick(db) == "failed: no reply recorded"
    s = _state(db)
    assert s["failures"] == 1
    assert "pg_net worker may not be running" in s["error"]
    # Deliberately no new request. If the worker is not consuming the queue, piling more
    # requests onto it grows an unlogged table with nobody reading it. The pending slot is
    # cleared instead, so the next tick starts a fresh probe: the cost of a lost reply is
    # one skipped tick, well inside the 35 minute staleness bound.
    assert s["pending"] is None
    assert s["runs"] == 1, "no second request was queued"
    assert _tick(db).startswith("nothing pending; sent "), "the tick after probes again"


# ── the two preconditions, each naming its own remedy ──────────────────────────

def test_without_pg_net_the_tick_records_what_is_missing_instead_of_raising(db):
    """It must record rather than raise. pg_cron runs each tick in its own transaction, so
    an exception would roll back the one row that is supposed to say what went wrong."""
    _arm(db, net=False)
    assert _tick(db) == "failed: pg_net is missing"
    s = _state(db)
    assert s["failures"] == 1 and "pg_net is not installed" in s["error"]
    assert "migration 013" in s["error"], "the note should name the remedy"
    assert s["runs"] == 0 and s["last_success_at"] is None


def test_without_the_vault_secret_the_tick_records_what_is_missing(db):
    _arm(db, secret=None)
    assert _tick(db) == "failed: no secret"
    s = _state(db)
    assert s["failures"] == 1 and "Vault secret" in s["error"]
    assert "vault.create_secret" in s["error"]
    assert db.execute("select count(*) from net.http_request_queue").fetchone()[0] == 0, \
        "a request was sent without a key"


def test_an_empty_vault_secret_is_treated_as_a_missing_one(db):
    """A secret created with an empty value would otherwise send an empty header and get a
    403, which sends an operator looking at `operators` rather than at Vault."""
    _arm(db, secret="")
    assert _tick(db) == "failed: no secret"
    assert db.execute("select count(*) from net.http_request_queue").fetchone()[0] == 0


def test_pg_net_disappearing_between_two_ticks_does_not_wedge_the_record(db):
    """The reason the pg_net check is before the collect and not only before the send: the
    read of net._http_response would raise undefined_table, the transaction would roll back,
    and the record would say nothing on every tick from then on."""
    _arm(db)
    _tick(db)
    assert _state(db)["pending"] is not None
    db.execute("drop schema net cascade")

    assert _tick(db) == "failed: pg_net is missing"
    s = _state(db)
    assert s["pending"] is None, "the orphaned request should be let go of"
    assert s["failures"] == 1 and "pg_net is not installed" in s["error"]


# ── two ticks at the same moment ───────────────────────────────────────────────

def test_two_ticks_at_once_do_not_both_run(dsn):
    """Why the advisory lock is taken before the row is read, and not after.

    Two ticks overlapping would both collect the same reply and then both overwrite
    `pending_request_id`, orphaning one request and recording its absence as a failure on
    the tick after. Taken before the `for update`, the second tick is told so and returns;
    taken after, it would have blocked on the row instead and run the moment the first
    finished, which is the thing being prevented.
    """
    with psycopg.connect(dsn, autocommit=True) as setup:
        _arm(setup)

    with psycopg.connect(dsn) as first, psycopg.connect(dsn, autocommit=True) as second:
        # Deliberately not autocommit: the lock is transaction-scoped, so holding the
        # transaction open is what makes two ticks concurrent.
        assert first.execute("select ops_monitor_tick()").fetchone()[0].startswith("nothing pending")

        # A short lock timeout so that losing the advisory lock shows up as a failure here
        # rather than as a hung test. Without it, the second tick reaches the `for update`
        # and waits on the row until the first commits, which is a pytest run that never
        # finishes rather than one that says what broke.
        second.execute("set lock_timeout = '2s'")
        assert second.execute("select ops_monitor_tick()").fetchone()[0] == \
            "skipped: another tick is running"
        first.commit()

    with psycopg.connect(dsn, autocommit=True) as after:
        assert _state(after)["runs"] == 1, "the second tick ran as well"
        assert after.execute(
            "select count(*) from net.http_request_queue").fetchone()[0] == 1


def test_a_tick_that_fails_half_way_does_not_wedge_every_later_one(dsn):
    """A transaction-scoped lock rather than a session-scoped one. The point of the choice
    is what happens after something goes wrong, so this makes something go wrong."""
    with psycopg.connect(dsn, autocommit=True) as db:
        _arm(db)
        # Make the send itself fail, inside the function, after the lock is taken.
        broken = ("create or replace function net.http_get(url text,"
                  " params jsonb default '{}'::jsonb, headers jsonb default '{}'::jsonb,"
                  " timeout_milliseconds int default 5000) returns bigint language plpgsql"
                  " as $fn$ begin raise exception 'curl exploded'; end $fn$")
        working = ("create or replace function net.http_get(url text,"
                   " params jsonb default '{}'::jsonb, headers jsonb default '{}'::jsonb,"
                   " timeout_milliseconds int default 5000) returns bigint language sql"
                   " as $fn$ insert into net.http_request_queue (method, url, headers,"
                   " timeout_milliseconds) values ('GET', url, headers,"
                   " timeout_milliseconds) returning id $fn$")
        db.execute(broken)
        with pytest.raises(psycopg.errors.RaiseException):
            db.execute("select ops_monitor_tick()")

        # The lock went with the failed transaction, so the next tick is not refused. A
        # session-scoped lock would have held it until this connection closed, and every
        # tick after would have reported "another tick is running" with none running.
        db.execute(working)
        assert db.execute("select ops_monitor_tick()").fetchone()[0].startswith("nothing pending")


# ── who may call it, and who may read the record ──────────────────────────────
#
# Written the same way as the lease sweep's tests, and for the same reason: Supabase hands
# `anon` and `authenticated` broad privileges on new objects in `public`, which this
# disposable Postgres does not, so a test that only checked "anon cannot read" would pass on
# a database where the revokes had never run. Each test grants what the platform would first.

def test_reapplying_012_strips_the_blanket_grants_the_platform_hands_out(db):
    db.execute("grant select, insert, update, delete on ops_monitor_state to anon, authenticated")
    db.execute("grant execute on function ops_monitor_tick() to anon, authenticated, service_role")
    assert db.execute("select has_table_privilege('anon', 'ops_monitor_state', 'select')").fetchone()[0]

    db.execute(M012.read_text())

    for role in ("anon", "authenticated"):
        for priv in ("select", "insert", "update", "delete"):
            assert not db.execute("select has_table_privilege(%s, 'ops_monitor_state', %s)",
                                  (role, priv)).fetchone()[0], f"{role} kept {priv}"
        assert not db.execute(
            "select has_function_privilege(%s, 'ops_monitor_tick()', 'execute')",
            (role,)).fetchone()[0], f"{role} kept execute on the tick"

    # The monitor's record is read by ops-health under the service key, and that is all the
    # service key gets. It must not be able to run the tick: the tick reads a decrypted Vault
    # secret and sends it over the network, and it would also refresh the heartbeat, which is
    # the one thing that must only ever be refreshed by a real answer.
    assert db.execute(
        "select has_table_privilege('service_role', 'ops_monitor_state', 'select')").fetchone()[0]
    for priv in ("insert", "update", "delete"):
        assert not db.execute("select has_table_privilege('service_role', 'ops_monitor_state', %s)",
                              (priv,)).fetchone()[0], f"the service key can {priv} the heartbeat"
    assert not db.execute(
        "select has_function_privilege('service_role', 'ops_monitor_tick()', 'execute')"
    ).fetchone()[0], "the service key should not be able to probe or refresh the record"


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_the_record_is_readable_by_nobody_who_is_not_the_service_key(db, role):
    _arm(db)
    _tick(db)
    db.execute(f"set role {role}")
    if role == "service_role":
        # bypassrls in the stubs, as on Supabase: the service key is the one reader.
        assert db.execute("select count(*) from ops_monitor_state").fetchone()[0] == 1
    else:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("select * from ops_monitor_state")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_granting_execute_still_does_not_buy_a_probe(db, role):
    """Defence in depth, the same shape as the sweep's. Even handed EXECUTE, these roles
    cannot probe: the function runs as the invoker and they cannot read the row, let alone
    the Vault secret."""
    _arm(db)
    db.execute(f"grant execute on function ops_monitor_tick() to {role}")
    db.execute(f"set role {role}")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("select ops_monitor_tick()")


# ── 013 refuses rather than half-applying ─────────────────────────────────────

def test_013_refuses_without_pg_cron_and_says_what_to_do(db):
    """This disposable server has no pg_cron, which is exactly the state 013 is written for.
    The promise in its header is that nothing has been applied, so the flag stays off."""
    _arm(db, enabled=False)
    with pytest.raises(psycopg.errors.FeatureNotSupported) as e:
        db.execute(M013.read_text())
    assert "pg_cron is not enabled" in str(e.value)
    assert "Nothing in 013 has been applied" in str(e.value)
    # The file opens its own transaction, so the refusal leaves this connection in an
    # aborted one. Ending it is what proves the refusal rolled the whole file back.
    db.execute("rollback")
    assert _state(db)["enabled"] is False, "013 switched the monitor on before refusing"


def test_013_names_each_missing_piece_separately(db):
    """Four preconditions, four remedies. "It did not work" is not a useful thing to read at
    the end of a release, and each of these has a different next step."""
    # pg_cron is absent on this server throughout, so it is always the first to fire; the
    # others are asserted by reading the file, which is the only way to see them here.
    text = M013.read_text()
    for needed in ("pg_cron is not enabled", "pg_net is not enabled",
                   "ops_monitor_tick() is missing", "the operator key is not in Vault yet",
                   "a schema holding this monitor''s key is exposed through the Data API"):
        assert needed in text, needed
    # And each refusal says the same thing about state, which is what makes re-running safe.
    assert text.count("Nothing in 013 has been applied") == 5


def test_013_never_contains_a_key_and_only_names_the_secret(db):
    """The rule for every file in this repository, asserted where it would hurt most."""
    import re
    for path in (M012, M013):
        for found in re.findall(r"op_[A-Za-z0-9_.-]*", path.read_text()):
            assert found == "op_...", f"{path.name} appears to contain a key: {found}"
    assert "ops_monitor_admin_key" in M013.read_text(), "the secret is referred to by name"


# ── switching off while a request is in flight ─────────────────────────────────

def test_disabling_the_monitor_releases_the_request_in_flight(db):
    """What the disabled path has to do besides stand down.

    Returning with the request still held left two things behind: a `pending_request_id`
    nothing would ever collect, and its reply in `net._http_response` until pg_net's own six
    hour prune rather than until the tick that asked for it.
    """
    _arm(db)
    _tick(db)
    rid = _state(db)["pending"]
    _reply(db, rid, status_code=200, content=OK_BODY)

    db.execute("update ops_monitor_state set enabled = false")
    assert _tick(db) == "skipped: disabled, released the request in flight"

    s = _state(db)
    assert s["pending"] is None and s["pending_since"] is None
    assert db.execute("select count(*) from net._http_response where id = %s",
                      (rid,)).fetchone()[0] == 0, "the uncollected reply was left behind"
    # Standing down is not a probe result: nothing failed and nothing succeeded.
    assert s["failures"] == 0 and s["last_success_at"] is None and s["error"] is None

    # And a second tick while still disabled has nothing left to release.
    assert _tick(db) == "skipped: disabled"


def test_re_enabling_later_does_not_blame_pg_net_for_an_abandoned_request(db):
    """The misdiagnosis this fixes.

    Before the release above, switching back on days later collected a request whose reply
    pg_net had long since pruned, and recorded "the pg_net worker may not be running": a
    true-sounding verdict on a system that was working perfectly.
    """
    _arm(db)
    _tick(db)
    _reply(db, _state(db)["pending"], status_code=200, content=OK_BODY)
    db.execute("update ops_monitor_state set enabled = false")
    _tick(db)                                        # releases
    db.execute("delete from net._http_response")     # pg_net's prune, days later
    db.execute("update ops_monitor_state set enabled = true")

    assert _tick(db).startswith("nothing pending; sent "), \
        "re-enabling should start a fresh probe, not collect a request it let go of"
    s = _state(db)
    assert s["failures"] == 0
    assert s["error"] is None, f"a spurious failure was recorded: {s['error']}"


def test_being_disabled_without_pg_net_installed_is_not_a_failure(db):
    """The release has to be guarded: reading net._http_response when the extension is gone
    would raise, and a disabled monitor must not record a failure about anything."""
    _arm(db, net=False)
    db.execute("update ops_monitor_state set pending_request_id = 42,"
               " pending_since = now(), enabled = false")
    assert _tick(db) == "skipped: disabled, released the request in flight"
    s = _state(db)
    assert s["pending"] is None and s["failures"] == 0 and s["error"] is None


# ── a failure leaves no stale verdict beside it ────────────────────────────────

@pytest.mark.parametrize("reply", [
    dict(status_code=502, content="bad gateway"),
    dict(status_code=403, content="forbidden"),
    dict(timed_out=True),
    dict(error_msg="Could not resolve host: api.test"),
    dict(status_code=200, content="<html>hello</html>"),
])
def test_a_failure_clears_the_verdict_the_last_answer_left(db, reply):
    """`last_reported_status` and `last_failing_checks` describe an answer. Left in place
    beside a current failure they read as though the platform were still reporting them,
    which is the opposite of what the row is for."""
    _arm(db)
    _tick(db)
    _reply(db, _state(db)["pending"], status_code=503, content=ALERT_BODY)
    _tick(db)
    assert _state(db)["failing"] == ["deliveries_failing", "scoring_events_failed"]

    _reply(db, _state(db)["pending"], **reply)
    _tick(db)
    s = _state(db)
    assert s["failures"] == 1
    assert s["failing"] is None, "a stale failing-checks list survived a failure"
    assert s["reported"] is None, "a stale reported status survived a failure"


# ── the exposed-schema guard, against real pg_db_role_setting rows ─────────────
#
# 013 cannot be run end to end here: it refuses on pg_cron, which this server does not have
# and which cannot be faked, because `pg_extension` is a catalog. So these tests lift the
# guard's own SQL out of the migration file and run that, which is the next best thing to
# running the file: the query under test is the one that ships, not a copy of it.

def _guard_sql():
    """The guard's two reads, lifted out of 013 as written.

    `exposed` is what `pgrst.db_schemas` says, across every row that carries it, and
    `offending` is which of the schemas holding this monitor's key appear in it. They are
    kept apart in the migration because "could not establish" and "says net" are different
    answers with different remedies, and the first must never read as a pass.
    """
    text = M013.read_text()
    exposed = re.search(r"(select string_agg\(split_part.*?)\s*;", text, re.S).group(1)
    offending = re.search(r"(select array_agg\(distinct btrim\(sch\).*?)\s*;", text, re.S).group(1)
    return exposed.replace("into exposed", ""), offending.replace("into offending", "")


def _ask_the_guard(db):
    """What 013 would conclude: the setting it read, which schemas offend, and whether it
    refuses. Composed from the migration's own SQL so the query under test is the one that
    ships rather than a copy that can drift away from it."""
    exposed, offending = _guard_sql()
    return db.execute(
        f"with v(exposed) as ({exposed}), o(offending) as (select ({offending}) from v)"
        f" select v.exposed, o.offending, o.offending is not null as refuses"
        f"   from v cross join o").fetchone()


def _authenticator(db, *, cluster_wide=None, in_this_database=None):
    db.execute("do $$ begin create role authenticator nologin;"
               " exception when duplicate_object then null; end $$")
    db.execute("alter role authenticator reset all")
    if cluster_wide:
        for setting in cluster_wide:
            db.execute(f"alter role authenticator set {setting}")
    if in_this_database:
        for setting in in_this_database:
            db.execute(f"alter role authenticator in database {db.info.dbname}"
                       f" set {setting}")


def test_the_guard_reads_the_setting_from_whichever_row_holds_it(db):
    """The bug this guard was rewritten for, reproduced.

    Two rows: one cluster-wide, one for this database, which is where Supabase puts
    `pgrst.db_schemas`. The first version read one arbitrary row with `limit 1` and no
    `order by`, and against this exact shape it read the cluster-wide one, found no `net`,
    and installed while `net` was exposed the whole time.
    """
    _authenticator(db,
                   cluster_wide=["statement_timeout = '8s'"],
                   in_this_database=['"pgrst.db_schemas" = \'public,net\''])
    rows = db.execute("select count(*) from pg_db_role_setting s join pg_roles r"
                      " on r.oid = s.setrole where r.rolname = 'authenticator'").fetchone()[0]
    assert rows == 2, "the two-row shape this test is about was not created"

    exposed, offending, refuses = _ask_the_guard(db)
    assert exposed == "public,net"
    assert offending == ["net"]
    assert refuses is True, "the guard passed while net was exposed"

    # And what the old query did with the same database, so the regression is on the record.
    missed = db.execute("select setconfig::text from pg_db_role_setting s"
                        " join pg_roles r on r.oid = s.setrole"
                        " where r.rolname = 'authenticator' limit 1").fetchone()[0]
    assert "pgrst.db_schemas" not in missed, \
        "this database no longer reproduces the wrong-row case, so the test proves nothing"


@pytest.mark.parametrize("label, settings, exposed, offending, refuses", [
    # ── neither exposed ───────────────────────────────────────────────────────
    ("the ordinary safe setting", ['"pgrst.db_schemas" = \'public, graphql_public\''],
     "public, graphql_public", None, False),
    # ── net exposed ───────────────────────────────────────────────────────────
    ("net exposed", ['"pgrst.db_schemas" = \'public,net\''], "public,net", ["net"], True),
    ("net exposed with spaces", ['"pgrst.db_schemas" = \'public, net, graphql_public\''],
     "public, net, graphql_public", ["net"], True),
    ("net alone", ['"pgrst.db_schemas" = \'net\''], "net", ["net"], True),
    # ── vault exposed: service_role already holds select and delete on
    #    vault.decrypted_secrets, so exposing it hands the service key every secret ──
    ("vault exposed", ['"pgrst.db_schemas" = \'public,vault\''],
     "public,vault", ["vault"], True),
    ("vault exposed with spaces", ['"pgrst.db_schemas" = \'public, vault\''],
     "public, vault", ["vault"], True),
    ("vault alone", ['"pgrst.db_schemas" = \'vault\''], "vault", ["vault"], True),
    # ── both exposed: named together, so one refusal is not fixed twice ───────
    ("both exposed", ['"pgrst.db_schemas" = \'public,net,vault\''],
     "public,net,vault", ["net", "vault"], True),
    ("both exposed, vault first", ['"pgrst.db_schemas" = \'vault, net\''],
     "vault, net", ["net", "vault"], True),
    ("both exposed and duplicated", ['"pgrst.db_schemas" = \'net,public,net,vault\''],
     "net,public,net,vault", ["net", "vault"], True),
    # ── lookalikes: exact tokens, so neither of these is a hit. The old regex
    #    matched any `net` token anywhere in the whole setting string. ─────────
    ("a lookalike schema name", ['"pgrst.db_schemas" = \'public,network_logs\''],
     "public,network_logs", None, False),
    ("a lookalike vault name", ['"pgrst.db_schemas" = \'public,vaults,my_vault\''],
     "public,vaults,my_vault", None, False),
    ("an unrelated setting whose value is net",
     ['"pgrst.db_schemas" = \'public\'', '"pgrst.db_extra_search_path" = \'net\''],
     "public", None, False),
])
def test_the_guard_decides_on_exact_schema_tokens(db, label, settings, exposed, offending,
                                                  refuses):
    _authenticator(db, cluster_wide=settings)
    assert _ask_the_guard(db) == (exposed, offending, refuses), label


def test_an_unestablished_setting_is_not_a_pass(db):
    """PostgREST can be configured from its environment rather than from a role setting, and
    then there is nothing here to read. 013 must say so rather than carry on quietly: the
    old version treated finding *a* row as having established the answer, which skipped the
    manual-confirmation notice at the same moment it failed open."""
    _authenticator(db, cluster_wide=["statement_timeout = '8s'"])
    exposed, offending, refuses = _ask_the_guard(db)
    assert exposed is None, "the guard claimed to have read a setting that is not there"
    assert offending is None and refuses is False

    text = M013.read_text()
    assert "if exposed is null then" in text
    assert "raise notice" in text.split("if exposed is null then")[1].split("elsif")[0], \
        "a guard that could not establish the answer must say so, not pass silently"
    assert "Could not establish" in text


def test_the_guard_never_runs_after_installation(db):
    """Stated here because it is a limitation, not a bug, and the kind that gets forgotten:
    exposing `net` tomorrow is a dashboard setting no migration and no scheduled check in
    this repository will notice."""
    assert "That check runs once" in M013.read_text()
    assert "install-time" in (HEALTH / "docs" / "operations.md").read_text().lower()


@pytest.mark.parametrize("label, cluster_wide, per_database, offending", [
    # The setting in the per-database row, which is where Supabase puts it, with an
    # unrelated cluster-wide row alongside. This is the shape the `limit 1` version read
    # the wrong half of.
    ("per-database holds it, cluster-wide row first",
     ["statement_timeout = '8s'"], ['"pgrst.db_schemas" = \'public,vault\''], ["vault"]),
    # The same two rows with the roles reversed, so the answer cannot depend on which row
    # the planner happens to return.
    ("cluster-wide holds it, per-database row unrelated",
     ['"pgrst.db_schemas" = \'public,net\''], ["statement_timeout = '8s'"], ["net"]),
    # Both rows carry a db_schemas setting. Aggregating reads both, so a schema exposed in
    # either one is found rather than whichever row came back first.
    ("both rows carry the setting, net in one and vault in the other",
     ['"pgrst.db_schemas" = \'public,net\''], ['"pgrst.db_schemas" = \'public,vault\''],
     ["net", "vault"]),
    # Several unrelated settings in the row that also carries db_schemas.
    ("the setting buried among others",
     ["statement_timeout = '8s'", "idle_in_transaction_session_timeout = '30s'"],
     ['"pgrst.db_schemas" = \'public, net\'', "work_mem = '4MB'"], ["net"]),
    ("neither row exposes anything that matters",
     ["statement_timeout = '8s'"], ['"pgrst.db_schemas" = \'public, graphql_public\''], None),
])
def test_the_guard_reads_every_role_setting_row_whatever_the_order(
        db, label, cluster_wide, per_database, offending):
    """The BLOCKER this guard was rewritten for, in every row arrangement.

    A role routinely has two rows in `pg_db_role_setting`: one cluster-wide
    (`setdatabase = 0`) and one per database, and `limit 1` without an `order by` returns an
    arbitrary one. The first version of this guard read whichever it got, and where that was
    not the row carrying `pgrst.db_schemas` it found nothing, installed, and skipped the
    manual-confirmation notice as well, because having read *a* row made `exposed` non-null.
    """
    _authenticator(db, cluster_wide=cluster_wide, in_this_database=per_database)
    rows = db.execute("select count(*) from pg_db_role_setting s join pg_roles r"
                      " on r.oid = s.setrole where r.rolname = 'authenticator'").fetchone()[0]
    assert rows == 2, f"{label}: the two-row shape under test was not created"

    _, got, refuses = _ask_the_guard(db)
    assert got == offending, label
    assert refuses is (offending is not None), label


def test_both_schemas_are_named_together_in_one_refusal(db):
    """Exposing both is one dashboard change, and a refusal naming only the first would be
    fixed, re-run, and refuse again."""
    _authenticator(db, cluster_wide=['"pgrst.db_schemas" = \'public,net,vault\''])
    _, offending, refuses = _ask_the_guard(db)
    assert refuses is True and offending == ["net", "vault"]

    text = M013.read_text()
    assert "array_to_string(offending, ', ')" in text, \
        "the refusal should name what it found, not just that it found something"
    for reason in ("net.http_request_queue", "vault.decrypted_secrets",
                   "postgres cannot revoke"):
        assert reason in text, f"the hint should explain {reason}"


def test_the_guard_covers_vault_because_service_role_can_already_read_it(db):
    """Why `vault` is in this check at all, recorded where it will be read.

    Checked read-only against production on 2026-11-01:
    `has_table_privilege('service_role','vault.decrypted_secrets','select')` is **true**, and
    `anon` is false. Supabase grants that directly from `supabase_admin` when it creates the
    extension, SELECT and DELETE, and `postgres` cannot revoke a grant it did not make. It
    is harmless only because `service_role` is NOLOGIN and `vault` is not exposed through the
    Data API. This check is what keeps the second half of that true.
    """
    text = M013.read_text()
    assert "'net', 'vault'" in text, "the guard must test both schemas"
    assert "SELECT *and DELETE*" in text or "select *and DELETE*" in text.lower(), \
        "the comment should record that the grant includes DELETE, not only SELECT"
    assert "NOLOGIN" in text, "the comment should say why this is not already a breach"
