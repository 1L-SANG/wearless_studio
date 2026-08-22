# FaceMarket security hardening — Claude handoff

Date: 2026-08-22 KST

This is a repository-grounded handoff, not a verbatim transcript. It intentionally excludes hidden system/developer prompts, private chain-of-thought, and secret values. The exact product history remains available through Git, tests, plans, and reports referenced below.

## Copy/paste prompt for Claude

```text
You are taking over a large FaceMarket biometric-security and OpenDID hardening branch. Work from evidence in the repository, not from this summary alone. Do not deploy, mutate a shared database, delete R2 data, execute a production cutover, push, merge, or reuse any credential without explicit user authorization.

Repository:
- Common repo: /Users/nojeong-un/devs/wearless_studio
- Working tree: /Users/nojeong-un/devs/wearless_studio/.worktrees/facemarket-security-hardening
- Branch: codex/facemarket-security-hardening
- Verified QA-report HEAD before this handoff document: a3998acf63bdfc01c940498942805b1990a59c2f
- Last production-code HEAD: a1a65cf8d9502d1689da00e2e68e7fa4ee7106ba
- Base branch: main
- At handoff: 0 behind / 123 ahead of main, clean worktree

User goal:
- Make FaceMarket safe enough to support verified real-person models.
- Implement everything below except real money payout/disbursement.
- Let models upload mildly retouched photos, but keep them quarantined until revalidation.
- Use AWS Face Liveness in us-east-1 (N. Virginia).
- Require government-ID portrait versus live face 1:1 matching.
- Make FaceLicense VC mandatory for real-model generation.
- Make face freeze/purge and account deletion complete and retry-safe.
- Prepare private OpenDID Server 3 deployment and persistence.
- Ultimately give the user a browser-based QA path.

Explicit product decisions:
- Actual payout/disbursement is excluded. Existing settlement/payment-intent rows are local evidence only.
- Photo order is front -> angle45 -> side. The apparent 45/side reversal was inspected and the wizard uses this order.
- Retouched photos are allowed only as private quarantine inputs. Adding/replacing them invalidates old assets/VC and requires the entire identity, liveness, and match flow again.
- No user-uploaded ID portrait fallback. Only the reviewed OACX transaction portrait is acceptable.
- Current UI flow is consent -> three photos -> AWS Liveness -> OACX -> processing -> license terms.
- Production must fail closed if AWS, OACX, Holder, current biometric evidence, allowed-use policy, or VC verification is unavailable.
- Virtual models and product-only cuts must stay independent of FaceMarket biometric/VC checks.

What was implemented:

1. Biometric enrollment
- Dedicated enrollment state and append-only migrations.
- Separate consent, front/angle45/side uploads, optional retouched photos, one-use liveness session, OACX completion, processing, and license terms.
- AWS Rekognition Face Liveness browser session and temporary credential boundary.
- OACX portrait parsing through an injected contract. Production contract remains disabled until the provider field schema is verified.
- SFace one-to-one matching for ID portrait vs live reference and retouched photos vs live reference.
- Startup rejects missing, boolean, zero, negative, NaN, infinity, or out-of-domain biometric thresholds before DB/AWS initialization.
- Raw OACX tokens, government portraits, live references, embeddings, credentials, and detailed scores are memory-only and wiped/released on terminal paths.
- Cancellation, timeout, retry, duplicate session, replay, process interruption, and late completion races are covered.

2. Private face assets
- Enrollment photos stay in private R2 quarantine.
- Approved assets are versioned and tied to current enrollment/evidence policy.
- Upload/cleanup uses durable intents, leases, ownership fences, and retry-safe terminal cleanup.
- Catalog thumbnails are fixed non-biometric SVGs. Catalog and thumbnail routes do not expose private keys or fetch face bytes.
- Owner face access requires current eligible enrollment/license/evidence and returns private no-store responses.

3. Enrollment wizard/frontend
- Model registration wizard implements consent, ordered photos, lazy-loaded Amplify FaceLiveness, OACX, polling, retry, timeout, and enrollment-bound license terms.
- AWS credentials, liveness session ID, OACX token, and images are not persisted to local/session storage or logged.
- Liveness dependency is lazy-loaded to avoid loading the large bundle on initial app startup.
- Manual direct face-license build/generation paths were removed or guarded.

4. Allowed/forbidden use policy
- Closed category lists are shared across issuance, persistence, routes, editor UI, and worker verification.
- Unknown, malformed, cross-list, or forbidden categories fail closed.
- Forbidden use wins over allowed use.
- Worn REAL paths require a persisted category before enqueue; retry shares the same guard.
- Product-only requests clear retained model IDs and do not enter FaceMarket settlement or verification.

5. Mandatory OpenDID FaceLicense VC
- Holder endpoints use exact-body HMAC authentication, timestamp/nonce replay protection, bounded request bodies, and a singleton persistent nonce directory.
- FaceLicense issuance uses strict idempotency keys (`fm-license:<UUID>`), atomic file persistence, directory fsync, restart replay, and fail-closed corrupt/unresolved intents.
- Server Holder client signs requests and rejects unsafe path references.
- License activation happens only after issuance and current-evidence finalization.
- Holder outage or invalid/revoked VC blocks real-model use.
- Owner revoke and cutover freeze enqueue durable revoke jobs atomically.
- Reconciler uses leased/fenced PostgreSQL claims and signed verify -> revoke -> verify operations.

6. Runtime authorization
- Server resolves current model, current license, current passed enrollment, approved photo, current derived assets, and persisted use category.
- Detail/editor routes snapshot server-owned authority into queued jobs; client `_facemarket` input is discarded.
- Workers verify before private reads/provider calls and again under the writer boundary before publishing.
- Late revoke/cutover/account purge causes full refund, no success result, no settlement, no public output, and candidate cleanup/durable cleanup intent.
- Legacy project license-pin face fallback was removed. A REAL UUID cannot silently become virtual/faceless.
- Vary requests derived from REAL output inherit only trusted producer-job provenance and remain purge-discoverable.

7. Freeze, purge, account deletion, and cutover
- Initial cutover batches have durable lifecycle, immutable manifest identity, approval/close/freeze/reconcile/resume gates, and same-batch retry.
- One global writer boundary serializes enrollment completion, asset writers, cutover, withdrawal, and account deletion.
- Purge discovers DB-known and prefix-discovered objects in private/public buckets, deletes, relists, heads, and only then removes DB references.
- Partial R2 failure retains references and resumes the same batch/job.
- Aggregate-only receipts survive identity deletion without retaining raw user/model/profile/key/digest/CI/VC/provider identifiers.
- Existing models/licenses are frozen without reactivating old biometric evidence.

8. CDN/browser privacy
- REAL-derived detail/editor/vary output is server-marked sensitive and receives `private, no-store` from R2, `/file`, and `/bytes`.
- Mirror/back classification follows attached REAL evidence, not just visible-face badges.
- Unmarked legacy `source=ai` assets are conservatively treated as sensitive. New ordinary/virtual/product outputs explicitly record false and remain immutable.
- Cloudflare R2-key prefix purge is durable, paced, retryable on bounded 429/Retry-After, and token-safe.
- A service-private purge manifest is committed before origin deletion. Revision CAS and an advisory lock prevent stale workers from deleting a newer target set.
- Frontend cache capability defaults to e=1. Only exact build-time `VITE_ASSET_CACHE_VERSION=2` emits e=2 after backend deployment.
- Sensitive `/file?e=2` redirects to API `/bytes?e=2`, not to a query-mutated presigned R2 URL.
- API capability URLs bypass Cloudflare image transforms; direct public images still use transforms.

9. OpenDID Server 3 preparation
- Holder dependency/build is pinned to reviewed OpenDID V2-compatible source and Java 21.
- Private single-server Compose/systemd configuration covers PostgreSQL, Besu, TAS, Issuer, CAS, Holder, private binds, and singleton Holder lifecycle.
- Bootstrap/provision, inventory, export, restore, ownership/permissions, managed/self-managed smoke, VC metadata verification, and restart-persistence harnesses exist.
- Restore is fail closed on checksum/path/link/ownership/password/state collisions and was tested with real PostgreSQL 16.4.
- Server 3 itself was not deployed and no live data migration/firewall mutation occurred.

Key append-only migrations, in order:
- supabase/migrations/20260821010100_facemarket_biometric_runtime.sql
- supabase/migrations/20260821010200_facemarket_mandatory_vc.sql
- supabase/migrations/20260821020000_facemarket_cutover_lifecycle.sql
- supabase/migrations/20260822000000_facemarket_purge_receipts.sql
- supabase/migrations/20260822010000_ai_output_cleanup_intents.sql
- supabase/migrations/20260822020000_facemarket_purge_manifests.sql
- supabase/migrations/20260822030000_facemarket_purge_manifest_revision.sql

Important implementation commits:
- 47ef614d readiness gates
- f90c941f..00500f6d OpenDID deploy/export/restore/cutover proof
- c9af0fc2..efe4ace4 biometric schema, provider boundaries, quarantine, liveness, completion, wizard
- 498b071c..22b087b9 mandatory VC, Holder HMAC/idempotency, revoke, lifecycle smoke
- 03135629..136191cb use-category and current runtime authorization
- 81abda14..5c78e6a1 cutover/purge/account deletion and same-batch recovery
- 305ce8a2 legacy face fallback removal
- 6bff2607 strict positive biometric thresholds
- 5f4aa61d..0a7f33f6 durable CDN purge manifest and revision CAS
- a1a65cf8 rollout gating, transform bypass, trusted REAL vary lineage, late-revoke cleanup
- a3998acf final QA report only

Use `git log --oneline --reverse main..HEAD` for the complete 123-commit decision history. Every commit is intended to use Lore trailers; verify before rewriting history.

Current verification evidence:
- Full backend: 3291 passed, 112 expected skips, 391 warnings.
- Full frontend: 944 passed.
- Vite production build: 3166 modules, exit 0.
- Holder Gradle clean test: 37 passed.
- Migration contract subset: 4 passed.
- OpenDID local export/provision/restore/smoke harnesses passed; live PostgreSQL integration is opt-in.
- Independent final review: Critical 0. Merge/local automated QA YES. Production biometric destructive cutover NO.

Known gaps and STOP gates:
- There is no one-command interactive biometric QA sandbox. `pnpm dev:mock` tests general UI only; mockAdapter does not implement the enrollment/provider flow.
- A complete browser flow requires Supabase auth/database, private/public R2, AWS credentials and browser role, real Face Liveness, a usable OACX transaction portrait contract/token, and reachable OpenDID Holder.
- The OACX `/trans` portrait field path, encoding, maximum bytes, TTL, and allowed retention/use are not provider-verified.
- No consented biometric gold-set calibration or live mobile-browser Face Liveness test was run.
- No disposable PostgreSQL run applied the complete migration stack under realistic concurrent traffic.
- Server 3 bootstrap, firewall, secret delivery, data migration, and live issue -> valid -> revoke -> revoked -> restart were not executed.
- Existing historical `/bytes?e=1` browser/CDN copies and Cloudflare Image variants were not live-purged. Production STOP #9 requires source/API or one-time hostname purge and a real HIT -> purge -> MISS/404 proof.
- Bytes already downloaded to a user's device cannot be remotely recalled.
- Real payout/disbursement remains excluded.
- A user pasted a live-looking API token in chat. A JWT-prefix repository scan was clean, but the credential should be revoked/rotated and never repeated.
- The earlier browser error `String contains non ISO-8859-1 code point` was reported before the hardening work. Do not claim it is fixed without reproducing the current HTTP path; the retained final evidence does not isolate that incident.

Current local manual QA truth:
- General mock UI only:
  cd /Users/nojeong-un/devs/wearless_studio/.worktrees/facemarket-security-hardening
  pnpm dev:mock
  open http://localhost:5173
- This does not prove the biometric wizard end to end.
- Real HTTP frontend needs `.env.local` with `VITE_API_MODE=http`, a local API base URL, and Supabase public auth values.
- Backend can run via `server/docker-compose.yml` on localhost:8081 or uvicorn directly, but full FaceMarket startup requires real DB/R2/provider/Holder inputs and the applied migrations.
- Keep `VITE_ASSET_CACHE_VERSION=1` until the new backend and migrations are deployed and smoke-tested. Only then build the frontend with exact value 2.

Minimum real biometric settings include names only; never invent or expose values:
- APP_ENV=dev
- FACEMARKET_ENABLED=true
- FM_BIOMETRIC_ENROLLMENT_ENABLED=true
- FM_OACX_CONTRACT_MODE=dev-mock-v1 only in dev
- FM_LIVENESS_REGION=us-east-1
- FM_LIVENESS_BROWSER_ROLE_ARN
- FM_LIVENESS_CONFIDENCE_THRESHOLD
- FM_ID_LIVE_THRESHOLD
- FM_RETOUCHED_LIVE_THRESHOLD
- FM_MATCH_POLICY_VERSION
- FM_CI_PEPPER
- FM_FACE_QC_ENABLED=true
- DATABASE_URL and Supabase server credentials
- public/private R2 configuration including R2_FACE_BUCKET
- OPENDID_HOLDER_URL and OPENDID_HOLDER_HMAC_SECRET
- standard AWS credentials/trust that can create liveness sessions and assume the browser role

Primary documents:
- docs/research/2026-08-22-facemarket-security-hardening-qa-report.md
- docs/superpowers/specs/2026-08-21-facemarket-biometric-runtime-hardening-design.md
- docs/superpowers/plans/2026-08-21-facemarket-biometric-enrollment.md
- docs/superpowers/plans/2026-08-21-facemarket-mandatory-vc-cutover.md
- docs/superpowers/plans/2026-08-21-facemarket-runtime-authorization.md
- docs/superpowers/plans/2026-08-21-facemarket-purge-reverification.md
- docs/runbooks/facemarket-opendid-single-server.md

Your first task after reading this handoff:
1. Verify the branch/HEAD/worktree and rerun the smallest relevant checks before making claims.
2. Tell the user plainly that production-path code and automated tests exist, but interactive biometric local QA is not currently turnkey.
3. If asked to make it turnkey, first design the smallest hermetic local QA provider boundary that cannot activate in production. Reuse existing dependency injection and test adapters; do not add a second business flow or weaken production startup gates.
4. Keep external provider/live deployment/destructive operations behind explicit credentials and user authorization.
5. Do not merge or push without the user's choice.

Recommended verification commands:
  cd /Users/nojeong-un/devs/wearless_studio/.worktrees/facemarket-security-hardening
  git status --short
  git rev-parse HEAD
  git diff --check main...HEAD
  cd server && .venv/bin/pytest -q
  cd .. && pnpm test:frontend
  pnpm build
  cd services/fm-holder && ./gradlew clean test

Treat skipped live-provider/database tests as unresolved gates, not successes. Preserve unrelated user changes. Use append-only migrations and the existing shared authorization/purge helpers rather than adding parallel abstractions.
```

## Exact repository evidence

- Final local QA report: [2026-08-22-facemarket-security-hardening-qa-report.md](./2026-08-22-facemarket-security-hardening-qa-report.md)
- Full commit sequence: `git log --oneline --reverse main..HEAD`
- Full file delta: `git diff --stat main...HEAD`
- Exact source diff: `git diff main...HEAD`
- Current plans/specifications are under `docs/superpowers/`.

## Security note

Do not paste the previously supplied API token into this handoff, environment files, tests, logs, or a new chat. Revoke and replace it through the provider before any live QA.
