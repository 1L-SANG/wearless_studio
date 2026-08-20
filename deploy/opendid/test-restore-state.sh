#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXPORT="$ROOT/deploy/opendid/export-state.sh"
RESTORE="$ROOT/deploy/opendid/restore-state.sh"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/opendid-restore-test.XXXXXX")"
tmp="$(cd -P "$tmp" && pwd)"
trap 'rm -rf "$tmp"' EXIT

fakebin="$tmp/bin"
mkdir -p "$fakebin"

fail=0
ok() { printf 'PASS %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; fail=$((fail + 1)); }
want_file() { [ -f "$1" ] && ok "$2" || bad "$2"; }
want_missing() { [ ! -e "$1" ] && ok "$2" || bad "$2"; }
want_grep() { grep -Eq "$1" "$2" && ok "$3" || bad "$3"; }
want_mode_600() {
  local mode
  mode=$(stat -f %Lp "$1" 2>/dev/null || stat -c %a "$1")
  [ "$mode" = "600" ] && ok "$2" || bad "$2 (got $mode)"
}
hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}
tree_hash() {
  [ -d "$1" ] || return 0
  find "$1" -type f | sort | while IFS= read -r file; do
    printf '%s  %s\n' "$(hash_file "$file")" "${file#$1/}"
  done
}
archive_hash() {
  tree_hash "$1"
}
write_sums() {
  local archive=$1 holder
  holder=$(sed -n 's/^holder_data=//p' "$archive/EXPORT-MANIFEST.txt")
  : >"$archive/SHA256SUMS"
  for name in EXPORT-MANIFEST.txt postgres.dump.sql besu-data.tar opendid-files.tar; do
    printf '%s  %s\n' "$(hash_file "$archive/$name")" "$name" >>"$archive/SHA256SUMS"
  done
  if [ "$holder" = present ]; then
    printf '%s  holder-data.tar\n' "$(hash_file "$archive/holder-data.tar")" >>"$archive/SHA256SUMS"
  fi
  chmod 600 "$archive/SHA256SUMS"
}

cat >"$fakebin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'docker' >>"${FAKE_LOG:?}"
printf ' %q' "$@" >>"$FAKE_LOG"
printf '\n' >>"$FAKE_LOG"

case "$1" in
  inspect)
    [ "$2" = "-f" ] || exit 9
    template=$3
    name=$4
    case "$template" in
      '{{.State.Running}}')
        case " ${FAKE_RUNNING_CONTAINERS:-} " in *" $name "*) echo true ;; *) echo false ;; esac
        ;;
      '{{range .Config.Env}}{{println .}}{{end}}')
        printf 'POSTGRES_USER=%s\nPOSTGRES_DB=%s\n' "${FAKE_SOURCE_DB_USER:-omn}" "${FAKE_SOURCE_DB_NAME:-omn}"
        ;;
      *) exit 9 ;;
    esac
    ;;
  volume)
    case "$2" in
      inspect)
        [ -d "${FAKE_VOLUMES:?}/$3" ] || exit 1
        printf '%s\n' "$FAKE_VOLUMES/$3"
        ;;
      create)
        mkdir -p "$FAKE_VOLUMES/$3"
        printf '%s\n' "$3"
        ;;
      *) exit 9 ;;
    esac
    ;;
  exec)
    shift
    interactive=0
    if [ "${1:-}" = -i ]; then interactive=1; shift; fi
    container=$1
    shift
    case "${1:-}" in
      pg_dumpall)
        cat "${FAKE_DB_FIXTURE:?}"
        ;;
      pg_isready)
        exit 0
        ;;
      psql)
        if [ "$interactive" = 1 ]; then
          cat >"${FAKE_VOLUMES:?}/${OPENDID_POSTGRES_VOLUME:?}/restored.sql"
        else
          case " $* " in *' -c DROP ROLE '*) : ;; *) exit 9 ;; esac
        fi
        ;;
      *) printf 'unexpected docker exec for %s: %s\n' "$container" "$*" >&2; exit 9 ;;
    esac
    ;;
  run)
    src_volume=''
    target_volume=''
    out_dir=''
    archive_dir=''
    for arg in "$@"; do
      case "$arg" in
        *:/source:ro) src_volume=${arg%%:*} ;;
        *:/target) target_volume=${arg%%:*} ;;
        *:/out) out_dir=${arg%:/out} ;;
        *:/archive:ro) archive_dir=${arg%:/archive:ro} ;;
      esac
    done
    if [ -n "$src_volume" ] && [ -n "$out_dir" ]; then
      tar -C "$FAKE_VOLUMES/$src_volume" -cf "$out_dir/besu-data.tar" .
    elif [ -n "$src_volume" ]; then
      [ -z "$(find "$FAKE_VOLUMES/$src_volume" -mindepth 1 -print -quit)" ]
    elif [ -n "$target_volume" ] && [ -n "$archive_dir" ]; then
      tar -C "$FAKE_VOLUMES/$target_volume" -xf "$archive_dir/besu-data.tar"
    else
      printf 'unexpected docker run: %s\n' "$*" >&2
      exit 9
    fi
    ;;
  compose)
    exit 0
    ;;
  *) printf 'unexpected docker command: %s\n' "$*" >&2; exit 9 ;;
