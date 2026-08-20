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
want_tar_has() { tar -tf "$1" | grep -Eq "$2" && ok "$3" || bad "$3"; }
want_tar_lacks() { ! tar -tf "$1" | grep -Eq "$2" && ok "$3" || bad "$3"; }
want_one_line_value() {
  local key=$1 want=$2 file=$3 label=$4
  local got
  got=$(grep -E "^$key=" "$file" || true)
  [ "$got" = "$key=$want" ] && ok "$label" || bad "$label (got $(printf '%s' "$got" | tr '\n' '|'))"
}
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
      if [ "$3" = "{{range .Config.Env}}{{println .}}{{end}}" ]; then
        case "$4" in
          "$OPENDID_POSTGRES_CONTAINER")
            echo 'POSTGRES_USER=omn'
            echo 'POSTGRES_DB=omn'
            echo 'POSTGRES_PASSWORD=do-not-print'
            ;;
          *) exit 1 ;;
        esac
        exit 0
      fi
      case "$4" in
        "$OPENDID_BESU_CONTAINER") echo "${FAKE_BESU_RUNNING:-false}" ;;
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
    container=$2
    cmd=$3
    has_i=0
    if [ "$container" = "-i" ]; then
      has_i=1
      container=$3
      cmd=$4
    fi
    if [ "${FAKE_CONSUME_STDIN:-0}" = "1" ] && [ "$has_i" = "1" ]; then
      while IFS= read -r _line; do :; done || true
    fi
    if [ "$container" = "$OPENDID_POSTGRES_CONTAINER" ] && [ "$cmd" = "pg_dumpall" ]; then
      printf '%s\n' '-- fake pg_dumpall'
      exit 0
    fi
    if [ "$container" = "$OPENDID_POSTGRES_CONTAINER" ] && [ "$cmd" = "psql" ]; then
      sql="${*: -1}"
      db='postgres'
      prev=''
      for arg in "$@"; do
        if [ "$prev" = "-d" ]; then db="$arg"; fi
        prev="$arg"
      done
      user=''
      prev=''
      for arg in "$@"; do
        if [ "$prev" = "-U" ]; then user="$arg"; fi
        prev="$arg"
      done
      [ "$user" = "${FAKE_EXPECT_PGUSER:-$user}" ] || exit 2
      case "$sql" in
        *"from pg_database"*) [ "$db" = "${FAKE_EXPECT_PGDB:-$db}" ] || exit 2 ;;
      esac
      case "${FAKE_SQL_FAIL:-}" in
        namespace) case "$sql" in *"count(*) from public.namespace"*) exit 2 ;; esac ;;
      esac
      case "$sql" in
        *"current_setting('server_version'"*) echo '16.4' ;;
        *"from pg_database"*)
          echo 'omn|100'
          echo 'tas|200'
          echo 'issuer|300'
          echo 'cas|400'
          echo 'wallet|500'
          echo 'verifier|600'
          echo 'api|700'
          echo 'holder|800'
          ;;
        *"information_schema.tables"*"table_schema='public'"*) echo '2' ;;
        *"to_regclass('public.namespace')"*) [ "$db" = "issuer" ] && echo 'namespace' || echo '' ;;
        *"to_regclass('public.vc_schema')"*) [ "$db" = "issuer" ] && echo 'vc_schema' || echo '' ;;
        *"to_regclass('public.issue_profile')"*) [ "$db" = "issuer" ] && echo 'issue_profile' || echo '' ;;
        *"to_regclass('public.list_vc_plan')"*) [ "$db" = "tas" ] && echo 'list_vc_plan' || echo '' ;;
        *"to_regclass('public.entity')"*) [ "$db" = "tas" ] && echo 'entity' || echo '' ;;
        *"to_regclass('public.issuer')"*) [ "$db" = "issuer" ] && echo 'issuer' || echo '' ;;
        *"to_regclass('public.cas')"*) [ "$db" = "cas" ] && echo 'cas' || echo '' ;;
        *"to_regclass('public.ca')"*) echo '' ;;
        *"count(*) from public.namespace where namespace_id='kr.wearless.facelicense'"*) [ "$db" = "issuer" ] && echo '1' || exit 1 ;;
        *"count(*) from public.vc_schema where vc_schema_id='facelicense'"*) [ "$db" = "issuer" ] && echo '1' || exit 1 ;;
        *"count(*) from public.issue_profile where vc_plan_id='vcplanface0000000001'"*) [ "$db" = "issuer" ] && echo '1' || exit 1 ;;
        *"count(*) from public.list_vc_plan where vc_plan_id='vcplanface0000000001'"*) [ "$db" = "tas" ] && echo '1' || exit 1 ;;
        *"count(*) from public.entity"*) [ "$db" = "tas" ] && echo '5' || exit 1 ;;
        *"count(*) from public.issuer"*) [ "$db" = "issuer" ] && echo '1' || exit 1 ;;
        *"count(*) from public.cas"*) [ "$db" = "cas" ] && echo '1' || exit 1 ;;
        *"count(*) from public.ca"*) exit 1 ;;
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
[ "${FAKE_ACTIVE_SERVICE:-}" = "$2" ] && exit 0
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
unset OPENDID_POSTGRES_USER OPENDID_DB_USER OPENDID_POSTGRES_DB OPENDID_DB_NAME
export FAKE_EXPECT_PGUSER="omn"
export FAKE_EXPECT_PGDB="omn"
export FAKE_CONSUME_STDIN=1
mkdir -p "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME" "$FAKE_VOLUMES/$OPENDID_BESU_VOLUME" \
  "$OPENDID_SECRETS_DIR/TA" "$OPENDID_SECRETS_DIR/Issuer" "$OPENDID_SECRETS_DIR/CA" "$OPENDID_CONFIG_DIR"
