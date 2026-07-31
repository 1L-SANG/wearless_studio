#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git -C "$TMP" init -q
git -C "$TMP" config user.name test
git -C "$TMP" config user.email test@example.com
git -C "$TMP" checkout -q -b feat/orb-motion-fx
touch "$TMP/README"
git -C "$TMP" add README
git -C "$TMP" commit -qm initial
git -C "$TMP" branch backup/pre-pdf-scrub

cp "$ROOT/scripts/scrub_insight_pdf.sh" "$TMP/scrub.sh"
before="$(git -C "$TMP" rev-parse HEAD)"
if (cd "$TMP" && bash scrub.sh) >"$TMP/output" 2>&1; then
  echo "expected existing backup to stop the scrub" >&2
  exit 1
fi

grep -q "backup/pre-pdf-scrub" "$TMP/output"
test "$(git -C "$TMP" rev-parse HEAD)" = "$before"
