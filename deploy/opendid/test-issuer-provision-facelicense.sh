#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/scripts/issuer-provision-facelicense.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/issuer-provision-test.XXXXXX")"
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
[ "$1" = "${PG_CONTAINER:-postgre-opendid}" ] || exit 9
shift
[ "$1" = psql ] || exit 9
unsafe="${UNSAFE_VALUE:?}"
for arg in "$@"; do
  [ "$arg" != *"$unsafe"* ] || { echo "unsafe value reached argv" >&2; exit 7; }
done
sql=$(cat)
case "$sql" in
  *"list_vc_plan"*) echo "${FAKE_LVP:-1}" ;;
  *"namespace"*) echo 1 ;;
  *"vc_schema"*) echo 1 ;;
  *"issue_profile"*) echo 2 ;;
  *) echo 1 ;;
esac
SH
chmod +x "$fakebin/docker"

cat >"$fakebin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'curl' >>"${FAKE_LOG:?}"
printf ' %q' "$@" >>"$FAKE_LOG"
printf '\n' >>"$FAKE_LOG"
exit 9
SH
chmod +x "$fakebin/curl"

export PATH="$fakebin:$PATH"
export FAKE_LOG="$tmp/fake.log"
export UNSAFE_VALUE="face' OR '1'='1"
export PG_USER=omn
export FL_NAMESPACE_ID="$UNSAFE_VALUE"
export FL_VC_SCHEMA="$UNSAFE_VALUE"
export FL_VC_PLAN=vcplanface0000000001

if "$SCRIPT" >"$tmp/out" 2>&1; then
  ok 'existing resources verify without unsafe SQL interpolation'
else
  bad 'existing resources verify without unsafe SQL interpolation'
fi
want_grep 'facelicense_plan=present' "$tmp/out" 'script proves FaceLicense plan exists'
want_no_grep "$UNSAFE_VALUE" "$FAKE_LOG" 'unsafe env value never appears in psql argv'
want_no_grep "$UNSAFE_VALUE" "$tmp/out" 'unsafe env value is not logged'

FAKE_LVP=0 "$SCRIPT" >"$tmp/missing-plan.out" 2>&1 \
  && bad 'missing FaceLicense TAS plan exits nonzero' \
  || ok 'missing FaceLicense TAS plan exits nonzero'
want_grep 'facelicense_plan=missing' "$tmp/missing-plan.out" 'missing plan is reported'
want_no_grep '발급 가능' "$tmp/missing-plan.out" 'missing plan never prints issuance-ready message'

[ "$fail" -eq 0 ]
