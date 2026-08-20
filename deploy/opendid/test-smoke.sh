#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/deploy/opendid/smoke.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/opendid-smoke-test.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

fakebin="$tmp/bin"
mkdir -p "$fakebin"
fail=0
ok() { printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; fail=$((fail + 1)); }
want_grep() { grep -Eq "$1" "$2" && ok "$3" || bad "$3"; }
want_no_grep() { ! grep -Eq "$1" "$2" && ok "$3" || bad "$3"; }

cat >"$fakebin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'docker' >>"${FAKE_LOG:?}"
printf ' %q' "$@" >>"$FAKE_LOG"
printf '\n' >>"$FAKE_LOG"
case "$1" in
  inspect)
    [ "$2" = -f ] || exit 9
    case "$3:$4" in
      '{{.State.Running}}:postgre-opendid') echo true ;;
      '{{.State.Running}}:opendid-besu-node') echo true ;;
      '{{.Config.Image}}:opendid-besu-node') echo 'hyperledger/besu:25.5.0' ;;
      *) exit 1 ;;
    esac
    ;;
  exec)
    shift
    if [ "${1:-}" = -i ]; then shift; fi
    [ "$1" = postgre-opendid ] || exit 9
    shift
    [ "$1" = psql ] || exit 9
    sql=$(cat)
    case "$sql" in
      *"issue_profile"*) echo 1 ;;
      *"list_vc_plan"*) echo 1 ;;
      *"from public.entity"*) echo 5 ;;
      *"from vc where vc_id"*) cat "${FAKE_STATUS_FILE:?}" ;;
      *) echo 1 ;;
    esac
    ;;
  start|stop)
    printf '%s %s\n' "$1" "$2" >>"${FAKE_DOCKER_STATE_LOG:?}"
    if [ "${FAKE_STOP_FAIL:-}" = "$2" ]; then exit 5; fi
    if [ "$1" = start ] && [ "${FAKE_START_FAIL:-}" = "$2" ] && [ "$(cat "${FAKE_START_FAIL_ONCE:?}")" = 1 ]; then
      printf '0\n' >"${FAKE_START_FAIL_ONCE:?}"
      exit 6
    fi
    exit 0
    ;;
  *) exit 9 ;;
esac
SH
chmod +x "$fakebin/docker"

cat >"$fakebin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'curl' >>"${FAKE_LOG:?}"
printf ' %q' "$@" >>"$FAKE_LOG"
printf '\n' >>"$FAKE_LOG"
url="${@: -1}"
case "$url" in
  *:9001*) exit 7 ;;
  *:8090*/actuator/health|*:8091*/actuator/health|*:8094*/actuator/health|*:8100*/holder/health)
    printf '{"status":"UP"}\n'
    ;;
  *:8545*)
    body=$(cat)
    case "$body" in
      *eth_chainId*) printf '{"result":"%s"}\n' "${FAKE_CHAIN_ID:-0x539}" ;;
      *eth_getCode*) printf '{"result":"0x60016001"}\n' ;;
      *) printf '{"result":"ACTIVE"}\n' ;;
    esac
    ;;
  */wallet)
    printf '{"modelId":"opaque-model","did":"did:fixture:holder"}\n'
    ;;
  */register-did)
    printf '{"status":"registered","userDid":"did:fixture:user"}\n'
    ;;
  */issue-vc)
    printf 'ACTIVE\n' >"${FAKE_STATUS_FILE:?}"
    printf '{"vcId":"opaque-sensitive-id","status":"issued","vc":{"body":"OPAQUE_BODY_MARKER"}}\n'
    ;;
  */revoke-vc)
    printf 'REVOKED\n' >"${FAKE_STATUS_FILE:?}"
    printf '{"status":"revoked","revoked":true}\n'
    ;;
  *) printf '{}\n' ;;
esac
SH
chmod +x "$fakebin/curl"

cat >"$fakebin/java" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'java' >>"${FAKE_LOG:?}"
printf ' %q' "$@" >>"$FAKE_LOG"
printf '\n' >>"$FAKE_LOG"
sleep 20
SH
chmod +x "$fakebin/java"

cat >"$fakebin/python3" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  *verify-vcmeta.py*blockchain.properties*)
    cat "${FAKE_STATUS_FILE:?}"
    ;;
  *verify-vcmeta.py*)
    printf 'onchain_checked=1\nonchain_active=1\nonchain_revoked=0\nonchain_query_error=0\nonchain_mismatch=0\n'
    ;;
  *)
    exec /usr/bin/python3 "$@"
    ;;
esac
SH
chmod +x "$fakebin/python3"

export PATH="$fakebin:$PATH"
export FAKE_LOG="$tmp/fake.log"
export FAKE_DOCKER_STATE_LOG="$tmp/docker-state.log"
export FAKE_START_FAIL_ONCE="$tmp/start-fail-once"
printf '0\n' >"$FAKE_START_FAIL_ONCE"
export FAKE_STATUS_FILE="$tmp/status"
printf 'ACTIVE\n' >"$FAKE_STATUS_FILE"
export OPENDID_LOCAL_SOURCE="$tmp/source"
export FM_HOLDER_JAR="$tmp/fm-holder.jar"
export OPENDID_POSTGRES_USER=omn
export OPENDID_POSTGRES_PASSWORD=db-secret
export OPENDID_ISSUER_DB=issuer
export OPENDID_TAS_DB=tas
export OPENDID_BLOCKCHAIN_PROPERTIES="$tmp/blockchain.properties"
export OPENDID_TAS_WALLET_PW=tas-secret
export OPENDID_ISSUER_WALLET_PW=issuer-secret
export OPENDID_ISSUER_ZKP_WALLET_PW=issuer-zkp-secret
export OPENDID_CAS_WALLET_PW=cas-wallet-secret
export FM_HOLDER_PEPPER=pepper-secret
export FM_WALLET_PROVIDER_PW=wallet-secret
export FM_CAS_PROVIDER_PW=cas-secret
mkdir -p "$OPENDID_LOCAL_SOURCE/jars/TA" "$OPENDID_LOCAL_SOURCE/jars/Issuer" "$OPENDID_LOCAL_SOURCE/jars/CA" \
  "$OPENDID_LOCAL_SOURCE/jars/Wallet" "$OPENDID_LOCAL_SOURCE/shells/Besu/TA" "$OPENDID_LOCAL_SOURCE/shells/Besu/Issuer"
