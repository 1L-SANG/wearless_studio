#!/usr/bin/env bash
# restore-state.sh <archive-dir> [--apply]
#
# Restore an export-state.sh archive onto a FRESH target (plan Task 4, Step 2).
# Contract:
#   - sha256 pre-verify EVERY artifact first; abort before touching volumes on mismatch.
#   - Without --apply: print the plan and exit (no changes).
#   - Refuse if the target PostgreSQL/Besu volume is non-empty.
#   - Restore the Besu archive into the empty target volume (physical).
#   - Boot PostgreSQL 16.4 on the empty target volume and restore pg_dumpall (logical).
#   - Restore wallet/DID/config into the target secrets dir at 0600.
#   - Restore Holder data into the target holder dir if present.
#   - The source archive is only ever READ — never modified.
#
# Env overrides:
#   OPENDID_TARGET_PG_VOLUME    (default opendid_postgres_data)   # matches infra.compose.yml
#   OPENDID_TARGET_BESU_VOLUME  (default opendid_besu_data)
#   OPENDID_TARGET_SECRETS_DIR  (default /opt/opendid/secrets)
#   OPENDID_TARGET_HOLDER_DIR   (default /opt/opendid/state/holder)
#   OPENDID_RESTORE_PG_IMAGE    (default postgres:16.4)
#   OPENDID_UTIL_IMAGE          (default = restore pg image)
#   OPENDID_PG_SUPERUSER        (default: read pg_superuser from metadata.txt)
set -euo pipefail

TARGET_PG_VOLUME="${OPENDID_TARGET_PG_VOLUME:-opendid_postgres_data}"
TARGET_BESU_VOLUME="${OPENDID_TARGET_BESU_VOLUME:-opendid_besu_data}"
TARGET_SECRETS_DIR="${OPENDID_TARGET_SECRETS_DIR:-/opt/opendid/secrets}"
TARGET_HOLDER_DIR="${OPENDID_TARGET_HOLDER_DIR:-/opt/opendid/state/holder}"
PG_IMAGE="${OPENDID_RESTORE_PG_IMAGE:-postgres:16.4}"
UTIL_IMAGE="${OPENDID_UTIL_IMAGE:-$PG_IMAGE}"

die() { echo "ERROR: $*" >&2; exit 1; }

ARCHIVE=""; APPLY="no"
for a in "$@"; do
  case "$a" in
    --apply) APPLY="yes" ;;
    -*) die "unknown flag: $a" ;;
    *) [ -z "$ARCHIVE" ] && ARCHIVE="$a" || die "unexpected arg: $a" ;;
  esac
done
[ -n "$ARCHIVE" ] || die "usage: restore-state.sh <archive-dir> [--apply]"
[ -d "$ARCHIVE" ] || die "archive dir not found: $ARCHIVE"
[ -f "$ARCHIVE/MANIFEST.sha256" ] || die "no MANIFEST.sha256 in $ARCHIVE"
command -v docker >/dev/null 2>&1 || die "docker not available"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum   >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else die "no sha256 tool (sha256sum/shasum)"; fi
}

meta_get() { sed -n "s/^$1=//p" "$ARCHIVE/metadata.txt" 2>/dev/null | head -n1; }

volume_nonempty() { # returns 0 if the named volume exists AND has contents
  local out
  out="$(docker run --rm --entrypoint sh -v "$1":/vol:ro "$UTIL_IMAGE" -c 'ls -A /vol 2>/dev/null | head -1' 2>/dev/null || true)"
  [ -n "$out" ]
}

# ---- 1) checksum PRE-VERIFY (always, before touching anything) -------------
echo "==> verifying archive checksums"
mism=0
while read -r want file; do
  [ -n "$want" ] || continue
  [ -f "$ARCHIVE/$file" ] || { echo "  MISSING: $file"; mism=1; continue; }
  got="$(sha256_of "$ARCHIVE/$file")"
  if [ "$got" != "$want" ]; then echo "  MISMATCH: $file"; mism=1; else echo "  ok: $file"; fi
