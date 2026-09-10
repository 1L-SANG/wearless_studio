# Source-directed wearshot stages Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Root owns commits, PR updates and live API calls. Implementers own exclusive task files and do not spawn agents.

**Goal:** Reproduce preferred source-directed wearshots with separate length authority and measure Image2 versus Flare→Sunburst with/without QC overhead.

**Architecture:** Extend the frozen contract with optional length/reference-mode fields, bind them through the existing typed API/worker, then exercise the real first-generation/conditional-repair functions in a durable measured workflow. Preserve old manifests when extensions are absent.

**Tech Stack:** Existing Python/dataclasses/FastAPI/httpx/Pillow/pytest and application image/vision adapters; no new SDK or service.

**Spec:** docs/superpowers/specs/2026-09-10-wearshot-directed-stages.md

## Global Constraints

- No AGENTS/CLAUDE instructions (previous user revocation), Goal restart, merge, production deployment/activation, key replacement, private DB/R2 writes or unrelated dirty-file edits.
- No global legacy model switch, new dependency, image resampling/compositing, hidden fallback or automatic uncertain resubmission.
- Old generated images, manifests, failed QC and receipts stay immutable. Public PR contains code/tests/docs only, not image/face corpus or local private paths.
- Color=mannequin; construction/material=seller; explicitly approved length reference=length only. Changing the authority is never rewriting a machine verdict.
- Source-locked mode requires original example first and no capture-photo input. Same physical place, distance/crop, small natural variation.
- Native long-edge2048, supported16px canvas and <=0.5% aspect rounding remain. Full release and exact returned-size checks stay enabled.
- TDD and independent scoped review before live calls. Root reuses existing isolated worktree/branch and preserves unrelated changes.

### Task 1: Length authority, source-locked rendering and secure runtime binding

**Files:** modify agents/wearshot_contract.py, wearshot_prompt.py, wearshot_qc.py, wearshot_runtime.py; models.py, repo.py and routes/worker only where the optional fields need actual propagation; experiments/wearshot_repair_ab.py decoder; new tests/test_wearshot_directed_contract.py and test_wearshot_length_api.py. Preserve legacy templates and old optional-field behavior.

**Interfaces:**

```python
GarmentBinding(..., approved_length_key: str | None = None)
WearshotContract(..., directing_mode: str | None = None)
bind_contract(..., directing_mode: str | None = None)
# role approvedLength is garment-scoped, has immutable image bytes and no seller ordinal.
# Only non-None extensions appear in to_dict(), preserving old metadata hashes.
# New request lengthReferenceAssets: dict[str, UUID] defaults empty;
# directingMode: Literal['source_locked_v1'] | None defaults None.
```

- [ ] RED pure boundary tests: old no-extension metadata/prompt/fingerprint unchanged; wrong/missing/unbound length role or garment rejected; selected length reference does not replace color/model/structure refs; new mode rejects capture image or non-first example. Use literal expected keys and actual tiny PNG bytes.
- [ ] GREEN core and renderers: explicit per-garment length owner; fit excludes separately approved hem-level differences; source-first primary canvas and same-place constraint for new mode; exact old rendering retained when absent. QC garments require matching lengthReferenceKey metadata only when length extension exists, and final release revalidates it.
- [ ] RED runtime/API: explicit target/matching length asset selection, invalid/unused/seed/nonowned/deleted/private/REAL-derived asset blocked, queued source drift blocked, no reservation/provider before preflight; old omitted fields preserve previous snapshot/request shape.
- [ ] GREEN narrow owned public project-image resolver and queued projection; same explicit length bytes/keys to generation/repair/QC. Use existing asset REAL provenance helpers for privacy; do not treat unknown AI lineage as public. UI/upload/migration out of scope.
- [ ] Test actual route→snapshot→prepare→generator/QC binding, matching garment scope, hidden-length NA without bypass, and old replay decoder round trip. Do not fake old FAIL as PASS: new reference gives new fingerprint and requires new QC.
- [ ] Report exact field/signature/result changes and RED/GREEN; root reviews and commits only owned files.

Example behavioral assertions:

