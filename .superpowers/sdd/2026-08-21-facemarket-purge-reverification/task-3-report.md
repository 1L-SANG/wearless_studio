# Task 3 report — shared biometric purge engine

## Status

Implemented and verified the controller-ruling scope only:

- Created `server/app/services/biometric_purge.py`.
- Created `server/tests/test_biometric_purge.py`.
- Did not modify `personalization_purge_job`, routes, cutover/VC wiring, or external storage.

## Behavior shipped

- Public API accepts exactly one server-discovered scope:
  - `user_id` for `withdrawal` / `account_delete`
  - `batch_id` for `reverification`
- Fails closed with bounded `PurgeIncomplete.code` values.
- Requires both `app.state.r2` and `app.state.r2_face` before DB discovery or R2 mutation.
- Discovers scoped models, licenses, profiles, enrollments, FaceMarket cleanup rows, personalization rows, derived jobs, recursive generation/edit lineage, derived asset rows, lineage prompt keys, and deterministic prefixes from DB state.
- Lists prefixes in both buckets before delete, deletes `(bucket_label, key)` targets, then re-lists prefixes and heads original targets before DB cleanup.
- Revalidates DB discovery inside the final transaction and fails closed if newly discovered known targets or prefixes were not part of the reconciled target set.
- Cleans DB only after confirmed object absence:
  - clears FaceMarket face references and model asset fields;
  - deletes FaceMarket model/enrollment cleanup rows;
  - deletes scoped biometric enrollment rows when schema supports them;
  - deletes personalization photos/generations/identity rows and marks user-scope profiles purged;
  - tombstones scoped derived `assets` instead of hard-deleting them;
  - clears related lineage prompt keys and editor/project/job URL-bearing state.
- Leaves status transitions, consent/audit/account anonymization, settlements, and VC queue behavior untouched for later tasks.
- Suppresses raw DB/R2 exception chaining from public `PurgeIncomplete` tracebacks.

## TDD evidence

RED:

```text
.venv/bin/pytest tests/test_biometric_purge.py -q
E   ModuleNotFoundError: No module named 'app.services.biometric_purge'
```

GREEN / verification:

```text
.venv/bin/pytest tests/test_biometric_purge.py -q
12 passed, 2 skipped, 1 warning
```

```text
.venv/bin/python -m py_compile app/services/biometric_purge.py tests/test_biometric_purge.py
exit 0
```

```text
.venv/bin/pytest tests/test_biometric_purge.py tests/test_r2.py \
  tests/test_facemarket_biometric_migration.py \
  tests/test_facemarket_biometric_enrollment.py \
  tests/test_facemarket_cutover_migration.py -q
131 passed, 4 skipped, 1 warning
```

Optional live DB probe:

```text
FACEMARKET_TEST_DATABASE_URL=${DATABASE_URL:-postgresql://postgres:postgres@127.0.0.1:54322/postgres} \
  .venv/bin/pytest tests/test_biometric_purge.py -q -rs
12 passed, 2 skipped, 1 warning
```

The local DB explicitly skipped live cleanup tests because it lacks the latest biometric/cutover schema columns and tables.

Fix-round RED:

```text
.venv/bin/pytest tests/test_biometric_purge.py -q
4 failed, 8 passed, 2 skipped, 1 warning
```

Failures covered the independent review gaps: recursive lineage objects survived, second-pass discovery expansion did not fail closed, idempotent replay rediscovered cleaned keys, and exception chaining exposed a raw object key.

Fix-round GREEN:

```text
.venv/bin/pytest tests/test_biometric_purge.py -q
12 passed, 2 skipped, 1 warning
```

```text
.venv/bin/python -m py_compile app/services/biometric_purge.py tests/test_biometric_purge.py
exit 0
```

```text
.venv/bin/pytest tests/test_biometric_purge.py tests/test_r2.py \
  tests/test_facemarket_biometric_migration.py \
  tests/test_facemarket_biometric_enrollment.py \
  tests/test_facemarket_cutover_migration.py -q
131 passed, 4 skipped, 1 warning
```

## Notes / concerns

- Root `AGENTS.md` was requested but absent in the worktree/root checkout; only dependency copies under `node_modules` exist. I followed the AGENTS payload supplied with the task.
- The always-on fake DB/R2 suite now covers the corrected brief matrix: user/batch isolation, enrollment rows and both cleanup outboxes, both buckets with the same key, partial delete retry, relist/head/list uncertainty, unfrozen/active/unfenced preflight, recursive lineage, tombstone sanitization, second-pass expansion, status/VC/audit untouched, idempotence, and result/exception/repr/log redaction.
- Actual schema/FK trace confirmed `fm_biometric_enrollment_photos.enrollment_id` is `ON DELETE CASCADE`; the deterministic fake now models that cascade.
- No real R2, Holder, VC, route, worker, or migration action was performed.