esac
SH
chmod +x "$fakebin/docker"

cat >"$fakebin/systemctl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[ "$1" = is-active ] || exit 9
exit 3
SH
chmod +x "$fakebin/systemctl"

export PATH="$fakebin:$PATH"
export FAKE_LOG="$tmp/docker.log"
export FAKE_VOLUMES="$tmp/volumes"
export FAKE_DB_FIXTURE="$tmp/database.sql"
printf '%s\n' '-- FaceLicense fixture' 'CREATE TABLE fixture(id integer);' 'INSERT INTO fixture VALUES (7);' >"$FAKE_DB_FIXTURE"
mkdir -p "$FAKE_VOLUMES"

make_archive() {
  local label=$1 holder=$2
  local source_root="$tmp/source-$label"
  local source_pg="source_${label}_pg"
  local source_besu="source_${label}_besu"
  local archive="$tmp/archive-$label"
  mkdir -p "$FAKE_VOLUMES/$source_pg" "$FAKE_VOLUMES/$source_besu" \
    "$source_root/secrets/TA" "$source_root/secrets/Issuer" "$source_root/config"
  printf 'postgres source marker\n' >"$FAKE_VOLUMES/$source_pg/PG_VERSION"
  printf 'besu-%s\n' "$label" >"$FAKE_VOLUMES/$source_besu/chain.db"
  printf 'wallet-%s\n' "$label" >"$source_root/secrets/TA/tas.wallet"
  printf 'did-%s\n' "$label" >"$source_root/secrets/Issuer/issuer.did"
  printf 'contract-%s\n' "$label" >"$source_root/config/blockchain.properties"
  if [ "$holder" = present ]; then
    mkdir -p "$source_root/state/holder/model-7"
    printf 'holder-%s\n' "$label" >"$source_root/state/holder/model-7/wallet.json"
  fi

  OPENDID_POSTGRES_CONTAINER="source-$label-postgres" \
  OPENDID_BESU_CONTAINER="source-$label-besu" \
  OPENDID_POSTGRES_VOLUME="$source_pg" \
  OPENDID_BESU_VOLUME="$source_besu" \
  OPENDID_ROOT="$source_root" \
  OPENDID_SECRETS_DIR="$source_root/secrets" \
  OPENDID_CONFIG_DIR="$source_root/config" \
  OPENDID_HOLDER_DATA_DIR="$source_root/state/holder" \
    "$EXPORT" "$archive" >/dev/null
  printf '%s\n' "$archive"
}

set_target() {
  local label=$1
  export OPENDID_POSTGRES_CONTAINER="target-$label-postgres"
  export OPENDID_BESU_CONTAINER="target-$label-besu"
  export OPENDID_POSTGRES_VOLUME="target_${label}_pg"
  export OPENDID_BESU_VOLUME="target_${label}_besu"
  export OPENDID_ROOT="$tmp/target-$label"
  export OPENDID_SECRETS_DIR="$OPENDID_ROOT/secrets"
  export OPENDID_HOLDER_DATA_DIR="$OPENDID_ROOT/state/holder"
  export OPENDID_COMPOSE_FILE="$tmp/infra.compose.yml"
  export OPENDID_ENV_FILE="$tmp/opendid.env"
  export OPENDID_POSTGRES_USER=omn
  export OPENDID_POSTGRES_DB=omn
  export OPENDID_POSTGRES_PASSWORD=test-only
  : >"$OPENDID_COMPOSE_FILE"
  : >"$OPENDID_ENV_FILE"
}

