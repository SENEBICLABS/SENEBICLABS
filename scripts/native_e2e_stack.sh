#!/usr/bin/env bash
# A throwaway stack for rehearsing the platform end to end:
# Postgres with both repos' schemas, PostgREST behind /rest/v1 (as Supabase serves it),
# the HEALTH API on :8765 and the workforce app on :3765.
#
#   scripts/native_e2e_stack.sh up                       start it
#   scripts/native_e2e_stack.sh stop-health | start-health   crash and restart the API
#   scripts/native_e2e_stack.sh down                     stop everything, delete all data
#
# Nothing here reads production settings: every value a process needs is set below and
# overrides the local .env files. Needs docker, psql, the HEALTH venv, and Node 22+ via nvm.
set -euo pipefail

HEALTH="$(cd "$(dirname "$0")/.." && pwd)"
WORKFORCE="${WORKFORCE_REPO:-$HEALTH/../workforce/senebiclabs-workforce-platform}"
STATE="${NATIVE_E2E_STATE:-/tmp/senebi-native-e2e}"
PGPORT=55432
JWT_PGRST="e2e-jwt-secret-for-a-throwaway-postgrest-only-0123456789"
JWT_APP="e2e-jwt-secret-long-enough-for-the-min-0123456789"
ADMIN="e2e-admin-key"
NODE_BIN="$(ls -d "$HOME"/.nvm/versions/node/v2[2-9]*/bin 2>/dev/null | tail -1)"

# Each server runs detached (setsid -f) as the leader of its own session and process
# group, and writes its own pid, which is therefore also that group's id. Nothing is left
# holding this script's output, so `up` can be piped, and stopping kills the whole tree
# (npx, next, next-server; uvicorn) by group and touches nothing else.
stop_group() { [ -f "$1" ] && kill -- "-$(cat "$1")" 2>/dev/null || true; rm -f "$1"; }

start_health() {
  # shellcheck disable=SC1091
  . "$STATE/health.env"
  setsid -f bash -c 'cd "$1" && echo $$ > "$2" && shift 2 && exec "$@"' _ "$HEALTH" "$STATE/health.pid" \
    env SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_SERVICE_KEY="$KEY" ADMIN_API_KEY="$ADMIN" \
    SELF_URL=http://127.0.0.1:8765 RESEND_API_KEY= ANTHROPIC_API_KEY= \
    ANALYSE_ACCESS_TOKEN= PORTAL_SECRET=e2e-portal-secret-0123456789 \
    ./.venv/bin/uvicorn app.main:app --port 8765 >> "$STATE/health.log" 2>&1 < /dev/null
  until curl -sf http://127.0.0.1:8765/api/v1/health >/dev/null; do sleep 1; done
}

down() {
  stop_group "$STATE/health.pid"
  stop_group "$STATE/workforce.pid"
  docker rm -f senebi-e2e-proxy senebi-e2e-postgrest senebi-sqltest >/dev/null 2>&1 || true
  rm -rf "$STATE"
  echo "stopped"
}

