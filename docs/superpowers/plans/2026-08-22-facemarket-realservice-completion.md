# FaceMarket real-service completion (+ hackathon flow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the confirmed real-service correctness gaps left on the `codex/facemarket-security-hardening` branch so FaceMarket (verified real-person model marketplace) is production-complete, and the same running flow doubles as the hackathon MVP demo.

**Architecture:** The branch (124 commits) is audited SOLID (29/33 adversarial checks refuted). Remaining work = 4 confirmed defects + a few enforcement gaps, all pure-code and testable, PLUS a decision-gated rollout track (holder deploy / enable biometric / deploy-gate). Phase 1 = code-only fixes I execute now. Phase 2 = rollout, gated on two user decisions + external deps. Phase 3 = record the 4-step demo.

**Tech Stack:** Python/FastAPI (server), Java 21/Spring (services/fm-holder), React/Vite (src), Supabase/Postgres, AWS Rekognition Face Liveness (us-east-1), OpenDID (TAS/Issuer/CAS/Holder + Besu), OmniOne Chain (Besu settlement).

**Spec:** Grounded in the takeover audit (`scratchpad/takeover_digest.md`, this session) + `docs/research/2026-08-22-facemarket-claude-handoff.md` + `docs/research/2026-08-20-facemarket-security-remediation-technical-report.md`.

## Global Constraints (copy verbatim; every task inherits these)

- **Preserve every production STOP / fail-closed gate.** Do NOT weaken them to make things easier. Any dev-only path stays gated on `app_env == "dev"` and must be inert when `app_env == "production"` (pattern: `facemarket_enrollment.py:1998-2001`, `cx_identity.py:178-181`).
- **Migrations are append-only.** No `DROP`/`ALTER ... TYPE`/`SET NOT NULL` on existing columns. New file per change under `supabase/migrations/`.
- **No deploy, no shared-DB mutation, no R2 deletion, no production cutover, no push, no merge, no credential reuse without explicit user authorization.**
- **Preserve the identity-authority spine:** a UUID `modelId` is only ever REAL or REJECTED (never silently VIRTUAL/NONE/faceless); client `_facemarket` input stays discarded; server-owned authority snapshot only.
- **Preserve:** the `_asset_is_real_derived` no-store classifier, use-policy fail-closed ordering (forbidden wins), and memory-wiping of biometric buffers on all terminal paths.
- **Every code change is TDD:** failing test first, minimal fix, green, commit. Small commits.

---

## Phase 0 — Coordination gate (do before any edit)

- [ ] **Confirm branch ownership.** Codex last committed `6c02bcf1` ~10h ago, worktree clean, no edits in the last window — treat as released, but confirm with the user that Codex is stopped before editing. If Codex resumes, pause and re-sync.
- [ ] **Record baseline HEAD** (`6c02bcf1...`) so every Phase-1 commit is attributable and revertable.

---

## Phase 1 — Code-only real-service robustness (executable now, no external deps)

Ordered by severity. Each task is self-contained, testable without live AWS/holder/DB, and completes a real-service correctness gap. All are also invisible-to-demo (they harden the service, they are not the video) — so they progress the real service without blocking the hackathon path.

### Task 1: Account deletion must erase FaceMarket biometrics for profile-less / already-purged users (confirmed HIGH, #3)

**Files:**
- Modify: `server/app/personalization.py:460-464` (the `UPDATE ... WHERE status <> 'purged'` that no-ops for these users)
- Modify: `server/app/routes.py:538-551` (`DELETE /me/account` path that passes `profile=None`)
- Read/Modify: `server/app/facemarket_cutover.py:907` (`quiesce_personalization_writers` / `_load_purging_profile` requirement) and `server/app/workers/personalization_purge_job.py:98`
- Test: `server/tests/test_facemarket_cutover.py` (or `test_biometric_purge.py`)

**Interfaces:**
- Produces: an account-deletion path where a user with NO personalization profile (or an already-`purged` one) still (a) erases any FaceMarket biometric assets/enrollments they own, (b) writes the aggregate purge receipt, (c) reaches `ready_for_identity_delete` — never silently freeze-only.

- [ ] **Step 1: Write the failing test** — a user with a FaceMarket model + biometric enrollment but personalization profile absent (or `status='purged'`) invokes account deletion; assert biometric rows/objects are erased, a receipt row is written, and the terminal state is `ready_for_identity_delete` (not merely frozen).
- [ ] **Step 2: Run it — expect FAIL** (today: 0-row update ⇒ no purge ⇒ frozen-only).
- [ ] **Step 3: Fix** — decouple the FaceMarket biometric erase from the personalization-profile `purging` precondition: drive erase off model/enrollment ownership, and make the purge job still write the receipt + advance state when no profile exists. Keep the writer-boundary lock and idempotency.
- [ ] **Step 4: Run tests — green.** Also run the existing cutover/purge suite to confirm no regression.
- [ ] **Step 5: Commit** — `fix(facemarket): erase biometrics on account deletion for profile-less/purged users`.

