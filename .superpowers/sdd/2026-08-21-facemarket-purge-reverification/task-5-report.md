# Task 5 report — FaceMarket writer boundary and quiescence

## Result

Implemented the controller override for Task 5:

- added the shared PostgreSQL FaceMarket writer boundary in `repo.py`;
- made job claim skip closed FaceMarket writer kinds and only skip personalization generation for purging/purged profiles;
- added idempotent pending job cancellation with credit release and cancelled event replay safety;
- added `facemarket_cutover.py` close/quiesce primitives for initial cutover and personalization;
- fenced real FaceMarket route, enrollment, license, finalizer, asset-worker, and personalization writer flows;
- preserved virtual/cache/editor-vary paths and unrelated job dispatch;
- kept cutover orchestration free of freeze, purge-engine calls, Holder calls, production approval/apply, and R2 mutation;
- applied controller addendum: `close_initial_cutover_writers` sets `started_at = coalesce(started_at, now())` in the same approved-to-draining update, while draining/failed replay preserves the first `started_at`.

## RED evidence

- `tests/test_job_claim_priority.py tests/test_facemarket_cutover.py`: failed first with missing `app.facemarket_cutover`.
- `tests/test_routes.py::test_editor_new_owns_category_model_and_license_snapshot`: failed first because the real-new editor path did not take the cutover boundary.
- Broad affected-file run exposed fake/expectation gaps around detail route ordering, enrollment pre-R2 commit timing, and finalizer SQL coverage; these were fixed before the final run.

## GREEN evidence

- `cd server && .venv/bin/pytest -q tests/test_facemarket_cutover.py` → `4 passed, 1 skipped`.
- `cd server && .venv/bin/pytest -q tests/test_facemarket_licenses.py::test_final_activation_rejects_batch_linked_pre_completion_evidence_and_revokes_vc tests/test_facemarket_licenses.py::test_final_activation_allows_batch_linked_strict_post_completion_evidence tests/test_facemarket_licenses.py::test_license_starts_pending_and_activates_only_after_vc` → `3 passed`.
- `cd server && .venv/bin/pytest -q tests/test_facemarket_cutover.py tests/test_job_claim_priority.py tests/test_routes.py tests/test_detail_page.py tests/test_facemarket_biometric_enrollment.py tests/test_fm_model_asset_job.py tests/test_facemarket_licenses.py tests/test_personalization.py` → `304 passed, 97 skipped`.
- `cd server && .venv/bin/pytest -q` → `3050 passed, 109 skipped`.
- `cd server && .venv/bin/python -m py_compile ...` → clean.
- `git diff --check` → clean.
- dependency-manifest diff check → clean.
- added-line leak scan → no new secret logging or scoped-value result exposure found.

## Live gaps

- `FACEMARKET_TEST_DATABASE_URL` advisory/refund test is present but skipped in this run because the environment variable was not configured.
- No live R2/AWS/Holder/provider calls were run; all R2/Holder paths remained mocked or local fakes.

---

## Fix round 1 result

Fixed the five review findings without migration, freeze/purge caller, production action, external mutation, or new dependency:

- cancelled Task5 pending jobs now keep `jobs.status = 'cancelled'` but emit supported `job_events.event_type = 'error'` with bounded code/message payload;
- photo upload now rechecks the global FaceMarket cutover boundary after the photo session fence and before cleanup intent/R2/link writes;
- detail cache hits remain open; FaceMarket cutover rejection is only on the no-cache writer path immediately before license lock/job/reserve;
- personalization start/refine enqueue paths load the profile `for_update=True` through job/generation insert and commit;
- asset job cutover sentinel terminalizes with exact bounded `facemarket_cutover_in_progress` payload.

The earlier controller addendum remains covered: `close_initial_cutover_writers` sets `started_at = coalesce(started_at, now())` in the same approved-to-draining update.

## Fix round 1 RED evidence

- Targeted review-fix command failed before implementation with `4 failed, 1 passed, 2 skipped`:
  - cancellation emitted unsupported `cancelled` event type;
  - photo upload still wrote R2 when cutover closed between first preflight and photo fence;
  - detail cache hit still took the cutover/license writer path;
  - asset cutover sentinel emitted generic `asset_build_failed`.
- Personalization start/refine lock tests were present but skipped locally because their database-gated fixture is skipped in this environment.

## Fix round 1 GREEN evidence

- `cd server && .venv/bin/pytest -q tests/test_facemarket_cutover.py::test_cancel_pending_job_refunds_once_and_writes_cancelled_event tests/test_facemarket_cutover.py::test_cutover_cancel_event_type_matches_current_schema_contract tests/test_facemarket_biometric_enrollment.py::test_cutover_close_between_photo_preflight_and_fence_writes_no_r2_or_link tests/test_detail_page.py::test_detail_cached_success_commits_verified_lock_immediately_before_return tests/test_personalization.py::test_start_generation_locks_profile_row_until_enqueue_commit tests/test_personalization.py::test_refine_generation_locks_profile_row_until_enqueue_commit tests/test_fm_model_asset_job.py::test_cutover_closed_asset_sentinel_terminalizes_with_exact_bounded_code` → `5 passed, 2 skipped`.
- `cd server && .venv/bin/pytest -q tests/test_facemarket_cutover.py tests/test_facemarket_biometric_enrollment.py tests/test_detail_page.py tests/test_personalization.py tests/test_fm_model_asset_job.py` → `164 passed, 99 skipped`.
- `cd server && .venv/bin/pytest -q tests/test_facemarket_cutover.py tests/test_job_claim_priority.py tests/test_routes.py tests/test_detail_page.py tests/test_facemarket_biometric_enrollment.py tests/test_fm_model_asset_job.py tests/test_facemarket_licenses.py tests/test_personalization.py` → `307 passed, 99 skipped`.
- `cd server && .venv/bin/pytest -q` → `3053 passed, 111 skipped`.
- `cd server && .venv/bin/python -m py_compile ...` → clean.
- `git diff --check` → clean.
- dependency-manifest diff check → clean.
- added-line leak/external-mutation/freeze/purge scan → clean.
- schema/static scan confirmed current `job_events` CHECK accepts `error` and `close_initial_cutover_writers` still sets `started_at = coalesce(started_at, now())` in the approved-to-draining update.

## Fix round 1 live gaps

- `FACEMARKET_TEST_DATABASE_URL` live advisory/refund test remains skipped when the URL is absent.
- Personalization start/refine `for_update` route tests remain skipped locally under the database-gated personalization fixture.
- No live R2/AWS/Holder/provider calls were run; all external paths remained mocked or local fakes.
