# FaceMarket security hardening QA report

Date: 2026-08-22 KST

Result: local dark-launch GO / production cutover STOP.

## Scope

This report records local integration evidence only. No production/staging cutover, batch creation, batch approval, R2 deletion, account deletion, Holder/AWS/OACX/provider/chain call, deployment, shared Supabase mutation, or real payout was executed.

Implemented scope verified here:

- biometric enrollment and current-evidence authorization contracts;
- mandatory local FaceLicense VC state binding and durable revoke queue contracts;
- late runtime authorization for detail/editor workers;
- strict purge/reconciliation, failed-resume, account deletion, and local cutover controller contracts;
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
| Focused FaceMarket/personalization security pytest set | `301 passed, 104 skipped, 1 warning in 5.97s` |
| Broader backend slice | `155 passed, 98 skipped, 1 warning in 3.07s` |
| Full backend pytest | `3119 passed, 112 skipped, 391 warnings in 40.71s` |
| Full backend pytest with skip summary | `3119 passed, 112 skipped, 391 warnings in 43.06s` |
| Python syntax | `cd server && .venv/bin/python -m compileall -q app scripts` exited 0 |
| Whitespace | `git diff --check` produced no output |
| Frontend test suite | `929 pass, 0 fail, 0 skipped` |
| Production frontend build | `vite build` exited 0 |
| CLI validation | `python -m scripts.facemarket_security_cutover --help` exited 0 |
| Dependency manifest diff | no diff in `package.json`, `pnpm-lock.yaml`, `server/pyproject.toml`, `server/sam_service/requirements.txt` |
| Biometric log scan | `73 passed, 5 skipped, 1 warning`; grep found no forbidden biometric storage, VC, CI, or digest identifiers |

Expected environment-gated skips:

- 8 skips: `FACEMARKET_TEST_DATABASE_URL is not configured`.
- 2 skips: `set FACEMARKET_TEST_DATABASE_URL for live purge DB test`.
- 1 skip: `set FACEMARKET_TEST_DATABASE_URL for live job discovery test`.
- 98 skips: personalization tests skipped because an external `JobDispatcher` is polling the local test DB queue.
- 2 skips: SFace weights are not bundled locally and are Docker-build-only.
- 1 skip: pre-existing WIP `test_selling_points.py` prompt-context signature mismatch.

Measured frontend bundle facts from this run:

- `dist/assets/index-QP9qouxY.js`: 1,157.44 kB, gzip 307.14 kB.
- `dist/assets/FaceLivenessStep-CdlQDrxZ.js`: 1,634.93 kB, gzip 324.81 kB.
- `dist/assets/Editor-BXCFvsop.js`: 585.13 kB, gzip 191.10 kB.

The initial-bundle reduction from the enrollment work was not remeasured against a pre-hardening baseline in this task. Face-match/liveness accuracy remains unmeasured; measuring it requires a consented gold set and live provider evaluation.

## Security behavior covered by local tests

Measured local tests cover:

- Current-evidence authorization: real-model API and worker gates require active license, verified model, passed current enrollment, current asset evidence, allowed category, and VC-valid local/Holder status.
- AWS liveness enrollment contracts: local/dev adapters keep raw session IDs, browser-issued AWS credential material, raw portraits, live references, and embeddings out of durable payloads/logs; production enablement remains gated.
- Mandatory VC: license activation is bound to VC issuance state, verification is fail-closed, local revoke/freeze transitions enqueue durable revoke jobs, and Holder outage does not reopen local real-model generation.
- Durable revoke: owner revoke and cutover freeze/revoke paths keep one durable queue row per VC and preserve local non-active status across Holder failure.
- Strict purge/reconciliation: both face and public buckets are discovered, deleted, relisted, and headed before DB cleanup; R2/list/head/delete failures preserve DB references for retry.
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
7. Destructive cutover approval, reviewed target batch, operator confirmation, and change window.

No production VC flow, Server 3 deployment, destructive cutover, real R2 deletion, or production account deletion was run in this QA task.
