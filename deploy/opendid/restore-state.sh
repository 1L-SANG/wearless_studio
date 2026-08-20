#!/usr/bin/env bash
set -euo pipefail
umask 077

POSTGRES_CONTAINER=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
BESU_CONTAINER=${OPENDID_BESU_CONTAINER:-opendid-besu-node}
POSTGRES_VOLUME=${OPENDID_POSTGRES_VOLUME:-postgre_opendid_data}
BESU_VOLUME=${OPENDID_BESU_VOLUME:-besu_opendid_data}
OPENDID_ROOT=${OPENDID_ROOT:-/opt/opendid}
HOLDER_DATA_DIR=${OPENDID_HOLDER_DATA_DIR:-$OPENDID_ROOT/state/holder}
COMPOSE_FILE=${OPENDID_COMPOSE_FILE:-$OPENDID_ROOT/infra.compose.yml}
ENV_FILE=${OPENDID_ENV_FILE:-$OPENDID_ROOT/opendid.env}
POSTGRES_USER=${OPENDID_POSTGRES_USER:-}
POSTGRES_DB=${OPENDID_POSTGRES_DB:-}
POSTGRES_PASSWORD=${OPENDID_POSTGRES_PASSWORD:-}
RESTORE_USER=${OPENDID_RESTORE_POSTGRES_USER:-opendid_restore_admin}
OWNER=${OPENDID_OWNER:-opendid}
GROUP=${OPENDID_GROUP:-opendid}

die() { printf 'REFUSING: %s\n' "$*" >&2; exit 2; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found"; }
sha256_value() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}
regular_file() { [ -f "$1" ] && [ ! -L "$1" ]; }
checksum_for() {
  local name=$1 count expected actual
  regular_file "$ARCHIVE/$name" || die "missing or unsafe archive file: $name"
  count=$(awk -v name="$name" '$2 == name { count++ } END { print count + 0 }' "$SUMS")
  [ "$count" = 1 ] || die "checksum entry must occur once: $name"
  expected=$(awk -v name="$name" '$2 == name { print $1 }' "$SUMS")
  [ "${#expected}" = 64 ] && printf '%s\n' "$expected" | grep -Eq '^[0-9a-fA-F]{64}$' \
    || die "invalid checksum entry: $name"
  actual=$(sha256_value "$ARCHIVE/$name")
  [ "$actual" = "$expected" ] || die "checksum mismatch: $name"
}
validate_tar() {
  local archive=$1 kind=$2 entry normalized
  local names="$WORK/tar-names" verbose="$WORK/tar-verbose"
  tar -tf "$archive" >"$names" || die "invalid tar archive: $(basename "$archive")"
  tar -tvf "$archive" >"$verbose" || die "invalid tar metadata: $(basename "$archive")"
  awk 'substr($0, 1, 1) != "-" && substr($0, 1, 1) != "d" { exit 1 }' "$verbose" \
    || die "links or special files are not allowed: $(basename "$archive")"
  while IFS= read -r entry || [ -n "$entry" ]; do
    normalized=${entry#./}
    [ -n "$normalized" ] || continue
    case "$normalized" in
      /*|..|../*|*/..|*/../*) die "unsafe archive path: $entry" ;;
    esac
    if [ "$kind" = opendid ]; then
      case "$normalized" in
        .|secrets|secrets/|config|config/|secrets/*/|config/*/) : ;;
        secrets/*|config/*)
          case "$(basename "$normalized")" in
            *.wallet|*.zkpwallet|*.did|blockchain.properties|besu.dat) : ;;
            *) die "unexpected OpenDID archive entry: $entry" ;;
          esac
          ;;
        *) die "OpenDID archive path is outside secrets/config: $entry" ;;
      esac
    fi
  done <"$names"
}
volume_exists() { docker volume inspect "$1" >/dev/null 2>&1; }
container_absent() { ! docker inspect -f '{{.State.Running}}' "$1" >/dev/null 2>&1; }
path_has_no_symlink() {
  local path=$1 current=/ rest part
  case "$path" in /*) rest=${path#/} ;; *) return 1 ;; esac
  while [ -n "$rest" ]; do
    case "$rest" in
      */*) part=${rest%%/*}; rest=${rest#*/} ;;
      *) part=$rest; rest='' ;;
    esac
    [ -n "$part" ] || continue
    current=${current%/}/$part
    [ ! -L "$current" ] || return 1
  done
}

