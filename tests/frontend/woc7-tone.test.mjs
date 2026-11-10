import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolvePageTokens, baseColorOf, sectionTokens } from '../../src/lib/pageTemplates/tokens.js';
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';

const light = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
  .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
const contrast = (a, b) => (Math.max(light(a), light(b)) + .05) / (Math.min(light(a), light(b)) + .05);

for (const templateId of ['emo', 'show']) {
  test(`WOC7 ${templateId} point colors tint pale panels while dark ink remains neutral`, () => {
    const mono = resolvePageTokens({ templateId, colorMode: 'mono' }, 'blue');
    for (const swatch of ['blue', 'beige', 'green', 'pink']) {
      const point = resolvePageTokens({ templateId, colorMode: 'point' }, swatch);
      assert.equal(point.colors.accentBg, catalog.pointPalette[swatch].accentBg);
      assert.equal(point.colors.ink, mono.colors.ink); assert.equal(point.colors.invertBg, mono.colors.invertBg);
      if (templateId === 'show') assert.deepEqual(sectionTokens(point, { block: { invert: true } }).colors,
        sectionTokens(mono, { block: { invert: true } }).colors);
    }
  });
  test(`WOC7 ${templateId} stored point on a neutral/unknown base is effectively mono without mutating storage`, () => {
    for (const swatch of ['white', 'gray', 'black', 'null', null, undefined, 'unknown']) {
      const stored = { templateId, colorMode: 'point' };
      const resolved = resolvePageTokens(stored, swatch);
      assert.equal(resolved.colorMode, 'mono'); assert.deepEqual(resolved.colors, resolvePageTokens({ templateId, colorMode: 'mono' }, swatch).colors);
      assert.equal(stored.colorMode, 'point');
    }
    assert.equal(resolvePageTokens({ templateId, colorMode: 'point' }, baseColorOf({ colors: [] }).swatchId).colorMode, 'mono');
  });
}
test('WOC7 existing point palettes keep readable text on their pale button surfaces', () => {
  for (const [key, paint] of Object.entries(catalog.pointPalette)) assert.ok(contrast(paint.accentInk, paint.accentBg) >= 4.5, key);
});
test('WOC7 existing showroom pale panel nodes actually use the chosen point palette', () => {
  const fixtures = JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json'));
  const input = structuredClone(fixtures.cases.find(c => c.name === 'woc5-show-P01-SHOW-01-reuse').input);
  input.pageStyle = { ...input.analysis.pageStyle, colorMode: 'point' };
  const point = assemblePage(input).filter(b => b.pageSectionKind === 'first');
  assert.ok(point.flatMap(b => b.elements).some(el => el.type === 'shape' && el.fill === catalog.pointPalette.blue.accentBg));
  input.pageStyle.colorMode = 'mono';
  assert.ok(!assemblePage(input).filter(b => b.pageSectionKind === 'first').flatMap(b => b.elements).some(el => el.fill === catalog.pointPalette.blue.accentBg));
});
test('WOC7 both engines return identical effective modes, pale and dark colors for every palette', () => {
  const cases = ['emo', 'show'].flatMap(templateId => ['mono', 'point'].flatMap(colorMode =>
    [...Object.keys(catalog.pointPalette), null, 'unknown'].map(swatch => [{ templateId, colorMode }, swatch])));
  const result = spawnSync((process.env.PAGE_TEMPLATE_PYTHON || new URL('../../server/.venv/bin/python', import.meta.url).pathname), ['-c',
    'import json,sys;from app.agents.page_templates import resolve_page_tokens;json.dump([resolve_page_tokens(*case) for case in json.load(sys.stdin)],sys.stdout)'],
  { input: JSON.stringify(cases), encoding: 'utf8', env: { ...process.env, PYTHONPATH: 'server' } });
  assert.equal(result.status, 0, result.stderr); assert.deepEqual(JSON.parse(result.stdout), cases.map(args => resolvePageTokens(...args)));
});
test('WOC7 analysis no longer offers color cards or their explanation', () => {
  for (const path of ['src/features/analysis/PageTemplatePicker.jsx', 'src/features/analysis/AnalysisForm.jsx']) {
    const code = readFileSync(path, 'utf8');
    assert.doesNotMatch(code, /PageColorModePicker|onColorModeChange|changeColorMode|selectColorModeStyle/);
    assert.doesNotMatch(code, /옷 색에 맞추면 라벨, 제목, 옅은 바탕이 바뀌어요/);
  }
});
