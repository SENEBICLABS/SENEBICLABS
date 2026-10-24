#!/usr/bin/env bash
# A throwaway stack for rehearsing the platform end to end:
# Postgres with both repos' schemas, PostgREST behind /rest/v1 (as Supabase serves it),
# the HEALTH API on :8765 and the workforce app on :3765.
#
#   scripts/native_e2e_stack.sh up                       Label Studio unreachable (a closed
#                                                         port): proves the native store
#                                                         depends on nothing of it
#   scripts/native_e2e_stack.sh up --with-label-studio   adds a throwaway Label Studio on
#                                                         :8091, for the transition tests
#   scripts/native_e2e_stack.sh up --with-label-studio --schema before
#                                                         the schema as production has it
#                                                         BEFORE this release (no 021, no
#                                                         HEALTH 005+): new code must still
#                                                         serve Label Studio projects
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
LS_PORT=8091
LS_TOKEN_E2E="e2e0000000000000000000000000000000000tok"
LS_SECRET_E2E="e2e-ls-webhook-secret"
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
    SELF_URL=http://127.0.0.1:8765 LS_URL="$E2E_LS_URL" LS_TOKEN="$E2E_LS_TOKEN" \
    LS_WEBHOOK_SECRET="$E2E_LS_SECRET" LS_CALLBACK_URL="$E2E_LS_CALLBACK" RESEND_API_KEY= ANTHROPIC_API_KEY= \
    ANALYSE_ACCESS_TOKEN= PORTAL_SECRET=e2e-portal-secret-0123456789 \
    ./.venv/bin/uvicorn app.main:app --port 8765 >> "$STATE/health.log" 2>&1 < /dev/null
  until curl -sf http://127.0.0.1:8765/api/v1/health >/dev/null; do sleep 1; done
}

down() {
  stop_group "$STATE/health.pid"
  stop_group "$STATE/workforce.pid"
  docker rm -f senebi-e2e-proxy senebi-e2e-postgrest senebi-sqltest senebi-e2e-ls >/dev/null 2>&1 || true
  rm -rf "$STATE"
  echo "stopped"
}

start_label_studio() {
  docker run -d --rm --name senebi-e2e-ls --network host -e LABEL_STUDIO_PORT=$LS_PORT \
    -e LABEL_STUDIO_USERNAME=e2e@senebi.test -e LABEL_STUDIO_PASSWORD=e2e-password-123 \
    -e LABEL_STUDIO_USER_TOKEN="$LS_TOKEN_E2E" -e LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true \
    -e SSRF_PROTECTION_ENABLED=false --tmpfs /label-studio/data:uid=1001 \
    heartexlabs/label-studio:latest label-studio start --port $LS_PORT >/dev/null
  until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$LS_PORT/api/version)" = "200" ]; do sleep 2; done
  # Label Studio 1.2x ships with legacy tokens off (docker/labelstudio-vm/SETUP.md says to
  # turn them on); this instance is throwaway, so do it directly.
  docker exec senebi-e2e-ls bash -c 'cd /label-studio/label_studio && DJANGO_SETTINGS_MODULE=core.settings.label_studio \
    /label-studio/.venv/bin/python manage.py shell -c "
from organizations.models import Organization
from jwt_auth.models import JWTSettings
for o in Organization.objects.all():
    s, _ = JWTSettings.objects.get_or_create(organization=o)
    s.legacy_api_tokens_enabled = True
    s.save()
"' >/dev/null 2>&1
}