printf 'besu source\n' >"$FAKE_VOLUMES/$OPENDID_BESU_VOLUME/block"
printf 'wallet-secret\n' >"$OPENDID_SECRETS_DIR/TA/tas.wallet"
printf 'did-secret\n' >"$OPENDID_SECRETS_DIR/Issuer/issuer.did"
printf 'chain-secret\n' >"$OPENDID_SECRETS_DIR/CA/blockchain.properties"
printf 'besu-secret\n' >"$OPENDID_SECRETS_DIR/CA/besu.dat"
adversarial=$'bad\n--checkpoint-action=exec=touch SHOULD_NOT_EXIST.wallet'
printf 'adversarial-secret\n' >"$OPENDID_SECRETS_DIR/CA/$adversarial"
printf 'leading-secret\n' >"$OPENDID_SECRETS_DIR/CA/-leading.wallet"
printf 'unrelated-secret\n' >"$OPENDID_SECRETS_DIR/CA/unrelated.txt"
printf 'config-secret\n' >"$OPENDID_CONFIG_DIR/ta.yml"
before_hash=$(find "$FAKE_VOLUMES" "$OPENDID_ROOT" -type f -exec shasum -a 256 {} + | sort)

symlink_target="$tmp/symlink-target"
mkdir -p "$symlink_target"
printf 'keep me\n' >"$symlink_target/existing"
ln -s "$symlink_target" "$tmp/symlink-out"
before_symlink_hash=$(find "$symlink_target" -type f -exec shasum -a 256 {} + | sort)
if "$EXPORT" "$tmp/symlink-out" >"$tmp/opendid-export-symlink.out" 2>&1; then
  bad 'export refuses symlink output path'
else
  ok 'export refuses symlink output path'
fi
after_symlink_hash=$(find "$symlink_target" -type f -exec shasum -a 256 {} + | sort)
[ "$before_symlink_hash" = "$after_symlink_hash" ] && ok 'symlink target unchanged' || bad 'symlink target unchanged'

nonempty="$tmp/nonempty"
mkdir -p "$nonempty"
printf x >"$nonempty/existing"
if "$EXPORT" "$nonempty" >"$tmp/opendid-export-overwrite.out" 2>&1; then
  bad 'export refuses nonempty output'
else
  ok 'export refuses nonempty output'
fi
if [ ! -s "$FAKE_LOG" ]; then
  ok 'overwrite refusal happens before docker/systemctl'
else
  bad 'overwrite refusal happens before docker/systemctl'
fi

active_out="$tmp/active-out"
FAKE_ACTIVE_SERVICE=opendid-tas "$EXPORT" "$active_out" >"$tmp/opendid-export-active.out" 2>&1 \
  && bad 'export refuses active systemd service' || ok 'export refuses active systemd service'
[ ! -e "$active_out/postgres.dump.sql" ] && ok 'active service refusal creates no dump' || bad 'active service refusal creates no dump'

besu_running_out="$tmp/besu-running-out"
FAKE_BESU_RUNNING=true "$EXPORT" "$besu_running_out" >"$tmp/opendid-export-besu-running.out" 2>&1 \
  && bad 'export refuses running Besu' || ok 'export refuses running Besu'
[ ! -e "$besu_running_out/postgres.dump.sql" ] && ok 'running Besu refusal creates no dump' || bad 'running Besu refusal creates no dump'

