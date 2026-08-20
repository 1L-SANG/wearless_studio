#!/usr/bin/env bash
set -euo pipefail

POSTGRES_CONTAINER=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
BESU_CONTAINER=${OPENDID_BESU_CONTAINER:-opendid-besu-node}
POSTGRES_VOLUME=${OPENDID_POSTGRES_VOLUME:-postgre_opendid_data}
BESU_VOLUME=${OPENDID_BESU_VOLUME:-besu_opendid_data}
POSTGRES_VOLUME_FALLBACKS=${OPENDID_POSTGRES_VOLUME_FALLBACKS:-postgre_postgre_opendid_data}
BESU_VOLUME_FALLBACKS=${OPENDID_BESU_VOLUME_FALLBACKS:-besu_besu_opendid_data}
POSTGRES_USER=${OPENDID_POSTGRES_USER:-${OPENDID_DB_USER:-}}
OPENDID_ROOT=${OPENDID_ROOT:-/opt/opendid}
SECRETS_DIR=${OPENDID_SECRETS_DIR:-$OPENDID_ROOT/secrets}
CONFIG_DIR=${OPENDID_CONFIG_DIR:-$OPENDID_ROOT/config}
HOLDER_DATA_DIR=${OPENDID_HOLDER_DATA_DIR:-$OPENDID_ROOT/state/holder}
APP_SERVICES=${OPENDID_APP_SERVICES:-opendid-tas opendid-issuer opendid-cas fm-holder}
WRITER_PORTS="8090 8091 8094 8100 9001"

die() { printf 'REFUSING: %s\n' "$*" >&2; exit 2; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found"; }
resolve_volume() {
  local volume=$1
  shift || true
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    printf '%s\n' "$volume"
    return 0
  fi
  for volume in "$@"; do
    if docker volume inspect "$volume" >/dev/null 2>&1; then
      printf '%s\n' "$volume"
      return 0
    fi
  done
  return 1
}
sha256_one() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi
}
container_env_value() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$POSTGRES_CONTAINER" 2>/dev/null |
    sed -n "s/^$1=//p" | head -1
}
check_writers_stopped() {
  local svc port
  if command -v systemctl >/dev/null 2>&1; then
    for svc in $APP_SERVICES; do
      if systemctl is-active "$svc" >/dev/null 2>&1; then
        die "$svc is active; stop Holder/TAS/Issuer/CAS before export"
      fi
    done
    return 0
  fi
  if command -v lsof >/dev/null 2>&1; then
    for port in $WRITER_PORTS; do
      if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        die "port $port is listening; stop Holder/TAS/Issuer/CAS before export"
      fi
    done
    return 0
  fi
  die "systemctl or lsof not found; install lsof or run on a systemd host to verify OpenDID writers are stopped"
}

[ "$#" = 1 ] || die "usage: $0 <new-empty-output-dir>"
OUT_INPUT=$1
OUT_PARENT=$(dirname -- "$OUT_INPUT")
OUT_BASE=$(basename -- "$OUT_INPUT")
[ "$OUT_BASE" != "." ] && [ "$OUT_BASE" != ".." ] || die "unsafe output path: $OUT_INPUT"
[ -d "$OUT_PARENT" ] || die "output parent does not exist: $OUT_PARENT"
[ ! -e "$OUT_INPUT" ] && [ ! -L "$OUT_INPUT" ] || die "output path already exists: $OUT_INPUT"
OUT_PARENT_REAL="$(cd -P "$OUT_PARENT" && pwd)"
OUT="$OUT_PARENT_REAL/$OUT_BASE"

