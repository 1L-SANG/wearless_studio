# Approved-mannequin wearshot v2

Status: implementation approved by the user after FEEDBACK_CONTRACT_REV2. Existing PR247 remains draft; no rollout, merge, Goal restart, or unrelated changes are authorized here.

## User contract

1. Selected, tone-adjusted approved mannequin pixels own garment-local color, worn fit, relative length, ease, silhouette and hem fall. Seller images own permanent construction/material/details. Apply this to target AND every selected matching garment.
2. Face/body sheets own only selected model appearance/proportions. Example clothing and body-sheet clothing cannot dictate new garment length, color or hem pooling. Model substitution is not a quality fix.
3. Preserve source camera distance, framing/subject scale and crop. Final output versus original example must contain a small natural pose OR background change. Both unchanged fails. Noise, grading and clothing substitution alone do not count. Repair of an already varied base does not require another variation.
4. Product-specific essentials retain evidence ordinals/bindings. Do not invent universal center seams, straight hems or no-pooling rules. The concrete navy top and purple trousers are regression examples, not production branch conditions.
5. Repairs use a hash-bound base plus original authority references. Protect only explicitly verified good attributes; correct failed attributes from their owning sources. A preferred face does not approve that base's trouser pooling.
6. Camera texture is separate from garment material and color. Clean/soft rendering cannot override approved garment colors. Final anchor comparison remains on every v2 path; same-light before/after preservation adds a second check where appropriate.
7. Structure, material, fit/length, binding and minimum variation failures block v2 release. Legacy critical behavior and hash-pinned v1 templates remain unchanged.

## Architecture

Version `approved_mannequin_v2` is explicit and opt-in. A new immutable contract binds exact image references, garment IDs, approved mannequin keys, seller evidence/essentials, selected model, source framing/crop, and preselected variation axis. One fingerprinted contract feeds rendering, repair, QC and release. No generic seller-only best-of may arbitrate v2 mannequin color.

Core reference roles: target/matching seller evidence, approved garment mannequin, model face/body, original example, optional camera reference. Every approved anchor is garment-scoped; one composite may serve two scopes only with verified matching provenance or explicit owner selection. Model/reference bytes are hashed; arbitrary client prose cannot create authority. Optional body evidence is represented honestly rather than fabricated.

New generation and repair renderers do not mutate the immutable confirmed-v1 template. A v2 QC schema contains per-garment color, fit/length, permanent structure/material, per-essential detail evidence, global camera/crop/identity/anatomy/capture/light, and pose/background variation observations. Deterministic validation checks coverage and bindings; it does not pretend to prove visual truth. Partial/hidden faces are explicitly represented so QC must not demand an outpainted full face.

Output framing is preserved within the provider's 16-pixel canvas grid, not an impossible exact reduced rational for small references such as387×515 or720×883. Native long-edge2048 with the nearest supported short edge may differ by at most0.5% in relative aspect ratio; larger distortion holds. Requested and returned output dimensions must match exactly. There is no forced2:3 conversion, post-crop, resampling or claimed pixel-identical framing.

A QC compatibility projection may expose existing gate names for worker/log compatibility, but v2 variation semantics and per-garment/details must remain explicit and versioned. Repair instructions are deterministic templates from failed axes, never free-form judge prose. Repair comparison verifies the same contract/base binding and no regression in approved axes. Full v2 release is separate from the older three-gate critical policy.

## Production binding and API seam

Existing detail-page generate route accepts an optional strict body with contractVersion and matchingMannequinAssets (selected matching ID to owned mannequin asset UUID). No body preserves v1. An explicit v2 request cannot return old editor blocks or join a differently bound active/idempotent job as if applied: conflicting prior output/job returns409 without deletion or new charge.

Resolve requested target mannequin from current selected candidate/version using active tone asset when present, and snapshot it into job payload. Explicit matching anchors must resolve through real owned mannequin-cuts/project lineage, not arbitrary uploaded images. Tone lineage must resolve its original cut, ownership, project and original byte hash. A verified selected composite can provide its matching region only for exact original metadata.matchItemId; missing/second/unproven matching anchors hold before generation. Worker revalidates after enqueue. No schema migration or automatic inferred approval.

Version/anchor/variation request fields are typed IDs/enums only. Optional `captureProfile` is `clean` or `soft` (default soft) and is part of the immutable queue projection and contract, making both named methods API-usable without client prose. Saved editor blocks remain the authoritative matching/model selections. Source framing is bound from verified directing metadata or explicit trusted local test observations. New optional API capability is not a claim that the existing UI already exposes all anchor choices.

## Sunburst stage2 A/B

Use identical frozen base, v2 authority packet, failed/protected axes, rendered repair prompt, size and medium quality for GPT Image2 versus `gpt-image-2.5-sunburst-2026-09-08`. Initial scope: four representative repairs ×two models =eight outputs, at most one repeat of split pairs (up tofour additional outputs) after assessing initial results. No global model flip.

Metadata retrieval currently404 for Sunburst; one actual edit request after valid packet preparation establishes access. If403 persists, log it, stop that provider, finish unaffected implementation and dry-run validation. Do not call a missing arm an A/B result or waste repeated baseline generations. No silent fallback.

Initial cases cover minimum variation/capture, navy hem/detail with correct length, natural face with mannequin-red color, and long trousers without unsupported pooling. Existing preferred bases are immutable. Color/material/face results are separately judged, not averaged into an attractiveness score. Images/credentials/local receipts remain out of public PR.
