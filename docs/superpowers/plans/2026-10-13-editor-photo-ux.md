# Editor photo UX implementation plan

> **For agentic workers:** Use superpowers:executing-plans and test-driven-development. The owner approved the interactive design and explicitly requested implementation; execute inline, then obtain an independent review.

**Goal:** Make existing photo editing easier without changing the production AI ORB or right mini-preview.
**Architecture:** Keep Editor's existing document, save and history contracts. Put crop geometry and photo replacement/transfer decisions in small pure helpers, and wire them into the existing pointer engine and inspector.
**Tech Stack:** React 18, JavaScript, existing CSS tokens, Node frontend tests, Vite mock mode.
**Spec:** The owner-approved v4 comparison and final corrections recorded below.

## Global constraints / approved design

- Initial baseline: `30806694` (PR 539), branch `codex/editor-photo-ux`. On 2026-10-15 integrate main `e5528c63` in `D:/wearless_studio/.worktrees/editor-photo-ux-pr541`; the primary D-drive checkout is in use by another task and is not modified.
- Keep CanvasAiLayer/useCanvasAi and production MiniPreview unchanged.
- Rename the wardrobe tool to 보관함. Do not add explanatory text below its title or other headings. Keep existing upload/group UI.
- Selecting one canvas photo and clicking a library photo replaces its source while preserving its frame. With no selected photo, use existing insertion. No extra replace/add buttons or selected-library-photo marker.
- Put original-photo download and crop in the existing left image inspector. No floating photo toolbar. Add a modest inspector shadow and preserve established controls/copy.
- Double-click opens inline crop. Interior drag moves the retained range; Shift-drag moves the underlying image. All four edges and eight handles work. In-canvas controls: 자유/1:1/3:4/4:5/16:9 and a 100–200% zoom slider (two additional ratios approved in the 2026-10-14 follow-up). Outside click commits; Enter/apply also commits; Esc/cancel discards; one Undo restores a commit.
- Drag alignment shows short numeric gaps and equal spacing; Alt temporarily bypasses snapping. No target-name labels or demo buttons.
- Only a photo-body move may cross blocks after a deliberate drop well inside another block. Resize/rotate/crop never transfer. No move/Undo floating buttons. Preserve source block, photo appearance, target contents and global history. Do not transfer while detail-page generation is running.
- During a cross-block photo move, show a thin black destination border, 1.5 screen pixels at all canvas zoom levels. Preserve that marker in the existing 이어보기 mode without adding a card shadow there.
- Automatic edge scrolling is now approved for a single eligible photo-body drag only: near the canvas viewport's top/bottom, continue scrolling while held; stop away from the edge, outside the viewport, at the scroll boundary, or on drop/cancel/blur/unmount. Resize/crop/text/shape paths remain unchanged. Manual scroll displacement compensation remains part of correct photo movement.
- Remove only the existing normal AI panel title 새 이미지 만들기 beneath the toolbar's AI heading, per explicit follow-up confirmation. Keep generation controls and pending-request confirmation/safety messages.
- No paid generation, purchase, main-branch merge or production deployment. PR creation/update was subsequently requested explicitly by the owner (PR 541).

## Review focus

1. Crop commit before the clicked inspector/library action: no stale selected source or dropped click; one history entry per action.
2. Cancelled/blurred drags, zoom and scrolled canvases: never transfer on lost pointer or resizing, restore live DOM.
3. Slots/info forms and image-flow groups: replacement must preserve geometry; transfer must not orphan form bindings or silently replace target photos.
4. Locked/hidden/pending images and deleted targets: no unauthorized mutation or resurrection.
5. Original download uses the stable original source, not the thumbnail; asynchronous failure is visible and temporary URLs are released.

## Task 1: Existing editor photo interactions

**Files:**
- Create `src/features/editor/editorCrop.js`, `editorPhotoEditing.js` and their frontend tests.
- Modify `Editor.jsx`, `EditorPanels.jsx`, `editorSnap.js`, `src/styles/features.css`.
- Preserve `CanvasAiLayer.jsx`, `useCanvasAi.js` and the body of `MiniPreview`.

**Interfaces:**
- Crop helper consumes the existing `{fx,fy,fw,fh,ox,oy,iw,ih}` draft and returns drafts/Element patches; does not change the saved crop schema.
- Photo helper consumes saved blocks and a selected image ID; replacement returns immutable blocks, transfer never modifies target elements.
- Spacing helper consumes existing measured rectangles and canvas deltas, returning a delta and numeric gap segments; existing snap return shape is preserved.