up() {
  [ -n "$NODE_BIN" ] || { echo "Node 22+ not found under ~/.nvm" >&2; exit 1; }
  local with_ls="" schema="current"
  while [ $# -gt 0 ]; do
    case "$1" in
      --with-label-studio) with_ls="--with-label-studio" ;;
      --schema) shift; schema="$1" ;;
    esac
    shift
  done
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
  # from 005 on, exactly as the runbook applies them.
  local files=("$HEALTH"/supabase_schema.sql "$HEALTH"/migrations/00[2-4]_*.sql "$WORKFORCE"/database.sql)
  for f in "$WORKFORCE"/migrations/*.sql; do
    # Production before this release: no 021. (022 only declares what production has.)
    [ "$schema" = "before" ] && [ "$(basename "$f")" = "021_pool_project.sql" ] && continue
    files+=("$f")
  done
  if [ "$schema" = "before" ]; then
    files+=("$HEALTH"/migrations/007_*.sql)
    # The two pools columns production has by hand (021 declares them).
    echo "alter table pools add column if not exists eval_config jsonb; alter table pools add column if not exists maximum_annotations integer;" > "$STATE/prod-pools.sql"
    files+=("$STATE/prod-pools.sql")
  else
    files+=("$HEALTH"/migrations/00[5-9]_*.sql)
  fi
  for f in "${files[@]}"; do
    $P -d senebi_e2e -f "$f" 2>&1 | grep -v NOTICE || true
  done
  $P -d senebi_e2e -c "grant usage on schema public to anon, authenticated, service_role;
    grant all on all tables in schema public to service_role;
    grant all on all sequences in schema public to service_role;
    grant execute on all functions in schema public to service_role;"

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

  local ls_url="http://127.0.0.1:9" ls_token="unused" ls_secret="" ls_callback=""
  if [ "$with_ls" = "--with-label-studio" ]; then
    start_label_studio
    ls_url="http://127.0.0.1:$LS_PORT"; ls_token="$LS_TOKEN_E2E"; ls_secret="$LS_SECRET_E2E"
    ls_callback="http://127.0.0.1:8765/api/v1/ls/webhook"
  fi
  # Throwaway values only, so start-health can bring the API back with the same settings.
  cat > "$STATE/health.env" <<EOF
KEY='$KEY'
E2E_LS_URL='$( [ -n "$ls_secret" ] && echo "$ls_url" )'
E2E_LS_TOKEN='$( [ -n "$ls_secret" ] && echo "$ls_token" )'
E2E_LS_SECRET='$ls_secret'
E2E_LS_CALLBACK='$ls_callback'
EOF
  start_health

  setsid -f bash -c 'cd "$1" && echo $$ > "$2" && shift 2 && exec "$@"' _ "$WORKFORCE" "$STATE/workforce.pid" \
    env PATH="$NODE_BIN:$PATH" NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321 \
    SUPABASE_SERVICE_ROLE_KEY="$KEY" LABEL_STUDIO_API_URL="$ls_url" LABEL_STUDIO_API_TOKEN="$ls_token" \
    MAGIC_LINK_SECRET=e2e-magic-link-secret-long-enough-0123456789 JWT_SECRET="$JWT_APP" \
    SENDGRID_API_KEY= RESEND_API_KEY= GOOGLE_CLIENT_ID= GOOGLE_CLIENT_SECRET= VERCEL_OIDC_TOKEN= \
    NEXT_PUBLIC_APP_URL=http://127.0.0.1:3765 OPS_API_KEY=e2e-ops-key \
    HEALTH_API_BASE=http://127.0.0.1:8765/api/v1/project HEALTH_ADMIN_KEY="$ADMIN" HEALTH_KEY_HEADER=X-Admin-Key \
    npx next dev -p 3765 > "$STATE/workforce.log" 2>&1 < /dev/null

  until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3765/api/pools)" = "401" ]; do sleep 1; done
  cat <<EOF
stack up (logs in $STATE). Run the rehearsal:

  NATIVE_E2E_API=http://127.0.0.1:8765 NATIVE_E2E_APP=http://127.0.0.1:3765 \\
  NATIVE_E2E_DSN=postgresql://postgres:test@127.0.0.1:$PGPORT/senebi_e2e \\
  NATIVE_E2E_ADMIN_KEY=$ADMIN NATIVE_E2E_JWT_SECRET=$JWT_APP \\
  NATIVE_E2E_STACK=$0 NATIVE_E2E_SCHEMA=$schema $( [ -n "$ls_secret" ] && echo "NATIVE_E2E_LS=http://127.0.0.1:$LS_PORT" ) \\
  PYTHONPATH=. .venv/bin/pytest tests/e2e/test_native_cutover.py -p no:playwright

The SQL suite can use the same Postgres:
  SQLTEST_DSN=postgresql://postgres:test@127.0.0.1:$PGPORT/postgres PYTHONPATH=. .venv/bin/pytest tests/sql
EOF
}

case "${1:-}" in
  up) shift; up "$@" ;;
  down) down ;;
  stop-health) stop_group "$STATE/health.pid"; echo "health stopped" ;;
  start-health) start_health; echo "health started" ;;
  *) echo "usage: $0 up [--with-label-studio] | down | stop-health | start-health" >&2; exit 2 ;;
esac
