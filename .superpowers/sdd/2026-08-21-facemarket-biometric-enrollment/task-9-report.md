# Task 9 report — VC-gated model/license activation

## Summary

- Replaced FaceMarket license creation with JSON-only `enrollmentId` contract.
- Removed multipart/direct face/profile license creation from the route.
- Added owner-scoped enrollment evidence validation before pending license creation and again before activation.
- Made Holder wallet/register/issue synchronous and mandatory before `fm_licenses.status='active'`, `fm_models.status='verified'`, and enrollment `passed`.
- Added pending-license idempotency via the existing enrollment uniqueness and `Idempotency-Key: fm-license:<license_id>`.
- Added biometric startup gate for `OPENDID_HOLDER_URL`.

## RED evidence

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket_licenses.py -k 'license_starts_pending or holder_failure or repeated_pending or active_retry or enrollment_contract or multipart_license_request or malformed_holder or final_stale or biometric_startup'
```

Output:

```text
FFFFF.FFF                                                                [100%]
FAILED tests/test_facemarket_licenses.py::test_license_starts_pending_and_activates_only_after_vc
FAILED tests/test_facemarket_licenses.py::test_holder_failure_leaves_everything_non_active
FAILED tests/test_facemarket_licenses.py::test_repeated_pending_post_reuses_license_and_holder_idempotency
FAILED tests/test_facemarket_licenses.py::test_active_retry_returns_existing_card_without_reissue
FAILED tests/test_facemarket_licenses.py::test_enrollment_contract_rejects_stale_foreign_and_incomplete_assets
FAILED tests/test_facemarket_licenses.py::test_malformed_holder_issue_response_stays_pending
FAILED tests/test_facemarket_licenses.py::test_final_stale_transition_does_not_report_active
FAILED tests/test_facemarket_licenses.py::test_biometric_startup_requires_holder_url
8 failed, 1 passed, 25 deselected, 1 warning
```

Expected failure reasons: route still required multipart face/profile, Holder issue was background best-effort, startup did not require Holder URL, and final activation was not stale-guarded.

## GREEN evidence

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket_licenses.py -k 'license_starts_pending or holder_failure or repeated_pending or active_retry or enrollment_contract or multipart_license_request or malformed_holder or final_stale or biometric_startup'
```

Output:

```text
.........                                                                [100%]
9 passed, 25 deselected, 1 warning
```

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket_licenses.py
```

Output:

```text
..................................                                       [100%]
34 passed, 1 warning
```

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket_licenses.py tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_identity.py
```

Output:

```text
........................................................................ [ 52%]
.................................................................        [100%]
137 passed, 1 warning
```

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket*.py tests/test_fm_model_asset_job.py tests/test_detail_page_identity_source.py tests/test_detail_page_license_face.py tests/test_identity_source.py
```

Output:

```text
281 passed, 4 skipped, 1 warning
```

Command:

```bash
cd server && uv run pytest -q
```

Output:

```text
2732 passed, 103 skipped, 391 warnings
```

Command:

```bash
cd server && uv run python -m compileall app tests
```

Output:

```text
Listing 'app'...
Listing 'app/agents'...
Listing 'app/data'...
Listing 'app/services'...
Listing 'app/workers'...
Listing 'tests'...
Listing 'tests/fixtures'...
Listing 'tests/golden'...
Compiling 'tests/test_facemarket_biometric_enrollment.py'...
Compiling 'tests/test_facemarket_identity.py'...
Compiling 'tests/test_facemarket_licenses.py'...
```

Command:

```bash
git diff --check
```

Output:

```text
# no output
```

## Leakage checks

Checked that production code does not log Holder response bodies or VC claims, and tests assert failure responses/logs omit seeded Holder body/claim sentinels.

Command:

```bash
rg -n 'iv\\.text|issue\\.text|response\\.text|claims.*logger|logger\\..*claims|logger\\..*body|SECRET_' server/app/facemarket.py
```

Expected output: no matches.

## Notes

- `face_image_key` is still stored privately in `fm_licenses` and used only by the owner-gated face route.
- Production biometric enrollment remains off; this task does not add Holder HMAC/mandatory verify hardening.
