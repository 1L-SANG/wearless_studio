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

---

## Fix round 1 — cleanup durability, stale-worker fencing, rollback compatibility

Status: complete in the Task 8 scope. This round keeps feature-on promotion enrollment-bound, preserves the documented feature-flag-off rollback path, and does not restore FaceMarket pairwise similarity, QC scores, raw exception metadata, or untracked stable-key cleanup.

Controller ruling applied for the flag-off route: when `fm_biometric_enrollment_enabled=false`, legacy `{modelId}` jobs may consume the pre-existing personalization 3-photo source only for rollback compatibility. The cost is explicit: production enablement remains the security cutover, because legacy identity provenance exists only while the biometric flag is off.

### RED evidence

Focused worker/cleanup regressions:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_facemarket_biometric_cleanup.py
```

Result before the fix: 6 expected failures covered:

- final swap did not keep cleanup intents durable through a synthetic crash immediately after commit;
- prewrite R2 failure left copied/put deterministic keys without durable cleanup intent;
- lost final lease still deleted attempt keys directly;
- lost final lease could still try model/enrollment failure mutation through the stale worker path;
- feature-flag-off `{modelId}` jobs were rejected as unprocessable payloads;
- due `license_pending` cleanup rows were not selected/drained by the sweep.

Focused resolver regression:

```text
cd server && uv run pytest -q tests/test_identity_source.py -k legacy_assets
```

Result before the fix: failed because the resolver had no explicit, fenced `allow_legacy` rollback mode.

### GREEN / fix evidence

Minimum root-cause fixes:

- inserted prewrite cleanup rows before enrollment original copies and derived model asset puts;
- inserted quarantine/prior-asset cleanup rows inside the final swap transaction before the job `done` commit;
- left post-commit cleanup rows durable on delete failure and added `license_pending` sweep reconciliation;
- fenced cleanup deletion against current enrollment photos and current `fm_model_assets` references;
- stopped raw attempt-key deletion on lease loss/cancel, leaving durable cleanup instead;
- gated model/enrollment failure mutation on `_finalize_job_failure()` success plus exact model/user/enrollment/status binding;
- preserved flag-off rollback by consuming ordered legacy `front`, `angle45`, `side` personalization photos only when biometrics are disabled, with no pairwise/QC score path;
- added resolver `allow_legacy` so legacy assets are accepted only for verified/ready models with `legacy-personalization-v1` evidence and no current enrollment.

Focused command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_facemarket_biometric_cleanup.py tests/test_identity_source.py
```

Result: `33 passed, 1 warning`.

Affected command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_facemarket_biometric_cleanup.py tests/test_facemarket_biometric_enrollment.py tests/test_identity_source.py tests/test_detail_page_identity_source.py tests/test_detail_page_license_face.py tests/test_facemarket_identity.py tests/test_cut_input_authority.py -q
```

Result: all selected affected tests passed (`165 passed`, 1 warning equivalent; output was dot-only because of double quiet mode).

Full server command:

```text
cd server && uv run pytest -q
```

Result after fixing affected test monkeypatch signatures for the resolver's `allow_legacy` kwarg: `2705 passed, 103 skipped, 391 warnings in 35.77s`.

Compile/diff/leak checks:

```text
cd server && uv run python -m compileall -q app/workers/fm_model_asset_job.py app/facemarket_enrollment.py app/agents/identity_source.py app/workers/detail_page_job.py app/workers/editor_image_job.py app/facemarket.py tests/test_fm_model_asset_job.py tests/test_facemarket_biometric_cleanup.py tests/test_identity_source.py tests/test_facemarket_biometric_enrollment.py tests/test_cut_input_authority.py
git diff --check
cd server && rg -n "pairwise_min_similarity|load_face_qc|qc_score|str\\(exc\\)" app/workers/fm_model_asset_job.py
cd server && rg -n "provider leaked/key\\.png|old/front\\.png|quarantine/front\\.png" app/workers/fm_model_asset_job.py app/facemarket_enrollment.py app/agents/identity_source.py app/workers/detail_page_job.py app/workers/editor_image_job.py app/facemarket.py
```

Result: compile and diff checks exited 0; both leakage scans returned no matches.

Self-review follow-up: the first `license_pending` sweep query used `distinct ... for update skip locked`; this was replaced with an `exists` predicate so live PostgreSQL does not reject the lock query. The fake cursor branch was narrowed to that cleanup query after a focused rerun exposed an accidental match against current-enrollment reads.

Final reruns after that self-review:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_facemarket_biometric_cleanup.py tests/test_identity_source.py tests/test_facemarket_biometric_enrollment.py
```

