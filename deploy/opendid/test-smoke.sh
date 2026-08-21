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
for arg in "$@"; do
  if [ -n "${FM_HOLDER_HMAC_SECRET:-}" ] && [[ "$arg" == *"$FM_HOLDER_HMAC_SECRET"* ]]; then
    : >"${FAKE_SECRET_ARG_MARKER:?}"
    exit 12
  fi
done
url=''
output=''
write_format=''
data=''
timestamp=''
nonce=''
signature=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output=$2; shift 2 ;;
    -w) write_format=$2; shift 2 ;;
    -H)
      case "$2" in
        'X-FM-Timestamp: '*) timestamp=${2#*: } ;;
        'X-FM-Nonce: '*) nonce=${2#*: } ;;
        'X-FM-Signature: '*) signature=${2#*: } ;;
      esac
      shift 2
      ;;
    -d|--data-binary) data=$2; shift 2 ;;
    http://*|https://*) url=$1; shift ;;
    *) shift ;;
  esac
done
case "$url" in
  */holder/models/*|*/holder/vc/verify) printf 'curl holder_post\n' >>"${FAKE_LOG:?}" ;;
  *) printf 'curl %s\n' "$url" >>"${FAKE_LOG:?}" ;;
esac
emit() {
  if [ -n "$output" ]; then printf '%s\n' "$1" >"$output"; else printf '%s\n' "$1"; fi
}
case "$url" in
  *:9001*) exit 7 ;;
  *:8090*/actuator/health|*:8091*/actuator/health|*:8094*/actuator/health|*:8100*/holder/health)
    emit '{"status":"UP"}'
    ;;
  *:8545*)
    body=$(cat)
    case "$body" in
      *eth_chainId*) emit "{\"result\":\"${FAKE_CHAIN_ID:-0x539}\"}" ;;
      *eth_getCode*) emit '{"result":"0x60016001"}' ;;
      *) emit '{"result":"ACTIVE"}' ;;
    esac
    ;;
  */wallet|*/register-did|*/issue-vc|*/revoke-vc|*/holder/vc/verify)
    if [ -z "$timestamp$nonce$signature" ]; then
      emit '{"error":"unauthorized"}'
      [ -n "$write_format" ] && printf '401'
      [ -n "$write_format" ] || exit 22
      exit 0
    fi
    case "$data" in @*) body_file=${data#@} ;; *) exit 8 ;; esac
    mode=$(stat -f %Lp "$body_file" 2>/dev/null || stat -c %a "$body_file")
    [ "$mode" = 600 ] || exit 8
    target=${url#*://}
    target=/${target#*/}
    FM_CAPTURED_TIMESTAMP=$timestamp FM_CAPTURED_NONCE=$nonce FM_CAPTURED_SIGNATURE=$signature \
      "${REAL_PYTHON:?}" - "$target" "$body_file" <<'PY'
import hashlib, hmac, os, pathlib, sys

target, body_path = sys.argv[1:]
body = pathlib.Path(body_path).read_bytes()
canonical = "\n".join((
    "v1",
    "POST",
    target,
    os.environ["FM_CAPTURED_TIMESTAMP"],
    os.environ["FM_CAPTURED_NONCE"],
    hashlib.sha256(body).hexdigest(),
)).encode()
expected = hmac.new(
    os.environ["FM_HOLDER_HMAC_SECRET"].encode(), canonical, hashlib.sha256
).hexdigest()
if not hmac.compare_digest(expected, os.environ["FM_CAPTURED_SIGNATURE"]):
    raise SystemExit(9)
PY
    printf 'hmac=ok body_mode=600\n' >>"${FAKE_HMAC_LOG:?}"
    require_issued_vc() {
      if ! "${REAL_PYTHON:?}" - "$body_file" "${FAKE_ISSUED_VC_FILE:?}" <<'PY'
import json, pathlib, sys

body = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
issued_vc = pathlib.Path(sys.argv[2]).read_text().strip()
if body != {"vcId": issued_vc}:
    raise SystemExit(11)
PY
      then
        : >"${FAKE_VC_MISMATCH_MARKER:?}"
        exit 11
      fi
    }
    case "$url" in
      */wallet) emit '{"modelId":"opaque-model","did":"did:fixture:holder"}' ;;
      */register-did) emit '{"status":"registered","userDid":"did:fixture:user"}' ;;
      */issue-vc)
        "${REAL_PYTHON:?}" - "$body_file" <<'PY'
