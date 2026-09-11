# PR280 등록과 지급 안정성 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans with test-driven-development. Work remains uncommitted until the user explicitly requests publication.

**Goal:** Fix the seven reviewed defects and the follow-up simulation gaps without real transfers or production changes.

**Architecture:** Keep verified identity separate from photo-only revalidation. Resolve canonical and legacy photo slots consistently. Manual payouts use immutable confirmation records containing the selected ledger entries, amount, owner and encrypted account snapshot; retries refer to the same record.

**Tech Stack:** FastAPI, PostgreSQL, Vite React JSX, pytest, node:test.

**Spec:** User-approved design in this conversation, 2026-09-11: seven initial findings plus six follow-up safeguards. Existing domain contract: documents/facemarket_mvp_implementation_brief_v1.md section 8.

## Global Constraints

- No commits, pushes, merges, production migrations, real transfers, Holder, OACX, R2 or email requests.
- Existing committed migrations are append-only. Add forward migrations only.
- Preserve unrelated work. Target worktree: .worktrees/facemarket-phase-c, initial HEAD 8b059a9d7cb49b4320a554f961254900abf9c840.
- Use existing UI components and API adapters, no redesign or new dependencies.
- Write failing behavior tests before production edits. Test real handlers and components with only external dependencies substituted.
- Account plaintext is transient only, masked after 60 seconds and never put into logs, browser persistence or audit payloads.

## Task 1: Manual payout confirmations

**Ownership:** server/app/facemarket_payout.py, new payout helper modules, new forward payout migration, payout backend tests, src/features/admin/AdminPayoutStatements.jsx and adminPayoutStatements.js, src/features/model/mypage/MyPageEarnings.jsx, payout-specific API adapters and tests. Coordinate shared adapter edits before changing files.

- [x] Reproduce stale totals, paid reversal, duplicate paid timestamp mutation, split snapshot aggregation and wrong displayed payment date.
- [x] Keep unpaid totals live, held means no payment authorization. Closed months only, enforced in server in Asia/Seoul time.
- [x] Confirm selected unallocated settlement IDs and compute amount/count from exactly that selection in one transaction/snapshot. Enforce unique active allocation. Additional settlements remain separate unpaid balance, including after prior payment.
- [x] Persist a confirmation UUID, responsible admin, immutable encrypted full account snapshot and immutable amount/entries. Recover a lost confirmation response by idempotency key or existing active confirmation. Never overwrite past paid amounts.
- [x] Explicitly distinguish prepared, transfer-started, paid and cancelled-before-transfer. Only responsible admin advances a confirmation. Do not automatically cancel uncertain transfers or release their entries. Repeated requests return the same outcome.
- [x] Check account version atomically before starting transfer; require new confirmation if changed. After start, finish against original snapshot even if account subsequently changes. Old generic paid status and paid reversal APIs fail closed.
- [x] Connect existing admin UI to confirmation/recovery, full account tuple reveal, 60-second masking, immutable paid display and actual payment date. No blind second transfer following a timeout. Show additional unpaid balance separately from confirmed payments.
- [x] Run payout backend/frontend tests. Use forward migration SQL validation on local ephemeral PostgreSQL if available; do not use configured production DB.

## Task 2: Enrollment, photo consistency and recovery

**Ownership:** server/app/facemarket_enrollment.py, server/app/workers/fm_model_asset_job.py, photo helper module, new forward enrollment migration if required, enrollment tests; src/features/model/ModelRegister.jsx, registerSlots.js, enrollment API adapters and tests.

- [x] Add failures for canonical face01 issuance evidence, legacy front read/delete, application name/DOB mismatch with face comparison disabled, terminal restart, no-edit back navigation, photo-only revalidation.
- [x] New writes use canonical slots, reads and deletes resolve actual stored rows. Canonical row wins deterministically when both exist; delete the whole logical slot, retain object cleanup retry tracking. Share resolution with worker and issuance/public catalog consumers.
- [x] Always compare application name and DOB when application gate is enabled, independently of photo face-match flag.
- [x] Reopen only license_pending with no pending/active issuance, atomically against license creation. Preserve identity and photos; clear dependent asset readiness and use a photo-only completion path that does not insert identity verification again. Reuse approved unchanged photos safely. Fence asynchronous work against changed state/evidence.
- [x] View-back without edits returns to conditions without completing enrollment again. Actual edit first obtains server reopen. vc_pending and passed remain read-only. Terminal failed/expired restart does not call unsupported cancel; uncertain active cancellation is not ignored.
- [x] Run registration frontend, enrollment/worker backend tests and reachable HTTP replay scenarios.

## Task 3: Cross-boundary certificate and verification

**Ownership:** server/app/facemarket.py, certificate Holder claim contract and tests, targeted CI diagnostic tests, implementation verification notes.

