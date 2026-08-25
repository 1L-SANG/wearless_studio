#!/usr/bin/env bash
# test-restore-state.sh — round-trip export -> restore on THROWAWAY volumes
# (plan Task 4, Step 3). Never touches the real opendid volumes.
#
# Verifies, on temp resources only:
#   - DB / table / fixture rows survive the round trip
#   - Besu fixture-file checksum matches after restore
#   - wallet/DID checksum matches after restore
#   - Holder data: present case restored, missing case left empty
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
EXPORT="$HERE/export-state.sh"
RESTORE="$HERE/restore-state.sh"
UTIL_IMAGE="${OPENDID_UTIL_IMAGE:-postgres:16.4}"
TAG="opendidrt$$"

SRC_PG="src-pg-$TAG"
SRC_PG_VOL="srcpgvol$TAG"
SRC_BESU_VOL="srcbesuvol$TAG"
VERIFY_PG="verify-pg-$TAG"
TGTA_PG_VOL="tgtapg$TAG";  TGTA_BESU_VOL="tgtabesu$TAG"
TGTB_PG_VOL="tgtbpg$TAG";  TGTB_BESU_VOL="tgtbbesu$TAG"
WORK="$(mktemp -d)"
SECRETS_DIR="$WORK/secrets"
HOLDER_SRC="$WORK/holder-src"       # present case
OUT_A="$WORK/out-present"
OUT_B="$WORK/out-missing"
TGTA_SECRETS="$WORK/tgtA-secrets"; TGTA_HOLDER="$WORK/tgtA-holder"
TGTB_SECRETS="$WORK/tgtB-secrets"; TGTB_HOLDER="$WORK/tgtB-holder"