import json, pathlib, re, sys

body = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
if not re.fullmatch(r"fm-license:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", body.get("idempotencyKey", "")):
    raise SystemExit(10)
PY
        issues=$(cat "${FAKE_ISSUE_COUNT:?}")
        printf '%s\n' "$((issues + 1))" >"$FAKE_ISSUE_COUNT"
        (umask 077; printf 'opaque-sensitive-id\n' >"${FAKE_ISSUED_VC_FILE:?}")
        printf 'valid\n' >"${FAKE_STATUS_FILE:?}"
        printf 'valid\n' >"${FAKE_TRANSIENT_STATUS_FILE:?}"
        printf 'issue\n' >>"${FAKE_EVENT_LOG:?}"
        emit '{"vcId":"opaque-sensitive-id","status":"issued","vc":{"body":"OPAQUE_BODY_MARKER"}}'
        ;;
      */revoke-vc)
        require_issued_vc
        printf 'revoked\n' >"${FAKE_STATUS_FILE:?}"
        printf 'revoked\n' >"${FAKE_TRANSIENT_STATUS_FILE:?}"
        printf 'revoke\n' >>"${FAKE_EVENT_LOG:?}"
        emit '{"status":"revoked","revoked":true}'
        ;;
      */holder/vc/verify)
        require_issued_vc
        if [ -f "${FAKE_TRANSIENT_STATUS_FILE:?}" ]; then
          status_source=live
        else
          status_source=restarted
          cp "${FAKE_STATUS_FILE:?}" "$FAKE_TRANSIENT_STATUS_FILE"
        fi
        status=$(cat "$FAKE_TRANSIENT_STATUS_FILE")
        printf 'verify %s %s\n' "$status_source" "$status" >>"${FAKE_EVENT_LOG:?}"
        emit "{\"status\":\"$status\"}"
        ;;
    esac
    ;;
  *) emit '{}' ;;
esac
SH
chmod +x "$fakebin/curl"

cat >"$fakebin/systemctl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'systemctl'
  printf ' %s' "$@"
  printf '\n'
} >>"${FAKE_LOG:?}"
case "$1" in
  is-active)
    [ "$2" = --quiet ]
    case "$3" in
      opendid-tas.service|opendid-issuer.service|opendid-cas.service|fm-holder.service) exit 0 ;;
      *) exit 3 ;;
    esac
    ;;
  restart)
    [ "$2" = fm-holder ]
    rm -f "${FAKE_TRANSIENT_STATUS_FILE:?}"
    printf 'restart\n' >>"${FAKE_EVENT_LOG:?}"
    ;;
  *) exit 9 ;;
esac
SH
chmod +x "$fakebin/systemctl"

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

export REAL_PYTHON
REAL_PYTHON=$(command -v python3)
export PATH="$fakebin:$PATH"
export FAKE_LOG="$tmp/fake.log"
: >"$FAKE_LOG"
export FAKE_HMAC_LOG="$tmp/hmac.log"
export FAKE_ISSUE_COUNT="$tmp/issue-count"
export FAKE_DOCKER_STATE_LOG="$tmp/docker-state.log"
export FAKE_START_FAIL_ONCE="$tmp/start-fail-once"
printf '0\n' >"$FAKE_START_FAIL_ONCE"
export FAKE_STATUS_FILE="$tmp/status"
export FAKE_TRANSIENT_STATUS_FILE="$tmp/transient-status"
export FAKE_ISSUED_VC_FILE="$tmp/issued-vc"
export FAKE_EVENT_LOG="$tmp/events.log"
export FAKE_SECRET_ARG_MARKER="$tmp/secret-in-argv"
export FAKE_VC_MISMATCH_MARKER="$tmp/vc-mismatch"
printf 'valid\n' >"$FAKE_STATUS_FILE"
printf '0\n' >"$FAKE_ISSUE_COUNT"
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
export FM_HOLDER_HMAC_SECRET=test-only-holder-hmac
export FM_WALLET_PROVIDER_PW=wallet-secret
export FM_CAS_PROVIDER_PW=cas-secret