out="$tmp/out"
export OPENDID_TEST_OUT="$out"
"$EXPORT" "$out" >"$tmp/export.out"
want_file "$out/postgres.dump.sql" 'pg_dumpall file created'
want_file "$out/besu-data.tar" 'Besu archive created'
want_file "$out/opendid-files.tar" 'wallet DID blockchain archive created'
want_file "$out/SHA256SUMS" 'checksum manifest created'
want_grep '^holder_data=missing$' "$out/EXPORT-MANIFEST.txt" 'holder missing recorded'
want_grep 'postgres.dump.sql$' "$out/SHA256SUMS" 'dump checksum recorded'
want_grep 'besu-data.tar$' "$out/SHA256SUMS" 'Besu checksum recorded'
want_mode_600 "$out/postgres.dump.sql" 'dump permission is 0600'
want_mode_600 "$out/besu-data.tar" 'Besu archive permission is 0600'
want_mode_600 "$out/opendid-files.tar" 'files archive permission is 0600'
want_tar_has "$out/opendid-files.tar" '(^|/)tas\.wallet$' 'wallet included in file archive'
want_tar_has "$out/opendid-files.tar" '(^|/)issuer\.did$' 'DID included in file archive'
want_tar_has "$out/opendid-files.tar" '(^|/)blockchain\.properties$' 'blockchain properties included in file archive'
want_tar_has "$out/opendid-files.tar" '(^|/)besu\.dat$' 'besu.dat included in file archive'
want_tar_lacks "$out/opendid-files.tar" '(^|/)ta\.yml$|(^|/)unrelated\.txt$' 'unrelated config/secret excluded from file archive'
extract="$tmp/extract"
mkdir -p "$extract"
tar -xf "$out/opendid-files.tar" -C "$extract"
[ -f "$extract/secrets/CA/$adversarial" ] && ok 'newline adversarial wallet archived verbatim' || bad 'newline adversarial wallet archived verbatim'
[ -f "$extract/secrets/CA/-leading.wallet" ] && ok 'leading-dash wallet archived verbatim' || bad 'leading-dash wallet archived verbatim'
[ ! -e "$out/SHOULD_NOT_EXIST.wallet" ] && [ ! -e "$OPENDID_ROOT/SHOULD_NOT_EXIST.wallet" ] && ok 'adversarial filename does not inject extra file' || bad 'adversarial filename does not inject extra file'
want_no_grep 'wallet-secret|did-secret|chain-secret|config-secret' "$tmp/export.out" 'export does not print secrets'
after_hash=$(find "$FAKE_VOLUMES" "$OPENDID_ROOT" -type f -exec shasum -a 256 {} + | sort)
[ "$before_hash" = "$after_hash" ] && ok 'source files unchanged' || bad 'source files unchanged'

"$INVENTORY" >"$tmp/inventory.out"
want_grep 'postgres_container=present' "$tmp/inventory.out" 'inventory reports postgres presence'
want_grep 'db=omn ' "$tmp/inventory.out" 'inventory includes first DB'
want_grep 'db=holder ' "$tmp/inventory.out" 'inventory includes last DB despite stdin-consuming docker exec'
want_grep 'postgres_version=16.4' "$tmp/inventory.out" 'inventory resolves postgres env user/db'
want_grep 'facelicense_namespace_rows=1' "$tmp/inventory.out" 'inventory counts FaceLicense namespace rows'
want_grep 'facelicense_schema_rows=1' "$tmp/inventory.out" 'inventory counts FaceLicense schema rows'
want_grep 'facelicense_plan_rows=2' "$tmp/inventory.out" 'inventory counts FaceLicense plan rows'
want_grep 'entity_rows=5' "$tmp/inventory.out" 'inventory counts entity rows'
want_grep 'issuer_rows=1' "$tmp/inventory.out" 'inventory counts issuer rows'
want_grep 'cas_rows=1' "$tmp/inventory.out" 'inventory counts CAS rows'
want_one_line_value wallet_files 3 "$tmp/inventory.out" 'wallet count is exact single line'
want_one_line_value did_files 1 "$tmp/inventory.out" 'DID count is exact single line'
want_one_line_value blockchain_config_files 1 "$tmp/inventory.out" 'blockchain config count is exact single line'
want_one_line_value app_config_files 1 "$tmp/inventory.out" 'app config count is exact single line'
want_grep 'holder_data=missing' "$tmp/inventory.out" 'inventory reports missing holder data'
want_no_grep 'wallet-secret|did-secret|chain-secret|config-secret' "$tmp/inventory.out" 'inventory does not print secrets'

FAKE_SQL_FAIL=namespace "$INVENTORY" >"$tmp/inventory-sql-fail.out"
want_grep 'facelicense_namespace_rows=unknown' "$tmp/inventory-sql-fail.out" 'SQL failure reports unknown, not zero'

exit "$fail"
