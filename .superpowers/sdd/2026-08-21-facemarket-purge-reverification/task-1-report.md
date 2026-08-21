## Task 1 report

Status: implemented and verified.

Changes:
- Added `20260821020000_facemarket_cutover_lifecycle.sql` with durable private cutover batch state.
- Added `previous_status` and non-cascading `reverification_batch_id` linkage for models and licenses.
- Made `fm_licenses.face_image_digest` nullable for cutover/reverification.
- Added a partial unique index allowing at most one active/resumable batch across `approved`, `draining`, `applying`, `reconciling`, and `failed`.
- Updated `LicenseCard.face_image_digest` to `str | None` with a response-model regression.

Controller overrides observed:
- Existing biometric status constraints were not redefined.
- Mandatory VC revocation jobs were not referenced.
- Batch linkage foreign keys do not use `ON DELETE SET NULL`.
- `failed` is included in the active-batch uniqueness set.
- No route behavior was changed.

TDD evidence:
- Red: `cd server && .venv/bin/pytest tests/test_facemarket_cutover_migration.py -q`
  - Failed because the migration file was missing.
- Red: `cd server && .venv/bin/pytest tests/test_facemarket_licenses.py::test_license_card_allows_missing_face_digest_during_reverification_cutover -q`
  - Failed because `LicenseCard` rejected `None` for `face_image_digest`.
- Green:
  - `cd server && .venv/bin/pytest tests/test_facemarket_cutover_migration.py -q`
  - `cd server && .venv/bin/pytest tests/test_facemarket_licenses.py::test_license_card_allows_missing_face_digest_during_reverification_cutover -q`
  - `cd server && .venv/bin/pytest tests/test_facemarket_licenses.py -q`

Verification:
- `tests/test_facemarket_cutover_migration.py`: 1 passed, 1 skipped because `FACEMARKET_TEST_DATABASE_URL` is not configured.
- `tests/test_facemarket_licenses.py`: 113 passed.

Concerns:
- Live PostgreSQL migration apply was skipped locally because `FACEMARKET_TEST_DATABASE_URL` is unset.
- Existing Starlette `TestClient` deprecation warning remains unrelated.
