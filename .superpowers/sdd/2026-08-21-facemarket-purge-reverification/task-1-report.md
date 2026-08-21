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

## Fix round 1

Reviewer issue:
- The static migration test only checked token presence and globally rejected `ON DELETE SET NULL`; it did not independently prove both `reverification_batch_id` columns had non-deletable FKs to `public.fm_cutover_batches(id)`.

Changes:
- Strengthened the static contract to require both `fm_models_reverification_batch_id_fkey` and `fm_licenses_reverification_batch_id_fkey` to bind `reverification_batch_id` to `public.fm_cutover_batches(id)`.
- The static contract rejects `ON DELETE SET NULL` and `ON DELETE CASCADE` inside each FK definition.
- Strengthened the optional live PostgreSQL test to inspect `pg_constraint` and `information_schema.referential_constraints` for both FK constraints, target table/schema/column, and delete action.
- Production DDL was not changed.

TDD evidence:
- Red: temporarily removed the `public.fm_cutover_batches(id)` reference from the model FK and ran `cd server && .venv/bin/pytest tests/test_facemarket_cutover_migration.py -q`.
  - Failed on `fm_models_reverification_batch_id_fkey must link reverification_batch_id to fm_cutover_batches(id)`.
- Green:
  - `cd server && .venv/bin/pytest tests/test_facemarket_cutover_migration.py -q`
  - `cd server && .venv/bin/pytest tests/test_facemarket_licenses.py -q`

Verification:
- `tests/test_facemarket_cutover_migration.py`: 1 passed, 1 skipped because `FACEMARKET_TEST_DATABASE_URL` is not configured.
- `tests/test_facemarket_licenses.py`: 113 passed.