archive_missing=$(make_archive missing missing)

set_target checksum
cp -R "$archive_missing" "$tmp/archive-checksum"
printf '\n-- tampered\n' >>"$tmp/archive-checksum/postgres.dump.sql"
: >"$FAKE_LOG"
before_bad_archive=$(archive_hash "$tmp/archive-checksum")
if "$RESTORE" "$tmp/archive-checksum" --apply >"$tmp/checksum.out" 2>&1; then
  bad 'checksum mismatch is refused'
else
  ok 'checksum mismatch is refused'
fi
[ ! -s "$FAKE_LOG" ] && ok 'checksum verification precedes Docker access' || bad 'checksum verification precedes Docker access'
want_missing "$OPENDID_ROOT" 'checksum mismatch leaves target filesystem absent'
want_missing "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME" 'checksum mismatch leaves target volume absent'
[ "$before_bad_archive" = "$(archive_hash "$tmp/archive-checksum")" ] && ok 'checksum failure leaves archive unchanged' || bad 'checksum failure leaves archive unchanged'

set_target dryrun
: >"$FAKE_LOG"
before_dry_archive=$(archive_hash "$archive_missing")
"$RESTORE" "$archive_missing" >"$tmp/dryrun.out"
want_grep '^mode=dry-run$' "$tmp/dryrun.out" 'dry-run declares mode'
want_grep '^holder_data=missing$' "$tmp/dryrun.out" 'dry-run reports missing Holder state'
[ ! -s "$FAKE_LOG" ] && ok 'dry-run does not access Docker' || bad 'dry-run does not access Docker'
want_missing "$OPENDID_ROOT" 'dry-run leaves target filesystem absent'
want_missing "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME" 'dry-run leaves target volume absent'
[ "$before_dry_archive" = "$(archive_hash "$archive_missing")" ] && ok 'dry-run leaves archive unchanged' || bad 'dry-run leaves archive unchanged'

set_target nonempty
mkdir -p "$FAKE_VOLUMES/$OPENDID_BESU_VOLUME"
printf 'keep\n' >"$FAKE_VOLUMES/$OPENDID_BESU_VOLUME/existing"
before_nonempty=$(tree_hash "$FAKE_VOLUMES/$OPENDID_BESU_VOLUME")
if "$RESTORE" "$archive_missing" --apply >"$tmp/nonempty.out" 2>&1; then
  bad 'nonempty target volume is refused'
else
  ok 'nonempty target volume is refused'
fi
[ "$before_nonempty" = "$(tree_hash "$FAKE_VOLUMES/$OPENDID_BESU_VOLUME")" ] && ok 'nonempty volume remains unchanged' || bad 'nonempty volume remains unchanged'
want_missing "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME" 'nonempty preflight creates no other volume'
want_missing "$OPENDID_ROOT" 'nonempty preflight creates no target files'

set_target existing
mkdir -p "$OPENDID_ROOT/secrets/TA"
printf 'keep existing\n' >"$OPENDID_ROOT/secrets/TA/tas.wallet"
before_existing=$(tree_hash "$OPENDID_ROOT")
if "$RESTORE" "$archive_missing" --apply >"$tmp/existing.out" 2>&1; then
  bad 'existing target secret is refused'
else
  ok 'existing target secret is refused'
fi
[ "$before_existing" = "$(tree_hash "$OPENDID_ROOT")" ] && ok 'existing target secret remains unchanged' || bad 'existing target secret remains unchanged'
want_missing "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME" 'existing-file preflight creates no volume'