up() {
  [ -n "$NODE_BIN" ] || { echo "Node 22+ not found under ~/.nvm" >&2; exit 1; }
  mkdir -p "$STATE"
  docker run -d --rm --name senebi-sqltest -e POSTGRES_PASSWORD=test \
    -p 127.0.0.1:$PGPORT:5432 --tmpfs /var/lib/postgresql/data postgres:15 >/dev/null
  until PGPASSWORD=test psql -h 127.0.0.1 -p $PGPORT -U postgres -c 'select 1' >/dev/null 2>&1; do sleep 1; done

  local P="psql -h 127.0.0.1 -p $PGPORT -U postgres -v ON_ERROR_STOP=1 -q"
  export PGPASSWORD=test
  $P -d postgres -c "create database senebi_e2e"
  $P -d senebi_e2e <<'SQL'
create schema if not exists auth;
create or replace function auth.uid() returns uuid language sql as 'select null::uuid';
create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
create role authenticator login noinherit password 'e2e';
grant anon, authenticated, service_role to authenticator;
SQL
  # Both repos' migrations, in release order: workforce's (including 021) before HEALTH's
  # from 005 on, exactly as the runbook applied them.
  #
  # 011 is left out on purpose: it schedules the lease sweep through pg_cron, which this
  # throwaway Postgres does not have, and it is written to refuse rather than half-apply.
  # Everything the application reads lives in 010, so the stack is still representative.
  local files=("$HEALTH"/supabase_schema.sql "$HEALTH"/migrations/00[2-4]_*.sql "$WORKFORCE"/database.sql)
  files+=("$WORKFORCE"/migrations/*.sql "$HEALTH"/migrations/00[5-9]_*.sql
          "$HEALTH"/migrations/010_*.sql)
  for f in "${files[@]}"; do
    $P -d senebi_e2e -f "$f" 2>&1 | grep -v NOTICE || true
  done
  $P -d senebi_e2e -c "grant usage on schema public to anon, authenticated, service_role;
    grant all on all tables in schema public to service_role;
    grant all on all sequences in schema public to service_role;
    grant execute on all functions in schema public to service_role;"

  # One sweep, standing in for the schedule 011 installs, because this Postgres has no
  # pg_cron. Without it `reservations_sweep_stalled` alerts for the whole run, and the test
  # that proves an overlong lease answers 503 would pass on this instead of on the lease.
  # Twenty minutes is far longer than a run, so one sweep at setup is enough.
  $P -d senebi_e2e -c "select sweep_reservations()" >/dev/null

  local KEY
  KEY="$(python3 - "$JWT_PGRST" <<'PY'
import sys, hmac, hashlib, base64, json
b = lambda d: base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=")
h, p = b({"alg": "HS256", "typ": "JWT"}), b({"role": "service_role", "iss": "e2e", "exp": 4102444800})
s = base64.urlsafe_b64encode(hmac.new(sys.argv[1].encode(), h + b"." + p, hashlib.sha256).digest()).rstrip(b"=")
print((h + b"." + p + b"." + s).decode())
PY
)"
  printf '{\n\tadmin off\n\tauto_https off\n}\n:54321 {\n\thandle_path /rest/v1/* {\n\t\treverse_proxy 127.0.0.1:54322\n\t}\n\trespond 404\n}\n' > "$STATE/Caddyfile"
  docker run -d --rm --name senebi-e2e-postgrest --network host \
    -e PGRST_DB_URI="postgres://authenticator:e2e@127.0.0.1:$PGPORT/senebi_e2e" -e PGRST_DB_SCHEMAS=public \
    -e PGRST_DB_ANON_ROLE=anon -e PGRST_JWT_SECRET="$JWT_PGRST" -e PGRST_SERVER_PORT=54322 \
    postgrest/postgrest:v12.2.3 >/dev/null
  docker run -d --rm --name senebi-e2e-proxy --network host \
    -v "$STATE/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.8.4 >/dev/null

  # Throwaway values only, so start-health can bring the API back with the same settings.
  cat > "$STATE/health.env" <<EOF
KEY='$KEY'
EOF
  start_health

  setsid -f bash -c 'cd "$1" && echo $$ > "$2" && shift 2 && exec "$@"' _ "$WORKFORCE" "$STATE/workforce.pid" \
    env PATH="$NODE_BIN:$PATH" NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321 \
    SUPABASE_SERVICE_ROLE_KEY="$KEY" \
    MAGIC_LINK_SECRET=e2e-magic-link-secret-long-enough-0123456789 JWT_SECRET="$JWT_APP" \
    SENDGRID_API_KEY= RESEND_API_KEY= GOOGLE_CLIENT_ID= GOOGLE_CLIENT_SECRET= VERCEL_OIDC_TOKEN= \
    NEXT_PUBLIC_APP_URL=http://127.0.0.1:3765 OPS_API_KEY=e2e-ops-key \
    NEXT_PUBLIC_HEARTBEAT_INTERVAL_MS="${HEARTBEAT_INTERVAL_MS:-}" \
    HEALTH_API_BASE=http://127.0.0.1:8765/api/v1/project HEALTH_ADMIN_KEY="$ADMIN" HEALTH_KEY_HEADER=X-Admin-Key \
    npx next dev -p 3765 > "$STATE/workforce.log" 2>&1 < /dev/null

  until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3765/api/pools)" = "401" ]; do sleep 1; done
  cat <<EOF
stack up (logs in $STATE). Run the rehearsal:

  NATIVE_E2E_API=http://127.0.0.1:8765 NATIVE_E2E_APP=http://127.0.0.1:3765 \\
  NATIVE_E2E_DSN=postgresql://postgres:test@127.0.0.1:$PGPORT/senebi_e2e \\
  NATIVE_E2E_ADMIN_KEY=$ADMIN NATIVE_E2E_JWT_SECRET=$JWT_APP \\
  NATIVE_E2E_STACK=$0 \\
  PYTHONPATH=. .venv/bin/pytest tests/e2e/test_native_cutover.py -p no:playwright

The SQL suite can use the same Postgres:
  SQLTEST_DSN=postgresql://postgres:test@127.0.0.1:$PGPORT/postgres PYTHONPATH=. .venv/bin/pytest tests/sql
EOF
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  stop-health) stop_group "$STATE/health.pid"; echo "health stopped" ;;
  start-health) start_health; echo "health started" ;;
  *) echo "usage: $0 up | down | stop-health | start-health" >&2; exit 2 ;;
esac
