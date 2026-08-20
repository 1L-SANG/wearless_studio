#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE=${OPENDID_LOCAL_SOURCE:-/Users/nojeong-un/devs/did-orchestrator-server/source/did-orchestrator-server}
PGC=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
BESU=${OPENDID_BESU_CONTAINER:-opendid-besu-node}
PGUSER=${OPENDID_POSTGRES_USER:-}
ISSUER_DB=${OPENDID_ISSUER_DB:-issuer}
TAS_DB=${OPENDID_TAS_DB:-tas}
BESU_RPC=${OPENDID_BESU_RPC_URL:-http://127.0.0.1:8545}
CONTRACT_FILE=${OPENDID_BLOCKCHAIN_PROPERTIES:-$SOURCE/shells/Besu/blockchain.properties}
HOLDER_JAR=${FM_HOLDER_JAR:-$ROOT/services/fm-holder/build/libs/fm-holder-0.1.0.jar}
PLAN=${FL_VC_PLAN:-vcplanface0000000001}
MODEL_ID=${FM_SMOKE_MODEL_ID:-fm-smoke-$(date +%s)-$$}
if [ -n "${JAVA_CMD:-}" ]; then
  JAVA=$JAVA_CMD
elif [ -x /opt/homebrew/opt/openjdk@21/bin/java ]; then
  JAVA=/opt/homebrew/opt/openjdk@21/bin/java
else
  JAVA=java
fi

if [ -n "${OPENDID_SMOKE_TMP:-}" ]; then
  tmp=$OPENDID_SMOKE_TMP
  mkdir -p "$tmp"
  keep_tmp=1
else
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/opendid-smoke.XXXXXX")"
  keep_tmp=0
fi
holder_data="$tmp/holder-data"
mkdir -p "$holder_data"
chmod 700 "$tmp" "$holder_data"
pids=''
stopped_docker=''
cleanup() {
  local pid
  for pid in $pids; do kill "$pid" >/dev/null 2>&1 || true; done
  wait $pids >/dev/null 2>&1 || true
  local name
  for name in $stopped_docker; do docker start "$name" >/dev/null 2>&1 || true; done
  if [ "$keep_tmp" = 0 ]; then
    python3 - "$tmp" <<'PY'
import shutil, sys
shutil.rmtree(sys.argv[1], ignore_errors=True)
PY
  fi
}
trap cleanup EXIT

log() { printf '%s=%s\n' "$1" "$2"; }
die() { log smoke_error "$1"; exit 1; }

require_file() { [ -f "$1" ] || die "$2"; }
require_file "$SOURCE/jars/TA/did-ta-server-2.0.0.jar" missing_tas_jar
require_file "$SOURCE/jars/Issuer/did-issuer-server-2.0.0.jar" missing_issuer_jar
require_file "$SOURCE/jars/CA/did-ca-server-2.0.0.jar" missing_cas_jar
require_file "$HOLDER_JAR" missing_holder_jar
require_file "$CONTRACT_FILE" missing_blockchain_properties

if [ -z "$PGUSER" ]; then
  PGUSER=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$PGC" 2>/dev/null | sed -n 's/^POSTGRES_USER=//p' | head -1 || true)
fi
PGPASSWORD_VALUE=${OPENDID_POSTGRES_PASSWORD:-$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$PGC" 2>/dev/null | sed -n 's/^POSTGRES_PASSWORD=//p' | head -1 || true)}
[ -n "$PGUSER" ] || die postgres_user_missing
[ -n "$PGPASSWORD_VALUE" ] || die postgres_password_missing

OPENDID_DB_HOST=${OPENDID_DB_HOST:-127.0.0.1:5430}
OPENDID_DB_USER=${OPENDID_DB_USER:-$PGUSER}
OPENDID_DB_PASSWORD=${OPENDID_DB_PASSWORD:-$PGPASSWORD_VALUE}
OPENDID_TAS_DB=${OPENDID_TAS_DB:-$TAS_DB}
OPENDID_ISSUER_DB=${OPENDID_ISSUER_DB:-$ISSUER_DB}
OPENDID_CAS_DB=${OPENDID_CAS_DB:-cas}
export OPENDID_DB_HOST OPENDID_DB_USER OPENDID_DB_PASSWORD OPENDID_TAS_DB OPENDID_ISSUER_DB OPENDID_CAS_DB
export OPENDID_TAS_WALLET_PW=${OPENDID_TAS_WALLET_PW:-${OPENDID_WALLET_PW:-}}
export OPENDID_ISSUER_WALLET_PW=${OPENDID_ISSUER_WALLET_PW:-${OPENDID_WALLET_PW:-}}
export OPENDID_ISSUER_ZKP_WALLET_PW=${OPENDID_ISSUER_ZKP_WALLET_PW:-${OPENDID_WALLET_PW:-}}
export OPENDID_CAS_WALLET_PW=${OPENDID_CAS_WALLET_PW:-${OPENDID_WALLET_PW:-}}
export FM_HOLDER_DATA_DIR=${FM_HOLDER_DATA_DIR:-$holder_data}
export FM_HOLDER_PEPPER=${FM_HOLDER_PEPPER:-}
export FM_WALLET_PROVIDER_PATH=${FM_WALLET_PROVIDER_PATH:-$SOURCE/jars/Wallet/wallet.wallet}
export FM_CAS_PROVIDER_PATH=${FM_CAS_PROVIDER_PATH:-$SOURCE/jars/CA/cas.wallet}
export FM_WALLET_PROVIDER_PW=${FM_WALLET_PROVIDER_PW:-}
export FM_CAS_PROVIDER_PW=${FM_CAS_PROVIDER_PW:-}

for name in OPENDID_TAS_WALLET_PW OPENDID_ISSUER_WALLET_PW OPENDID_ISSUER_ZKP_WALLET_PW OPENDID_CAS_WALLET_PW FM_HOLDER_PEPPER FM_WALLET_PROVIDER_PW FM_CAS_PROVIDER_PW; do
  [ -n "${!name}" ] || die "${name}_missing"
done

psql_scalar() {
  local db=$1 sql=$2
  shift 2
  printf '%s\n' "$sql" | docker exec -i "$PGC" psql -X -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$db" "$@" -tA 2>/dev/null | tr -d '[:space:]'
}
prop_value() {
  sed -n "s/^$2=//p" "$1" | head -1
}
chain_decimal() {
  python3 - "$1" <<'PY'
import sys
value = sys.argv[1].strip()
print(int(value, 16) if value.startswith("0x") else int(value))
PY
}
track_stop() {
  docker stop "$1" >/dev/null
  stopped_docker="$stopped_docker $1"
}
untrack_started() {
  local keep='' name
  for name in $stopped_docker; do [ "$name" = "$1" ] || keep="$keep $name"; done
  stopped_docker=$keep
}
track_start() {
  docker start "$1" >/dev/null
  untrack_started "$1"
}

contract=$(sed -n 's/^evm\.contract\.address=//p' "$CONTRACT_FILE" | head -1)
case "$contract" in 0x????????????????????????????????????????) : ;; *) die contract_invalid ;; esac