done < "$ARCHIVE/MANIFEST.sha256"
[ "$mism" -eq 0 ] || die "checksum verification failed — aborting before any volume/DB change"

HOLDER_STATUS="$(meta_get holder_data)"; HOLDER_STATUS="${HOLDER_STATUS:-missing}"
SU="${OPENDID_PG_SUPERUSER:-$(meta_get pg_superuser)}"; SU="${SU:-postgres}"

# ---- plan / dry-run --------------------------------------------------------
echo
echo "== restore plan =="
echo "  archive        : $ARCHIVE  (read-only)"
echo "  pg superuser   : $SU"
echo "  pg volume      : $TARGET_PG_VOLUME   (restore pg_dumpall via $PG_IMAGE)"
echo "  besu volume    : $TARGET_BESU_VOLUME (restore besu-volume.tgz)"
echo "  secrets dir    : $TARGET_SECRETS_DIR (0600)"
echo "  holder dir     : $TARGET_HOLDER_DIR  (holder_data=$HOLDER_STATUS)"
if [ "$APPLY" != "yes" ]; then
  echo
  echo "dry-run: pass --apply to execute. No changes made."
  exit 0
fi

# ---- refuse non-empty target volumes --------------------------------------
echo
echo "==> checking target volumes are empty"
volume_nonempty "$TARGET_PG_VOLUME"   && die "target pg volume '$TARGET_PG_VOLUME' is not empty — refuse (fresh target only)"
volume_nonempty "$TARGET_BESU_VOLUME" && die "target besu volume '$TARGET_BESU_VOLUME' is not empty — refuse (fresh target only)"

# ---- restore besu (physical) into empty volume ----------------------------
echo "==> restoring besu volume"
docker run --rm --entrypoint sh -i -v "$TARGET_BESU_VOLUME":/vol "$UTIL_IMAGE" \
  -c 'tar xzf - -C /vol' < "$ARCHIVE/besu-volume.tgz"

# ---- restore postgres (logical) -------------------------------------------
echo "==> booting postgres $PG_IMAGE on target volume for restore"
RPG="opendid-restore-pg-$$"
cleanup() { docker rm -f "$RPG" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$RPG" \
  -e POSTGRES_USER="$SU" -e POSTGRES_PASSWORD=restore-boot -e POSTGRES_DB=postgres \
  -v "$TARGET_PG_VOLUME":/var/lib/postgresql/data "$PG_IMAGE" >/dev/null

echo "--> waiting for postgres to become ready"
ready=no
for _ in $(seq 1 60); do
  if docker exec "$RPG" pg_isready -U "$SU" -d postgres >/dev/null 2>&1; then ready=yes; break; fi
  sleep 1
done
[ "$ready" = yes ] || die "restore postgres did not become ready"

echo "--> applying pg_dumpall"
docker exec -i "$RPG" psql -v ON_ERROR_STOP=0 -U "$SU" -d postgres < "$ARCHIVE/pg_dumpall.sql" >/dev/null
docker stop "$RPG" >/dev/null
cleanup; trap - EXIT

# ---- restore secrets (wallet/DID/config) at 0600 --------------------------
echo "==> restoring secrets to $TARGET_SECRETS_DIR (0600)"
umask 077
mkdir -p "$TARGET_SECRETS_DIR"
tar xzf "$ARCHIVE/secrets.tgz" -C "$TARGET_SECRETS_DIR"
find "$TARGET_SECRETS_DIR" -type d -exec chmod 0700 {} +
find "$TARGET_SECRETS_DIR" -type f -exec chmod 0600 {} +

# ---- restore holder data if present ---------------------------------------
if [ "$HOLDER_STATUS" = "present" ] && [ -f "$ARCHIVE/holder-data.tgz" ]; then
  echo "==> restoring holder data to $TARGET_HOLDER_DIR"
  mkdir -p "$TARGET_HOLDER_DIR"
  tar xzf "$ARCHIVE/holder-data.tgz" -C "$TARGET_HOLDER_DIR"
else
  echo "==> holder data missing in archive — NOT creating model wallets (see runbook: cannot revoke gap)"
fi

echo "==> restore complete. holder_data=$HOLDER_STATUS"