if curl -fsS -H "X-Harness-Probe: $FM_HOLDER_HMAC_SECRET" \
  http://127.0.0.1:8100/holder/health >/dev/null 2>&1; then
  bad 'fake curl rejects a secret passed in argv'
else
  ok 'fake curl rejects a secret passed in argv'
fi
[ -e "$FAKE_SECRET_ARG_MARKER" ] \
  && ok 'fake curl records an opaque secret-argv violation' \
  || bad 'fake curl records an opaque secret-argv violation'
want_no_grep 'test-only-holder-hmac' "$FAKE_LOG" 'fake curl never logs the secret-bearing argv'
rm -f "$FAKE_SECRET_ARG_MARKER"
: >"$FAKE_LOG"

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
want_grep 'holder_unsigned=blocked' "$tmp/out" 'smoke rejects unsigned Holder mutation'
want_grep 'holder_valid=valid' "$tmp/out" 'smoke verifies issued VC through Holder'
want_grep 'holder_revoked=revoked' "$tmp/out" 'smoke verifies revoke through Holder'
want_grep 'restart_holder_revoked=revoked' "$tmp/out" 'smoke verifies revoked after restart through Holder'
want_no_grep 'opaque-sensitive-id|OPAQUE_BODY_MARKER|did:fixture|pepper-secret|wallet-secret|cas-secret|tas-secret|issuer-secret' "$tmp/out" 'smoke stdout redacts VC IDs, bodies, DIDs, and secrets'
want_no_grep 'test-only-holder-hmac|pepper-secret|wallet-secret|cas-secret|tas-secret|issuer-secret' "$FAKE_LOG" 'smoke does not pass secrets in argv'
want_grep '^hmac=ok body_mode=600$' "$FAKE_HMAC_LOG" 'smoke signs exact mode-600 body files'
[ "$(cat "$FAKE_ISSUE_COUNT")" = 1 ] && ok 'smoke issues exactly one VC' || bad 'smoke issues exactly one VC'

for unit in opendid-tas opendid-issuer opendid-cas; do
  want_grep 'server\.address=127\.0\.0\.1' \
    "$ROOT/deploy/opendid/systemd/$unit.service" "$unit binds loopback"
done
want_grep 'server\.address=\$\{FM_HOLDER_BIND_ADDRESS\}' \
  "$ROOT/deploy/opendid/systemd/fm-holder.service" 'Holder binds configured private address'
want_grep '^Type=exec$' "$ROOT/deploy/opendid/systemd/fm-holder.service" 'Holder uses exec process tracking'
want_grep '^KillMode=control-group$' "$ROOT/deploy/opendid/systemd/fm-holder.service" 'Holder stops its whole process group'
want_grep '^TimeoutStopSec=[0-9]+$' "$ROOT/deploy/opendid/systemd/fm-holder.service" 'Holder stop is bounded'
want_grep '^ExecStartPre=/usr/bin/test -n "\$\{FM_HOLDER_BIND_ADDRESS\}"$' \
  "$ROOT/deploy/opendid/systemd/fm-holder.service" 'Holder refuses a blank bind address'
want_grep '^FM_HOLDER_HMAC_SECRET=$' "$ROOT/deploy/opendid/env.example" 'example requires an explicit Holder HMAC secret'
want_grep '^FM_HOLDER_BIND_ADDRESS=$' "$ROOT/deploy/opendid/env.example" 'example requires an explicit Holder bind address'
RUNBOOK="$ROOT/docs/runbooks/facemarket-opendid-single-server.md"
want_grep 'test "\$FM_HOLDER_BIND_ADDRESS" = "\$SERVER3_PRIVATE_BIND_ADDRESS"' \
  "$RUNBOOK" 'runbook maps the Server 3 private bind before startup'
want_grep 'http://\$\{FM_HOLDER_BIND_ADDRESS\}:8100/holder/health' \
  "$RUNBOOK" 'runbook health-checks the private Holder listener'
