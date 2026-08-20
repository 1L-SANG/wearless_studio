#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/scripts/opendid-provision.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/opendid-provision-test.XXXXXX")"
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
[ "$1" = exec ] || exit 9
shift
if [ "${1:-}" = -i ]; then shift; fi
container=$1; shift
[ "$container" = "${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}" ] || exit 9
[ "$1" = psql ] || exit 9
sql=$(cat)
case "$sql" in
  *"from public.entity"*) echo "${FAKE_ENTITIES:-5}" ;;
  *"from public.issue_profile"*) echo "${FAKE_PLAN_ISSUER:-1}" ;;
  *"from public.list_vc_plan"*) echo "${FAKE_PLAN_TAS:-1}" ;;
  *"from public.vc"*) echo "${FAKE_VCS:-12}" ;;
  *) echo 0 ;;
esac
SH
chmod +x "$fakebin/docker"

cat >"$fakebin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'curl' >>"${FAKE_LOG:?}"
printf ' %q' "$@" >>"$FAKE_LOG"
printf '\n' >>"$FAKE_LOG"
for arg in "$@"; do
  [ "$arg" != *"${OPENDID_PW:-__unset__}"* ] || { echo 'secret reached argv' >&2; exit 7; }
done
body=$(cat || true)
if [ "${FAKE_CURL_FAIL:-}" = 1 ]; then exit 22; fi
case " $* " in
  *'/entities/list'*)
    if [ "${FAKE_LIST_FAIL:-}" = 1 ]; then exit 22; fi
    if [ "${FAKE_LIST_RESPONSE+x}" ]; then
      printf '%s\n' "$FAKE_LIST_RESPONSE"
    else
      printf '{"content":[{"state":"registered"},{"state":"registered"},{"state":"registered"},{"state":"registered"}]}\n'
    fi
    ;;
  *' /create/all '*|*'/create/all'*)
    [ "$body" = '{"password": "fresh-secret"}' ] || { echo 'unexpected create body' >&2; exit 6; }
    printf '{}\n'
    ;;
  *) printf '{}\n' ;;
esac
SH
chmod +x "$fakebin/curl"

export PATH="$fakebin:$PATH"
export FAKE_LOG="$tmp/fake.log"
export OPENDID_POSTGRES_USER=omn
export OPENDID_ISSUER_DB=issuer
export OPENDID_TAS_DB=tas

if "$SCRIPT" >"$tmp/populated.out" 2>&1; then
  ok 'populated state exits without bootstrap'
else
  bad 'populated state exits without bootstrap'
fi
want_grep 'opendid_bootstrap=skip_populated' "$tmp/populated.out" 'populated state reports explicit skip'
want_grep 'facelicense_plan=present' "$tmp/populated.out" 'populated state proves FaceLicense plan'
want_no_grep 'create/all' "$FAKE_LOG" 'populated state never calls create/all'
want_no_grep 'omn' "$tmp/populated.out" 'default password is not printed'

missing_secret_marker="$tmp/missing-secret.marker"
printf 'one-shot\n' >"$missing_secret_marker"
chmod 600 "$missing_secret_marker"
FAKE_ENTITIES=0 FAKE_PLAN_ISSUER=0 FAKE_PLAN_TAS=0 FAKE_VCS=0 OPENDID_FRESH_STATE_MARKER="$missing_secret_marker" \
  "$SCRIPT" >"$tmp/fresh-missing-secret.out" 2>&1 \
  && bad 'fresh bootstrap requires explicit OPENDID_PW' \
  || ok 'fresh bootstrap requires explicit OPENDID_PW'
want_grep 'OPENDID_PW=missing' "$tmp/fresh-missing-secret.out" 'missing secret reports env name only'
want_no_grep 'omn' "$tmp/fresh-missing-secret.out" 'missing secret does not mention fallback password'

FAKE_ENTITIES=0 FAKE_PLAN_ISSUER=0 FAKE_PLAN_TAS=0 FAKE_VCS=0 OPENDID_PW=fresh-secret \
  "$SCRIPT" >"$tmp/fresh-no-marker.out" 2>&1 \
  && bad 'empty DB without fresh marker fails closed' \
  || ok 'empty DB without fresh marker fails closed'
want_grep 'opendid_bootstrap=ambiguous' "$tmp/fresh-no-marker.out" 'empty DB without marker reports ambiguous'

marker="$tmp/fresh.marker"
printf 'one-shot\n' >"$marker"
chmod 600 "$marker"
FAKE_LOG="$tmp/fresh-ok.log" FAKE_ENTITIES=0 FAKE_PLAN_ISSUER=0 FAKE_PLAN_TAS=0 FAKE_VCS=0 \
  OPENDID_PW=fresh-secret OPENDID_FRESH_STATE_MARKER="$marker" "$SCRIPT" >"$tmp/fresh-ok.out" 2>&1 \
  && ok 'fresh marker bootstrap succeeds' \
  || bad 'fresh marker bootstrap succeeds'
