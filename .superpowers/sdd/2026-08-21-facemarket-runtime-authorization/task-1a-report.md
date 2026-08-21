# Task 1A: Runtime License Category Boundary

## Initial implementation

- Commit: `01d54d95`
- Closed `allowedUse` and `forbiddenUse` to their distinct approved six-value tuples at the enrollment-bound JSON endpoint.
- Preserved whitespace trimming, empty-value removal, order, duplicate removal, biometric JSON issuance/idempotency, and legacy multipart rejection.
- Verification: 11 focused cases and all 63 license tests passed; compile, diff, and scoped leak checks passed.

## Review round 1

- Finding: both pending-license reuse paths replaced validated request terms with stored terms without revalidating them, allowing legacy free strings to reach enrollment mutation and Holder issuance.
- RED: initial pending-row reuse failed 2 stored allowed/forbidden cases; insert-conflict reload failed the same 2 cases.
- Fix: after either row-selection path resolves the authoritative terms, run both lists through the existing `_clean_uses` boundary once before the enrollment update and Holder call.
- GREEN: 9 focused retry/multipart cases and all 67 license tests passed; compile, diff, and scoped leak checks passed.
- Gap: live Holder integration was not exercised.
