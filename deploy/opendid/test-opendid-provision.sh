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
case " $* " in
  *' /create/all '*|*'/create/all'*)
    echo 'create/all must not be called for populated state' >&2
    exit 8
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

FAKE_ENTITIES=0 FAKE_PLAN_ISSUER=0 FAKE_PLAN_TAS=0 FAKE_VCS=0 \
  "$SCRIPT" >"$tmp/fresh-missing-secret.out" 2>&1 \
  && bad 'fresh bootstrap requires explicit OPENDID_PW' \
  || ok 'fresh bootstrap requires explicit OPENDID_PW'
want_grep 'OPENDID_PW=missing' "$tmp/fresh-missing-secret.out" 'missing secret reports env name only'
want_no_grep 'omn' "$tmp/fresh-missing-secret.out" 'missing secret does not mention fallback password'

[ "$fail" -eq 0 ]
