# Prod env change proposal — resolves deploy-gate #1 (PROPOSAL, not applied)

Do NOT apply until Server3 holder exists and its URL/HMAC secret are provisioned. Applying `FM_VC_REQUIRED=true` WITHOUT a reachable holder trips the second half of the startup gate (`OpenDID Holder URL and HMAC secret are required when FaceMarket VC is mandatory`, `server/app/main.py:94-102`) — the two must land together.

## Why the branch crash-loops prod today
`copilot/api/manifest.yml` currently: `APP_ENV=production` (:55) + `FACEMARKET_ENABLED="true"` (:201) + **no `FM_VC_REQUIRED`**. The startup gate `_validate_facemarket_vc_settings` (main.py:85-93) raises `RuntimeError("FACEMARKET_VC_REQUIRED=true is required for production FaceMarket")` → ECS boot fails. This is a deliberate STOP gate; the fix is to satisfy it (holder + flag), not relax it.

## Stage 1 — CX verify + VC + Chain live (NO biometric; needs Server3 holder only)
Set in `copilot/api/manifest.yml`:
```yaml
# variables:
  FACEMARKET_ENABLED: "true"            # unchanged
  FM_VC_REQUIRED: "true"                # NEW — satisfies the deploy gate; VC becomes mandatory
  # biometric stays OFF at stage 1 (blocked on the vendor OACX portrait contract — see the Phase-2 plan):
  FM_BIOMETRIC_ENROLLMENT_ENABLED: "false"   # unchanged
  FM_OACX_CONTRACT_MODE: disabled            # unchanged
# secrets (SSM SecureString, provisioned like the existing FM_CHAIN_* secrets):
  OPENDID_HOLDER_URL: /copilot/wearless/prod/secrets/OPENDID_HOLDER_URL          # NEW — Server3 holder PRIVATE url (:8100, reachable only from Server1)
  OPENDID_HOLDER_HMAC_SECRET: /copilot/wearless/prod/secrets/OPENDID_HOLDER_HMAC_SECRET  # NEW — holder exact-body HMAC secret
```
Effect: prod boots (gate satisfied); FaceLicense VC issue/verify/revoke go live against Server3; Chain settlement already wired (`FM_CHAIN_*`). The revoke reconciler now auto-starts because the holder is configured (Task 4). `FM_CI_PEPPER` must already be set (verify + biometric both need it).

Pre-req before flipping: Server3 up + holder reachable privately from Server1 (see `docs/runbooks/facemarket-opendid-single-server.md`), and the migrations applied (see `migrate-prod-dryrun.sh`). Verify on STAGING first (`APP_ENV` non-production doesn't fire the gate, so stage the holder-reachability + issue/verify/revoke smoke there).

## Stage 2 — enable biometric (BLOCKED on vendor OACX portrait contract)
Only after the vendor confirms the ID-portrait field/encoding/TTL and a `prod` OACX biometric contract is implemented:
```yaml
  FM_BIOMETRIC_ENROLLMENT_ENABLED: "true"
  FM_OACX_CONTRACT_MODE: <prod-contract-name>     # NOT dev-mock-v1 (dev-only, main.py/cx_identity refuse it in prod)
  FM_LIVENESS_CONFIDENCE_THRESHOLD: "<value>"
  FM_ID_LIVE_THRESHOLD: "<value>"
  FM_RETOUCHED_LIVE_THRESHOLD: "<value>"
  FM_MATCH_POLICY_VERSION: "<value>"
  # FM_LIVENESS_BROWSER_ROLE_ARN already wired (:234); FM_LIVENESS_REGION=us-east-1 already set (:206)
```
Boot will now hard-require `FM_CI_PEPPER` + the SFace ONNX weights in the image (Task 3 startup validation) — confirm both are present in the prod image before flipping.

## Secrets to provision (SSM, ap-northeast-2, like scripts/fm-chain-secrets.sh)
- `OPENDID_HOLDER_URL` — Server3 holder private URL
- `OPENDID_HOLDER_HMAC_SECRET` — holder HMAC secret (must match Server3 holder config)
- (stage 2) any biometric threshold values if you prefer them as secrets rather than plaintext vars