Result: `126 passed, 1 warning`.

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_facemarket_biometric_cleanup.py tests/test_facemarket_biometric_enrollment.py tests/test_identity_source.py tests/test_detail_page_identity_source.py tests/test_detail_page_license_face.py tests/test_facemarket_identity.py tests/test_cut_input_authority.py
```

Result: `165 passed, 1 warning`.

```text
cd server && uv run pytest -q
```

Result: `2705 passed, 103 skipped, 391 warnings in 43.23s`.

### Remaining concerns

No live PostgreSQL advisory/row-lock race or live R2 crash harness was run. The new deterministic regressions cover the required crash points, retry rows, DB-reference cleanup skip, stale lease behavior, flag-off rollback coherence, and metadata redaction in-process.

---

## Fix round 2 — flag-off legacy rollback write fence

Status: complete in the Task 8 scope. This round addresses the remaining flag-off rollback gap without changing the feature-on enrollment path and without restoring pairwise QC, numeric QC scores, raw exception metadata, or private-key logging.

Root cause: the rollback branch wrote `legacy-{job_id}` asset keys before any durable model-state fence. A crash/cancel/lease-loss after R2 writes could leave new rollback objects outside any committed usable state, and successful repeats could upsert new keys while leaving prior legacy keys behind.

### RED evidence

Focused command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py
```

Result before the fix: `4 failed, 27 passed, 1 warning`.

Expected failures covered:

- flag-off jobs still wrote `legacy-job-*` keys instead of stable rollback keys;
- prewrite R2 failure did not leave an `assets_status='building'` resolver fence;
- lost final lease after legacy writes did not prove resolver-closed state;
- repeated rollback builds did not remove prior legacy version keys.

### GREEN / fix evidence

Minimum root-cause fixes:

- flag-off legacy jobs now first revalidate the job lease and set the verified model to `assets_status='building'` before R2 writes;
- rollback asset keys are stable under `enrollments/legacy/assets/...`, eliminating successful-repeat key churn;
- old legacy asset keys are deleted while the model is resolver-closed and before the final ready commit; keys equal to the new stable set are not deleted;
- final commit revalidates the job lease and `status='verified' and assets_status='building'` before marking assets ready;
- legacy failure finalization no longer mutates model/enrollment failure state through the enrollment-bound failure path, preserving fail-closed resolver behavior;
- resolver already refuses interrupted rollback because `allow_legacy` still requires `assets_status='ready'`.

Focused command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py
```

Result: `31 passed, 1 warning`.

Affected command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py tests/test_detail_page_identity_source.py tests/test_detail_page_license_face.py tests/test_facemarket_identity.py tests/test_facemarket_biometric_cleanup.py tests/test_facemarket_biometric_enrollment.py tests/test_cut_input_authority.py
```

Result: `169 passed, 1 warning`.

Full server command:

```text
cd server && uv run pytest -q
```

Result: `2709 passed, 103 skipped, 391 warnings in 35.35s`.

Compile/diff/leak checks:

```text
git diff --check
cd server && uv run python -m compileall -q app/workers/fm_model_asset_job.py app/agents/identity_source.py tests/test_fm_model_asset_job.py tests/test_identity_source.py
cd server && rg -n "pairwise_min_similarity|load_face_qc|qc_score|str\\(exc\\)" app/workers/fm_model_asset_job.py
cd server && rg -n "provider leaked/key\\.png|legacy-job-old|legacy-job-1|old/front\\.png|quarantine/front\\.png" app/workers/fm_model_asset_job.py app/agents/identity_source.py app/workers/detail_page_job.py app/workers/editor_image_job.py app/facemarket.py app/facemarket_enrollment.py
```

