#!/usr/bin/env bash
# OpenDID 스택(opendid 서비스 + 공유 HMAC)에 필요한 프로드 시크릿을 copilot 로 한 번에 넣는다.
# 값은 이 머신 안에서만 조립된다(내 대화엔 안 넘어옴):
#   - scratchpad/omnione-values.env  → 계약주소·TAS/ISSUER 키
#   - 로컬 오케스트레이터 월렛 4개    → base64
#   - 월렛 비번                       → OmniOne 기본 omnioneopendid12!@ (월렛이 이 비번으로 생성됨)
#   - HMAC·pepper                     → 여기서 랜덤 생성(프로드 홀더는 fresh store 라 무방)
# 프롬프트로 받는 것(너만 아는 값): Supabase DB 비번, OmniOne Chain RPC URL(토큰 포함).
#
# ⚠️ copilot-aws(profile wearless) 로그인 상태에서 실행. 값은 --values 로 넘어가 ps 에 잠깐 보인다
#    (1회성·본인 머신이라 허용). 재실행 시 copilot 이 기존 시크릿을 덮어쓴다.
set -euo pipefail

APP=wearless
ENV=prod
COPILOT="${COPILOT:-copilot-aws}"
ORCH="${OPENDID_ORCH_DIR:-$HOME/devs/did-orchestrator-server/source/did-orchestrator-server}"
VALUES_ENV="${OMNIONE_VALUES_ENV:-}"

# scratchpad 경로가 세션마다 달라서 인자/환경변수로 받거나 찾는다.
if [ -z "$VALUES_ENV" ]; then
  VALUES_ENV="$(ls -t /private/tmp/claude-*/**/scratchpad/omnione-values.env 2>/dev/null | head -1 || true)"
fi
[ -f "$VALUES_ENV" ] || { echo "omnione-values.env 를 못 찾음 — OMNIONE_VALUES_ENV=<경로> 로 지정하세요"; exit 1; }
# shellcheck disable=SC1090
source "$VALUES_ENV"
: "${OMNIONE_DID_CONTRACT_ADDRESS:?env파일에 계약주소 없음}" "${OMNIONE_TAS_KEY:?}" "${OMNIONE_ISSUER_KEY:?}"

b64() { base64 < "$1" | tr -d '\n'; }
WTAS="$(b64 "$ORCH/jars/TA/tas.wallet")"
WISS="$(b64 "$ORCH/jars/Issuer/issuer.wallet")"
WCAS="$(b64 "$ORCH/jars/CA/cas.wallet")"
WHOL="$(b64 "$ORCH/jars/Wallet/wallet.wallet")"

WALLET_PW="omnioneopendid12!@"
HMAC="$(openssl rand -hex 32)"
PEPPER="$(openssl rand -hex 32)"

read -rsp "Supabase DB 비번(직결 postgres): " DB_PW; echo
read -rp  "OmniOne Chain RPC URL(토큰 포함, api 의 FM_CHAIN_RPC_URL 과 같은 값 가능): " RPC_URL
[ -n "$DB_PW" ] && [ -n "$RPC_URL" ] || { echo "DB 비번·RPC 둘 다 필요"; exit 1; }

put() {  # $1=name $2=value  (opendid 서비스 스코프)
  printf '  - %s\n' "$1"
  "$COPILOT" secret init --app "$APP" --name "$1" --values "${ENV}=$2" >/dev/null
}

echo "== copilot 시크릿 등록(app=$APP env=$ENV) =="
put OPENDID_DB_HOST     "db.pedonlvyhoyedzdmmwco.supabase.co"
put OPENDID_DB_PORT     "5432"
put OPENDID_DB_USER     "postgres"
put OPENDID_DB_PASSWORD "$DB_PW"
put OMNIONE_CHAIN_RPC_URL        "$RPC_URL"
put OMNIONE_DID_CONTRACT_ADDRESS "$OMNIONE_DID_CONTRACT_ADDRESS"
put OMNIONE_TAS_KEY     "$OMNIONE_TAS_KEY"
put OMNIONE_ISSUER_KEY  "$OMNIONE_ISSUER_KEY"
put OPENDID_WALLET_PASSWORD "$WALLET_PW"
put FM_HOLDER_PEPPER    "$PEPPER"
put OPENDID_HOLDER_HMAC_SECRET "$HMAC"   # api·holder 공유(같은 SSM 파라미터)
put WALLET_TAS_B64      "$WTAS"
put WALLET_ISSUER_B64   "$WISS"
put WALLET_CAS_B64      "$WCAS"
put WALLET_HOLDER_B64   "$WHOL"

echo
echo "== 완료. 남은 api-측 시크릿(생체등록에 필요, 아직 없으면) =="
echo "  FM_CI_PEPPER              → 랜덤:  $COPILOT secret init --app $APP --name FM_CI_PEPPER --values ${ENV}=\$(openssl rand -hex 32)"
echo "  FM_LIVENESS_BROWSER_ROLE_ARN → AWS Rekognition Face Liveness 브라우저용 IAM role ARN(prod). 없으면 별도 생성 필요."
echo
echo "다음: OMNIONE 엔티티 프로비저닝 → copilot svc deploy opendid → 헬시 → copilot svc deploy api."