pass=1
cleanup() {
  docker rm -f "$SRC_PG" "$VERIFY_PG" >/dev/null 2>&1 || true
  docker volume rm -f "$SRC_PG_VOL" "$SRC_BESU_VOL" \
    "$TGTA_PG_VOL" "$TGTA_BESU_VOL" "$TGTB_PG_VOL" "$TGTB_BESU_VOL" >/dev/null 2>&1 || true
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; pass=0; }
ok()   { echo "  ok: $*"; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
wait_pg() { for _ in $(seq 1 60); do docker exec "$1" pg_isready -U omn -d "$2" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
besu_marker_sha() { # named-volume -> sha of marker.txt content
  docker run --rm --entrypoint sh -v "$1":/vol:ro "$UTIL_IMAGE" -c 'cat /vol/marker.txt 2>/dev/null' \
    | ( if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi ) | awk '{print $1}'
}

echo "== setting up throwaway source fixtures =="
docker volume create "$SRC_PG_VOL"   >/dev/null
docker volume create "$SRC_BESU_VOL" >/dev/null
docker run --rm --entrypoint sh -v "$SRC_BESU_VOL":/vol "$UTIL_IMAGE" \
  -c 'echo "besu-marker-'"$TAG"'" > /vol/marker.txt; mkdir -p /vol/database; echo blockdata > /vol/database/000001.log'

mkdir -p "$SECRETS_DIR"/{TA,Issuer,CA,Wallet}
echo "FAKE-WALLET-DO-NOT-USE-$TAG" > "$SECRETS_DIR/TA/tas.wallet"
echo '{"did":"did:example:tas"}'   > "$SECRETS_DIR/TA/did.json"
echo "rpc=http://127.0.0.1:8545"    > "$SECRETS_DIR/TA/blockchain.properties"
WALLET_SHA_SRC="$(sha256_of "$SECRETS_DIR/TA/tas.wallet")"

mkdir -p "$HOLDER_SRC"
echo "holder-state-$TAG" > "$HOLDER_SRC/models.json"

docker run -d --name "$SRC_PG" \
  -e POSTGRES_USER=omn -e POSTGRES_PASSWORD=fixture -e POSTGRES_DB=omn \
  -v "$SRC_PG_VOL":/var/lib/postgresql/data "$UTIL_IMAGE" >/dev/null
wait_pg "$SRC_PG" omn || { fail "source pg not ready"; echo "test-restore-state: FAIL"; exit 1; }
docker exec "$SRC_PG" psql -U omn -d omn -c \
  "CREATE TABLE fixture_rows(id int primary key, note text); INSERT INTO fixture_rows VALUES (1,'alpha'),(2,'bravo'),(3,'charlie');" >/dev/null
BESU_SHA_SRC="$(besu_marker_sha "$SRC_BESU_VOL")"

common_export_env() {
  export OPENDID_PG_CONTAINER="$SRC_PG"
  export OPENDID_BESU_CONTAINER="besu-absent-$TAG"
  export OPENDID_BESU_VOLUME="$SRC_BESU_VOL"
  export OPENDID_SECRETS_DIR="$SECRETS_DIR"
  export OPENDID_WRITER_PORTS=""
  export OPENDID_PG_SUPERUSER=omn
  export OPENDID_UTIL_IMAGE="$UTIL_IMAGE"
}

echo "== CASE A: holder PRESENT — export =="
common_export_env
export OPENDID_HOLDER_DATA_DIR="$HOLDER_SRC"
bash "$EXPORT" "$OUT_A" >/dev/null || fail "export (present) failed"
grep -q '^holder_data=present$' "$OUT_A/metadata.txt" && ok "export recorded holder_data=present" || fail "present not recorded"
[ -f "$OUT_A/holder-data.tgz" ] && ok "holder-data.tgz present" || fail "holder-data.tgz missing"

echo "== CASE A: restore --apply to fresh target =="
OPENDID_TARGET_PG_VOLUME="$TGTA_PG_VOL" \
OPENDID_TARGET_BESU_VOLUME="$TGTA_BESU_VOL" \
OPENDID_TARGET_SECRETS_DIR="$TGTA_SECRETS" \
OPENDID_TARGET_HOLDER_DIR="$TGTA_HOLDER" \
OPENDID_UTIL_IMAGE="$UTIL_IMAGE" \
OPENDID_RESTORE_PG_IMAGE="$UTIL_IMAGE" \
  bash "$RESTORE" "$OUT_A" --apply >/dev/null || fail "restore (present) failed"

echo "== CASE A: verify round trip =="
# DB rows
docker run -d --name "$VERIFY_PG" -v "$TGTA_PG_VOL":/var/lib/postgresql/data "$UTIL_IMAGE" >/dev/null
if wait_pg "$VERIFY_PG" omn; then
  got="$(docker exec "$VERIFY_PG" psql -U omn -d omn -tAc \
    "SELECT count(*)||'|'||coalesce(string_agg(note,',' ORDER BY id),'') FROM fixture_rows" 2>/dev/null | tr -d '[:space:]')"
  [ "$got" = "3|alpha,bravo,charlie" ] && ok "DB rows match ($got)" || fail "DB rows mismatch (got '$got')"
else
  fail "verify pg not ready"
fi
docker rm -f "$VERIFY_PG" >/dev/null 2>&1 || true
# besu fixture checksum
BESU_SHA_TGT="$(besu_marker_sha "$TGTA_BESU_VOL")"
[ -n "$BESU_SHA_SRC" ] && [ "$BESU_SHA_SRC" = "$BESU_SHA_TGT" ] && ok "besu fixture checksum matches" || fail "besu checksum mismatch"
# wallet checksum
if [ -f "$TGTA_SECRETS/TA/tas.wallet" ]; then
  [ "$(sha256_of "$TGTA_SECRETS/TA/tas.wallet")" = "$WALLET_SHA_SRC" ] && ok "wallet checksum matches" || fail "wallet checksum mismatch"
  [ "$(if stat -c '%a' "$TGTA_SECRETS/TA/tas.wallet" >/dev/null 2>&1; then stat -c '%a' "$TGTA_SECRETS/TA/tas.wallet"; else stat -f '%Lp' "$TGTA_SECRETS/TA/tas.wallet"; fi)" = "600" ] \
    && ok "restored wallet is 0600" || fail "restored wallet not 0600"
else
  fail "wallet not restored"
fi
# holder present
[ -f "$TGTA_HOLDER/models.json" ] && ok "holder data restored (present case)" || fail "holder data not restored"

echo "== CASE B: holder MISSING — export + restore =="
common_export_env
export OPENDID_HOLDER_DATA_DIR="$WORK/holder-does-not-exist"
bash "$EXPORT" "$OUT_B" >/dev/null || fail "export (missing) failed"
grep -q '^holder_data=missing$' "$OUT_B/metadata.txt" && ok "export recorded holder_data=missing" || fail "missing not recorded"
[ -f "$OUT_B/holder-data.tgz" ] && fail "holder-data.tgz should be absent" || ok "no holder-data.tgz"

OPENDID_TARGET_PG_VOLUME="$TGTB_PG_VOL" \
OPENDID_TARGET_BESU_VOLUME="$TGTB_BESU_VOL" \
OPENDID_TARGET_SECRETS_DIR="$TGTB_SECRETS" \
OPENDID_TARGET_HOLDER_DIR="$TGTB_HOLDER" \
OPENDID_UTIL_IMAGE="$UTIL_IMAGE" \
OPENDID_RESTORE_PG_IMAGE="$UTIL_IMAGE" \
  bash "$RESTORE" "$OUT_B" --apply >/dev/null || fail "restore (missing) failed"
# holder dir must not have been populated with model wallets
if [ -d "$TGTB_HOLDER" ] && [ -n "$(ls -A "$TGTB_HOLDER" 2>/dev/null)" ]; then
  fail "holder dir populated in missing case"
else
  ok "holder dir left empty (missing case)"
fi

echo
if [ "$pass" -eq 1 ]; then echo "test-restore-state: PASS"; exit 0; else echo "test-restore-state: FAIL"; exit 1; fi