want_grep 'create_all=ok' "$tmp/fresh-ok.out" 'fresh bootstrap requires successful create/all'
want_grep 'tas_register=ok' "$tmp/fresh-ok.out" 'fresh bootstrap requires successful TAS registration'
want_grep 'entities_register=ok' "$tmp/fresh-ok.out" 'fresh bootstrap requires successful entity registration'
want_grep 'entities_registered=4' "$tmp/fresh-ok.out" 'fresh bootstrap verifies registered entity count'
[ ! -e "$marker" ] && ok 'fresh marker is consumed after success' || bad 'fresh marker is consumed after success'
want_no_grep 'fresh-secret' "$tmp/fresh-ok.log" 'fresh secret never reaches curl argv'

marker_fail="$tmp/fresh-fail.marker"
printf 'one-shot\n' >"$marker_fail"
chmod 600 "$marker_fail"
FAKE_LOG="$tmp/fresh-fail.log" FAKE_CURL_FAIL=1 FAKE_ENTITIES=0 FAKE_PLAN_ISSUER=0 FAKE_PLAN_TAS=0 FAKE_VCS=0 \
  OPENDID_PW=fresh-secret OPENDID_FRESH_STATE_MARKER="$marker_fail" "$SCRIPT" >"$tmp/fresh-fail.out" 2>&1 \
  && bad 'fresh bootstrap fails closed on curl failure' \
  || ok 'fresh bootstrap fails closed on curl failure'
[ -e "$marker_fail" ] && ok 'failed bootstrap preserves marker' || bad 'failed bootstrap preserves marker'
want_no_grep 'fresh-secret' "$tmp/fresh-fail.log" 'failed bootstrap keeps secret out of argv'

marker_list_fail="$tmp/fresh-list-fail.marker"
printf 'one-shot\n' >"$marker_list_fail"
chmod 600 "$marker_list_fail"
FAKE_LOG="$tmp/fresh-list-fail.log" FAKE_LIST_FAIL=1 FAKE_ENTITIES=0 FAKE_PLAN_ISSUER=0 FAKE_PLAN_TAS=0 FAKE_VCS=0 \
  OPENDID_PW=fresh-secret OPENDID_FRESH_STATE_MARKER="$marker_list_fail" "$SCRIPT" >"$tmp/fresh-list-fail.out" 2>&1 \
  && bad 'fresh bootstrap fails closed when entities list HTTP fails' \
  || ok 'fresh bootstrap fails closed when entities list HTTP fails'
[ -e "$marker_list_fail" ] && ok 'entities list HTTP failure preserves marker' || bad 'entities list HTTP failure preserves marker'
want_no_grep 'entities_registered=unknown|완료' "$tmp/fresh-list-fail.out" 'entities list HTTP failure is not reported as success'

marker_malformed="$tmp/fresh-list-malformed.marker"
printf 'one-shot\n' >"$marker_malformed"
chmod 600 "$marker_malformed"
FAKE_LOG="$tmp/fresh-list-malformed.log" FAKE_LIST_RESPONSE='not-json' FAKE_ENTITIES=0 FAKE_PLAN_ISSUER=0 FAKE_PLAN_TAS=0 FAKE_VCS=0 \
  OPENDID_PW=fresh-secret OPENDID_FRESH_STATE_MARKER="$marker_malformed" "$SCRIPT" >"$tmp/fresh-list-malformed.out" 2>&1 \
  && bad 'fresh bootstrap fails closed when entities list is malformed' \
  || ok 'fresh bootstrap fails closed when entities list is malformed'
[ -e "$marker_malformed" ] && ok 'malformed entities list preserves marker' || bad 'malformed entities list preserves marker'
want_no_grep 'entities_registered=unknown|완료' "$tmp/fresh-list-malformed.out" 'malformed entities list is not reported as success'

marker_short="$tmp/fresh-list-short.marker"
printf 'one-shot\n' >"$marker_short"
chmod 600 "$marker_short"
FAKE_LOG="$tmp/fresh-list-short.log" FAKE_LIST_RESPONSE='{"content":[{}]}' FAKE_ENTITIES=0 FAKE_PLAN_ISSUER=0 FAKE_PLAN_TAS=0 FAKE_VCS=0 \
  OPENDID_PW=fresh-secret OPENDID_FRESH_STATE_MARKER="$marker_short" "$SCRIPT" >"$tmp/fresh-list-short.out" 2>&1 \
  && bad 'fresh bootstrap fails closed when entity count is insufficient' \
  || ok 'fresh bootstrap fails closed when entity count is insufficient'
[ -e "$marker_short" ] && ok 'insufficient entities list preserves marker' || bad 'insufficient entities list preserves marker'
want_no_grep '완료' "$tmp/fresh-list-short.out" 'insufficient entities list is not reported as success'

[ "$fail" -eq 0 ]
