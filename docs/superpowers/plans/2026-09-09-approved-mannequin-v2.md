# Approved-mannequin v2 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Root owns git and paid calls. Sequential code implementers; read-only research and access checks may run in parallel.

**Goal:** Implement the approved feedback contract consistently through real generation, repair, QC and release, then run a controlled Sunburst stage2 comparison if access permits.

**Architecture:** Immutable garment-scoped contract + versioned render/QC layer; secure optional API/worker binding; existing replay runner extended for bound repair pairs. Legacy paths stay untouched unless v2 explicitly selected.

**Tech Stack:** existing Python/dataclasses/httpx/Pillow/pytest, FastAPI request models, current repo asset getters and JSON job payloads.

**Spec:** docs/superpowers/specs/2026-09-09-approved-mannequin-v2.md. User approval is current request to implement preceding feedback rules and test Sunburst.

## Global Constraints

- No AGENTS/CLAUDE instructions (user revoked), Goal restart, deploy, merge, R2/production DB writes, new asset approvals inferred, or unrelated dirty-file changes.
- Workers do not stage/commit/push or spawn agents. Root commits only reviewed task files into existing DraftPR247.
- TDD and task-scoped independent review; report code tests separately from visual accuracy. Preserve generic/confirmed-v1 defaults and pinned template bytes.
- No product names, fr-case IDs, navy/gray or pooling shapes hardcoded in production logic. Product facts are bound data.
- Generated artifacts and private paths remain local. Same v2 contract, references and base must govern generation, repair and QC; no downgraded seller-only fallback.
- Explicit missing evidence is a preflight hold. No automatic generation/approval of a missing matching mannequin. Optional API selection can use an existing owned mannequin from another owned project.

### Task1: Shared immutable contract, renderers and v2 QC

**Files:** create server/app/agents/wearshot_contract.py, wearshot_prompt.py, wearshot_qc.py and tests/test_wearshot_contract_v2.py, test_wearshot_qc_v2.py. No existing worker/routes/generator edits yet.

**Interfaces:** Define frozen types `BoundReference(key, role, image, garment_id=None, evidence_ordinal=None, asset_id=None)`, `EssentialDetail(code, value, evidence_keys)`, `GarmentBinding(garment_id, mannequin_key, seller_keys, essentials=())`, `FrameLock(shot, face_visibility, description)`, `WearshotContract` built by `bind_contract`. Contract carries target binding, tuple matching bindings, expected matching IDs, exact references, example/model face/body/capture keys, frame lock, variation_axis(pose/background), capture_profile(clean/soft), and computed fingerprint. `to_dict()` serializes metadata/hashes without bytes. Provide `make_repair_plan(contract, base_image, failed_axes, approved_axes)` with hash-bound base and disjoint bounded attribute keys. Public exact signatures and result shapes must be documented in report for Task2.

- [ ] RED: mismatched/duplicate keys and garmentIDs, absent target or selected matching anchor, wrong reference role/seller ordinal, unsupported essentials, mismatched model/example bindings, malformed image bytes/metadata and mutable source structures fail. Valid multiple generic garments work without item-specific code.
- [ ] Implement deterministic contract fingerprint and caller-owned applicability (partial/hidden faces, optional body, visible essentials). Do not let provider redefine missing bindings. Bind seller facts to exact reference keys; retain evidence ordinals.
- [ ] Render `render_generation(contract)` and `render_repair(contract, repair_plan)` using the same authority description. Repair inputs must be base+bound authority refs, not source image alone; fail before call if base/contract mismatches. No blanket stage1 clothing freeze; only verified approved axes. Default soft forbids global color/WB/exposure shifts. Preserve declared relative hem/fit from mannequin rather than source example garment.
- [ ] RED/QC: per-garment/detailed coverage required; pose-only variation passes without forcing background change; both unchanged fails; grading-only variation fails; camera/crop failure fails; missing detail/length or protected-axis regression fails; partial/hidden face is not a full-face demand.
- [ ] Implement strict v2 schema/validator and async `verdict(settings, contract, candidate, repair_plan=None, base_image=None)` using existing vision transport GPT-only, explicit model setting with safe default6astra, no raw provider error text. References/order/fingerprint and candidate hash recorded. Produce per-garment/details plus compatibility `gates` and passed/failed attributes for Task2. Implement `release_allowed` / `compare_repair` that reject binding mismatch and protected regressions, not merely fewer failures.
- [ ] GREEN with actual pure tests and mocked external transport; no paid calls. Report exact interfaces + RED/GREEN + files.

