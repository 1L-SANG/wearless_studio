### Task 2 Report: Strict R2 HEAD Absence Evidence

Status: implemented in `R2Client.head()`; no `head_strict()` added.

Changes:
- `server/app/r2.py`: treats absence as confirmed only when `head_object` returns HTTP status `404` or exact error code `404`, `NoSuchKey`, or `NotFound`.
- `server/tests/test_r2.py`: added status-only 404 coverage plus 403, 500, malformed `ClientError`, and botocore transport timeout propagation coverage.

TDD evidence:
- Red: `.venv/bin/pytest tests/test_r2.py -q` failed on status-only 404 before the production change.
- Green: `.venv/bin/pytest tests/test_r2.py -q` passed after the `head()` fix.

Scope notes:
- No new method, dependency, or external R2 call.
- Upload, public URL, delete, and metadata return shape are unchanged.

### Round 1 Reviewer Fix

Changes:
- Kept malformed `ResponseMetadata` and nonnumeric `HTTPStatusCode` on the fail-closed path by falling through to the original bare `raise`.
- Added coverage for throttling, malformed status values/shapes, and `EndpointConnectionError`.

TDD evidence:
- Red: `.venv/bin/pytest tests/test_r2.py -q` failed because malformed status escaped as `ValueError`/`AttributeError` instead of the original `ClientError`.
- Green: `.venv/bin/pytest tests/test_r2.py -q` passed after safe status parsing.
