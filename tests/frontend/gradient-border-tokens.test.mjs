import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const tokens = read('src/styles/tokens.css');
const files = [
  ['src/styles/app.css', ['.toast']],
  ['src/styles/features.css', ['.sp-chip', '.ward-cell .ai-flag', '.btn-glowring',
    '.btn-glowring:hover:not([disabled])', '.ward-cell.fresh::after']],
  ['src/features/pricing/Pricing.module.css', ['.buttonRing']],
];

test('border tokens match the six landing hero colors and close the seam with the first color', () => {
  ['#2E4985', '#4762AE', '#4297B6', '#658CCC', '#7480D4', '#3F518E'].forEach((color, i) => {
    assert.ok(tokens.includes(`--ring-flow-${i + 1}: ${color};`), color);
  });
  const stops = tokens.match(/--ring-flow-stops:\s*([^;]+);/)?.[1];
  assert.equal(stops, 'var(--ring-flow-1), var(--ring-flow-2), var(--ring-flow-3), var(--ring-flow-4), var(--ring-flow-5), var(--ring-flow-6), var(--ring-flow-1)');
  assert.match(tokens, /--glow-sky:\s*#7fd0f0;/);
  assert.match(tokens, /--glow-sage:\s*#b9a5e8;/);
  assert.match(tokens, /--glow-sun:\s*#e3ddf3;/);
  assert.match(tokens, /--glow-mauve:\s*#5fa7dd;/);
});

for (const [path, selectors] of files) test(`${path}: every listed conic border uses border stops, never background glow colors`, () => {
  const css = read(path);
  for (const selector of selectors) {
    const rule = css.slice(css.indexOf(`${selector} {`), css.indexOf('}', css.indexOf(`${selector} {`)) + 1);
    assert.ok(rule.startsWith(`${selector} {`), `${selector} exists`);
    assert.match(rule, /conic-gradient\(from var\(--[a-z-]+(?:, 0deg)?\), var\(--ring-flow-stops\)\)/, selector);
    assert.doesNotMatch(rule, /--glow-/, selector);
  }
});
