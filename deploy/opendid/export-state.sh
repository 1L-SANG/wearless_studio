#!/usr/bin/env bash
# export-state.sh <new-empty-output-dir>
#
# Cold, consistent export of the OpenDID source state for the single-server move.
# Contract (plan Task 3, Step 2):
#   1. Refuse unless Holder/TAS/Issuer/CAS/Besu writers are stopped.
#      (PostgreSQL stays UP — it is the dump source; all WRITERS are quiesced.)
#   2. pg_dumpall of the whole cluster (roles + all databases).
#   3. Read-only archive of the Besu volume.
#   4. Archive of entity wallet/DID + blockchain config.
#   5. Archive Holder data if present, else record holder_data=missing.
#   6. Sensitive artifacts are 0600.
#   7. sha256 MANIFEST over every artifact.
# Refuses to overwrite an existing output dir. Reads source volumes read-only;
# never mutates source state. Prints only IDs/status — never secrets or PII.
#
# Env overrides: see inventory-state.sh, plus:
#   OPENDID_WRITER_PORTS  (default "8090 8091 8094 8100 8545") ports that must be closed
#   OPENDID_UTIL_IMAGE    (default postgres:16.4) small image used for volume tar (has tar+sh)
set -euo pipefail

PG_CONTAINER="${OPENDID_PG_CONTAINER:-postgre-opendid}"
BESU_CONTAINER="${OPENDID_BESU_CONTAINER:-opendid-besu-node}"
BESU_VOLUME="${OPENDID_BESU_VOLUME:-besu_besu_opendid_data}"
SECRETS_DIR="${OPENDID_SECRETS_DIR:-/opt/opendid/secrets}"
HOLDER_DATA_DIR="${OPENDID_HOLDER_DATA_DIR:-/opt/opendid/state/holder}"
WRITER_PORTS="${OPENDID_WRITER_PORTS-8090 8091 8094 8100 8545}"
UTIL_IMAGE="${OPENDID_UTIL_IMAGE:-postgres:16.4}"

die() { echo "ERROR: $*" >&2; exit 1; }

[ "$#" -eq 1 ] || die "usage: export-state.sh <new-empty-output-dir>"
OUT="$1"
command -v docker >/dev/null 2>&1 || die "docker not available"

# ---- refuse to overwrite --------------------------------------------------
[ -e "$OUT" ] && die "output dir already exists: $OUT (refusing to overwrite)"

container_running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" = "true" ]; }
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1 && { exec 3>&- 3<&- 2>/dev/null; return 0; } || return 1; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum   >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else die "no sha256 tool (sha256sum/shasum)"; fi
}

# ---- preconditions: writers must be stopped -------------------------------
container_running "$PG_CONTAINER" || die "postgres container '$PG_CONTAINER' is not running (needed for pg_dumpall)"
if container_running "$BESU_CONTAINER"; then
  die "Besu container '$BESU_CONTAINER' is still running — stop it before export"
fi
for p in $WRITER_PORTS; do
  if port_open "$p"; then die "a writer is still listening on 127.0.0.1:$p — stop Holder/TAS/Issuer/CAS/Besu first"; fi
done

# secrets must exist to be worth exporting
[ -d "$SECRETS_DIR" ] && [ -n "$(ls -A "$SECRETS_DIR" 2>/dev/null)" ] || \
  die "secrets dir '$SECRETS_DIR' is missing/empty — nothing to archive (set OPENDID_SECRETS_DIR)"

# ---- detect postgres superuser -------------------------------------------
SU="${OPENDID_PG_SUPERUSER:-}"
if [ -z "$SU" ]; then
  env_user="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$PG_CONTAINER" 2>/dev/null | sed -n 's/^POSTGRES_USER=//p' | head -n1)"
  for cand in "$env_user" omn opendid postgres; do
    [ -n "$cand" ] || continue
    if docker exec "$PG_CONTAINER" psql -U "$cand" -d postgres -tAc 'SELECT 1' >/dev/null 2>&1; then SU="$cand"; break; fi
  done
fi
[ -n "$SU" ] || die "could not detect postgres superuser (set OPENDID_PG_SUPERUSER)"

# ---- create output dir (0700) --------------------------------------------
umask 077
mkdir -p "$OUT"
chmod 0700 "$OUT"
echo "==> exporting to $OUT (superuser=$SU)"

# ---- 1) pg_dumpall (0600; may contain role password hashes) ---------------
echo "--> pg_dumpall"
docker exec "$PG_CONTAINER" pg_dumpall -U "$SU" > "$OUT/pg_dumpall.sql"
chmod 0600 "$OUT/pg_dumpall.sql"

# ---- 2) besu volume archive (read-only mount, streamed to host) -----------
echo "--> besu volume archive"
docker run --rm --entrypoint sh -v "$BESU_VOLUME":/vol:ro "$UTIL_IMAGE" \
  -c 'tar czf - -C /vol .' > "$OUT/besu-volume.tgz"
chmod 0600 "$OUT/besu-volume.tgz"

# ---- 3) wallet / DID / blockchain config archive --------------------------
echo "--> secrets (wallet/DID/blockchain config) archive"
tar czf "$OUT/secrets.tgz" -C "$SECRETS_DIR" .
chmod 0600 "$OUT/secrets.tgz"

# ---- 4) holder data archive OR record missing -----------------------------
HOLDER_STATUS="missing"
if [ -d "$HOLDER_DATA_DIR" ] && [ -n "$(ls -A "$HOLDER_DATA_DIR" 2>/dev/null)" ]; then
  echo "--> holder data archive"
  tar czf "$OUT/holder-data.tgz" -C "$HOLDER_DATA_DIR" .
  chmod 0600 "$OUT/holder-data.tgz"
  HOLDER_STATUS="present"
else
  echo "--> holder data: missing (recording holder_data=missing)"
fi

# ---- 5) metadata (no secrets) --------------------------------------------
{
  echo "generated=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "pg_superuser=$SU"
  echo "pg_container=$PG_CONTAINER"
  echo "besu_container=$BESU_CONTAINER"
  echo "besu_volume=$BESU_VOLUME"
  echo "besu_image=$(docker inspect -f '{{.Config.Image}}' "$BESU_CONTAINER" 2>/dev/null || echo unknown)"
  echo "pg_image=$(docker inspect -f '{{.Config.Image}}' "$PG_CONTAINER" 2>/dev/null || echo unknown)"
  echo "holder_data=$HOLDER_STATUS"
  echo "secrets=present"
} > "$OUT/metadata.txt"
chmod 0644 "$OUT/metadata.txt"

# ---- 6) sha256 manifest over every artifact -------------------------------
echo "--> sha256 manifest"
: > "$OUT/MANIFEST.sha256"
( cd "$OUT" && for f in pg_dumpall.sql besu-volume.tgz secrets.tgz holder-data.tgz metadata.txt; do
    [ -f "$f" ] || continue
    printf '%s  %s\n' "$(sha256_of "$f")" "$f"
  done ) >> "$OUT/MANIFEST.sha256"
chmod 0644 "$OUT/MANIFEST.sha256"

echo "==> export complete. holder_data=$HOLDER_STATUS"
echo "    files:"
( cd "$OUT" && ls -l | awk 'NR>1{printf "      %s %s\n", $1, $NF}' )
echo "    (no secrets, keys, or PII printed above)"
