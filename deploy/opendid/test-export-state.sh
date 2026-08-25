#!/usr/bin/env bash
# test-export-state.sh — fixture test for export-state.sh (plan Task 3, Step 3).
#
# Uses THROWAWAY temp postgres/besu volumes + fake wallet files ONLY.
# Never touches the real opendid volumes. Verifies:
#   - output-dir overwrite is blocked
#   - dump / archive / checksum are created
#   - private files are 0600 (dir 0700)
#   - holder_data=missing is recorded
#   - source volumes are unchanged by the export
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
EXPORT="$HERE/export-state.sh"
UTIL_IMAGE="${OPENDID_UTIL_IMAGE:-postgres:16.4}"
TAG="opendidtest$$"

SRC_PG="src-pg-$TAG"
SRC_PG_VOL="srcpgvol$TAG"
SRC_BESU_VOL="srcbesuvol$TAG"
WORK="$(mktemp -d)"
SECRETS_DIR="$WORK/secrets"
HOLDER_DIR="$WORK/holder-absent"   # intentionally never created -> missing case
OUT="$WORK/out"

pass=1
cleanup() {
  docker rm -f "$SRC_PG" >/dev/null 2>&1 || true
  docker volume rm -f "$SRC_PG_VOL" "$SRC_BESU_VOL" >/dev/null 2>&1 || true
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; pass=0; }
ok()   { echo "  ok: $*"; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
perm_of() {
  if stat -c '%a' "$1" >/dev/null 2>&1; then stat -c '%a' "$1"; else stat -f '%Lp' "$1"; fi
}
vol_sig() { # deterministic signature of all files in a named volume
  docker run --rm --entrypoint sh -v "$1":/vol:ro "$UTIL_IMAGE" \
    -c 'cd /vol && find . -type f -exec sha256sum {} \; 2>/dev/null | sort'
}

echo "== setting up throwaway fixtures =="
docker volume create "$SRC_PG_VOL"   >/dev/null
docker volume create "$SRC_BESU_VOL" >/dev/null

# besu fixture volume: known marker + fake chain file
docker run --rm --entrypoint sh -v "$SRC_BESU_VOL":/vol "$UTIL_IMAGE" \
  -c 'echo "besu-marker-'"$TAG"'" > /vol/marker.txt; mkdir -p /vol/database; echo blockdata > /vol/database/000001.log'

# fake wallet / DID / blockchain config (clearly non-secret placeholders)
mkdir -p "$SECRETS_DIR"/{TA,Issuer,CA,Wallet}
echo "FAKE-WALLET-DO-NOT-USE-$TAG" > "$SECRETS_DIR/TA/tas.wallet"
echo '{"did":"did:example:tas"}'   > "$SECRETS_DIR/TA/did.json"
echo "rpc=http://127.0.0.1:8545"    > "$SECRETS_DIR/TA/blockchain.properties"
echo "FAKE-WALLET-DO-NOT-USE-$TAG" > "$SECRETS_DIR/Wallet/wallet.wallet"

# source postgres fixture
docker run -d --name "$SRC_PG" \
  -e POSTGRES_USER=omn -e POSTGRES_PASSWORD=fixture -e POSTGRES_DB=omn \
  -v "$SRC_PG_VOL":/var/lib/postgresql/data "$UTIL_IMAGE" >/dev/null
for _ in $(seq 1 60); do docker exec "$SRC_PG" pg_isready -U omn -d omn >/dev/null 2>&1 && break; sleep 1; done
docker exec "$SRC_PG" psql -U omn -d omn -c \
  "CREATE TABLE fixture_rows(id int primary key, note text); INSERT INTO fixture_rows VALUES (1,'alpha'),(2,'bravo'),(3,'charlie');" >/dev/null

SIG_BEFORE="$(vol_sig "$SRC_BESU_VOL")"

echo "== running export =="
export OPENDID_PG_CONTAINER="$SRC_PG"
export OPENDID_BESU_CONTAINER="besu-absent-$TAG"   # not running
export OPENDID_BESU_VOLUME="$SRC_BESU_VOL"
export OPENDID_SECRETS_DIR="$SECRETS_DIR"
export OPENDID_HOLDER_DATA_DIR="$HOLDER_DIR"
export OPENDID_WRITER_PORTS=""                     # no entity writers in fixture
export OPENDID_PG_SUPERUSER=omn
export OPENDID_UTIL_IMAGE="$UTIL_IMAGE"

bash "$EXPORT" "$OUT" >/dev/null || fail "export exited non-zero"

echo "== assertions =="
# a) artifacts created
for f in pg_dumpall.sql besu-volume.tgz secrets.tgz metadata.txt MANIFEST.sha256; do
  [ -f "$OUT/$f" ] && ok "created $f" || fail "missing $f"
done
[ -f "$OUT/holder-data.tgz" ] && fail "holder-data.tgz should NOT exist (missing case)" || ok "no holder-data.tgz (missing case)"

# b) holder_data=missing recorded
grep -q '^holder_data=missing$' "$OUT/metadata.txt" && ok "metadata holder_data=missing" || fail "holder_data=missing not recorded"

# c) perms
[ "$(perm_of "$OUT")" = "700" ] && ok "out dir 0700" || fail "out dir not 0700 ($(perm_of "$OUT"))"
for f in pg_dumpall.sql besu-volume.tgz secrets.tgz; do
  [ "$(perm_of "$OUT/$f")" = "600" ] && ok "$f 0600" || fail "$f not 0600 ($(perm_of "$OUT/$f"))"
done

# d) manifest non-empty and checksums verify
[ -s "$OUT/MANIFEST.sha256" ] && ok "manifest non-empty" || fail "manifest empty"
while read -r want file; do
  [ -n "$want" ] || continue
  got="$(sha256_of "$OUT/$file")"
  [ "$got" = "$want" ] && ok "checksum ok: $file" || fail "checksum mismatch: $file"
done < "$OUT/MANIFEST.sha256"

# e) overwrite blocked
if bash "$EXPORT" "$OUT" >/dev/null 2>&1; then fail "overwrite of existing out dir was NOT blocked"; else ok "overwrite blocked"; fi

# f) source besu volume unchanged
SIG_AFTER="$(vol_sig "$SRC_BESU_VOL")"
[ "$SIG_BEFORE" = "$SIG_AFTER" ] && ok "source besu volume unchanged" || fail "source besu volume changed"

echo
if [ "$pass" -eq 1 ]; then echo "test-export-state: PASS"; exit 0; else echo "test-export-state: FAIL"; exit 1; fi