Result: compile and diff checks exited 0; both leak/QC scans returned no production matches.

### Remaining concerns

No live R2 crash harness was run. The rollback path intentionally uses the stable-key plus `assets_status='building'` fence direction from the controller note; interrupted rollback builds are resolver-closed until a later rollback job completes the same stable keys and marks the model ready.

---

## Fix round 3 — legacy rollback model session fence

Status: complete in the Task 8 scope. This round closes the remaining flag-off rollback race: stable legacy keys are now protected by a per-model PostgreSQL session advisory fence held across the rollback branch’s DB commits, R2 reads/writes, final lease/model revalidation, final commit, and cancellation-safe unlock. The feature-on enrollment path behavior is unchanged.

Root cause: round 2 set `assets_status='building'` and committed before R2 writes, then released row locks. Because stable rollback keys are shared by every legacy job for a model, a stale same-model worker could write after a newer job committed, fail its final lease check, and corrupt the newer usable key contents. The same stale window could delete prior committed keys before proving the final lease/model fence.

### RED evidence

Focused command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py
```

Result before the fix: `4 failed, 32 passed, 1 warning`.

Expected failures covered:

- second same-model flag-off worker R2-wrote while the first worker was blocked mid-write;
- lost final lease with old assets deleted the prior committed key;
- prior legacy key deletion happened before the final `jobs done` commit;
- cancellation did not release any model advisory fence because no fence existed.

### GREEN / fix evidence

Minimum root-cause fixes:

- added a model-scoped session advisory fence using the existing Task4 `pg_try_advisory_lock(namespace, hashtext(canonical_id))` / shielded `pg_advisory_unlock` pattern;
- acquired the fence before legacy job/model validation and kept the same checked-out connection across building commit, personalization input load, R2 reads/writes, final lease/model revalidation, final ready/job-done commit, and unlock;
- made contended lock acquisition fail closed on the same connection, avoiding nested pool checkout;
- moved prior legacy key deletion strictly after the successful final commit and skipped keys equal to the new stable set;
- kept unlock cancellation-safe and connection-discarding on unlock failure, matching Task4’s session-lock safety boundary.

Focused command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py
```

Result: `36 passed, 1 warning`.

Affected command:

```text
cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py tests/test_detail_page_identity_source.py tests/test_detail_page_license_face.py tests/test_facemarket_identity.py tests/test_facemarket_biometric_cleanup.py tests/test_facemarket_biometric_enrollment.py tests/test_cut_input_authority.py
```

Result: `174 passed, 1 warning`.

Full server command:

```text
cd server && uv run pytest -q
```

Result: `2714 passed, 103 skipped, 391 warnings in 51.68s`.

Compile/diff/leak checks:

```text
git diff --check
cd server && uv run python -m compileall -q app/workers/fm_model_asset_job.py app/agents/identity_source.py tests/test_fm_model_asset_job.py tests/test_identity_source.py
cd server && rg -n "pairwise_min_similarity|load_face_qc|qc_score|str\\(exc\\)" app/workers/fm_model_asset_job.py
cd server && rg -n "provider leaked/key\\.png|legacy-job-old|legacy-job-1|old/front\\.png|quarantine/front\\.png" app/workers/fm_model_asset_job.py app/agents/identity_source.py app/workers/detail_page_job.py app/workers/editor_image_job.py app/facemarket.py app/facemarket_enrollment.py
```

Result: compile and diff checks exited 0; both leak/QC scans returned no production matches.

### Remaining concerns

No live PostgreSQL/R2 concurrent-worker crash harness was run. Deterministic fakes cover same-model contention, stale lease before and after the fence, lost final lease with prior assets, post-commit-only prior-key deletion, cancellation unlock, and resolver-closed interruption.