case "$#" in
  1) APPLY=0 ;;
  2) [ "$2" = --apply ] || die "usage: $0 <archive-dir> [--apply]"; APPLY=1 ;;
  *) die "usage: $0 <archive-dir> [--apply]" ;;
esac

ARCHIVE_INPUT=$1
[ ! -L "$ARCHIVE_INPUT" ] && [ -d "$ARCHIVE_INPUT" ] || die "archive directory is missing or a symlink: $ARCHIVE_INPUT"
ARCHIVE=$(cd -P "$ARCHIVE_INPUT" && pwd)
SUMS="$ARCHIVE/SHA256SUMS"
regular_file "$SUMS" || die "missing or unsafe SHA256SUMS"
need tar
need awk
need grep

WORK=$(mktemp -d "${TMPDIR:-/tmp}/opendid-restore.XXXXXX")
POSTGRES_STARTED=0
CREATED_PG_VOLUME=0
CREATED_BESU_VOLUME=0
FILES_INSTALLED=0
HOLDER_INSTALLED=0
SUCCESS=0
cleanup() {
  status=$?
  if [ "$POSTGRES_STARTED" = 1 ]; then
    OPENDID_POSTGRES_USER="$RESTORE_USER" OPENDID_POSTGRES_DB=postgres \
      docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" stop postgres-opendid >/dev/null 2>&1 || true
  fi
  if [ "$SUCCESS" != 1 ]; then
    if [ "$FILES_INSTALLED" = 1 ]; then
      while IFS= read -r -d '' file; do
        rel=${file#$files_stage/}
        rm -f -- "$OPENDID_ROOT/$rel"
      done < <(find "$files_stage" -type f -print0)
    fi
    if [ "$HOLDER_INSTALLED" = 1 ]; then
      while IFS= read -r -d '' file; do
        rel=${file#$holder_stage/}
        rm -f -- "$HOLDER_DATA_DIR/$rel"
      done < <(find "$holder_stage" -type f -print0)
    fi
    if [ "$CREATED_PG_VOLUME" = 1 ]; then
      OPENDID_POSTGRES_USER="$RESTORE_USER" OPENDID_POSTGRES_DB=postgres \
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" rm -f -s postgres-opendid >/dev/null 2>&1 || true
      docker volume rm "$POSTGRES_VOLUME" >/dev/null 2>&1 || true
    fi
    [ "$CREATED_BESU_VOLUME" = 1 ] && docker volume rm "$BESU_VOLUME" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
  trap - EXIT
  exit "$status"
}
trap cleanup EXIT

checksum_for EXPORT-MANIFEST.txt
holder_count=$(grep -Ec '^holder_data=(present|missing)$' "$ARCHIVE/EXPORT-MANIFEST.txt" || true)
[ "$holder_count" = 1 ] || die "manifest must contain one holder_data state"
HOLDER_STATE=$(sed -n 's/^holder_data=//p' "$ARCHIVE/EXPORT-MANIFEST.txt")

expected_names='EXPORT-MANIFEST.txt postgres.dump.sql besu-data.tar opendid-files.tar'
[ "$HOLDER_STATE" = missing ] || expected_names="$expected_names holder-data.tar"
for name in $expected_names; do checksum_for "$name"; done

sum_count=$(awk 'NF { count++ } END { print count + 0 }' "$SUMS")
expected_count=4
[ "$HOLDER_STATE" = missing ] || expected_count=5
[ "$sum_count" = "$expected_count" ] || die "SHA256SUMS contains unexpected entries"
while read -r _hash name extra; do
  [ -n "${name:-}" ] || continue
  [ -z "${extra:-}" ] || die "invalid SHA256SUMS line"
  case " $expected_names " in *" $name "*) : ;; *) die "unexpected checksum target: $name" ;; esac
