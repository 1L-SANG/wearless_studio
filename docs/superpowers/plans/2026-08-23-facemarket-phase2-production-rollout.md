# FaceMarket Phase 2 — Production Rollout runbook (D1=biometric ON, D2=Server3 holder)

**Status:** PREP — no production action taken. Actual deploy/DB-migration/Server3 stand-up is a SEPARATE authorized step (irreversible + external + needs a deploy window + explicit go).

**Decisions locked (user, 2026-08-23):**
- **D1 = biometric IN launch scope** → prod must enable AWS Liveness + gov-ID 1:1 match → needs a vendor-verified prod OACX biometric contract (external, see BLOCKER).
- **D2 = full Server3 holder deploy** → stand up holder + OpenDID stack on a prod host + `FM_VC_REQUIRED=true` → resolves deploy-gate #1.

**Precondition (met):** branch `codex/facemarket-security-hardening` @ `b14c9f52` — 8 completion commits, all reviews clean, full suites green (backend 3307 / frontend 945 / holder 38, 0 fail).

---

## 🔴 HARD EXTERNAL BLOCKER (gates D1 — cannot be coded around)

**A vendor-verified production OACX biometric contract does not exist yet.** The only OACX biometric contract in the code is `dev-mock-v1`, gated to `app_env=="dev"` (`cx_identity.py:178-181`, `facemarket_enrollment.py:1998-2001`) and INERT in production. To enable biometric in prod, OmniOne must confirm the real OACX `/trans` portrait contract:
- the portrait field path + encoding in the trans-parse response,
- max bytes, TTL, allowed retention/use of the government-ID portrait.

Until that spec is obtained and a `prod` contract is implemented against it, `FM_BIOMETRIC_ENROLLMENT_ENABLED=true` in prod would fail closed (no shippable prod path). **This is the critical path for D1.** Action: get the OACX portrait contract from the OmniOne vendor channel.

---

## Rollout sequence (do NOT execute until authorized; each step reversible-until-noted)

### P2.1 — Stand up Server3 (holder + OpenDID) on a prod host
- Use `deploy/opendid/` (infra.compose.yml Postgres16.4 + Besu25.5.0 bound 127.0.0.1; systemd units for TAS/Issuer/CAS/fm-holder) + `docs/runbooks/facemarket-opendid-single-server.md`.
- Provision host, install Java 21 as JAVA_HOME, deliver secrets to `/opt/opendid/secrets` (0600), NO external exposure of :5432/:8090/:8091/:8094/:8545/:8100 — Server1(API) reaches only holder :8100 over private networking.
- Verify holder clean build (already fixed on this branch: vendored DTOs + Java21) on the target host.
- **State migration**: export existing OpenDID state (PostgreSQL ~125MB + Besu ledger + entity wallets) and restore on Server3 — the `deploy/opendid/{export,inventory,restore}-state.sh` + smoke harnesses exist. NOTE `services/fm-holder/data` gap: historical 12 VCs' model keys may be unrecoverable for revocation (accept or recover from a wallet backup).

### P2.2 — Resolve deploy-gate #1 (prod env) — reversible config
Set in `copilot/api/manifest.yml` (proposal, NOT yet applied):
- `FACEMARKET_VC_REQUIRED: "true"`
- `OPENDID_HOLDER_URL` = Server3 holder private URL (SSM secret)
- `OPENDID_HOLDER_HMAC_SECRET` = holder HMAC (SSM secret)
- keep `FACEMARKET_ENABLED: "true"`
→ satisfies `_validate_facemarket_vc_settings` (main.py:85-102): prod boots instead of crash-looping. Verify boot on staging first.

### P2.3 — Apply the 10 append-only migrations to prod PG16
- CI only greps SQL — it never applies DDL. Apply all 10 (7 biometric/purge + cx-digest + 2 settlement) to a real Postgres 16 **clone of prod** first (`FACEMARKET_TEST_DATABASE_URL`), confirm clean apply + no break to existing rows.
- ⚠️ IRREVERSIBLE: the `cx_tx_id` raw→sha256 backfill (`20260820000000`) cannot be undone — confirm acceptable before applying to real prod.

