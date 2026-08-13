# Editor Notion-style Drop Guide QA

## Evidence

- User interaction reference: `/Users/nojeong-un/Downloads/화면 기록 2026-08-12 오후 8.36.29.mov` and the final direction to match Notion's drag guide.
- Public pattern reference: Notion uses a thin blue placement guide while dragging blocks and keeps its add control separate.
- Browser comparison was intentionally not run because the user retained browser QA.

## Design decision

- The active guide is one `#2383E2` blue line with no center plus, halo, shadow, fade, or motion effect.
- Its source height is counter-scaled against the editor zoom so it remains visually 2px on screen instead of becoming too thin or thick at different canvas zoom levels.
- The generous 128px drop target and 56px vertical hit padding remain, so the simpler appearance does not reduce usability.
- Image-card padding remains at its original geometry; only the insertion guide changed.

## Verification

- Targeted drop-guide regression: passed.
- Frontend tests: 430 passed.
- Production build: passed with only the repository's existing Rollup chunk/import warnings.
- Visual and interaction comparison remains deferred to user QA.

final result: blocked

---

# Editing Page Palette and Block Insertion QA

## Visual truth

- Palette reference: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_cKTRY8/스크린샷 2026-08-13 오전 11.56.01.png`
- Drag-placement reference: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_LMO2Tm/화면 기록 2026-08-13 오전 11.56.45.mov`
- Extracted video contact sheet: `/tmp/wearless-editor-ref.v38kjA/contact-sheet.png`
- Extracted placement-band detail: `/tmp/wearless-editor-ref.v38kjA/drop-line-detail.png`

## Implementation captures

- Full editor: `/tmp/wearless-editor-qa/editor-full.jpg`
- Palette open: `/tmp/wearless-editor-qa/palette-full-final.jpg`
- Palette focus crop: `/tmp/wearless-editor-qa/palette-implementation.png`
- Side-by-side palette comparison: `/tmp/wearless-editor-qa/palette-comparison.png`
- Content cards and document order: `/tmp/wearless-editor-qa/content-drag-and-order.jpg`

## Capture conditions

- Browser: Codex in-app Browser, mock API mode.
- Viewport: 1920 × 1080 CSS px; device pixel ratio 1.
- Full captures: 1920 × 1080 px.
- Palette reference: 190 × 198 px.
- Palette implementation focus crop: 190 × 198 px, matching the reference pixel dimensions.
- Editor state: seeded 12-block document, 40% canvas zoom. Palette capture selected the product-name text layer. Content capture opened the Frame panel and scrolled its left rail to show “판매에 도움”.

## Findings and comparison history

1. Initial palette pass matched the 8-column geometry and reference colors, but component-specific quick colors appended five extra swatches. Severity: P1 because the visible grid no longer matched the supplied 8 × 8 reference.
2. The preset source was fixed to the reference's exact 64 colors. Browser recomputation then reported 64 rendered `.sf-preset` nodes, a 190 px popover, and the expected 8-column grid. The equal-size source/implementation crops were opened together in `palette-comparison.png`. Result: passed.
3. The shipping/returns block rendered as the last `.canvas-block` in the seeded document. Result: passed.
4. The Frame panel rendered all “내용 추가” sections in one scroll surface, including “판매에 도움”; all 17 frame/info cards exposed `draggable="true"`. Result: passed.
5. The reference video's insertion indicator is a full-width pale-blue band, not a thin Notion line. The implementation uses a screen-invariant 12 px band with subtle top/bottom edges and the expanded 128 px drag hit area. The browser's atomic drag command could not pause mid-drag for a stable screenshot, so this state is covered by the computed CSS inspection and frontend regression test. Result: passed.
6. Browser console contained only the project's pre-existing React Router v7 future-flag warnings; no runtime error was recorded during the editor checks.

## Final result

Passed. No open P0/P1 visual discrepancy remains for the requested palette, placement band, content-card visibility, or initial shipping/returns order.

---

# Mannequin Continue CTA Design QA

## Evidence