### Task 2: VC issuance must not permanently brick on a transient first-attempt failure (confirmed HIGH, #2)

**Files:**
- Modify: `services/fm-holder/src/main/java/kr/wearless/fmholder/api/IssueIdempotencyStore.java:104-105` (intent persisted+fsynced BEFORE `action.call()`, no cleanup on throw)
- Test: `services/fm-holder/src/test/java/kr/wearless/fmholder/api/IssueIdempotencyStoreTest.java`

**Interfaces:**
- Produces: idempotency semantics where a *thrown* first attempt does NOT leave a poison tombstone that blocks all future retries of the same `fm-license:<UUID>`; a *successful* result is still persisted exactly-once and replay-safe.

- [ ] **Step 1: Write the failing test** — first `action.call()` throws (simulate holder/wallet outage); assert a subsequent retry with the same idempotency key is allowed to run (not rejected as duplicate) and can succeed.
- [ ] **Step 2: Run it — expect FAIL** (today: intent tombstone written before the call ⇒ retry sees an unresolved intent ⇒ bricked).
- [ ] **Step 3: Fix** — persist only a *resolved success* record, or wrap `action.call()` so a thrown attempt removes/invalidates the pending intent; optionally add a stale-intent sweeper. Keep atomic write + fsync + directory fsync for the success record; keep restart-replay for genuine duplicates.
- [ ] **Step 4: Run `./gradlew test` (holder) — green.**
- [ ] **Step 5: Commit** — `fix(fm-holder): don't tombstone VC issuance on transient first-attempt failure`.

### Task 3: Startup must fail fast when biometric completion prerequisites are missing (MED)

**Files:**
- Modify: `server/app/facemarket_enrollment.py:1962-2001` (`validate_biometric_settings` — currently omits `fm_ci_pepper` and SFace ONNX weight-file existence)
- Read: `server/app/agents/face_qc.py:44-46` (weight paths), `server/app/facemarket_enrollment.py:1722-1724` (pepper use)
- Test: `server/tests/` biometric startup-validation test

**Interfaces:**
- Produces: a boot-time check so that when `fm_biometric_enrollment_enabled` is true, missing `fm_ci_pepper` or missing SFace/YuNet ONNX files raise at startup — instead of the server booting "healthy" and then stranding every enrollment in `processing`.

- [ ] **Step 1: Write the failing test** — biometric enabled + `fm_ci_pepper` unset ⇒ `validate_biometric_settings` raises; and biometric enabled + weight file absent ⇒ raises.
- [ ] **Step 2: Run it — expect FAIL** (today: neither is validated).
- [ ] **Step 3: Fix** — add both checks to `validate_biometric_settings`, mirroring the existing threshold-validation style. Fail-closed, clear message.
- [ ] **Step 4: Run tests — green.**
- [ ] **Step 5: Commit** — `fix(facemarket): validate ci_pepper + SFace weights at biometric startup`.

### Task 4: Revocation reconciler must run whenever revoke jobs can be enqueued (MED)

**Files:**
- Modify: `server/app/main.py:125-127` (reconciler starts only if `fm_vc_required`, but `revoke_license`/cutover enqueue whenever a license has a `vc_id`)
- Test: `server/tests/` reconciler-start test

**Interfaces:**
- Produces: the reconciler starts when a Holder is configured (revoke jobs are drainable), not only when `fm_vc_required` is true — so enqueued revoke jobs are never orphaned.

- [ ] **Step 1: Write the failing test** — holder configured, `fm_vc_required=false`; assert the reconciler is started (or that enqueued revoke jobs get drained).
- [ ] **Step 2: Run it — expect FAIL.**
- [ ] **Step 3: Fix** — gate reconciler start on holder-configured (URL+secret present) OR `fm_vc_required`, keeping the per-process single-flight + fencing.
- [ ] **Step 4: Run tests — green.**
- [ ] **Step 5: Commit** — `fix(facemarket): start VC revoke reconciler whenever holder is configured`.

### Task 5: Decide + implement `assets_source_hash` runtime behavior (MED)

**Files:**
- Read: `server/app/workers/fm_model_asset_job.py:353,545` (writes hash) and `server/app/agents/identity_source.py:34-66` (asset resolution — never reads it)
- Modify: whichever the decision selects
- Test: `server/tests/test_identity_source.py` or asset-job test

**Interfaces:**
- Produces: `assets_source_hash` is either (a) enforced at asset-resolution time (stale/mismatched source ⇒ asset rejected, fail-closed) or (b) explicitly dropped with a comment — no stored-but-ignored integrity field.

- [ ] **Step 1: Decision** — enforce vs drop. Default recommendation: **enforce** (integrity is the point of a real biometric service). Confirm with user if ambiguous.
- [ ] **Step 2: Write the failing test** (if enforce) — asset whose `assets_source_hash` no longer matches current enrollment source ⇒ `resolve_real_model_assets` returns None (fail-closed to REJECTED, never a stale face).
- [ ] **Step 3: Run it — expect FAIL.**
- [ ] **Step 4: Fix** — read + compare the hash in the resolution path; mismatch ⇒ None.
- [ ] **Step 5: Run tests — green. Commit** — `feat(facemarket): enforce assets_source_hash at identity resolution`.