### Task2: Secure API, worker and generator integration

**Files:** create server/app/agents/wearshot_runtime.py; modify models.py, routes.py, repo.py, config.py as needed; modify cut_generator.py, cut_plan.py, detail_page_job.py; add focused API/runtime/generator/worker tests. Preserve dirty prompts.py and mannequin files.

- [ ] RED/API: optional strict body `{contractVersion:'approved_mannequin_v2',matchingMannequinAssets:{matchingId:UUID},variationAxis:'pose'|'background'}`; omitted body legacy. Reject extra prose, unused mapping IDs, nonowned/nonmannequin/deleted anchors, mismatched tone lineage. Snapshot selected target active tone asset; worker does not silently reread a different latest selection.
- [ ] Add read-only owned-mannequin lookup through real assets/mannequin_cuts/projects joins, including tone→original lineage with matching source project/cut metadata. Worker verifies original source hash on tone asset. Explicit user-selected matching asset grants only worn color/fit/length, never construction. Exact composite matchItemId fallback allowed; missing anchor hold.
- [ ] Handle existing editor output and reused jobs:409 on changed/unprovable explicit v2 projection; no deleting output, no mismatched idempotent join, no new reservation on invalid bindings. Copy typed projection into job payload.
- [ ] Build bound contract from resolved assets + existing seller evidence contract hardFacts/evidenceOrdinals + model/source directing metadata. Fail if required target/matching evidence absent, do not fabricate fullbody. Append optional contract to prepared tuple safely; pass same object to generator, repairs, QC, release.
- [ ] Add optional contract/repair-plan arguments to existing cut_generator functions; v2 renders only new renderer and uses base+authorities for stage2. Reject mixing v2 with confirmed_v1 prompt_input. Keep exact source aspect output-size override; no forced2:3/crop postprocessing on v2. Separate stage2 model override for controlled test, no global default flip.
- [ ] Worker v2 bypasses seller-only best_of, runs mandatory v2 QC irrespective of legacy off mode, derives allowed corrections from failed attributes, protects only passed ones, checks before/after with same contract and releases only coherent v2 success. Do not reuse legacy relatedSceneDifferentPlace logic. Existing generic/confirmed paths retain behavior. No more than one automatic repair.
- [ ] GREEN tests prove actual API/jobpayload/binder/worker/generator call chain, active-tone and matching bytes, metadata changes after queue, no call before preflight, rejected stage2 preservation, detail/length/variation block before upload. Update typed CutPlan serialization only opt-in if needed; new version cannot be silently discarded.

### Task3: Bound post-QC replay and paired Image2/Sunburst experiment

**Files:** extend server/app/experiments/wearshot_replay.py or add a focused companion using shared v2 binder/renderers/generator; tests/test_wearshot_repair_ab.py. Root outputs/wearshot_feedback_v2_2026-09-09 owns private fixtures/receipts/review.

- [ ] RED/GREEN model allowlist accepts Image2 baseline and explicit Sunburst only on declared repair cases; no fallback. Validate same base/reference/order/contract/prompt/size/quality between pair arms except model. Stage2 uses real `cut_generator.repair` and v2 QC, not a separate handwritten image SDK runner.
- [ ] Durable started/completed/failed receipts, no overwrites/uncertain resubmits, real request model/bytes hashes, code/prompt versions and output sizes. QC provenance points to owning generation receipt. Keep legacy replay behavior unchanged.
- [ ] Prepare four frozen preferred-base repair cases with all target+matching approved mannequin anchors, selected face and seller facts; protect selected good face/color/length independently. First perform one actual Sunburst edit to establish access. If denied stop provider and report blocked comparison; implementation/dryruns still finish, at most one Image2 smoke if needed for implementation validation.
- [ ] If accessible, generate initial eight outputs (4pairs), v2 QC, independent visual inspection and comparable HTML. Only split pairs may repeat once (maximum4 additional images). Do not label independent repeats as sequential fixes or call larger model automatically better.
- [ ] Run broad isolated server suite (exclude only previously documented absent localDB and untracked historical fixture tests), review, update PR247 with code/docs only, verifyCI. No merge/deploy or feature activation.