touch "$OPENDID_LOCAL_SOURCE/jars/TA/did-ta-server-2.0.0.jar" \
  "$OPENDID_LOCAL_SOURCE/jars/Issuer/did-issuer-server-2.0.0.jar" \
  "$OPENDID_LOCAL_SOURCE/jars/CA/did-ca-server-2.0.0.jar" \
  "$OPENDID_LOCAL_SOURCE/jars/Wallet/wallet.wallet" \
  "$OPENDID_LOCAL_SOURCE/jars/CA/cas.wallet" \
  "$FM_HOLDER_JAR"
write_chain_files() {
  printf 'evm.chainId=%s\nevm.contract.address=0x1111111111111111111111111111111111111111\n' "${1:-1337}" >"$OPENDID_BLOCKCHAIN_PROPERTIES"
  printf 'evm.chainId=%s\nevm.contract.address=%s\n' "${2:-1337}" "${5:-0x1111111111111111111111111111111111111111}" >"$OPENDID_LOCAL_SOURCE/shells/Besu/blockchain.properties"
  printf 'evm.chainId=%s\nevm.contract.address=0x1111111111111111111111111111111111111111\n' "${3:-1337}" >"$OPENDID_LOCAL_SOURCE/shells/Besu/TA/blockchain.properties"
  printf 'evm.chainId=%s\nevm.contract.address=0x1111111111111111111111111111111111111111\n' "${4:-1337}" >"$OPENDID_LOCAL_SOURCE/shells/Besu/Issuer/blockchain.properties"
}
write_chain_files 1337 1337 1337 1337

if "$SCRIPT" >"$tmp/out" 2>&1; then
  ok 'smoke script completes happy path'
else
  sed -n '1,80p' "$tmp/out"
  bad 'smoke script completes happy path'
fi
want_grep 'smoke_result=ok' "$tmp/out" 'smoke reports aggregate success'
want_grep 'orchestrator=closed' "$tmp/out" 'smoke proves Orchestrator closed'
want_grep 'chain_contract=ok' "$tmp/out" 'smoke proves matching chain and contract config'
want_grep 'lifecycle_1=revoked' "$tmp/out" 'smoke reports first lifecycle revoked'
want_grep 'lifecycle_2=issued' "$tmp/out" 'smoke reports second issuance after restart'
want_no_grep 'opaque-sensitive-id|OPAQUE_BODY_MARKER|did:fixture|pepper-secret|wallet-secret|cas-secret|tas-secret|issuer-secret' "$tmp/out" 'smoke stdout redacts VC IDs, bodies, DIDs, and secrets'
want_no_grep 'pepper-secret|wallet-secret|cas-secret|tas-secret|issuer-secret' "$FAKE_LOG" 'smoke does not pass secrets in argv'

printf 'ACTIVE\n' >"$FAKE_STATUS_FILE"
write_chain_files 1 1337 1337 1337
"$SCRIPT" >"$tmp/chain-mismatch.out" 2>&1 \
  && bad 'smoke fails on chain ID mismatch' \
  || ok 'smoke fails on chain ID mismatch'
want_grep 'smoke_error=chain_contract_mismatch' "$tmp/chain-mismatch.out" 'chain mismatch reports opaque label'

printf 'ACTIVE\n' >"$FAKE_STATUS_FILE"
write_chain_files 1337 1337 1337 1337 0x2222222222222222222222222222222222222222
"$SCRIPT" >"$tmp/contract-mismatch.out" 2>&1 \
  && bad 'smoke fails on contract mismatch' \
  || ok 'smoke fails on contract mismatch'
want_grep 'smoke_error=chain_contract_mismatch' "$tmp/contract-mismatch.out" 'contract mismatch reports opaque label'

: >"$FAKE_DOCKER_STATE_LOG"
printf '1\n' >"$FAKE_START_FAIL_ONCE"
printf 'ACTIVE\n' >"$FAKE_STATUS_FILE"
write_chain_files 1337 1337 1337 1337
FAKE_START_FAIL=postgre-opendid "$SCRIPT" >"$tmp/restart-fail.out" 2>&1 \
  && bad 'smoke fails when restart cannot restore PostgreSQL' \
  || ok 'smoke fails when restart cannot restore PostgreSQL'
want_grep '^stop opendid-besu-node$' "$FAKE_DOCKER_STATE_LOG" 'restart stopped Besu before injected failure'
want_grep '^stop postgre-opendid$' "$FAKE_DOCKER_STATE_LOG" 'restart stopped PostgreSQL before injected failure'
pg_starts=$(grep -c '^start postgre-opendid$' "$FAKE_DOCKER_STATE_LOG" || true)
[ "$pg_starts" -ge 2 ] && ok 'cleanup restarts stopped PostgreSQL after failure' || bad 'cleanup restarts stopped PostgreSQL after failure'
want_grep '^start opendid-besu-node$' "$FAKE_DOCKER_STATE_LOG" 'cleanup restarts stopped Besu after failure'

[ "$fail" -eq 0 ]
