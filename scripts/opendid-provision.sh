#!/usr/bin/env bash
# OpenDID 스택 프로비저닝 — "Start All"(서버 기동)만으로는 부족하다. TAS·엔티티 DID 를
# 온체인에 등록해야 어떤 프로토콜 스텝(request-ecdh 등 클라 DID 온체인 조회)도 동작한다.
# 미프로비저닝 증상: SSRVTRA13001 "TAS is not registered", "Failed to retrieve DID document".
#
# 순서: create(엔티티 월렛/DID 생성) → 재기동(DID 로드) → TAS 등록 → 엔티티 4개 등록.
# (대시보드 :9001 pw=omn 이 하는 일을 헤드리스로 재현. dev TAS = permitAll → admin 인증 불필요.)
set -euo pipefail

ORC=${ORC:-http://localhost:9001}
TAS=${TAS:-http://localhost:8090}
PW=${OPENDID_PW:-omn}

echo "==> 1/4 create/all (엔티티 월렛·DID 생성)"
curl -s -X POST "$ORC/create/all" -H 'Content-Type: application/json' -d "{\"password\":\"$PW\"}" -w '\n[%{http_code}]\n'

echo "==> 2/4 재기동 (shutdown/all → startup/all, 엔티티가 새 DID 로드)"
curl -s "$ORC/shutdown/all" -w ' [%{http_code}]\n' >/dev/null
sleep 3
curl -s "$ORC/startup/all" -w ' [%{http_code}]\n' >/dev/null
echo "   엔티티 기동 대기..."
until curl -sf "$TAS/actuator/health" >/dev/null 2>&1; do sleep 4; done
until curl -sf "http://localhost:8091/actuator/health" >/dev/null 2>&1; do sleep 4; done

echo "==> 3/4 TAS DID 온체인 등록 (ta/register-simple)"
curl -s -X POST "$TAS/tas/admin/v1/ta/register-simple" -H 'Content-Type: application/json' \
  -d "{\"serverUrl\":\"$TAS\"}" -w '\n[%{http_code}]\n' | tail -c 120

echo "==> 4/4 엔티티 4개 온체인 등록 (issuer/cas/wallet/verifier)"
curl -s -X POST "$TAS/tas/admin/v1/entities/register-simple" -H 'Content-Type: application/json' -w '\n[%{http_code}]\n'

echo "==> 검증: entities/list"
curl -s "$TAS/tas/admin/v1/entities/list" | python3 -c "import sys,json;d=json.load(sys.stdin);c=d.get('content',[]);print('\n'.join(f\"  {e['did']:24} {e['role']:16} {e['status']}\" for e in c))" 2>/dev/null || echo "  (list parse failed)"
echo "완료. 이제 홀더 DID 앵커(anchor-did) + issue-vc 가능."