- Source visual truth: `/tmp/wearless-mannequin-hotspots-adjusted.jpg` plus the user direction to reuse the existing CTA CSS and move the action lower.
- Browser implementation: `/tmp/wearless-mannequin-cta-polished.jpg`
- Focused comparison: `/tmp/wearless-cta-polished-comparison.png`
- Review-fix browser recording: `/Users/daily/.config/browser-harness/agent-workspace/recordings/pr106-mannequin-smoke` (11 frames).
- Viewport and implementation pixels: 1749×1003 CSS px, 1749×1003 captured px, density 1.
- Focused comparison: equal 340×530 crops from the source and implementation.
- State: visual comparison used the real HTTP backend; review-fix interaction smoke used the mock flow with a generated mannequin, three hotspots, one selected value, and selection reset.

## Findings

- Fonts and typography: the CTA now uses the shared large primary-button type scale and icon size.
- Spacing and layout: the image-to-action gap increased from 14px to the existing 24px spacing token.
- Colors and tokens: the CTA reuses `btn-primary` and the existing `btn-glowring` treatment instead of maintaining mannequin-specific button styling.
- Image quality: the mannequin image and hotspot positions are unchanged.
- Copy and content: the short action copy remains, with the expected credit cost and any real-model fee restored on the same CTA.
- P0/P1/P2: none.

## Comparison history

1. P2: the CTA was a plain feature-specific rounded rectangle and sat too close to the image.
2. Replaced it with the shared `Button` primary/large/block classes, reused `btn-glowring`, and increased the gap to 24px.
3. Post-fix focused comparison shows the CTA lower, pill-shaped, and consistent with existing credit-consuming actions.
4. Review follow-up restores the paid-action cost, makes the picked value visible and reversible, and locks adjustment controls once proceed/save starts.

## Interaction and runtime checks

- The CTA remains available in the default mannequin state.
- The CTA, hotspots, option tiles, close, and reset controls disable while save/generation is in progress.
- Selected tiles use `aria-selected`; hotspot labels expose the picked value; `선택 취소` removes the pending adjustment.
- Reduced-motion rules directly cover the hotspot dot pseudo-element and tooltip transition.
- Browser smoke confirmed `이대로 진행 · 13 크레딧` → `수정 반영 · 2 크레딧` after choosing `레귤러`, then back to the original CTA after `선택 취소`.
- The smoke ended with no Vite error overlay.

final result: passed

# Editor Native Color and Bubble Border Design QA

## Evidence

- Source visual truth: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_L5mVV7/스크린샷 2026-08-12 오후 6.22.22.png` (836×548) and `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_MXYlOo/스크린샷 2026-08-12 오후 6.22.53.png` (236×244).
- Browser implementation: `/tmp/wearless-color-border-implementation.png` at a 1280×720 CSS-pixel viewport, density 1.
- Focused inspector capture: `/tmp/wearless-color-border-inspector.png` (320×660).
- Focused rendered bubble capture: `/tmp/wearless-bubble-render-only.png` (152×42).
- Combined comparison input opened for review: `/tmp/wearless-color-border-comparison.png` (1800×1110).
- State: first editor block selected, the grouped Q&A question/answer bubbles selected, Text inspector scrolled to fill and border controls.

## Findings

- Color interaction: the visible color chip is a direct native `input[type=color]`, so clicking it delegates the full color panel to macOS/browser instead of imitating the system panel in application CSS. HEX and opacity remain visible beside/below it for precise editing.
- Bubble border: new chat and FAQ bubbles use a subtle `#B9B9BE` 1px outline, matching the low-contrast edge in the source without creating a heavy card border.
- Border controls: border color, remove/restore action, 0.5–12px slider, and numeric pixel input are grouped under `말풍선 테두리`.
- Group behavior: browser interaction changed both selected Q&A bubbles from 1px to 3.5px and from `#B9B9BE` to `#FF3B30`; both SVG paths updated together, then were restored to the default.
- Layout and hierarchy: the existing inspector width, section rhythm, type scale, and token system are preserved. No unintended horizontal overflow was found.
- Platform-owned surface: the system color panel itself is not pixel-compared inside the browser capture because macOS owns that modal; the implementation comparison verifies the native trigger and the application-side controls around it.
- P0/P1/P2: none.

