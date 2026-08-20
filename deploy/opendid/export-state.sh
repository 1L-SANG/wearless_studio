#!/usr/bin/env bash
set -euo pipefail

POSTGRES_CONTAINER=${OPENDID_POSTGRES_CONTAINER:-postgre-opendid}
BESU_CONTAINER=${OPENDID_BESU_CONTAINER:-opendid-besu-node}
POSTGRES_VOLUME=${OPENDID_POSTGRES_VOLUME:-postgre_opendid_data}
BESU_VOLUME=${OPENDID_BESU_VOLUME:-besu_opendid_data}
POSTGRES_VOLUME_FALLBACKS=${OPENDID_POSTGRES_VOLUME_FALLBACKS:-postgre_postgre_opendid_data}
BESU_VOLUME_FALLBACKS=${OPENDID_BESU_VOLUME_FALLBACKS:-besu_besu_opendid_data}
POSTGRES_USER=${OPENDID_POSTGRES_USER:-${OPENDID_DB_USER:-postgres}}
OPENDID_ROOT=${OPENDID_ROOT:-/opt/opendid}
SECRETS_DIR=${OPENDID_SECRETS_DIR:-$OPENDID_ROOT/secrets}
CONFIG_DIR=${OPENDID_CONFIG_DIR:-$OPENDID_ROOT/config}
HOLDER_DATA_DIR=${OPENDID_HOLDER_DATA_DIR:-$OPENDID_ROOT/state/holder}
APP_SERVICES=${OPENDID_APP_SERVICES:-opendid-tas opendid-issuer opendid-cas fm-holder}

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

[ "$#" = 1 ] || die "usage: $0 <new-empty-output-dir>"
OUT=$1
[ ! -e "$OUT" ] || [ -d "$OUT" ] || die "output path is not a directory: $OUT"
if [ -e "$OUT" ] && [ -n "$(find "$OUT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  die "output directory is not empty: $OUT"
fi

need docker
need tar
need systemctl
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
chmod 700 "$OUT"

for svc in $APP_SERVICES; do
  if systemctl is-active "$svc" >/dev/null 2>&1; then
    die "$svc is active; stop Holder/TAS/Issuer/CAS before export"
  fi
done

besu_running=$(docker inspect -f '{{.State.Running}}' "$BESU_CONTAINER" 2>/dev/null || echo false)
[ "$besu_running" != "true" ] || die "$BESU_CONTAINER is running; stop Besu before export"

POSTGRES_VOLUME=$(resolve_volume "$POSTGRES_VOLUME" $POSTGRES_VOLUME_FALLBACKS) || die "PostgreSQL volume not found"
BESU_VOLUME=$(resolve_volume "$BESU_VOLUME" $BESU_VOLUME_FALLBACKS) || die "Besu volume not found"

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

tmp_list=$(mktemp)
trap 'rm -f "$tmp_list"' EXIT
if [ -d "$SECRETS_DIR" ]; then
  find "$SECRETS_DIR" -type f \( -name '*.wallet' -o -name '*.zkpwallet' -o -name '*.did' -o -name 'blockchain.properties' \) \
    -print | sed "s#^$OPENDID_ROOT/##" >>"$tmp_list"
fi
if [ -d "$CONFIG_DIR" ]; then
  find "$CONFIG_DIR" -type f -print | sed "s#^$OPENDID_ROOT/##" >>"$tmp_list"
fi
if [ -s "$tmp_list" ]; then
  tar -C "$OPENDID_ROOT" -cf "$OUT/opendid-files.tar" -T "$tmp_list"
else
  empty_dir=$(mktemp -d)
  tar -C "$empty_dir" -cf "$OUT/opendid-files.tar" .
  rmdir "$empty_dir"
fi
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