done <"$SUMS"
if [ "$HOLDER_STATE" = missing ]; then
  [ ! -e "$ARCHIVE/holder-data.tar" ] && [ ! -L "$ARCHIVE/holder-data.tar" ] \
    || die "manifest says Holder data is missing but archive exists"
fi

validate_tar "$ARCHIVE/besu-data.tar" data
validate_tar "$ARCHIVE/opendid-files.tar" opendid
[ "$HOLDER_STATE" = missing ] || validate_tar "$ARCHIVE/holder-data.tar" data

if [ "$APPLY" = 0 ]; then
  printf 'mode=dry-run\n'
  printf 'archive_dir=%s\n' "$ARCHIVE"
  printf 'postgres_volume=%s\n' "$POSTGRES_VOLUME"
  printf 'besu_volume=%s\n' "$BESU_VOLUME"
  printf 'opendid_root=%s\n' "$OPENDID_ROOT"
  printf 'holder_data=%s\n' "$HOLDER_STATE"
  printf 'plan=verify checksums, require fresh target, restore Besu/PostgreSQL/OpenDID files\n'
  exit 0
fi

need docker
need install
need id
need getent
[ -n "$POSTGRES_USER" ] || die "OPENDID_POSTGRES_USER is required for --apply"
[ -n "$POSTGRES_DB" ] || die "OPENDID_POSTGRES_DB is required for --apply"
[ -n "$POSTGRES_PASSWORD" ] || die "OPENDID_POSTGRES_PASSWORD is required for --apply"
printf '%s\n' "$RESTORE_USER" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$' || die "unsafe restore PostgreSQL user"
if grep -Eq "^(CREATE|ALTER) ROLE ($RESTORE_USER|\"$RESTORE_USER\")([ ;]|$)" "$ARCHIVE/postgres.dump.sql"; then
  die "restore bootstrap role already exists in source dump: $RESTORE_USER"
fi
regular_file "$COMPOSE_FILE" || die "missing or unsafe compose file: $COMPOSE_FILE"
regular_file "$ENV_FILE" || die "missing or unsafe environment file: $ENV_FILE"
id "$OWNER" >/dev/null 2>&1 || die "runtime owner does not exist: $OWNER"
getent group "$GROUP" >/dev/null 2>&1 || die "runtime group does not exist: $GROUP"
container_absent "$POSTGRES_CONTAINER" || die "target container already exists: $POSTGRES_CONTAINER"
container_absent "$BESU_CONTAINER" || die "target container already exists: $BESU_CONTAINER"

volume_exists "$POSTGRES_VOLUME" && die "PostgreSQL target volume already exists: $POSTGRES_VOLUME"
volume_exists "$BESU_VOLUME" && die "Besu target volume already exists: $BESU_VOLUME"
for identity_root in "$OPENDID_ROOT/secrets" "$OPENDID_ROOT/config"; do
  if [ -d "$identity_root" ] && find "$identity_root" \( -type f -o -type l \) \
    \( -name '*.wallet' -o -name '*.zkpwallet' -o -name '*.did' -o -name 'blockchain.properties' -o -name 'besu.dat' \) \
    -print -quit | grep -q .; then
    die "stale identity artifact exists under $identity_root"
  fi
done