docker inspect -f '{{.State.Running}}' "$PGC" >/dev/null 2>&1 || die postgres_missing
[ "$(docker inspect -f '{{.State.Running}}' "$PGC")" = true ] || die postgres_stopped
if ! docker inspect -f '{{.State.Running}}' "$BESU" >/dev/null 2>&1; then die besu_missing; fi
if [ "$(docker inspect -f '{{.State.Running}}' "$BESU")" != true ]; then docker start "$BESU" >/dev/null; fi
docker inspect -f '{{.Config.Image}}' "$BESU" | grep -q '25\.5\.0' || die besu_version
log postgres health
log besu health

rpc() {
  python3 - "$1" "$2" <<'PY' | curl -fsS --max-time 5 -H 'Content-Type: application/json' -d @- "$BESU_RPC" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result",""))'
import json, sys
method, param = sys.argv[1:]
params = [] if param == '-' else json.loads(param)
print(json.dumps({"jsonrpc":"2.0","method":method,"params":params,"id":1}))
PY
}
wait_besu_rpc() {
  for _ in $(seq 1 60); do
    rpc net_version - >/dev/null 2>&1 && { log besu_rpc health; return; }
    sleep 2
  done
  die besu_rpc_timeout
}
wait_besu_rpc
chain_id=$(rpc eth_chainId -)
[ -n "$chain_id" ] || die chain_id_missing
code=$(rpc eth_getCode "[\"$contract\",\"latest\"]")
[ "$code" != "0x" ] && [ -n "$code" ] || die contract_code_missing
rpc_chain=$(chain_decimal "$chain_id")
for file in "$SOURCE/shells/Besu/TA/blockchain.properties" "$SOURCE/shells/Besu/Issuer/blockchain.properties" "$SOURCE/shells/Besu/blockchain.properties" "$CONTRACT_FILE"; do
  [ -f "$file" ] || die chain_contract_mismatch
  [ "$(chain_decimal "$(prop_value "$file" evm.chainId)")" = "$rpc_chain" ] || die chain_contract_mismatch
  [ "$(prop_value "$file" evm.contract.address)" = "$contract" ] || die chain_contract_mismatch
