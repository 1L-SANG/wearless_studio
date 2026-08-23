#!/usr/bin/env bash
# Migration dry-run / apply for the FaceMarket real-service rollout (Phase 2, P2.3).
#
# CI only greps the SQL — it NEVER applies the DDL. This tool applies every
# app migration in supabase/migrations/ that the target has not yet recorded,
# in version order.
#
#   DRY-RUN (default): each pending migration runs inside BEGIN ... ROLLBACK, so
#     it validates cleanly against the target's CURRENT schema and persists
#     NOTHING. Run this against a real Postgres 16 CLONE of prod first.
#   APPLY (--apply): each pending migration runs + records its version and
#     COMMITS. Use only on the intended target (clone, then the authorized
#     prod run).
#
# ⚠️ Irreversible when applied: 20260820000000_facemarket_cx_token_digest.sql
#    backfills fm_identity_verifications.cx_tx_id from raw token -> sha256 digest.
#    It runs at most once (skipped if its version is already recorded); never
#    re-run it against already-digested data. The dry-run mode rolls it back.
#
# Usage:
#   deploy/opendid/migrate-prod-dryrun.sh "$DATABASE_URL"            # dry-run (safe, rolls back)
#   deploy/opendid/migrate-prod-dryrun.sh "$DATABASE_URL" --apply    # real apply (commits)
set -euo pipefail

DBURL="${1:?usage: migrate-prod-dryrun.sh <DATABASE_URL> [--apply]}"
MODE="${2:-dry}"
MIGDIR="$(cd "$(dirname "$0")/../../supabase/migrations" && pwd)"

command -v psql >/dev/null || { echo "psql not found (install postgresql-client)"; exit 2; }

# Versions the target already recorded (empty if the tracking table is absent).
APPLIED="$(psql "$DBURL" -tAc \
  "select version from supabase_migrations.schema_migrations" 2>/dev/null || true)"

fail=0; ran=0; skipped=0
for f in $(ls "$MIGDIR"/*.sql | sort); do
  ver="$(basename "$f" | sed -E 's/^([0-9]+)_.*/\1/')"
  if printf '%s\n' "$APPLIED" | grep -qx "$ver"; then
    skipped=$((skipped+1)); continue
  fi
  echo "→ $ver  ($(basename "$f"))"
  if [ "$MODE" = "--apply" ]; then
    # one transaction: the migration + its version record, together
    if psql "$DBURL" -v ON_ERROR_STOP=1 -1 -f "$f" \
         -c "insert into supabase_migrations.schema_migrations(version) values ('$ver') on conflict do nothing"; then
      echo "  applied + recorded"; ran=$((ran+1))
    else echo "  FAILED"; fail=1; break; fi
  else
    # dry-run: apply inside a transaction, then roll back — persists nothing
    if psql "$DBURL" -v ON_ERROR_STOP=1 <<SQL >/dev/null
begin;
\\i $f
rollback;
SQL
    then echo "  ok (dry-run rolled back)"; ran=$((ran+1))
    else echo "  FAILED"; fail=1; break; fi
  fi
done

echo "---"
echo "pending-run=$ran  already-applied(skipped)=$skipped  mode=${MODE}"
if [ "$fail" -eq 0 ]; then echo "RESULT: CLEAN"; else echo "RESULT: FAILED"; exit 1; fi