### Task 6: Enrollment wizard must not hard-reset the whole flow on a transient error (MED, #4)

**Files:**
- Modify: `src/features/model/ModelRegister.jsx:305-315` (`finishIdentity`/`abandonLiveness` catch destroys the enrollment on any liveness/OACX/network throw) and `:424-433`
- Test: `tests/frontend/` ModelRegister test

**Interfaces:**
- Produces: a transient liveness/OACX/network error returns the user to the failed step with a retry, instead of discarding the enrollment and forcing a restart from consent + 3 photos.

- [ ] **Step 1: Write the failing test** — simulate a network throw during identity completion; assert the enrollment id is retained and the user can retry the identity/liveness step (photos not re-required).
- [ ] **Step 2: Run it — expect FAIL.**
- [ ] **Step 3: Fix** — distinguish transient (retryable) from terminal (abandon) errors; only abandon on true terminal states. Keep server-side single-winner transitions and no-persist-to-storage rules.
- [ ] **Step 4: Run `pnpm test:frontend` — green.**
- [ ] **Step 5: Commit** — `fix(facemarket): retry enrollment step on transient error instead of full reset`.

---

## Phase 2 — Rollout (DECISION-GATED; external deps; NOT executed until decisions + auth)

These are blocked on two user decisions and external infrastructure. Do not start until resolved.

**Decision D1 — Is biometric (AWS Liveness + gov-ID 1:1 match) in the real-service launch scope?**
- Prod today: `FM_BIOMETRIC_ENROLLMENT_ENABLED=false`, `FM_OACX_CONTRACT_MODE=disabled`. The subsystem is dev-only until a **verified production OACX biometric contract** exists (the current one is `dev-mock-v1`, dev-gated). Enabling biometric in prod REQUIRES that vendor-verified OACX portrait contract (field path/encoding/TTL/retention) — an external dependency on OmniOne.
- If YES → Phase 2 must build/verify the prod OACX contract, then flip the flags. If NO → biometric stays a dev/demo showcase; prod runs CX-verify + VC + Chain only.

**Decision D2 — Deploy the OpenDID Holder to prod, or staging/tunnel for now? (also resolves deploy-gate #1)**
- The startup gate (`main.py:85-93`) forces: prod + FaceMarket ⇒ `FACEMARKET_VC_REQUIRED=true` ⇒ Holder URL + HMAC required. The committed manifest violates this (`FACEMARKET_ENABLED=true`, no `FM_VC_REQUIRED`, holder commented) ⇒ **deploying this branch as-is crash-loops prod.**
- Options: (A) stand up Holder (Server 3) + set `FACEMARKET_VC_REQUIRED=true` + `OPENDID_HOLDER_URL`/`_HMAC_SECRET` → full path; (B) expose the already-running local Holder via a tunnel and point prod at it (matches handoff "demo via local tunnel"); (C) keep `FACEMARKET_ENABLED=false` in prod until the holder is real. Do NOT relax the gate itself (STOP gate).

Outline tasks (detail after decisions):
- [ ] Resolve deploy-gate #1 per D2 (env + holder reachability), verify prod boots.
- [ ] Apply all 10 append-only migrations to a real Postgres 16 prod clone (`FACEMARKET_TEST_DATABASE_URL`) — CI only greps SQL, never applies DDL. Confirm the irreversible `cx_tx_id` backfill is acceptable.
- [ ] (If D1=YES) Build + vendor-verify the production OACX biometric contract; then enable `FM_BIOMETRIC_ENROLLMENT_ENABLED` + set `FM_OACX_CONTRACT_MODE`.
- [ ] Smoke the 4-step flow end-to-end on the target: CX verify → liveness + 1:1 match → VC issue/verify → Chain settle.

---

## Phase 3 — Hackathon MVP video (after Phase 2 makes the flow runnable)

- [ ] Verify Besu healthy (avoid the clock-retrograde 150s hang; `docs/runbooks/opendid-besu-clock.md`) before recording, to dodge the VC-issuance timing failure.
- [ ] Record the 4-step flow end-to-end (Step 01 신원검증 CX+liveness+match → Step 02 라이선스 VC + 허용/금지 용도 → Step 03 거래 → Step 04 온체인 70/20/10 정산) as the MVP 시연 동영상 (submission artifact).
- [ ] Capture the OpenDID VC (선택과제1) + Chain settlement (선택과제2) live moments for the 가산 10점 evidence.

---

## Self-review notes

- Phase 1 tasks each map to a confirmed defect or a census gate-broken finding, are testable without external infra, and preserve the STOP gates (Global Constraints).
- Phase 2 is intentionally not detailed into TDD steps: it is gated on D1/D2 and external vendor/infra deps; detailing it now would be placeholder guesswork.
- Task 5 carries an explicit decision, surfaced rather than assumed.
