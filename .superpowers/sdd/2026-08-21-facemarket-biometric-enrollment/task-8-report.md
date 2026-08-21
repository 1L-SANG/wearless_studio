# Task 8 Report — Enrollment-bound Asset Promotion

## Status

Complete. The FaceMarket asset worker now consumes only `{modelId,enrollmentId}` enrollment photos, promotes exactly `front`, `angle45`, `side` from quarantine to enrollment-scoped originals, writes versioned derived assets, and performs the asset swap behind a final job-lease/enrollment/model/user/status fence. Task 6 remains the only biometric comparison decision; the worker no longer loads Face QC, writes `qc_score`, or emits numeric QC metadata.

The swap records `source_enrollment_id`, `evidence_version`, `current_enrollment_id`, and a source hash derived from stored image digests. Models remain `pending` or `reverification_required`; successful promotion only moves enrollment to `license_pending` and assets to `ready`. Manual unbound asset builds now return `409 biometric_enrollment_required` while biometric enrollment is enabled.

Post-commit quarantine and prior-asset cleanup registers a durable cleanup reference before deleting R2 objects, removes the reference only after successful deletion, logs closed metadata only, and skips old asset keys that equal a newly registered key.

## TDD evidence

RED command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py
```

Result: `7 failed, 15 passed`. Failures showed the worker still queried `personalization_face_photos`, did not write source/evidence/current-enrollment metadata, lacked durable cleanup refs, accepted the manual build route under biometrics, and the resolver accepted pending/stale/no-evidence assets.

GREEN focused command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py
```

Result: `22 passed, 1 warning`.

Affected caller command:

```text
cd server && uv run pytest -q tests/test_detail_page_identity_source.py tests/test_detail_page_license_face.py tests/test_fm_model_asset_job.py tests/test_identity_source.py tests/test_facemarket_identity.py
```

Result: `47 passed, 1 warning`.

## Verification

Full server suite:

```text
cd server && uv run pytest -q
```

Result: `2699 passed, 103 skipped, 391 warnings in 34.66s`. Skips and warnings are the existing optional integration/dependency deprecation cases.

Additional checks:

```text
cd server && uv run python -m compileall -q app/workers/fm_model_asset_job.py app/agents/identity_source.py app/facemarket.py tests/test_fm_model_asset_job.py tests/test_identity_source.py tests/test_detail_page_identity_source.py
cd server && git diff --check
cd server && rg -n "pairwise_min_similarity|load_face_qc|qc_score|str\\(exc\\)" app/workers/fm_model_asset_job.py
```

Compile and diff checks exited `0`; the worker scan returned no matches.

## Remaining gaps

Live PostgreSQL concurrency, live R2 copy/delete failure semantics, and crash recovery timing were not exercised. Deterministic fakes cover ordering, final fence rejection, rollback cleanup, durable post-commit cleanup references, stale resolver rejection, and manual route gating.
