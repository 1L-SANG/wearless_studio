#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXPORT="$ROOT/deploy/opendid/export-state.sh"
INVENTORY="$ROOT/deploy/opendid/inventory-state.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/opendid-export-test.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

fakebin="$tmp/bin"
mkdir -p "$fakebin"

fail=0
ok() { printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; fail=$((fail + 1)); }
want_file() { [ -f "$1" ] && ok "$2" || bad "$2"; }
want_grep() { grep -Eq "$1" "$2" && ok "$3" || bad "$3"; }
want_no_grep() { ! grep -Eq "$1" "$2" && ok "$3" || bad "$3"; }
want_mode_600() {
  mode=$(stat -f %Lp "$1" 2>/dev/null || stat -c %a "$1")
  [ "$mode" = "600" ] && ok "$2" || bad "$2 (got $mode)"
}

cat >"$fakebin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "docker $*" >>"${FAKE_LOG:?}"
case "$1" in
  inspect)
    if [ "$2" = "-f" ]; then
      if [ "$3" = "{{.Config.Image}}" ]; then
        case "$4" in
          "$OPENDID_POSTGRES_CONTAINER") echo 'postgres:16.4' ;;
          "$OPENDID_BESU_CONTAINER") echo 'hyperledger/besu:25.5.0' ;;
          *) exit 1 ;;
        esac
        exit 0
      fi
      case "$4" in
        "$OPENDID_BESU_CONTAINER") echo false ;;
        "$OPENDID_POSTGRES_CONTAINER") echo true ;;
        *) echo false ;;
      esac
      exit 0
    fi
    ;;
  volume)
    [ "$2" = "inspect" ]
    [ -d "${FAKE_VOLUMES:?}/${@: -1}" ] || exit 1
    echo "$FAKE_VOLUMES/${@: -1}"
    exit 0
    ;;
  exec)
    if [ "$3" = "$OPENDID_POSTGRES_CONTAINER" ] && [ "$4" = "pg_dumpall" ]; then
      printf '%s\n' '-- fake pg_dumpall'
      exit 0
    fi
    if [ "$3" = "$OPENDID_POSTGRES_CONTAINER" ] && [ "$4" = "psql" ]; then
      sql="${*: -1}"
      case "$sql" in
        *"current_setting('server_version'"*) echo '16.4' ;;
        *"pg_database_size"*) echo 'opendid_tas|1000|1' ;;
        *"information_schema.tables"*) echo '2' ;;
        *"facelicense"*"schema"*) echo '3' ;;
        *"plan"*) echo '4' ;;
        *"entity"*) echo '5' ;;
        *"issuer"*) echo '1' ;;
        *"cas"*) echo '1' ;;
        *) echo '0' ;;
      esac
      exit 0
    fi
    ;;
  run)
    src=''
    out=''
    for arg in "$@"; do
      case "$arg" in
        "$OPENDID_BESU_VOLUME:"*) src="${arg%%:*}" ;;
        *":/out"*) out="${arg%:/out}" ;;
      esac
    done
    [ -n "$src" ] && [ -n "$out" ]
    tar -C "$FAKE_VOLUMES/$src" -cf "$out/besu-data.tar" .
    exit 0
    ;;
esac
echo "unexpected docker call: $*" >&2
exit 9
SH
chmod +x "$fakebin/docker"

cat >"$fakebin/systemctl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "systemctl $*" >>"${FAKE_LOG:?}"
[ "$1" = "is-active" ] || exit 9
exit 3
SH
chmod +x "$fakebin/systemctl"

export PATH="$fakebin:$PATH"
export FAKE_LOG="$tmp/fake.log"
export FAKE_VOLUMES="$tmp/volumes"
export OPENDID_POSTGRES_CONTAINER="opendid-test-postgres"
export OPENDID_BESU_CONTAINER="opendid-test-besu"
export OPENDID_POSTGRES_VOLUME="opendid_test_pg_volume"
export OPENDID_BESU_VOLUME="opendid_test_besu_volume"
export OPENDID_ROOT="$tmp/opendid-root"
export OPENDID_SECRETS_DIR="$OPENDID_ROOT/secrets"
export OPENDID_CONFIG_DIR="$OPENDID_ROOT/config"
export OPENDID_HOLDER_DATA_DIR="$OPENDID_ROOT/state/holder"
mkdir -p "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME" "$FAKE_VOLUMES/$OPENDID_BESU_VOLUME" \
  "$OPENDID_SECRETS_DIR/TA" "$OPENDID_SECRETS_DIR/Issuer" "$OPENDID_SECRETS_DIR/CA" "$OPENDID_CONFIG_DIR"
printf 'besu source\n' >"$FAKE_VOLUMES/$OPENDID_BESU_VOLUME/block"
printf 'wallet-secret\n' >"$OPENDID_SECRETS_DIR/TA/tas.wallet"
printf 'did-secret\n' >"$OPENDID_SECRETS_DIR/Issuer/issuer.did"
printf 'chain-secret\n' >"$OPENDID_SECRETS_DIR/CA/blockchain.properties"
printf 'config-secret\n' >"$OPENDID_CONFIG_DIR/ta.yml"
before_hash=$(find "$FAKE_VOLUMES" "$OPENDID_ROOT" -type f -exec shasum -a 256 {} + | sort)

nonempty="$tmp/nonempty"
mkdir -p "$nonempty"
printf x >"$nonempty/existing"
if "$EXPORT" "$nonempty" >/tmp/opendid-export-overwrite.out 2>&1; then
  bad 'export refuses nonempty output'
else
  ok 'export refuses nonempty output'
fi
if [ ! -s "$FAKE_LOG" ]; then
  ok 'overwrite refusal happens before docker/systemctl'
else
  bad 'overwrite refusal happens before docker/systemctl'
fi

out="$tmp/out"
export OPENDID_TEST_OUT="$out"
"$EXPORT" "$out" >"$tmp/export.out"
want_file "$out/postgres.dump.sql" 'pg_dumpall file created'
want_file "$out/besu-data.tar" 'Besu archive created'
want_file "$out/opendid-files.tar" 'wallet DID config archive created'
want_file "$out/SHA256SUMS" 'checksum manifest created'
want_grep '^holder_data=missing$' "$out/EXPORT-MANIFEST.txt" 'holder missing recorded'
want_grep 'postgres.dump.sql$' "$out/SHA256SUMS" 'dump checksum recorded'
want_grep 'besu-data.tar$' "$out/SHA256SUMS" 'Besu checksum recorded'
want_mode_600 "$out/postgres.dump.sql" 'dump permission is 0600'
want_mode_600 "$out/besu-data.tar" 'Besu archive permission is 0600'
want_mode_600 "$out/opendid-files.tar" 'files archive permission is 0600'
want_no_grep 'wallet-secret|did-secret|chain-secret|config-secret' "$tmp/export.out" 'export does not print secrets'
after_hash=$(find "$FAKE_VOLUMES" "$OPENDID_ROOT" -type f -exec shasum -a 256 {} + | sort)
[ "$before_hash" = "$after_hash" ] && ok 'source files unchanged' || bad 'source files unchanged'

"$INVENTORY" >"$tmp/inventory.out"
want_grep 'postgres_container=present' "$tmp/inventory.out" 'inventory reports postgres presence'
want_grep 'holder_data=missing' "$tmp/inventory.out" 'inventory reports missing holder data'
want_no_grep 'wallet-secret|did-secret|chain-secret|config-secret' "$tmp/inventory.out" 'inventory does not print secrets'

exit "$fail"