done
log chain_contract ok

issuer_plan=$(psql_scalar "$ISSUER_DB" "select count(*) from public.issue_profile where vc_plan_id = :'plan';" -v "plan=$PLAN")
tas_plan=$(psql_scalar "$TAS_DB" "select count(*) from public.list_vc_plan where vc_plan_id = :'plan';" -v "plan=$PLAN")
[ "$issuer_plan" -gt 0 ] && [ "$tas_plan" -gt 0 ] || die facelicense_plan_missing
log facelicense_plan present

if curl -fsS --max-time 2 http://127.0.0.1:9001/ >/dev/null 2>&1; then die orchestrator_open; fi
log orchestrator closed

write_config() {
  local file=$1 service=$2 port=$3 chain=$4 wallet=$5 extra=${6:-}
  local db_env="OPENDID_${service}_DB"
  local wallet_pw_env="OPENDID_${service}_WALLET_PW"
  cat >"$file" <<EOF
server:
  address: 127.0.0.1
  port: $port
spring:
  profiles:
    active: dev
  datasource:
    driver-class-name: org.postgresql.Driver
    url: jdbc:postgresql://\${OPENDID_DB_HOST}/\${$db_env}
    username: \${OPENDID_DB_USER}
    password: \${OPENDID_DB_PASSWORD}
  jpa:
    open-in-view: true
    show-sql: false
    hibernate:
      ddl-auto: none
      naming:
        physical-strategy: org.hibernate.boot.model.naming.CamelCaseToUnderscoresNamingStrategy
blockchain:
  file-path: $chain
setup:
  base-url: http://127.0.0.1
  path: $SOURCE/jars
tas:
  url: http://127.0.0.1:8090
wallet:
  file-path: $wallet
  password: \${$wallet_pw_env}
$extra
EOF
  chmod 600 "$file"
}

write_config "$tmp/ta.yml" TAS 8090 "$SOURCE/shells/Besu/TA/blockchain.properties" "$SOURCE/jars/TA/tas.wallet"
issuer_extra=''
if [ -f "$SOURCE/jars/Issuer/issuer.zkpwallet" ]; then
  issuer_extra="zkp-wallet:
  file-path: $SOURCE/jars/Issuer/issuer.zkpwallet
  password: \${OPENDID_ISSUER_ZKP_WALLET_PW}"
fi
write_config "$tmp/issuer.yml" ISSUER 8091 "$SOURCE/shells/Besu/Issuer/blockchain.properties" "$SOURCE/jars/Issuer/issuer.wallet" "$issuer_extra"
write_config "$tmp/cas.yml" CAS 8094 "$SOURCE/shells/Besu/blockchain.properties" "$SOURCE/jars/CA/cas.wallet"

start_java() {
  local jar=$1 config=$2 port=$3 label=$4
  "$JAVA" -jar "$jar" --server.address=127.0.0.1 "--server.port=$port" "--spring.config.additional-location=file:$config" >"$tmp/$label.log" 2>&1 &
  pids="$pids $!"
}
start_java "$SOURCE/jars/TA/did-ta-server-2.0.0.jar" "$tmp/ta.yml" 8090 tas
start_java "$SOURCE/jars/Issuer/did-issuer-server-2.0.0.jar" "$tmp/issuer.yml" 8091 issuer
start_java "$SOURCE/jars/CA/did-ca-server-2.0.0.jar" "$tmp/cas.yml" 8094 cas
start_java "$HOLDER_JAR" "$ROOT/deploy/opendid/config/holder.yml" 8100 holder

