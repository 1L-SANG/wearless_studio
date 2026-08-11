# Mannequin Continue CTA Design QA

## Evidence

- Source visual truth: `/tmp/wearless-mannequin-hotspots-adjusted.jpg` plus the user direction to reuse the existing CTA CSS and move the action lower.
- Browser implementation: `/tmp/wearless-mannequin-cta-polished.jpg`
- Focused comparison: `/tmp/wearless-cta-polished-comparison.png`
- Viewport and implementation pixels: 1749×1003 CSS px, 1749×1003 captured px, density 1.
- Focused comparison: equal 340×530 crops from the source and implementation.
- State: real HTTP backend, logged-in project, mannequin version 1, no pending adjustment.

## Findings

- Fonts and typography: the CTA now uses the shared large primary-button type scale and icon size.
- Spacing and layout: the image-to-action gap increased from 14px to the existing 24px spacing token.
- Colors and tokens: the CTA reuses `btn-primary` and the existing `btn-glowring` treatment instead of maintaining mannequin-specific button styling.
- Image quality: the mannequin image and hotspot positions are unchanged.
- Copy and content: `이대로 진행` remains unchanged.
- P0/P1/P2: none.

## Comparison history

1. P2: the CTA was a plain feature-specific rounded rectangle and sat too close to the image.
2. Replaced it with the shared `Button` primary/large/block classes, reused `btn-glowring`, and increased the gap to 24px.
3. Post-fix focused comparison shows the CTA lower, pill-shaped, and consistent with existing credit-consuming actions.

## Interaction and runtime checks

- The CTA remains available in the default mannequin state.
- Disabled, hover, focus, reduced-motion, and icon behavior come from the existing shared button classes.
- A clean reload produced no new console errors.

final result: passed
