# FaceMarket security hardening QA report

Date: 2026-08-22 KST

Result: local code QA GO / live-provider QA and production cutover STOP.

## Scope

This report records local integration evidence only. No production/staging cutover, batch creation, batch approval, R2 deletion, account deletion, Holder/AWS/OACX/provider/chain call, deployment, shared Supabase mutation, or real payout was executed.

Implemented scope verified here:

- biometric enrollment and current-evidence authorization contracts;
- mandatory local FaceLicense VC state binding and durable revoke queue contracts;
- late runtime authorization for detail/editor workers;
- strict purge/reconciliation, failed-resume, account deletion, and local cutover controller contracts, including a hermetic same-batch initial-cutover retry after partial object-store failure;
- private/no-store handling for every REAL-derived output plus durable Cloudflare purge retry targets;
- frontend enrollment/category/thumbnail contracts and production bundle build.

Real payout remains explicitly excluded. Settlement rows and UI-visible settlement behavior are local audit/payment-intent evidence only, not a proof of live payout rail execution.

## Boundary

The intended deployment remains three-server:

- Server 1: FastAPI API, application database access, FaceMarket catalog/license/generation gates, cutover control surface.
- Server 2: SAM/private segmentation service.
- Server 3: private OpenDID data plane: Holder, TAS, Issuer, CAS, OpenDID PostgreSQL, Besu.

This QA run did not execute Server 3 deployment, firewall validation, Holder bootstrap, production VC issue/revoke, or a production VC flow.

## Local verification evidence

All commands below ran from `/Users/nojeong-un/devs/wearless_studio/.worktrees/facemarket-security-hardening`.

| Check | Result |
| --- | --- |
| Full backend pytest | `3281 passed, 112 skipped`; 3393 collected, exit 0 on `0a7f33f6` |
| Python syntax | `cd server && .venv/bin/python -m compileall -q app scripts` exited 0 |
| Whitespace | `git diff --check` produced no output |
| Frontend test suite | `941 pass, 0 fail, 0 skipped` |
| Production frontend build | `vite build` transformed 3166 modules and exited 0; existing chunk-size warnings only |
| Holder clean build/test | `./gradlew clean test`: 37 tests, 0 failures/errors/skips |
| OpenDID local harness | export, provision, restore, managed/self-managed smoke suites all exited 0; live PostgreSQL integration remained explicitly gated |
| VC metadata codec | `deploy/opendid/test-verify-vcmeta.py`: 8 tests, OK |
| CLI validation | `python -m scripts.facemarket_security_cutover --help` exited 0 |
| Dependency manifest diff | no diff in `package.json`, `pnpm-lock.yaml`, `server/pyproject.toml`, `server/sam_service/requirements.txt` |
| Biometric log scan | `74 passed, 5 skipped, 1 warning`; grep found no forbidden biometric storage, VC, CI, or digest identifiers |

Expected environment-gated skips:

- 8 skips: `FACEMARKET_TEST_DATABASE_URL is not configured`.
- 2 skips: `set FACEMARKET_TEST_DATABASE_URL for live purge DB test`.
- 1 skip: `set FACEMARKET_TEST_DATABASE_URL for live job discovery test`.
- 98 skips: personalization tests skipped because an external `JobDispatcher` is polling the local test DB queue.
- 2 skips: SFace weights are not bundled locally and are Docker-build-only.
- 1 skip: pre-existing WIP `test_selling_points.py` prompt-context signature mismatch.

Measured frontend bundle facts from this run:

- `dist/assets/index-CWHfCLW4.js`: 1,157.71 kB, gzip 307.27 kB.
- `dist/assets/FaceLivenessStep-BHGEVsjd.js`: 1,634.93 kB, gzip 324.81 kB.
- `dist/assets/Editor-BhxQJzJ1.js`: 586.25 kB, gzip 191.46 kB.

The initial-bundle reduction from the enrollment work was not remeasured against a pre-hardening baseline in this task. Face-match/liveness accuracy remains unmeasured; measuring it requires a consented gold set and live provider evaluation.

## Security behavior covered by local tests

Measured local tests cover:

- Current-evidence authorization: real-model API and worker gates require active license, verified model, passed current enrollment, current asset evidence, allowed category, and VC-valid local/Holder status.
- AWS liveness enrollment contracts: local/dev adapters keep raw session IDs, browser-issued AWS credential material, raw portraits, live references, and embeddings out of durable payloads/logs; production enablement remains gated.
- Biometric threshold startup gates: liveness and SFace thresholds must be finite and strictly greater than zero; invalid configuration stops startup before DB or AWS client creation.
- Mandatory VC: license activation is bound to VC issuance state, verification is fail-closed, local revoke/freeze transitions enqueue durable revoke jobs, and Holder outage does not reopen local real-model generation.
- Durable revoke: owner revoke and cutover freeze/revoke paths keep one durable queue row per VC and preserve local non-active status across Holder failure.
- Strict purge/reconciliation: both face and public buckets are discovered, deleted, relisted, and headed before DB cleanup; R2/list/head/delete failures preserve DB references for retry.
- Durable cache purge: the complete DB/prefix-discovered target set is committed to a service-private manifest before origin deletion. A monotonic manifest revision and one scope lock prevent a stale worker from deleting targets discovered by a newer worker. Retry unions saved and newly discovered targets; the manifest is removed only in the same transaction as DB cleanup and the completion receipt.
- REAL-derived cache policy: detail and editor outputs that consumed REAL identity references are marked by the server and return `private, no-store` from R2, `/file`, and `/bytes`. Mirror/back cuts use attached identity evidence rather than the visible-face badge. Unmarked legacy AI output is conservatively sensitive; newly created virtual/product/non-REAL output records an explicit non-sensitive marker and remains immutable.
- Browser cache boundary: current clients use the `e=2` asset capability. Sensitive `/file?e=2` redirects to the API `/bytes?e=2` response with `private, no-store`, without appending unsigned query parameters to presigned R2 URLs.
- Cloudflare eviction: public R2 keys use host/path prefix purge, paced batches, sanitized failure handling, and bounded `429 Retry-After` retries. Missing zone/token fails before origin deletion.
- Same-batch initial-cutover retry: `test_fake_cutover_apply_resumes_same_batch_after_partial_r2_failure_without_duplicate_state` drives the real `apply_initial_cutover` through a hermetic partial object-store failure, verifies the batch fails without starting a second batch or duplicating revoke queue rows, then completes the same batch with durable terminal counts. The test explicitly treats idempotent retry delete attempts after unconfirmed list/head reconciliation as required fail-closed behavior, while confirming a third completed replay performs no additional deletes or revocation enqueue.
- Owner-only face access: biometric file access tests assert other-user and purged states return not-found/unauthorized behavior rather than raw storage exposure.
- Fixed non-biometric thumbnail: catalog/runtime paths avoid private face buckets for thumbnails and use non-biometric placeholders.
- Allowed/forbidden category enforcement: forbidden categories take precedence, product-only and virtual/vary paths do not enter real FaceMarket settlement/purge/cleanup paths, and retained/malicious real-model UUIDs are stripped from product-only requests before enqueue.
- Late runtime authorization: detail and editor workers recheck queued `_facemarket` snapshots before finalization; late revoke after output upload refunds, avoids success result/settlement/publication, and either deletes generated output or leaves a durable cleanup intent that survives restart.
- Account pre-delete/withdrawal: local tests cover writer closure, purge preconditions, transient R2 failure retry before identity anonymization, aggregate-only receipts, and no retained raw user/model/profile/enrollment/R2/digest/CI/VC/provider identifiers in receipts.

## Production STOP gates

Production cutover remains STOP until all of these are complete and reviewed:

1. Live AWS IAM and Face Liveness configuration in the approved region, including browser role, AI-services opt-out/legal review, and mobile-browser smoke.
2. OACX portrait contract approval: field path, encoding, maximum size, TTL, consent scope, and retention/deletion obligations.
3. Legal/privacy/consent approval for biometric collection, processing, retention, deletion receipt content, cross-border transfer, and vendor subprocessors.
4. Isolated live PostgreSQL migration/integration tests with `FACEMARKET_TEST_DATABASE_URL` set to a disposable database.
5. Holder/OpenDID Server 3 bootstrap, data migration, private networking/firewall rules, secret distribution, and restart persistence validation.
6. Live issue-valid-revoke-revoked-restart smoke through Server 1 to Server 3.
7. Apply `20260822020000_facemarket_purge_manifests.sql` and then `20260822030000_facemarket_purge_manifest_revision.sql` before enabling any destructive biometric purge path.
8. Deploy the backend before releasing the frontend that emits `e=2` asset capabilities; do not reverse this order.
9. Provision and validate `CLOUDFLARE_ZONE_ID` and the SSM-backed cache-purge token, confirm any Transform Rule post-transform paths, and prove a real `HIT -> origin delete -> prefix purge -> no body` cycle. Confirm no Edge TTL rule overrides `private, no-store`.
10. Destructive cutover approval, reviewed target batch, operator confirmation, and change window.

## Local QA boundary

The UI, API contracts, pure-provider adapters, failure recovery, and production bundles are ready for local QA. A true mobile Face Liveness plus OACX portrait comparison is not locally proven until AWS IAM/browser credentials and the OACX portrait response contract are supplied. The OpenDID scripts prove the lifecycle hermetically, but do not replace Server 3 deployment and live issue/revoke verification.

Cloudflare can remove server/CDN copies, not bytes already downloaded to a user's browser or device. The enforceable completion guarantee is that a purged object cannot be fetched again from the application, origin, or CDN; future REAL-derived responses use `no-store` to prevent new cache retention.

No production VC flow, Server 3 deployment, destructive cutover, real R2 deletion, or production account deletion was run in this QA task.
