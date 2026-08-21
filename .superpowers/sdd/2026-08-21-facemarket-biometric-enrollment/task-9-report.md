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

## Fix round 1 — reviewer findings

### Summary

- Added the license-creation biometric flag gate before JSON parsing, SQL, or Holder calls.
- Canonicalized `enrollmentId` as UUID before any SQL.
- Locked the owned license row first in final activation; active concurrent winners now return the active card.
- Rechecked pending activation evidence under row locks and required model status `pending|reverification_required`.
- Added the same model-status predicate to the final model update.
- Validated Holder register/issue JSON as objects and `vcId` as a nonempty string inside the guarded code.
- Fixed the ON CONFLICT reload path to use persisted pending-license terms for Holder claims.
- Updated stale Holder config comments to describe the joint biometric+Holder cutover.

### RED evidence

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket_licenses.py -k 'suspended_model or concurrent_winner or malformed_holder_register_body or malformed_holder_issue_body or flag_off or malformed_enrollment_uuid or conflict_reload'
```

Output:

```text
FF...FF..FFFF                                                            [100%]
FAILED tests/test_facemarket_licenses.py::test_final_activation_rejects_suspended_model_and_rolls_back
FAILED tests/test_facemarket_licenses.py::test_final_activation_concurrent_winner_returns_active_card
FAILED tests/test_facemarket_licenses.py::test_malformed_holder_issue_body_is_closed_502[issue_body0]
FAILED tests/test_facemarket_licenses.py::test_malformed_holder_issue_body_is_closed_502[not-object]
FAILED tests/test_facemarket_licenses.py::test_malformed_holder_issue_body_is_closed_502[issue_body4]
FAILED tests/test_facemarket_licenses.py::test_license_creation_flag_off_rejects_json_and_multipart_before_db
FAILED tests/test_facemarket_licenses.py::test_malformed_enrollment_uuid_rejected_before_sql
FAILED tests/test_facemarket_licenses.py::test_conflict_reload_uses_persisted_terms_for_holder_claims
8 failed, 5 passed, 34 deselected, 1 warning
```

### GREEN evidence

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket_licenses.py -k 'suspended_model or concurrent_winner or malformed_holder_register_body or malformed_holder_issue_body or flag_off or malformed_enrollment_uuid or conflict_reload'
```

Output:

```text
.............                                                            [100%]
13 passed, 34 deselected, 1 warning
```

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket_licenses.py
```

Output:

```text
...............................................                          [100%]
47 passed, 1 warning
```

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket_licenses.py tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_identity.py
```

Output:

```text
150 passed, 1 warning
```

Command:

```bash
cd server && uv run pytest -q tests/test_facemarket*.py tests/test_fm_model_asset_job.py tests/test_detail_page_identity_source.py tests/test_detail_page_license_face.py tests/test_identity_source.py
```

Output:

```text
294 passed, 4 skipped, 1 warning
```

Command:

```bash
cd server && uv run pytest -q
```

Output:

```text
2745 passed, 103 skipped, 391 warnings in 67.64s
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
```

Command:

```bash
git diff --check
```

Output:

```text
# no output
```

### Leakage checks

Command:

```bash
cd server && bash -lc 'rg -n "iv\\.text|issue\\.text|response\\.text|claims.*logger|logger\\..*claims|logger\\..*body|SECRET_" app/facemarket.py; status=$?; [ $status -eq 1 ]'
```

Output:

```text
# no output
```

Command:

```bash
cd server && rg -n 'SECRET_HOLDER_BODY_WITH_CLAIMS|SECRET_CLAIM|faceImageKey|facemarket/models/.*/approved' tests/test_facemarket_licenses.py
```

Output:

```text
28:APPROVED_FRONT_KEY = f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/approved/front.png"
430:        self.text = "SECRET_HOLDER_BODY_WITH_CLAIMS"
449:            return _HolderResponse(self.stub.fail_with_status, {"error": "SECRET_CLAIM"})
455:            return _HolderResponse(200, {"userDid": "did:dev:user-1", "claims": "SECRET_CLAIM"})
650:    assert "SECRET_HOLDER_BODY_WITH_CLAIMS" not in response.text
651:    assert "SECRET_CLAIM" not in caplog.text
759:    assert "SECRET_CLAIM" not in response.text
760:    assert "SECRET_CLAIM" not in caplog.text
846:    assert "SECRET_HOLDER_BODY_WITH_CLAIMS" not in response.text
847:    assert "SECRET_CLAIM" not in caplog.text
1210:        "sha256-", "face_image", "faceImage", "faceImageKey",
```