want_grep 'OPENDID_SMOKE_MODE=managed' "$RUNBOOK" 'runbook uses only managed target smoke'
want_no_grep 'OPENDID_SMOKE_MODE=self-managed' "$RUNBOOK" 'runbook never starts self-managed smoke on target'
runbook_smoke_line=$(grep -n 'env OPENDID_SMOKE_MODE=managed deploy/opendid/smoke.sh' "$RUNBOOK" | tail -1 | cut -d: -f1)
runbook_state_line=$(grep -n 'diff -u "\$SOURCE_ISSUER_STATE" "\$TARGET_ISSUER_STATE"' "$RUNBOOK" | tail -1 | cut -d: -f1)
runbook_vcmeta_line=$(grep -n 'deploy/opendid/verify-vcmeta.py' "$RUNBOOK" | tail -1 | cut -d: -f1)
runbook_orchestrator_line=$(grep -n '! ss -ltnH' "$RUNBOOK" | tail -1 | cut -d: -f1)
if [ "$runbook_smoke_line" -gt "$runbook_state_line" ] \
  && [ "$runbook_smoke_line" -gt "$runbook_vcmeta_line" ] \
  && [ "$runbook_smoke_line" -gt "$runbook_orchestrator_line" ]; then
  ok 'runbook runs mutating managed smoke after restored-state and read-only proofs'
else
  bad 'runbook runs mutating managed smoke after restored-state and read-only proofs'
fi

PLAN_FILE="$ROOT/docs/superpowers/plans/2026-08-21-facemarket-mandatory-vc-cutover.md"
TASK7_PLAN="$tmp/task7-plan"
sed -n '/^### Task 7:/,/^### Task 8:/p' "$PLAN_FILE" >"$TASK7_PLAN"
want_grep 'SERVER3_PRIVATE_BIND_ADDRESS' "$TASK7_PLAN" 'Task 7 requires the approved Server 3 private bind'
want_grep 'test "\$FM_HOLDER_BIND_ADDRESS" = "\$SERVER3_PRIVATE_BIND_ADDRESS"' \
  "$TASK7_PLAN" 'Task 7 checks the Holder bind mapping before startup'
want_grep 'OPENDID_SMOKE_MODE=managed' "$TASK7_PLAN" 'Task 7 invokes managed smoke explicitly'
want_no_grep '^[[:space:]]*deploy/opendid/smoke\.sh$' "$TASK7_PLAN" 'Task 7 never invokes default self-managed smoke'

README="$ROOT/deploy/opendid/README.md"
README_MANAGED="$tmp/readme-managed"
sed -n '/On an already-running Server 3/,/Managed mode also requires/p' "$README" >"$README_MANAGED"
want_grep '\. /opt/opendid/opendid\.env' "$README_MANAGED" 'README managed command loads the deployed environment'
want_grep 'sudo --preserve-env=FM_HOLDER_HMAC_SECRET,FM_HOLDER_BIND_ADDRESS' \
  "$README_MANAGED" 'README managed command has restart privilege and preserves required values'

: >"$FAKE_LOG"
: >"$FAKE_HMAC_LOG"
: >"$FAKE_EVENT_LOG"
printf '0\n' >"$FAKE_ISSUE_COUNT"
printf 'valid\n' >"$FAKE_STATUS_FILE"
rm -f "$FAKE_TRANSIENT_STATUS_FILE" "$FAKE_ISSUED_VC_FILE" "$FAKE_VC_MISMATCH_MARKER"
if OPENDID_SMOKE_MODE=managed \
  FM_HOLDER_BIND_ADDRESS=10.0.3.7 \
  OPENDID_LOCAL_SOURCE=/Users/developer/source \
  FM_HOLDER_JAR=/Users/developer/fm-holder.jar \
  OPENDID_SMOKE_TMP="$tmp/managed-work" \
  "$SCRIPT" >"$tmp/managed.out" 2>&1; then
  ok 'managed smoke completes happy path'
else
  sed -n '1,80p' "$tmp/managed.out"
  bad 'managed smoke completes happy path'