- [x] Write failing geometry/photo/spacing tests (literal expected values), run and observe RED.
- [x] Implement minimal helpers, wire existing UI, and run new tests plus existing editor/ORB regression tests → no new failures.
- [x] Build and operate the real mock Editor: replacement/add, native download, crop edges/interior/ratios/outside/Esc/Undo/Redo, cross-block drag vs resize, original preview/ORB. Gap/Alt/Shift calculations verified automatically; held modifier drag not browser-verified.
- [x] Run the complete frontend suite and compare with baseline; record all pre-existing failures and newly introduced failures separately.
- [x] Independent review of the final diff, fix substantive issues with regression tests, rerun checks. Leave local implementation for the owner; do not deploy.

## Verification ledger

- Baseline frontend suite on Windows: 3475 tests, 3015 passed, 459 failed, 1 skipped. Includes Vite tests using URL.pathname (`/D:/...`) as filesystem roots and pre-existing assertion failures. Full output: `.scratch/editor-photo-ux/baseline-test.log`. Do not alter unrelated tests/features as part of this editor request.
- Final frontend suite: 3501 tests, 3041 passed, 459 failed, 1 skipped; failure name set unchanged. Focused 85/85 passed. Original full build, final Editor QA build and final http-mode three-entry compilation passed (final compilation skips unchanged public catalog copying only).
- Independent review: frame-slot form bindings and crop/history boundaries fixed and re-reviewed. Browser QA found unchanged dimension-field blur could create duplicate history; regression RED→GREEN, live reproduction then passed.
- Ruling: keep the existing information-block rebuild policy and warning. Updating that form may replace manual added/moved photos; changing this policy is outside this photo-interaction task.
- Full evidence and named pre-existing failure inventory: `.scratch/editor-photo-ux/verification.md`. Real Editor mock remains at `http://127.0.0.1:5196/`. No real generation, push, merge, PR or deployment.

### 2026-10-14 owner scope correction

- The owner requested a thinner black destination marker and an audit removing unagreed additions. Removed automatic edge scrolling and the extra 3:4/16:9 crop choices; replaced the implementation's +/- zoom buttons with the approved mock's slider. Alt bypass, Shift pan and zoom were present in the approved mock, so they remain.
- Scope regression tests observed RED on the previous implementation, then GREEN after correction. Focused suite: 89/89 passed. Full suite: 3505 tests, 3045 passed, 459 failed, 1 skipped; failure-name set unchanged from the previous final run. New logs: `scope-focused-tests.log`, `scope-full-tests.log`.
- Actual Editor mock build passed (`scope-qa-build.log`). Chrome checked the three crop choices, zoom via keyboard, photo body transfer preserving size and Undo restoring its original block. Product CSS marker fixture checked 40%, 100%, 160% and 이어보기: black token and 1.5 screen-pixel inset at each scale. The fixture is style evidence, not a held-pointer screenshot of the real editor.
- Independent 6.1 Sol read-only scope audit and correction re-review found no further important reachable issues or new user-facing features. No deployment or remote writes.

### 2026-10-14 follow-up approval

- Owner approved 3:4/16:9 and photo-body edge scrolling after their behavior was explained, then confirmed removal of the existing 새 이미지 만들기 title. No other user-facing feature changed.
- RED→GREEN observed for missing ratio choices, scrolling side effects, and duplicate AI panel heading. Related tests: 106/106 passed. Complete frontend run: 3517 tests, 3057 passed, 459 existing failures, 1 skipped; failure-name set unchanged. An initial extra failure was an obsolete title expectation in example-owned-pose-direction.test.mjs; updated that expectation to the remaining cut/generation controls, reproduced, and reran the full suite. Existing Windows-path failures remain untouched.
- Actual Editor mock build passed, 10.36 seconds. Chrome verified 3:4 and 16:9 frame geometry, normal AI title absence, photo body edge drag scrolling 0→12px with size preserved, stop after release, and Undo restoring the source block. Continuous held scrolling is covered by real-handler frame-loop tests, not a long held-pointer browser performance test. Resize/crop exclusions are checked by separate source paths plus existing photo interaction coverage.
- Independent 6.1 Sol read-only review found no Critical/Important issues. Evidence: .scratch/editor-photo-ux/approved-additions-*.log and approved-additions-ai.png. No paid generation, push, PR or deployment.