wait_url() {
  local url=$1 label=$2
  for _ in $(seq 1 90); do
    curl -fsS --max-time 2 "$url" >/dev/null 2>&1 && { log "$label" health; return; }
    sleep 2
  done
  die "${label}_health_timeout"
}
wait_url http://127.0.0.1:8090/actuator/health tas
wait_url http://127.0.0.1:8091/actuator/health issuer
wait_url http://127.0.0.1:8094/actuator/health cas
wait_url http://127.0.0.1:8100/holder/health holder

post_json() {
  local url=$1 body=${2:-{}}
  printf '%s' "$body" | curl -fsS --max-time 60 -X POST -H 'Content-Type: application/json' -d @- "$url"
}
json_value() {
  python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

post_json "http://127.0.0.1:8100/holder/models/$MODEL_ID/wallet" >/dev/null
log lifecycle_wallet created
register_res=$(post_json "http://127.0.0.1:8100/holder/models/$MODEL_ID/register-did")
register_status=$(printf '%s' "$register_res" | json_value status)
[ "$register_status" = registered ] || die register_did_not_registered
log lifecycle_register_did registered
claims='{"plan":"facelicense","claims":{"allowed_use":"smoke","forbidden_use":"resale","unit_price":"0","license_valid_until":"2099-12-31","face_image_digest":"sha256:opaque","model_name":"smoke"}}'
issue1=$(post_json "http://127.0.0.1:8100/holder/models/$MODEL_ID/issue-vc" "$claims")
vc1=$(printf '%s' "$issue1" | json_value vcId)
[ -n "$vc1" ] || die issue_vc_missing_id
log lifecycle_issue_vc issued

chain_status_of() {
  python3 - "$ROOT/deploy/opendid/verify-vcmeta.py" "$CONTRACT_FILE" "$BESU_RPC" "$1" <<'PY'
import importlib.util, pathlib, sys
module_path, config_path, rpc_url, vc_id = sys.argv[1:]
spec = importlib.util.spec_from_file_location("verify_vcmeta", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
contract = module.properties(pathlib.Path(config_path)).get("evm.contract.address", "")
print(module.decode_vcmeta_status(module.eth_call(rpc_url, contract, module.encode_vcmeta_call(vc_id))))
PY
}
[ "$(chain_status_of "$vc1")" = ACTIVE ] || die lifecycle_valid_status
log lifecycle_valid active
revoke_res=$(post_json "http://127.0.0.1:8100/holder/models/$MODEL_ID/revoke-vc" "{\"vcId\":\"$vc1\"}")
revoke_status=$(printf '%s' "$revoke_res" | json_value status)
[ "$revoke_status" = revoked ] || die revoke_not_revoked
[ "$(chain_status_of "$vc1")" = REVOKED ] || die lifecycle_revoked_status
log lifecycle_1 revoked

for pid in $pids; do kill "$pid" >/dev/null 2>&1 || true; done
wait $pids >/dev/null 2>&1 || true
pids=''
track_stop "$BESU"
track_stop "$PGC"
track_start "$PGC"
track_start "$BESU"
start_java "$SOURCE/jars/TA/did-ta-server-2.0.0.jar" "$tmp/ta.yml" 8090 tas2
start_java "$SOURCE/jars/Issuer/did-issuer-server-2.0.0.jar" "$tmp/issuer.yml" 8091 issuer2
start_java "$SOURCE/jars/CA/did-ca-server-2.0.0.jar" "$tmp/cas.yml" 8094 cas2
start_java "$HOLDER_JAR" "$ROOT/deploy/opendid/config/holder.yml" 8100 holder2
wait_url http://127.0.0.1:8090/actuator/health tas
wait_url http://127.0.0.1:8091/actuator/health issuer
wait_url http://127.0.0.1:8094/actuator/health cas
wait_url http://127.0.0.1:8100/holder/health holder
[ "$(chain_status_of "$vc1")" = REVOKED ] || die restart_revoked_status
log restart_persistence revoked
issue2=$(post_json "http://127.0.0.1:8100/holder/models/$MODEL_ID/issue-vc" "$claims")
vc2=$(printf '%s' "$issue2" | json_value vcId)
[ -n "$vc2" ] || die second_issue_missing_id
log lifecycle_2 issued
log smoke_result ok