files_stage="$WORK/opendid-files"
mkdir -p "$files_stage"
tar -C "$files_stage" -xf "$ARCHIVE/opendid-files.tar"
while IFS= read -r -d '' file; do
  rel=${file#$files_stage/}
  target="$OPENDID_ROOT/$rel"
  path_has_no_symlink "$target" || die "target path is relative or contains a symlink: $target"
  [ ! -e "$target" ] && [ ! -L "$target" ] || die "target file already exists: $target"
done < <(find "$files_stage" -type f -print0)

holder_stage=''
if [ "$HOLDER_STATE" = present ]; then
  path_has_no_symlink "$HOLDER_DATA_DIR" || die "Holder target is relative or contains a symlink: $HOLDER_DATA_DIR"
  if [ -e "$HOLDER_DATA_DIR" ]; then
    [ -d "$HOLDER_DATA_DIR" ] || die "Holder target is not a directory: $HOLDER_DATA_DIR"
    [ -z "$(find "$HOLDER_DATA_DIR" -mindepth 1 -print -quit)" ] || die "Holder target is not empty: $HOLDER_DATA_DIR"
  fi
  holder_stage="$WORK/holder-data"
  mkdir -p "$holder_stage"
  tar -C "$holder_stage" -xf "$ARCHIVE/holder-data.tar"
fi

docker volume create "$POSTGRES_VOLUME" >/dev/null
CREATED_PG_VOLUME=1
docker volume create "$BESU_VOLUME" >/dev/null
CREATED_BESU_VOLUME=1
OPENDID_POSTGRES_USER="$RESTORE_USER" OPENDID_POSTGRES_DB=postgres \
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d postgres-opendid
POSTGRES_STARTED=1
ready=0
attempt=0
while [ "$attempt" -lt 30 ]; do
  if docker exec "$POSTGRES_CONTAINER" pg_isready -U "$RESTORE_USER" -d postgres >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done
[ "$ready" = 1 ] || die "PostgreSQL did not become ready"
docker exec -i "$POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 -U "$RESTORE_USER" -d postgres \
  <"$ARCHIVE/postgres.dump.sql"
lock_result=$(docker exec "$POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 -At -U "$RESTORE_USER" -d postgres \
  -c "ALTER ROLE \"$RESTORE_USER\" NOLOGIN PASSWORD NULL; SELECT rolcanlogin::text || ':' || (rolpassword IS NULL)::text FROM pg_authid WHERE rolname='$RESTORE_USER';")
printf '%s\n' "$lock_result" | grep -qx 'false:true' || die "restore bootstrap role was not locked"
docker exec "$POSTGRES_CONTAINER" psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c 'SELECT 1;' | grep -qx 1 || die "restored PostgreSQL user cannot connect"
OPENDID_POSTGRES_USER="$RESTORE_USER" OPENDID_POSTGRES_DB=postgres \
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" stop postgres-opendid
POSTGRES_STARTED=0
OPENDID_POSTGRES_USER="$RESTORE_USER" OPENDID_POSTGRES_DB=postgres \
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" rm -f postgres-opendid

docker run --rm -v "$BESU_VOLUME:/target" -v "$ARCHIVE:/archive:ro" alpine:3.20 \
  tar -C /target -xf /archive/besu-data.tar

while IFS= read -r -d '' file; do
  rel=${file#$files_stage/}
  target="$OPENDID_ROOT/$rel"
  install -d -o "$OWNER" -g "$GROUP" -m 700 "$(dirname "$target")"
  FILES_INSTALLED=1
  install -o "$OWNER" -g "$GROUP" -m 600 "$file" "$target"
done < <(find "$files_stage" -type f -print0)

if [ "$HOLDER_STATE" = present ]; then
  while IFS= read -r -d '' dir; do
    rel=${dir#$holder_stage/}
    [ "$rel" = "$dir" ] && rel=''
    install -d -o "$OWNER" -g "$GROUP" -m 700 "$HOLDER_DATA_DIR${rel:+/$rel}"
  done < <(find "$holder_stage" -type d -print0)
  while IFS= read -r -d '' file; do
    rel=${file#$holder_stage/}
    target="$HOLDER_DATA_DIR/$rel"
    HOLDER_INSTALLED=1
    install -o "$OWNER" -g "$GROUP" -m 600 "$file" "$target"
  done < <(find "$holder_stage" -type f -print0)
fi

SUCCESS=1
printf 'mode=applied\n'
printf 'holder_data=%s\n' "$HOLDER_STATE"