### 2026-10-15 approved review fixes and GIF integration

- Owner approved all five reviewed corrections, with template work restricted to a minimal information/manual-block preservation guard. Existing text-only template duplication and automatic empty-block removal remain out of scope; no template redesign or new data fields.
- Preserve both sides of the GIF merge: ImagePanel photo download/crop and GIF information/conversion, plus ordinary photo and original GIF/PNG/ZIP export functions.
- Two guards in the existing template predicate protect information/automatic blocks and non-template custom blocks. The old rule for existing templates remains unchanged. Moved photos explicitly use the existing `frameSlot:false` contract, while legacy undefined/true slots retain their meaning; cleared source slots keep provenance and `slotCleared`.
- Valid Delete/Backspace cancels an unconfirmed crop before the original element/slot deletion. Input-field keys remain unaffected. Original crop reset also clears the selected ratio.
- New real-handler regression cases observed 14 failures before the fix and 15/15 passes afterward. Final focused suite including merged GIF compatibility: 126/126 passed. GIF signing-input tests also assert that only the raster/PNG ZIP, not original GIFs, enters the signing boundary.
- Matched Windows full-suite comparison using `--test-force-exit` and concurrency 4: main e5528c63 has 3533 tests / 3062 pass / 469 fail / 1 skip; merged source has 3595 / 3124 pass / the same 469 failure names / 1 skip. The force-exit option is needed because old Windows-path SSR failures can leave Vite sockets open before harness cleanup; it does not make failed cases pass. Earlier baseline/full attempts without it were interrupted and are not successful checks.
- Original three-entry `pnpm build` passed (1m 4s); actual Editor mock build passed (6.97s). Astra xhigh correction re-review found no new Critical/Important regression or scope expansion. The crop keyboard harness uses a simplified React-history publication boundary, not a browser-history integration claim.
- Browser verification is unavailable in this turn: both supported automation tools fail before executing code with a kernel-assets path error, including after reset. Do not claim a fresh screenshot or actual merged GUI/PNG download smoke. Initial-head browser evidence remains historical; current JSX rendering, serialization and file-package tests passed.
- Detailed logs live under `D:/wearless_studio/.scratch/editor-photo-ux/fix-*.log`. Update PR 541 only after verification; do not merge main or deploy production.
- Post-push CI on 1460ebfa found one additional test-harness failure hidden by the pre-existing Windows CRLF import-stripping error: the old AI-slot Undo harness had not supplied the new crop state/flush boundary. Supplied its explicit no-active-crop dependencies and made only this harness's import stripping accept CRLF. Reproduced the Windows failure then passed all 5 real slot/Undo/ORB tests. No product behavior changed. The other CI failure is the same facemarket-register-v3 temporary-query-recovery case already failing on exact main e5528c63 (run 38404802670); no unrelated registration fix is included.

### 2026-10-15 실제 화면 리뷰 후 승인된 보완

- 오른쪽 사진의 자르기 도구막대가 작업 영역 밖으로 잘리는 문제를 수정한다. 실제 도구막대 너비와 작업 영역을 재서 안쪽에 놓고, 아래 공간이 부족하면 위에 놓는다. 좁은 작업 영역에서는 기존 버튼 묶음을 줄바꿈한다. 스크롤, 캔버스 확대율, 창 크기 변경 때 다시 배치하고 자르기가 끝나면 관찰을 정리한다.
- 비율을 바꿀 때 직전에 줄어든 영역을 다시 자르지 않고, 비율 선택 전의 기준 크기를 재사용한다. 손잡이로 크기를 직접 바꾸면 그 크기를 새 기준으로 쓴다. 사진 위치와 확대 상태는 유지하고, 원본 복원 시 비교 기준도 초기화한다. 이 기준은 편집 중 임시 상태이며 저장 crop 계약에 추가하지 않는다.
- 사진 인스펙터의 다운로드 버튼은 실제 동작을 명확히 알리도록 `원본 다운로드`로 표시한다.
- 재현 검사 8건이 기존 코드에서 실패한 것을 확인한 뒤 수정했다. 관련 검사 107건이 통과했고, 실제 에디터의 오른쪽 사진에서 적용과 취소 버튼 노출 및 3:4와 16:9 반복 선택 후 같은 크기 복원을 확인했다. 독립 코드 검토에서 추가 중요 결함을 발견하지 못했다. 전체 검사와 빌드의 최종 결과는 PR 본문에 기록한다.
