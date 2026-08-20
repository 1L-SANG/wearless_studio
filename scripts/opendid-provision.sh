#!/usr/bin/env bash
set -euo pipefail

ORC=${ORC:-http://localhost:9001}
TAS=${TAS:-http://localhost:8090}
PGC=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
PGUSER=${OPENDID_POSTGRES_USER:-${PG_USER:-}}
ISSUER_DB=${OPENDID_ISSUER_DB:-issuer}
TAS_DB=${OPENDID_TAS_DB:-tas}
PLAN=${FL_VC_PLAN:-vcplanface0000000001}
FRESH_MARKER=${OPENDID_FRESH_STATE_MARKER:-}

[ -n "$PGUSER" ] || { echo "OPENDID_POSTGRES_USER=missing" >&2; exit 1; }

q() {
  local db=$1 sql=$2
  shift 2
  printf '%s\n' "$sql" | docker exec -i "$PGC" psql -X -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$db" "$@" -tA 2>/dev/null | tr -d '[:space:]'
}

num() {
  local value
  value=$("$@" || printf unknown)
  case "$value" in ''|*[!0-9]*) printf unknown ;; *) printf '%s' "$value" ;; esac
}

entities=$(num q "$TAS_DB" "select count(*) from public.entity;")
issuer_plan=$(num q "$ISSUER_DB" "select count(*) from public.issue_profile where vc_plan_id = :'plan';" -v "plan=$PLAN")
tas_plan=$(num q "$TAS_DB" "select count(*) from public.list_vc_plan where vc_plan_id = :'plan';" -v "plan=$PLAN")
issuer_vcs=$(num q "$ISSUER_DB" "select count(*) from public.vc;")

case "$entities:$issuer_plan:$tas_plan:$issuer_vcs" in
  *unknown*) printf 'opendid_bootstrap=ambiguous\n'; exit 1 ;;
esac

plan=missing
[ "$issuer_plan" != unknown ] && [ "$tas_plan" != unknown ] && [ "$issuer_plan" -gt 0 ] && [ "$tas_plan" -gt 0 ] && plan=present

if [ "$entities" != 0 ] || [ "$issuer_plan" != 0 ] || [ "$tas_plan" != 0 ] || [ "$issuer_vcs" != 0 ]; then
  printf 'opendid_bootstrap=skip_populated\n'
  printf 'entities=%s\n' "$entities"
  printf 'facelicense_plan=%s\n' "$plan"
  printf 'issuer_vcs=%s\n' "$issuer_vcs"
  exit 0
fi

[ -n "$FRESH_MARKER" ] || { printf 'opendid_bootstrap=ambiguous\n'; exit 1; }
[ -f "$FRESH_MARKER" ] && [ ! -L "$FRESH_MARKER" ] || { printf 'opendid_bootstrap=ambiguous\n'; exit 1; }
mode=$(stat -f %Lp "$FRESH_MARKER" 2>/dev/null || stat -c %a "$FRESH_MARKER")
[ "$mode" = 600 ] || { printf 'opendid_bootstrap=ambiguous\n'; exit 1; }
[ -n "${OPENDID_PW:-}" ] || { echo "OPENDID_PW=missing" >&2; exit 1; }

post_json() {
  local label=$1 url=$2 body=$3
  printf '%s' "$body" | curl -fsS -X POST "$url" -H 'Content-Type: application/json' --data-binary @- -o /dev/null
  printf '%s=ok\n' "$label"
}

echo "==> 1/4 create/all (엔티티 월렛·DID 생성)"
post_json create_all "$ORC/create/all" "$(python3 -c 'import json,os; print(json.dumps({"password": os.environ["OPENDID_PW"]}))')"

echo "==> 2/4 재기동 (shutdown/all → startup/all, 엔티티가 새 DID 로드)"
curl -fsS "$ORC/shutdown/all" -o /dev/null
echo "shutdown_all=ok"
sleep 3
curl -fsS "$ORC/startup/all" -o /dev/null
echo "startup_all=ok"
echo "   엔티티 기동 대기..."
until curl -sf "$TAS/actuator/health" >/dev/null 2>&1; do sleep 4; done
until curl -sf "http://localhost:8091/actuator/health" >/dev/null 2>&1; do sleep 4; done

echo "==> 3/4 TAS DID 온체인 등록 (ta/register-simple)"
post_json tas_register "$TAS/tas/admin/v1/ta/register-simple" "{\"serverUrl\":\"$TAS\"}"

echo "==> 4/4 엔티티 4개 온체인 등록 (issuer/cas/wallet/verifier)"
post_json entities_register "$TAS/tas/admin/v1/entities/register-simple" '{}'

echo "==> 검증: entities/list"
entities_registered=$(curl -fsS "$TAS/tas/admin/v1/entities/list" | python3 -c 'import json,sys
try:
    data = json.load(sys.stdin)
    content = data.get("content")
    if not isinstance(content, list):
        raise ValueError
    print(len(content))
except Exception:
    raise SystemExit(1)
') || { echo "entities_registered=invalid"; exit 1; }
case "$entities_registered" in ''|*[!0-9]*) echo "entities_registered=invalid"; exit 1 ;; esac
[ "$entities_registered" -ge 4 ] || { echo "entities_registered=insufficient"; exit 1; }
printf 'entities_registered=%s\n' "$entities_registered"
python3 - "$FRESH_MARKER" <<'PY'
import os, sys
os.unlink(sys.argv[1])
PY
echo "완료. 이제 홀더 DID 앵커(anchor-did) + issue-vc 가능."