cp -R "$archive_missing" "$tmp/archive-traversal"
python3 - "$tmp/archive-traversal/opendid-files.tar" <<'PY'
import io
import tarfile
import sys
with tarfile.open(sys.argv[1], "w") as archive:
    info = tarfile.TarInfo("../escaped.wallet")
    payload = b"escape\n"
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))
PY
write_sums "$tmp/archive-traversal"
set_target traversal
if "$RESTORE" "$tmp/archive-traversal" --apply >"$tmp/traversal.out" 2>&1; then
  bad 'path-traversing archive is refused'
else
  ok 'path-traversing archive is refused'
fi
want_missing "$OPENDID_ROOT" 'path traversal creates no target files'
want_missing "$tmp/escaped.wallet" 'path traversal escapes nowhere'
want_missing "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME" 'path traversal creates no volume'

cp -R "$archive_missing" "$tmp/archive-symlink"
symlink_stage="$tmp/symlink-stage"
mkdir -p "$symlink_stage/secrets/TA"
ln -s ../../outside "$symlink_stage/secrets/TA/tas.wallet"
tar -C "$symlink_stage" -cf "$tmp/archive-symlink/opendid-files.tar" .
write_sums "$tmp/archive-symlink"
set_target symlink
if "$RESTORE" "$tmp/archive-symlink" --apply >"$tmp/symlink.out" 2>&1; then
  bad 'symlink archive entry is refused'
else
  ok 'symlink archive entry is refused'
fi
want_missing "$OPENDID_ROOT" 'symlink archive creates no target files'
want_missing "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME" 'symlink archive creates no volume'

round_trip() {
  local label=$1 holder=$2 archive source_root source_besu before_archive
  archive=$(make_archive "$label" "$holder")
  source_root="$tmp/source-$label"
  source_besu="$FAKE_VOLUMES/source_${label}_besu"
  before_archive=$(archive_hash "$archive")
  set_target "$label"
  "$RESTORE" "$archive" --apply >"$tmp/apply-$label.out"

  cmp -s "$FAKE_DB_FIXTURE" "$FAKE_VOLUMES/$OPENDID_POSTGRES_VOLUME/restored.sql" \
    && ok "$label PostgreSQL dump round-trips" || bad "$label PostgreSQL dump round-trips"
  [ "$(tree_hash "$source_besu")" = "$(tree_hash "$FAKE_VOLUMES/$OPENDID_BESU_VOLUME")" ] \
    && ok "$label Besu data round-trips" || bad "$label Besu data round-trips"
  cmp -s "$source_root/secrets/TA/tas.wallet" "$OPENDID_ROOT/secrets/TA/tas.wallet" \
    && ok "$label wallet round-trips" || bad "$label wallet round-trips"
  cmp -s "$source_root/secrets/Issuer/issuer.did" "$OPENDID_ROOT/secrets/Issuer/issuer.did" \
    && ok "$label DID round-trips" || bad "$label DID round-trips"
  cmp -s "$source_root/config/blockchain.properties" "$OPENDID_ROOT/config/blockchain.properties" \
    && ok "$label blockchain config round-trips" || bad "$label blockchain config round-trips"
  want_mode_600 "$OPENDID_ROOT/secrets/TA/tas.wallet" "$label wallet restored as 0600"
  want_mode_600 "$OPENDID_ROOT/secrets/Issuer/issuer.did" "$label DID restored as 0600"
  want_mode_600 "$OPENDID_ROOT/config/blockchain.properties" "$label blockchain config restored as 0600"
  if [ "$holder" = present ]; then
    cmp -s "$source_root/state/holder/model-7/wallet.json" "$OPENDID_HOLDER_DATA_DIR/model-7/wallet.json" \
      && ok "$label Holder data round-trips" || bad "$label Holder data round-trips"
    want_mode_600 "$OPENDID_HOLDER_DATA_DIR/model-7/wallet.json" "$label Holder data restored as 0600"
  else
    want_missing "$OPENDID_HOLDER_DATA_DIR" "$label does not invent missing Holder data"
  fi
  [ "$before_archive" = "$(archive_hash "$archive")" ] && ok "$label source archive remains unchanged" || bad "$label source archive remains unchanged"
}

round_trip roundtrip-missing missing
round_trip roundtrip-present present

exit "$fail"
