#!/usr/bin/env bash
set -euo pipefail

ORC=${ORC:-http://localhost:9001}
TAS=${TAS:-http://localhost:8090}
PGC=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
PGUSER=${OPENDID_POSTGRES_USER:-${PG_USER:-}}
ISSUER_DB=${OPENDID_ISSUER_DB:-issuer}
TAS_DB=${OPENDID_TAS_DB:-tas}
PLAN=${FL_VC_PLAN:-vcplanface0000000001}

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

plan=missing
[ "$issuer_plan" != unknown ] && [ "$tas_plan" != unknown ] && [ "$issuer_plan" -gt 0 ] && [ "$tas_plan" -gt 0 ] && plan=present

if [ "$entities" != 0 ] || [ "$issuer_plan" != 0 ] || [ "$tas_plan" != 0 ] || [ "$issuer_vcs" != 0 ]; then
  printf 'opendid_bootstrap=skip_populated\n'
  printf 'entities=%s\n' "$entities"
  printf 'facelicense_plan=%s\n' "$plan"
  printf 'issuer_vcs=%s\n' "$issuer_vcs"
  exit 0
fi

[ -n "${OPENDID_PW:-}" ] || { echo "OPENDID_PW=missing" >&2; exit 1; }

echo "==> 1/4 create/all (엔티티 월렛·DID 생성)"
body=$(python3 -c 'import json,os; print(json.dumps({"password": os.environ["OPENDID_PW"]}))')
curl -s -X POST "$ORC/create/all" -H 'Content-Type: application/json' -d "$body" -o /dev/null -w 'create_all=%{http_code}\n'

echo "==> 2/4 재기동 (shutdown/all → startup/all, 엔티티가 새 DID 로드)"
curl -s "$ORC/shutdown/all" -w ' [%{http_code}]\n' >/dev/null
sleep 3
curl -s "$ORC/startup/all" -w ' [%{http_code}]\n' >/dev/null
echo "   엔티티 기동 대기..."
until curl -sf "$TAS/actuator/health" >/dev/null 2>&1; do sleep 4; done
until curl -sf "http://localhost:8091/actuator/health" >/dev/null 2>&1; do sleep 4; done

echo "==> 3/4 TAS DID 온체인 등록 (ta/register-simple)"
curl -s -X POST "$TAS/tas/admin/v1/ta/register-simple" -H 'Content-Type: application/json' \
  -d "{\"serverUrl\":\"$TAS\"}" -o /dev/null -w 'tas_register=%{http_code}\n'

echo "==> 4/4 엔티티 4개 온체인 등록 (issuer/cas/wallet/verifier)"
curl -s -X POST "$TAS/tas/admin/v1/entities/register-simple" -H 'Content-Type: application/json' -o /dev/null -w 'entities_register=%{http_code}\n'

echo "==> 검증: entities/list"
curl -s "$TAS/tas/admin/v1/entities/list" | python3 -c "import sys,json;d=json.load(sys.stdin);c=d.get('content',[]);print('entities_registered=%d' % len(c))" 2>/dev/null || echo "entities_registered=unknown"
echo "완료. 이제 홀더 DID 앵커(anchor-did) + issue-vc 가능."