### P2.4 — Enable biometric in prod (BLOCKED on the vendor OACX contract)
Only after P2.4-blocker cleared:
- implement the `prod` OACX biometric contract against the vendor spec;
- set `FM_BIOMETRIC_ENROLLMENT_ENABLED: "true"`, `FM_OACX_CONTRACT_MODE: <prod-value>`, `FM_LIVENESS_CONFIDENCE_THRESHOLD`/`FM_ID_LIVE_THRESHOLD`/`FM_RETOUCHED_LIVE_THRESHOLD`/`FM_MATCH_POLICY_VERSION` (`FM_LIVENESS_BROWSER_ROLE_ARN` already wired); `FM_CI_PEPPER` present (Task 3 now fails boot if missing); SFace ONNX weights present in the image (Task 3 checks at boot).
- confirm AWS Rekognition Face Liveness (us-east-1) role assume works from the browser.

### P2.5 — End-to-end smoke on the target (before opening to users)
- Besu healthy first (avoid the clock-retrograde 150s hang — `docs/runbooks/opendid-besu-clock.md`).
- 4-step flow: CX verify → liveness + gov-ID 1:1 match → VC issue → VC verify → owner revoke → verify-revoked → Chain 70/20/10 settle. Confirm the VC revoke reconciler drains (Task 4 now starts it whenever holder configured).

---

## Go / No-Go checklist (all must be YES before opening biometric enrollment to real users)
- [ ] Vendor OACX prod portrait contract obtained + `prod` contract implemented (BLOCKER).
- [ ] Server3 up, private-only, holder clean-build green on host, state restored + persistence proven across restart.
- [ ] Staging boots with FM_VC_REQUIRED=true + holder reachable (no crash-loop).
- [ ] 10 migrations applied to a prod CLONE cleanly; cx_tx_id backfill accepted.
- [ ] Biometric thresholds + FM_CI_PEPPER + SFace weights present in the prod image (boot-validated).
- [ ] End-to-end smoke green (issue→verify→revoke→revoked→settle) on target.
- [ ] Besu healthy pre-open.

## What is prep-able NOW (reversible, no prod touch)
1. The P2.2 manifest env diff (as a proposal/PR, not applied).
2. A migration dry-run script for the prod PG16 clone.
3. A consolidated Server3 deploy runbook from `deploy/opendid/` + the existing single-server runbook.
4. This go/no-go checklist.

## Not prep-able by me (needs you / vendor / authorized window)
- The vendor OACX prod contract (OmniOne).
- Actual Server3 provisioning, secret delivery, firewall.
- Actual prod DB migration + cutover (authorized deploy window + explicit go).

---

## PREP DELIVERABLES produced 2026-08-23 (reversible, no prod touch)
- **`deploy/opendid/prod-env-proposal.md`** — exact manifest env change for Stage 1 (CX+VC+Chain: `FM_VC_REQUIRED=true` + holder URL/HMAC secrets) and Stage 2 (biometric flags, vendor-blocked). NOT applied — apply only when Server3 holder exists (flag + holder must land together, else the second gate raises).
- **`deploy/opendid/migrate-prod-dryrun.sh`** — applies pending `supabase/migrations/*.sql` to a target in version order; DRY-RUN default (each migration BEGIN…ROLLBACK, persists nothing) for validation against a prod CLONE, `--apply` to commit. Skips already-recorded versions (so the irreversible `cx_tx_id` backfill runs at most once). `bash -n` clean; needs `psql` at run time (deploy env has it; not run locally — host has no psql).
- **Server3 stand-up**: use the EXISTING `docs/runbooks/facemarket-opendid-single-server.md` (461 lines) + the `deploy/opendid/` scaffolding (compose/systemd/export/restore/smoke/verify-vcmeta). No new runbook written — that one is complete.

### Migration safety re-confirmed (static)
- No `CONCURRENTLY` in the new migrations → all transaction-safe → the dry-run BEGIN/ROLLBACK wrapper is valid.
- Only one non-additive statement across the 8 new FaceMarket migrations: `20260821020000_facemarket_cutover_lifecycle.sql` does `alter table public.fm_licenses alter column face_image_digest drop not null` — a RELAXING (nullable-widening) change: existing rows stay valid, old code that always supplied a value still works. Safe. Everything else is `create … if not exists` / additive columns / drop-then-add CHECKs / DO-block FK guards.

### Staged rollout summary
- **Stage 1 (no vendor dep)**: Server3 holder up → `prod-env-proposal.md` Stage-1 env → `migrate-prod-dryrun.sh` on a prod clone (then `--apply` at cutover) → CX verify + VC + Chain live; deploy-gate #1 resolved. Verify on staging first.
- **Stage 2 (vendor-blocked)**: OACX portrait contract from OmniOne → implement `prod` contract → Stage-2 env → biometric live.