## Verification

- Frontend regression suite: 410 passed, 0 failed.
- Production build: passed; only the repository's existing Rollup chunk/import warnings remain.
- Browser smoke: native color inputs present, border width control present, grouped appearance updates verified, default state restored.

final result: passed

# Editor Speech-Bubble Visual QA

## Evidence

- Reference: `스크린샷 2026-08-12 오후 2.55.42.png`, `스크린샷 2026-08-12 오후 2.55.47.png`
- Surface: local editor at 40% canvas zoom
- Verified: paired chat bubbles, left/right tail direction, default outline, grouped selection, integrated HEX/color/opacity control
- Verified: text click selects the composite, parent-background click stays on one layer across repeated clicks, and parent-only Backspace deletion preserves its text child
- Verified: canvas click-away cannot clear a selection originating inside an element, block, or Moveable control
- P0/P1/P2: none
- P3: group selection intentionally shows each member outline in edit mode; export output remains clean

final result: passed

---

# Editor HEX Palette, Text Resize, and Bubble Radius Design QA

## Evidence

- Reference color UI: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_L5mVV7/스크린샷 2026-08-12 오후 6.22.22.png`.
- Reference bubble rounding: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_MXYlOo/스크린샷 2026-08-12 오후 6.22.53.png`.
- Browser comparison was intentionally not run because the user retained browser QA for this iteration.

## Implementation review

- Color controls now use an application-owned default palette plus one `HEX 색상` text entry; the native RGB-capable color input is removed.
- Text elements ignore the global aspect-ratio lock and expose all eight resize directions, including left and right width handles.
- New and migrated speech bubbles store a 45px default radius; the left inspector exposes a 0–100px radius slider and numeric field.
- Generated photo blocks remove their text layers during editor hydration and generation-result merging while FAQ, information, and custom frame blocks retain their copy.

## Verification

- Pure behavior regressions cover HEX presets, absence of native color inputs, generated-photo copy removal, free text resizing, and pixel-based bubble radius rendering.
- Frontend tests and production build passed.
- Visual and interaction comparison remains deferred to user QA.

final result: blocked

---

# Editor Between-Block Drop Guide Polish QA

## Evidence

- Interaction reference: `/Users/nojeong-un/Downloads/화면 기록 2026-08-12 오후 8.28.40.mov`.
- The extracted interaction frames showed that the 2px canvas line collapsed to a sub-pixel-looking guide at the current editor zoom and that the active gap needed a larger visual landing area.
- Browser comparison was intentionally not run because the user retained browser QA.

## Implementation review

- The insertion guide grows from 2px to 8px, using a near-black line, matching halo, and a larger 40px center action.
- The active drop row now provides 56px vertical padding on each side while preserving document layout with matching negative margins.
- The guide fades and thickens in while the center action springs into place; reduced-motion users receive the final state without animation.
- Newly created image blocks retain their original 60px horizontal and 50px vertical inset; the added space belongs only to the insertion guide.

## Verification

- Regression coverage verifies guide thickness, padded hit area, both animations, reduced-motion handling, and updated image-card geometry.
- Frontend tests: 430 passed.
- Production build: passed with only the repository's existing Rollup chunk/import warnings.
- Visual and interaction comparison remains deferred to user QA.

final result: blocked

---

# Editor Between-Block Image Drop and Crop Toolbar QA

## Evidence