fi
want_grep 'holder_unsigned=blocked' "$tmp/managed.out" 'managed smoke rejects unsigned Holder mutation'
want_grep 'holder_valid=valid' "$tmp/managed.out" 'managed smoke verifies issued VC'
want_grep 'holder_revoked=revoked' "$tmp/managed.out" 'managed smoke verifies revoked VC'
want_grep 'restart_holder_revoked=revoked' "$tmp/managed.out" 'managed smoke preserves revoked VC across Holder restart'
want_no_grep '^java( |$)' "$FAKE_LOG" 'managed smoke starts no Java process'
want_no_grep '^docker( |$)' "$FAKE_LOG" 'managed smoke does not inspect or mutate Docker'
want_grep '^systemctl restart fm-holder$' "$FAKE_LOG" 'managed smoke restarts fm-holder'
restart_count=$(grep -c '^systemctl restart fm-holder$' "$FAKE_LOG" || true)
[ "$restart_count" = 1 ] && ok 'managed smoke performs one service restart' || bad 'managed smoke performs one service restart'
want_no_grep '^systemctl (start|stop|reload|daemon-reload)' "$FAKE_LOG" 'managed smoke performs no other service mutation'
want_grep '^curl http://10\.0\.3\.7:8100/holder/health$' "$FAKE_LOG" 'managed smoke uses private Holder bind address'
want_no_grep '/Users/' "$FAKE_LOG" 'managed smoke uses no developer source path'
want_no_grep 'test-only-holder-hmac|opaque-sensitive-id|OPAQUE_BODY_MARKER|did:fixture' "$FAKE_LOG" 'managed command log excludes secrets, IDs, DIDs, and bodies'
[ "$(cat "$FAKE_ISSUE_COUNT")" = 1 ] && ok 'managed smoke issues exactly one VC' || bad 'managed smoke issues exactly one VC'
[ "$(wc -l <"$FAKE_HMAC_LOG" | tr -d '[:space:]')" = 7 ] \
  && ok 'managed smoke verifies all seven signed request bodies' \
  || bad 'managed smoke verifies all seven signed request bodies'
expected_events=$(printf '%s\n' \
  issue \
  'verify live valid' \
  revoke \
  'verify live revoked' \
  restart \
  'verify restarted revoked')
[ "$(cat "$FAKE_EVENT_LOG")" = "$expected_events" ] \
  && ok 'managed smoke verifies one VC across exactly one persisted Holder restart' \
  || bad 'managed smoke verifies one VC across exactly one persisted Holder restart'
[ ! -e "$FAKE_VC_MISMATCH_MARKER" ] \
  && ok 'managed revoke and verify bodies use the issued VC ID' \
  || bad 'managed revoke and verify bodies use the issued VC ID'

if (unset FM_HOLDER_HMAC_SECRET; OPENDID_SMOKE_MODE=managed FM_HOLDER_BIND_ADDRESS=10.0.3.7 "$SCRIPT") \
  >"$tmp/missing-hmac.out" 2>&1; then
  bad 'managed smoke refuses a missing HMAC secret'
else
  ok 'managed smoke refuses a missing HMAC secret'
fi
want_grep '^smoke_error=FM_HOLDER_HMAC_SECRET_missing$' "$tmp/missing-hmac.out" 'missing HMAC failure is aggregate-only'

if OPENDID_SMOKE_MODE=managed FM_HOLDER_BIND_ADDRESS= "$SCRIPT" \
  >"$tmp/missing-bind.out" 2>&1; then
  bad 'managed smoke refuses a missing Holder bind address'
else
  ok 'managed smoke refuses a missing Holder bind address'
fi
want_grep '^smoke_error=FM_HOLDER_BIND_ADDRESS_missing$' "$tmp/missing-bind.out" 'missing bind failure is aggregate-only'

printf 'valid\n' >"$FAKE_STATUS_FILE"
write_chain_files 1 1337 1337 1337
"$SCRIPT" >"$tmp/chain-mismatch.out" 2>&1 \
  && bad 'smoke fails on chain ID mismatch' \
  || ok 'smoke fails on chain ID mismatch'
want_grep 'smoke_error=chain_contract_mismatch' "$tmp/chain-mismatch.out" 'chain mismatch reports opaque label'

printf 'valid\n' >"$FAKE_STATUS_FILE"
write_chain_files 1337 1337 1337 1337 0x2222222222222222222222222222222222222222
"$SCRIPT" >"$tmp/contract-mismatch.out" 2>&1 \
  && bad 'smoke fails on contract mismatch' \
  || ok 'smoke fails on contract mismatch'
want_grep 'smoke_error=chain_contract_mismatch' "$tmp/contract-mismatch.out" 'contract mismatch reports opaque label'

: >"$FAKE_DOCKER_STATE_LOG"
printf '1\n' >"$FAKE_START_FAIL_ONCE"
printf 'valid\n' >"$FAKE_STATUS_FILE"
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
