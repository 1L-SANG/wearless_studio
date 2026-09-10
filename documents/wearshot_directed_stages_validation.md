# Source-directed wearshot: authority and stage validation

This is an opt-in extension of the existing wearshot-v2 pipeline, not a global image-provider switch. Implementation and experiment are distinct from production activation.

## Attribute authority

| Attribute | Binding |
| --- | --- |
| Physical place, camera distance, subject scale, crop | Original directing example |
| Identity / body | Selected model references |
| Garment color | That garment's approved mannequin |
| Construction, material, essential visible detail | That garment's seller evidence |
| Main body/leg hem level | Explicit approved-length reference, otherwise mannequin |
| Capture texture | Selected clean/soft profile; never independent color authority |

`directingMode: "source_locked_v1"` requires the original example as the first input, rejects a separate capture photograph, and preserves the same physical place. It permits small natural pose variation while retaining frame scale/crop and key contacts. Replacing a face/hair style alone does not count as pose variation.

`lengthReferenceAssets` maps `target` or an actually selected matching-garment ID to an owned, same-project image asset. It binds image bytes, asset/provenance metadata and the selected garment through snapshot, queue verification, preparation, generation, repair and QC. No selection makes an asset public. Private-face storage, REAL-derived assets and unknown AI lineage remain blocked. Ordinary owner-only project-image ACL visibility remains allowed for this private processing.

Length approval changes the evidence source, not a verdict. It does not authorize copying color, fabric waviness, face, room, neckline or sleeve length from the length-reference image. New QC observations must include the exact `lengthReferenceKey` for every garment when the optional length extension is present. Old requests, contracts and fingerprints remain unchanged when extensions are absent.

No upload/selection UI was added in this scope; API callers must explicitly provide the optional approved-length asset mapping. Unselected garments retain the existing default.

## Stage routing

Optional v2-only settings:

```text
WEARSHOT_GENERATION_MODEL=gpt-image-2.5-flare-2026-09-08
WEARSHOT_REPAIR_MODEL=gpt-image-2.5-sunburst-2026-09-08
WEARSHOT_QC_MODEL=gpt-6-astra
WEARSHOT_QC_TIMEOUT_SECONDS=180
```

These are documented settings, not changes applied to any environment. Unset image-stage overrides preserve existing routing. Legacy/detail/signature generation is not redirected by the new first-stage setting.

First generation → complete QC → at most one conditional correction → complete final QC. A first PASS skips correction. The new directed mode repairs only safely bound factual FAILs; uncertainty alone is held, not turned into a made-up repair instruction. Mixed known failures and unknowns can repair only the known failures and still require all final release checks. A failed correction cannot release the failed first candidate as fallback.

The native output canvas retains a 2048px long edge with supported 16px dimensions and limited aspect rounding. Returned dimensions are verified before release. Camera softness grants no global hue, saturation, white-balance or exposure change, and material/color checks remain separate.

## Experiment protocol

`python -m app.experiments.wearshot_stage_benchmark` defaults to no-call/no-write dry-run. Live phases require a single case and arm: `generate-first`, `qc-first`, `conditional-repair`, `qc-final`. Read-only `report` verifies the durable evidence chain.

Frozen arms: Image2 → Image2 versus Flare snapshot → Sunburst snapshot. Same source/authority packet, native 2K, medium quality and QC model/deadline within each paired case. Private corpus and face images are not included in this repository report or the public PR.

Each phase records source/contract/image/prompt/code hashes, actual model, native size, original input bytes, parent receipt, outcome and action timings. Started and terminal receipts are exclusive; interrupted or uncertain attempts block silent resubmission. This benchmark also freezes a single HTTP image attempt, including for 429. The general application's existing confirmed-429 retry default remains unchanged.

The no-QC time is the first-generation action duration, using the same first image that then enters QC. The QC-enabled critical path is the sum of required generation and QC action durations. It excludes CLI/manifest/controller/other-case queueing, storage and app/R2/DB transport. QC total already includes the focused identity review and is not double counted.

Report completed decision timing separately from accepted-result timing. A fast hold/rejection must never improve an accepted-result statistic. Stage-2 differences in this experiment compare workflows with different first parents and potentially different repair sets, not a model-only same-parent edit comparison.

## Verification and limits

- Root local full regression after the final fixture portability fix: 4,374 passed, 24 skipped, 391 existing warnings. The three previously documented local exclusions remain the unavailable local personalization DB suite and two untracked historical-fixture suites; no production DB was used for local tests.
- Focused final transport/benchmark/legacy paired coverage: 156 passed. Real adapter boundaries are exercised with HTTP stubs; strict model, bytes, quality and canvas arguments are asserted.
- Independent task reviews and final integration review are clean after fixing accepted-versus-held timing labels, preserving the old planner call shape, and limiting experiment HTTP attempts.
- Initial Linux CI exposed one test-fixture-only golden mismatch (4,555 passed, one failed): re-encoding equal PNG pixels did not produce the original snapshot bytes. The snapshot now freezes its original six PNG byte strings while retaining both original literal contract/prompt hashes. Default, altered and unavailable encoder regressions pass; 78 covering tests passed. No generation/QC code or live evidence was changed by this fix.
- Four-case private live results are summarized below; a small sample cannot prove general superiority.
- No merge, production deployment, environment activation, key change or automatic Goal restart is implied by this implementation.

## Live workload result

13 native 2K images completed: eight first generations and five conditional repairs. No image transport failures or automatic resubmissions occurred. All image inputs and actual model/size/quality requests were hash-bound; no resizing or camera postprocessing was applied.

| Workflow | Unreviewed first image, median (range) | QC-inclusive decision, median (range) | Final automatic release |
| --- | --- | --- | --- |
| Image2 → conditional Image2 | 57.4s (53.4–62.6) | 239.0s (98.1–278.8) | 1/4 released, 3/4 held |
| Flare → conditional Sunburst | 32.6s (25.4–37.1) | 138.3s (71.8–186.2) | 4/4 released |

First output median was about 43% shorter and completed decision median about 42% shorter for the Flare/Sunburst workflow in this sample. Decision time includes both accepted and held outcomes; it is not time to four usable images. Accepted-only medians (Image2 98.1s, n=1; Flare workflow 138.3s, n=4) have different case sets and must not be compared as model performance. The only jointly released case took 98.1s versus 71.8s. Production wall-clock overhead is excluded as specified above.

Two Flare first outputs passed without repair. The other two passed after Sunburst. Of four Image2 first outputs, one passed; all three corrected outputs remained held. Historical QC records and this experiment's criteria were not rewritten to promote held results.

Observed improvements: no cafe replacement of the original outdoor place; explicit length approval eliminated the former evidence conflict; center seam/rib/layered-edge preservation improved in the top-garment examples. A Flare first output nevertheless changed a red cardigan to black. QC caught it; Sunburst restored red while keeping face/arm/room substantially stable. An Image2 color repair added a button, illustrating why protected details must still be rechecked after editing.

Remaining limits: visually similar camera crops received different first/final judgments in one case, so tolerance calibration still matters. Passing the current soft-capture rubric is not proof of matching a user's exact older-iPhone aesthetic. This workload supports gated use of Flare→QC→conditional Sunburst as a candidate, not removal of QC or a universal quality claim. Do not apply a blanket second texture edit to already accepted images. The approved-length selection remains an explicit API capability; no production UI or environment was switched.