- Drag interaction reference: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_dQzfTg/화면 기록 2026-08-12 오후 7.24.58.mov`.
- Clipped crop toolbar reference: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_9j6Ct5/스크린샷 2026-08-12 오후 7.24.24.png`.
- Resulting image-block reference: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_YC8a8W/스크린샷 2026-08-12 오후 7.26.07.png`.
- Browser comparison was intentionally not run because the user retained browser QA.

## Implementation review

- The crop action bar is rendered as a direct child of the top-level canvas block, outside `.block-clip`, so `원본`, confirm, and cancel remain visible at a photo's lower edge.
- Wardrobe image drags activate the existing forgiving between-block drop rows and show a full-width insertion line with a centered plus marker.
- Dropping on that marker creates a dedicated white image block with 60px horizontal and 50px vertical inset, preserves the source aspect ratio, and keeps upload provenance.
- Moving across the plus marker no longer clears the active insertion state through a child `dragleave`.

## Verification

- Regression coverage verifies unclipped toolbar placement, wardrobe drag activation, stable plus-marker hover, and portrait block geometry.
- Frontend tests: 429 passed.
- Production build: passed with only the repository's existing Rollup chunk/import warnings.
- Visual and interaction comparison remains deferred to user QA.

final result: blocked

---

# Editor Wardrobe and Crop Controls Design QA

## Evidence

- Wardrobe reference: `/Users/nojeong-un/Downloads/WhatsApp Image 2026-08-12 at 19.06.03.jpeg`.
- Crop-control reference: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_hteZtJ/스크린샷 2026-08-12 오후 7.08.36.png`.
- Browser comparison was intentionally not run because the user retained browser QA for this iteration.

## Implementation review

- Generated detail-page photos are merged from editor blocks into the wardrobe and ordered by the product color list; user-supplied storyboard photos and editor uploads are grouped under `기타`.
- Upload provenance follows the image through click insertion, drag-and-drop, and frame filling so a saved canvas can reconstruct the `기타` group.
- The crop footer keeps `원본`, removes both explanatory pills, and adds compact mouse-accessible confirm and cancel controls with accessible labels.

## Verification

- New regressions cover color grouping, generated-photo deduplication, direct-upload classification, drag payload provenance, and crop-control source wiring.
- Frontend tests: 423 passed.
- Production build: passed with only the repository's existing Rollup chunk/import warnings.
- Visual and interaction comparison remains deferred to user QA.

final result: blocked

---

# Editor Combined Presets and Custom Palette QA

## Evidence

- Bubble outline reference: `/var/folders/3b/rzpkxc4j431b13dp6k16_vg40000gn/T/TemporaryItems/NSIRD_screencaptureui_M4uLnP/스크린샷 2026-08-12 오후 6.54.47.png`.
- Color-picker direction continues to use the earlier Photoshop-style reference while retaining the user's HEX-only input constraint.
- Browser comparison was intentionally not run because the user retained browser QA.

## Implementation review

- The same popover now keeps the default color grid visible and adds a continuous saturation/value palette plus a hue slider underneath it.
- Direct palette interaction updates the existing HEX field and never exposes RGB inputs.
- The neutral rectangular `.el` outline is suppressed only for unselected speech bubbles; their actual bubble stroke and editor selection controls remain intact.

## Verification

- Regression coverage verifies both color surfaces, HSV/HEX conversion, and the speech-bubble outline override.
- Frontend tests: 419 passed.
- Production build: passed with only existing bundle-size/import warnings.
- Visual and interaction comparison remains deferred to user QA.

final result: blocked

---

# Editor Parent-Bottom Drag Clamp Design QA

## Evidence

- Reference states: `/Users/nojeong-un/Downloads/스크린샷 2026-08-12 오후 7.16.16.png` and `/Users/nojeong-un/Downloads/스크린샷 2026-08-12 오후 7.16.20.png`.
- Browser comparison was intentionally not run because the user retained browser QA for this iteration.

## Implementation review

- Element and grouped-element movement now consumes only the remaining space inside the current parent block and stops when the lowest selected edge meets the block bottom.
- Keyboard down-arrow movement follows the same boundary as pointer dragging.
- Height normalization adds its 50px safety margin only when content actually overflows; an element already flush with the parent bottom no longer triggers another incremental growth step.
- Image resizing can still expand the block when the resized content genuinely exceeds the current block height.

## Verification

- The deterministic geometry regression reproduces the former 80px overshoot into 50px of remaining space and now clamps it to 50px.
- Repeated height normalization of a flush photo remains idempotent at the same parent height.
- Frontend tests: 425 passed.
- Production build: passed with only the repository's existing Rollup chunk/import warnings.
- Visual and interaction comparison remains deferred to user QA.

final result: blocked
