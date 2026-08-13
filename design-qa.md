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

---

# Extended Colorway Pair Design QA

## Evidence

- Source visual truth: `/Users/daily/Desktop/Wearless/Wearless2/1111.png` (1900×7688 px).
- Browser implementation capture: `/private/tmp/wearless-colorway-impl.png`.
- Side-by-side comparison: `/private/tmp/wearless-colorway-comparison.png`.
- Viewport: 1280×720 CSS px, device-pixel ratio 2.
- State: mock extended-mode storyboard data rendered through the production editor-block assembler and editor preview; the isolated fixture contained one additional-color pair.

## Findings

- Layout: the additional color is one edge-aligned two-column row, full shot on the left and medium shot on the right.
- Crop: the bottom-category medium slot uses the waist-to-feet crop contract; upper categories retain the existing head-to-hip crop contract.
- Labels: product name/color is centered below the images, followed by the matching-garment name/color in stronger type.
- Continuity: both cuts share one colorway group, model selection, and matching-garment selection while using distinct poses.
- Runtime: no application errors. Only the repository's pre-existing React Router v7 future-flag warnings appeared.
- Placeholder limitation: mock preview silhouettes validate composition and crop slots; generated photography remains the pipeline output.
- P0/P1/P2: none.

## Interaction and data checks

- The pair is emitted only for additional colors in extended mode.
- The main color keeps the primary matching garment; each additional color picks the selected compatible garment with the largest light/dark contrast, with the primary garment as deterministic fallback.
- If pair identity is broken by later editing, the assembler safely falls back to ordinary independent blocks instead of forcing an invalid paired layout.

final result: passed
