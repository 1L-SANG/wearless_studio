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