need docker
need tar
mkdir -m 700 "$OUT" || die "could not create output directory: $OUT"
[ -d "$OUT" ] && [ ! -L "$OUT" ] || die "output path is not a real directory: $OUT"
OUT="$(cd -P "$OUT" && pwd)"
case "$OUT" in
  "$OUT_PARENT_REAL"/*) : ;;
  *) die "output escaped parent: $OUT" ;;
esac

check_writers_stopped

besu_running=$(docker inspect -f '{{.State.Running}}' "$BESU_CONTAINER" 2>/dev/null || echo false)
[ "$besu_running" != "true" ] || die "$BESU_CONTAINER is running; stop Besu before export"

POSTGRES_VOLUME=$(resolve_volume "$POSTGRES_VOLUME" $POSTGRES_VOLUME_FALLBACKS) || die "PostgreSQL volume not found"
BESU_VOLUME=$(resolve_volume "$BESU_VOLUME" $BESU_VOLUME_FALLBACKS) || die "Besu volume not found"
[ -n "$POSTGRES_USER" ] || POSTGRES_USER=$(container_env_value POSTGRES_USER)
POSTGRES_USER=${POSTGRES_USER:-postgres}

manifest="$OUT/EXPORT-MANIFEST.txt"
{
  printf 'postgres_container=%s\n' "$POSTGRES_CONTAINER"
  printf 'postgres_volume=%s\n' "$POSTGRES_VOLUME"
  printf 'besu_container=%s\n' "$BESU_CONTAINER"
  printf 'besu_volume=%s\n' "$BESU_VOLUME"
} >"$manifest"

dump="$OUT/postgres.dump.sql"
docker exec -i "$POSTGRES_CONTAINER" pg_dumpall -U "$POSTGRES_USER" >"$dump"
chmod 600 "$dump"

docker run --rm -v "$BESU_VOLUME:/source:ro" -v "$OUT:/out" alpine:3.20 \
  tar -C /source -cf /out/besu-data.tar .
chmod 600 "$OUT/besu-data.tar"

tmp_stage=$(mktemp -d)
cleanup() { rm -rf "$tmp_stage"; }
trap cleanup EXIT
stage_file() {
  local source=$1 rel=$2 dest="$tmp_stage/$2"
  case "$rel" in /*|..|../*|*/..|*/../*) die "unsafe staged path: $rel" ;; esac
  [ ! -e "$dest" ] && [ ! -L "$dest" ] || die "multiple source files map to $rel"
  mkdir -p "$(dirname "$dest")"
  cp -p "$source" "$dest"
}
stage_normalized() {
  local root=$1 file rel
  [ -d "$root" ] || return 0
  find "$root" -type f \( -name '*.wallet' -o -name '*.zkpwallet' -o -name '*.did' -o -name 'blockchain.properties' -o -name 'besu.dat' \) -print0 |
    while IFS= read -r -d '' file; do
      case "$file" in
        "$OPENDID_ROOT"/*) rel=${file#$OPENDID_ROOT/} ;;
        *) continue ;;
      esac
      case "$rel" in
        /*|../*|*/../*) continue ;;
      esac
      stage_file "$file" "$rel"
    done
}
stage_entity() {
  local entity=$1 root="$OPENDID_ROOT/jars/$1" file rel
  [ -d "$root" ] || return 0
  find "$root" -type f \( -name '*.wallet' -o -name '*.zkpwallet' -o -name '*.did' \) -print0 |
    while IFS= read -r -d '' file; do
      rel=${file#$root/}
      stage_file "$file" "secrets/$entity/$rel"
    done
}
for entity in TA Issuer CA Wallet Verifier; do stage_entity "$entity"; done
[ ! -f "$OPENDID_ROOT/shells/Besu/TA/blockchain.properties" ] || \
  stage_file "$OPENDID_ROOT/shells/Besu/TA/blockchain.properties" secrets/TA/blockchain.properties
[ ! -f "$OPENDID_ROOT/shells/Besu/Issuer/blockchain.properties" ] || \
  stage_file "$OPENDID_ROOT/shells/Besu/Issuer/blockchain.properties" secrets/Issuer/blockchain.properties
[ ! -f "$OPENDID_ROOT/shells/Besu/blockchain.properties" ] || \
  stage_file "$OPENDID_ROOT/shells/Besu/blockchain.properties" secrets/CA/blockchain.properties
[ ! -f "$OPENDID_ROOT/shells/Besu/besu.dat" ] || \
  stage_file "$OPENDID_ROOT/shells/Besu/besu.dat" config/besu.dat
stage_normalized "$SECRETS_DIR"
stage_normalized "$CONFIG_DIR"
tar -C "$tmp_stage" -cf "$OUT/opendid-files.tar" .
chmod 600 "$OUT/opendid-files.tar"

if [ -d "$HOLDER_DATA_DIR" ] && [ -n "$(find "$HOLDER_DATA_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]; then
  tar -C "$HOLDER_DATA_DIR" -cf "$OUT/holder-data.tar" .
  chmod 600 "$OUT/holder-data.tar"
  printf 'holder_data=present\n' >>"$manifest"
else
  printf 'holder_data=missing\n' >>"$manifest"
fi
chmod 600 "$manifest"

(
  cd "$OUT"
  : >SHA256SUMS
  for f in EXPORT-MANIFEST.txt postgres.dump.sql besu-data.tar opendid-files.tar holder-data.tar; do
    [ -f "$f" ] && sha256_one "$f" >>SHA256SUMS
  done
  chmod 600 SHA256SUMS
)

printf 'export_dir=%s\n' "$OUT"
printf 'holder_data=%s\n' "$(grep '^holder_data=' "$manifest" | cut -d= -f2)"
