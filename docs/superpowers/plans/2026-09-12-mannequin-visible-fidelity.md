# 보이는 의류 구조 검사와 제한된 보정

> For agentic workers: use superpowers:subagent-driven-development or superpowers:executing-plans. 이전 대화의 설계와 사용자의 진행 승인을 구현한다.

**Goal:** 상품명으로 소매를 만들거나 정상적으로 안 보이는 뒷면을 결함으로 처리하지 않고, 실제 보이는 디자인 오류만 보정한다.

**Architecture:** 기존 마네킹 생성과 최종 보정 슬롯에 선택 가능한 사진 기반 구조 근거와 독립 세 영역 검사를 연결한다. 새 기능은 off가 기본이며 shadow는 관찰만, enforce는 확인된 문제에 한 번만 Sunburst 편집하고 전체 검사를 다시 한다. DB와 배포 설정은 변경하지 않는다.

**Tech Stack:** FastAPI Python, 기존 httpx vision_llm와 GeminiImageClient, Pillow, pytest.

**Spec:** 사용자 승인 내용 및 로컬 실험 `output/imagegen/mannequin-fullbody-routing-2026-09-12/SUMMARY.md`. 실험 폴더는 root checkout에 있으며 PR에 상품 사진을 커밋하지 않는다.

## Global Constraints

- Full-body white mannequin, 2K. Matching garments remain present.
- Source and candidate crops repeat existing pixels; never synthesize source details.
- A front view does not owe back visibility. A source-supported detail expected to be visible but genuinely missing remains a defect.
- Fixed independent roles: structure, details, appearance. Applicable checks vary by product.
- No product-specific hardcoded seam counts, pink-product rules, or borrowed scores.
- Known invalid candidates never become a pass because QC failed. Repair accepted only after fresh checks on its bytes.
- One final repair image slot, shared with existing final repair. No new unbounded loops.
- No production deployment, migration, or merge. Draft PR only.

## Task 1: Transport and evidence preparation

Files: `server/app/agents/vision_llm.py`, `server/tests/test_vision_llm.py`, new `server/app/agents/mannequin_photo_structure.py`, its tests and source prompt.

- [ ] Add failing tests capturing the HTTP request: legacy callers retain the same body; specialist callers explicitly send medium reasoning, high image detail and bounded completion. A truncated or refused response cannot pass parsing.
- [ ] Extend the existing `_call_gpt` with keyword-only options and optional metadata dictionary. Retain the same endpoint and no added retries or provider fallback.
- [ ] Photo preparation retains named original views and adds RGB source crops with actual parent coordinates. Type inference receives photographs, never product name. Evidence indices must refer to supplied source images. Keep uncertain attributes as hypotheses, not instructions to invent structure.
- [ ] Test wrong-title independence, malformed evidence, absent optional views, source byte preservation and crop metadata. Run targeted pytest.

## Task 2: Independent QC

Files: new `server/app/agents/mannequin_specialist_qc.py`, external prompt and `server/tests/test_mannequin_specialist_qc.py`.

- [ ] Write failing tests for visibility-only pass, actual missing front detail, invented seam, uncertain visible mismatch, invalid evidence indices, incomplete role set and rejected repair.
- [ ] `judge(settings, source_images, candidate, *, slots, clothing_type, match_image=None, fit_profile=None)` returns three independent validated role assessments plus hashes and usage. Each material issue includes source and candidate evidence and a bounded repair instruction.
- [ ] `repair_prompt(assessment)` contains only evidenced material corrections and preservation constraints. Unknown and visibility-only notes never become edit requests.
- [ ] `accept_repair(before, after)` requires all three fresh roles to pass; incomplete or regressed assessment cannot replace an image.

## Task 3: Service wiring

Files: `server/app/workers/mannequin_job.py`, `server/app/config.py`, `server/app/agents/prompts.py`, mannequin template, worker tests and `.env.example`.

- [ ] Write tests through real `_run_candidate` and storage boundary with fake external services: off is legacy, shadow never edits, normal hidden back causes no repair, failed detail spends one Sunburst edit, post-check failure cannot save repaired bytes, matching and base gates remain active.
- [ ] Add default-off mode and dedicated QC/repair model settings. Integrate source analysis and crops only in opted-in fresh generation, with evidence-aware metadata rather than names as construction authority. Retain requested framing and declared fit.
- [ ] Run specialist QC after existing final postpasses. Share the existing final image slot; never add a second final repair. Recheck normal image, matching, base and fit gates as applicable before storage.
- [ ] Run targeted and full isolated server tests without live DB environment variables.

## Task 4: Evidence, independent review and Draft PR

- [ ] Reuse successful and failed pink candidates and normal gray/brown controls. Run the actual new production module in a non-persistent local harness, recording hashes, exact models, usage and failures. No DB/R2 writes.
- [ ] Complete all three checks for the prior successful pink control. Test automatic correction without manually injecting the expected seam count. Keep failure evidence, do not cherry-pick a pass.
- [ ] Independent agent reviews code and generated output. Address actionable findings; preserve the report of unresolved visual limitations.
- [ ] Make a concise local HTML checkpoint, commit only owned code/tests/docs, push a new branch and create a Draft PR. Report tested and untested boundaries, then wait.