- [x] Use the common photo resolution in catalog, evidence load and activation. New face01 registration must reach issuance; no duplicate rows when both old and new slots exist.
- [x] Serialize issuance against photo reopening. Preserve digest checks, existing VC issue retry fencing and cleanup.
- [x] Align mutable DB terms with certificate role documented in the brief: signed identity/license evidence, current use conditions read from DB. Update both issuer and verifier/Holder contract together and cover behavior.
- [x] Diagnose the preview silent-error CI test without removing its behavior assertions; run CI-equivalent local frontend commands.
- [x] Run whole frontend suite, relevant broad backend suite and production build. Independently review changed surfaces, fix actionable findings, verify clean diff formatting.

## Execution record

- Baseline: 152 focused frontend tests and 368 backend tests passed in prior turn at unchanged HEAD. Two simulated reopen cases reproduced photos_required and identity_replay.
- No implementation changes exist at plan creation.
- Task 3 photo evidence: regression initially 4 failed, 1 passed. Shared predicate now passes 10 cases across SQLite and real PostgreSQL 17, including canonical-only, legacy-only, both insertion orders and canonical delete-pending fail-closed behavior.
- Task 3 issuance fence: new regression initially failed for missing registration lock, then caught inverted global-writer/registration lock order. Global writer boundary now precedes registration lock and license creation; test passed.
- Task 3 initial integration: 143 license and reconciler tests passed after stable consent/timestamp plumbing.
- CI reproduction: empty VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY reproduced the exact 1 != 0 console-error failure. Vite alias resolution bypassed the test auth stub. Matching the resolved absolute module and disabling env-file loading fixed the test under the same empty environment; production logging assertions remain unchanged.
- Disposable PostgreSQL container wearless-pr280-test-pg uses loopback port 55482 and no persistent volume. Existing local Supabase containers are untouched. Stop only this test container after verification.

## Final verification, 2026-09-12

- Frontend: CI=1 with empty Supabase environment, pnpm test:frontend: 1513 passed, 0 failed.
- Backend: 4768 passed, 83 skipped. Shared-database test_personalization.py deliberately excluded; configured production and shared local DBs were not used. Test-local database defaults were redirected to an unavailable loopback port.
- Explicit isolated PostgreSQL runs: payout 10, enrollment 6, photo evidence 5 passed; another 5 SQLite photo evidence cases passed. New migrations were executed against disposable schemas and concurrency tests used independent connections.
- Holder: full test and bootJar with --rerun-tasks: 40 tests, BUILD SUCCESSFUL. Frontend production build passed.
- Provisioning harnesses passed. The existing managed-smoke systemd/runbook static checks have 13 pre-existing failures, reproduced on unchanged HEAD; they were not changed as part of this repair. Actual issuer, bank, R2, OACX and mail calls remain untested and were not made.
- Frontend independent review clean after eliminating live-account plaintext from the payment screen. Root cross-review of the separately implemented backend identified and resolved stale status, incomplete API response and pending-license quarantine-cleanup regressions.
- Existing committed migrations unchanged. New files: 20260911170000_fm_manual_payout_confirmations.sql and 20260911235000_facemarket_photo_revalidation.sql. No duplicate migration versions remain.
- Deployment requires the two forward migrations and the v2 credential setup/Holder/API sequence in docs/runbooks/facemarket-v2-certificates.md. No production deployment, commit, push or merge performed.

## Main integration, 2026-09-12

- Integrated main 69368f0513141a8c4434c82ae487d9988c17b998 with git merge --no-commit --no-ff. HEAD remains 8b059a9d7cb49b4320a554f961254900abf9c840; MERGE_HEAD records main and the resolved index is awaiting an explicitly authorized commit.
- Backup of all reviewed local changes, including untracked files: stash af9cca99f407de8aee091a4ee75f57e8f37681b6, message pr280-reviewed-fixes-before-main-integration-20260912. Applied without dropping the backup.
- Resolved the 12 main conflict files and one additional retry-test conflict during restoration. No unmerged index entries or conflict markers remain.
- Preserved the 18-photo registration, MyPage and all payout/photo recovery fixes. Adopted main's no-duration-selection policy and until-withdrawal display. Terms PATCH ignores old duration inputs and preserves historical dates; permanent licenses cannot gain an expiry. The v2 VC contract remains immutable; main's C2PA date sentinel remains intact.
- Independent frontend review identified one stale withdrawal tooltip. Updated it to the merged canonical terms; its regression test failed before the copy fix and passed afterwards.
- Integrated tests retain new registration harnesses and v2 evidence expectations. Legal publishing comparison excludes manually maintained consent inputs from generated-output equality, while checking those public files exist.
- Final integrated frontend: 1528 passed. Backend: 4824 passed, 83 skipped, shared-DB test_personalization.py excluded as before. Production build and staged diff check passed. No production calls or migrations executed.
- GitHub PR has not been updated. A merge-resolution commit and push are required to publish these local results; neither was performed in this integration task.
