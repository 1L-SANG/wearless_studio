# Approved-mannequin v2 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Root owns git and paid calls. The user permitted parallel work: after the shared core review and stable interfaces, isolated runtime and harness files may be prepared in parallel. Actual paid comparison waits for both integration reviews.

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

- [x] RED: mismatched/duplicate keys and garmentIDs, absent target or selected matching anchor, wrong reference role/seller ordinal, malformed/unbound essentials, mismatched model/example bindings, malformed image bytes/metadata and mutable source structures fail. Valid multiple generic garments work without item-specific code.
- [x] Implement deterministic contract fingerprint and caller-owned applicability (partial/hidden faces, optional body, visible essentials and cropped garment axes). Do not let provider redefine missing bindings. Bind exact persisted seller fact codes to reference keys; retain evidence ordinals.
- [x] Render `render_generation(contract)` and `render_repair(contract, repair_plan)` using the same authority description. Repair inputs are base+bound authority refs; base/contract mismatches fail. No blanket clothing freeze. Camera-only soft forbids color/WB/exposure shifts; explicit color/light repairs retain mannequin/protected-color checks. Preserve declared mannequin-relative hem/fit. Clean/soft have shared concrete camera semantics.
- [x] RED/QC: per-garment/detailed coverage required; pose-only variation passes without forcing background change; both unchanged or grading-only fails; camera/crop failure fails; missing detail/length or protected-axis regression fails; partial/hidden face is not a full-face demand. Independent pose/background repairs are not frozen by a separate variation PASS.
- [x] Implement strict v2 schema/validator and GPT-only verdict, binding hashes/coverage/protected axes, full release and chained protection checks. No raw provider error text. Compatibility gates do not replace v2 full release.
- [x] GREEN with actual pure tests and mocked external transport; no paid calls. Independent review plus one fix round clean; root reproduced66core/286related tests. Core committed b1c56d02. This is not proof of visual fidelity.

### Task2: Secure API, worker and generator integration

**Files:** create server/app/agents/wearshot_runtime.py; modify models.py, routes.py, repo.py, config.py as needed; modify cut_generator.py, cut_plan.py, detail_page_job.py; add focused API/runtime/generator/worker tests. Preserve dirty prompts.py and mannequin files.

- [ ] RED/API: optional strict body `{contractVersion:'approved_mannequin_v2',matchingMannequinAssets:{matchingId:UUID},variationAxis:'pose'|'background',captureProfile:'clean'|'soft'}` (capture defaults soft); omitted body legacy. Reject extra prose, unused mapping IDs, nonowned/nonmannequin/deleted anchors, mismatched tone lineage. Snapshot selected target active tone asset; worker does not silently reread a different latest selection.
- [ ] Add read-only owned-mannequin lookup through real assets/mannequin_cuts/projects joins, including tone→original lineage with matching source project/cut metadata. Worker verifies original source hash on tone asset. Explicit user-selected matching asset grants only worn color/fit/length, never construction. Exact composite matchItemId fallback allowed; missing anchor hold.
- [ ] Handle existing editor output and reused jobs:409 on changed/unprovable explicit v2 projection; no deleting output, no mismatched idempotent join, no new reservation on invalid bindings. Copy typed projection into job payload.
- [ ] Build bound contract from resolved assets + existing seller evidence contract hardFacts/evidenceOrdinals + model/source directing metadata. Fail if required target/matching evidence absent, do not fabricate fullbody. Append optional contract to prepared tuple safely; pass same object to generator, repairs, QC, release.
- [ ] Add optional contract/repair-plan arguments to existing cut_generator functions; v2 renders only new renderer and uses base+authorities for stage2. Reject mixing v2 with confirmed_v1 prompt_input. Native long-edge2048 uses supported16-pixel dimensions within0.5% relative source/base aspect error; returned dimensions must equal request. No forced2:3, crop or resampling. Separate stage2 model override for controlled test, no global default flip.
- [ ] Worker v2 bypasses seller-only best_of, runs mandatory v2 QC irrespective of legacy off mode, derives allowed corrections from failed attributes, protects only passed ones, checks before/after with same contract and releases only coherent v2 success. Do not reuse legacy relatedSceneDifferentPlace logic. Existing generic/confirmed paths retain behavior. No more than one automatic repair.
- [ ] GREEN tests prove actual API/jobpayload/binder/worker/generator call chain, active-tone and matching bytes, metadata changes after queue, no call before preflight, rejected stage2 preservation, detail/length/variation block before upload. Update typed CutPlan serialization only opt-in if needed; new version cannot be silently discarded.

### Task3: Bound post-QC replay and paired Image2/Sunburst experiment

**Files:** extend server/app/experiments/wearshot_replay.py or add a focused companion using shared v2 binder/renderers/generator; tests/test_wearshot_repair_ab.py. Root outputs/wearshot_feedback_v2_2026-09-09 owns private fixtures/receipts/review.

- [ ] RED/GREEN model allowlist accepts Image2 baseline and explicit Sunburst only on declared repair cases; no fallback. Validate same base/reference/order/contract/prompt/size/quality between pair arms except model. Stage2 uses real `cut_generator.repair` and v2 QC, not a separate handwritten image SDK runner.
- [ ] Durable started/completed/failed receipts, no overwrites/uncertain resubmits, real request model/bytes hashes, code/prompt versions and output sizes. QC provenance points to owning generation receipt. Keep legacy replay behavior unchanged.
- [ ] Optional operator-owned humanFeedback can add bound failure axes and request protection only for actual machine+focused PASS axes. Keep original machine observations and feedback separate; never invent machine PASS or inject feedback prose into prompts.
- [ ] Prepare four frozen preferred-base repair cases with all target+matching approved mannequin anchors, selected face and seller facts; protect selected good face/color/length independently. First perform one actual Sunburst edit to establish access. If denied stop provider and report blocked comparison; implementation/dryruns still finish, at most one Image2 smoke if needed for implementation validation.
- [ ] If accessible, generate initial eight outputs (4pairs), v2 QC, independent visual inspection and comparable HTML. Only split pairs may repeat once (maximum4 additional images). Do not label independent repeats as sequential fixes or call larger model automatically better.
- [ ] Run broad isolated server suite (exclude only previously documented absent localDB and untracked historical fixture tests), review, update PR247 with code/docs only, verifyCI. No merge/deploy or feature activation.