```python
assert new_contract.target.mannequin_key == 'product-color'
assert new_contract.target.approved_length_key == 'approved-hem'
assert generation.reference_keys[0] == new_contract.example_key
assert new_contract.fingerprint != old_contract.fingerprint
assert not release_allowed(old_verdict, new_contract, candidate)
```

### Task 2: V2 stage routing, conditional workflow and measured replay

**Files:** config.py, workers/detail_page_job.py, agents/wearshot_runtime.py timing metadata; new experiments/wearshot_stage_benchmark.py and tests/test_wearshot_stage_benchmark.py; related narrow routing tests. Old paired repair tool remains a distinct protocol.

**Interfaces:** shared cut_generator.generate/repair unchanged; review_candidate and release_allowed from Task1. Add optional Settings.wearshot_generation_model, default None/WEARSHOT_GENERATION_MODEL. Existing repair setting remains None by default. New benchmark reuses exact contract decoder and durable receipt helpers.

- [ ] RED stage routing: v2-only first model override, unchanged legacy/detail/signature routing; QC PASS skips stage2; valid FAIL yields maxone correction; errors/UNJUDGEABLE without safe plan hold; no failed-stage fallback release.
- [ ] GREEN measured shared review metadata: monotonic primary/focused/total wall durations, truthful skipped face. No model/identity/authority fallback. Preserve Settings immutability and dedicated180second v2 QC deadline.
- [ ] RED/GREEN durable local benchmark: frozen exact models/prompt/refs/contract/size/medium/QC deadline; separate generate-first, qc-first, conditional-repair, qc-final phases; default dry-run no calls/writes. One explicit case/arm per live invocation. Same first image underpins QC-off timing and QC-on continuation; prior-stage receipt and candidate hash are checked before each next phase.
- [ ] First arms: Image2 and Flare snapshot. Second arms: Image2 and Sunburst snapshot respectively. Explicit generations must go through real cut_generator and application adapters, not a one-off SDK. Record actual transport model/bytes/prompt, stage role, parent image/receipt, output-size, usage/provider latency, wall stage duration and code hashes.
- [ ] Derive no-QC time from stage1 completion; QC-on critical path is sum of measured required stages with optional repair skip. Exclude agent pauses/other-case queuing. Report per-case outcomes and median/range; no false separate-run stopwatch or model-only stage2 comparison.
- [ ] Test synthetic timings with hand-derived sums, actual production entrypoint argument checks, QC-pass zero second requests, maxone correction, failures/cancellation durable no-resubmit, changed references/code/deadline/receipt/model rejected. No live calls by worker.

Example timing assertion:

```python
assert timing['withoutQcMs'] == 12000
assert timing['withQcCriticalPathMs'] == 12000 + 39000 + 12000 + 35000 + 42000
assert timing['manualQueueTimeIncluded'] is False
```

### Task 3: Provenance, live workload and handoff

**Files:** root-owned outputs/wearshot_directed_stages_2026-09-10; public documents only for nonprivate implementation notes/results, existing DraftPR247.

- [ ] Persist succinct historical lineage (input source vs previous output), actual provider/unknown builtin model, manual feedback vs machine QC, version number vs edit depth. Include main preferred bases and secondary attribute references.
- [ ] Freeze four case packets with example first, no capture photo, native2K/medium, navy length explicitly from fr03-v1, correct expected visibility (do not demand hidden black trouser pleats). Do not extend a face preference into garment approval. New manifests must not mutate old trials.
- [ ] Run one valid Flare first-generation request to establish access; no inference from metadata404. If denied, record blocked arm without fallback. If accessible, run8first outputs and at most8conditional corrections, no extra repeats. Alternate model order across cases and cap concurrent image calls2.
- [ ] Use each first output for both no-QC preview timing and subsequent QC-enabled branch. Inspect all actual images, record face/color/length/scene preservation and unresolved failures; do not claim global superiority from4cases.
- [ ] Render review with source/preferred base/stage1/QC/stage2/final clearly labeled, uniform image frames and per-case feedback. Include timing definitions and raw lineage links; static validate local links, do not bypass earlier local-browser restrictions.
- [ ] Focused and broad server regression (only3previouslydocumented localDB/history exclusions), independent final review, existing DraftPR update and CI. No merge/production activation or Goal restart. Report exact completion versus genuine remaining blockers.
