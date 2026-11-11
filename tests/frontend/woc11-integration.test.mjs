import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignChoices } from '../../src/lib/pageTemplates/designChoices.js';
import { sectionDesignAxes } from '../../src/lib/pageTemplates/designAxes.js';
import { changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { assemblePage, measureTextWidth, wrapText } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';
import details from '../../src/mock/detailRecommendationsFixture.json' with { type: 'json' };

let vite, Picker;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent',
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ StoryboardSectionPicker: Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
});
after(() => vite?.close());
const ids = ['PT-PHOTO-CARD-01', 'PT-TEXT-PHOTO-01'];
const product = { name: '디테일 니트', clothingType: 'top', colors: [{ id: 'base', name: '블루', isBase: true, swatchId: 'blue',
  images: [{ slot: 'Front', src: '/front.jpg' }, { slot: 'Detail', src: '/detail.jpg' }] }] };
const input = templateId => {
  const analysis = { targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId }, detailRecommendations: details,
    partDescriptionCopy: details.candidates.filter(c => c.kind !== 'fabric').map(c => ({ point: c.label, desc: '끝을 가지런히 정돈한 마감이라\n전체 선이 단정해 보여요' })) };
  return { product, analysis, storyboard: seedStoryboard(product, analysis) };
};
const pointRun = value => sectionRuns(value.storyboard).find(run => run.kind === 'points');

for (const templateId of ['emo', 'show']) test(`WOC11 ${templateId} points picker uses one flat pair with the correct default`, () => {
  const value = input(templateId), run = pointRun(value), catalogs = { detailProduct: product, analysis: value.analysis, genExamples: [] };
  const expected = templateId === 'emo' ? ids : [...ids].reverse();
  assert.equal(run.designId, expected[0]);
  assert.deepEqual(sectionDesignChoices(run, { storyboard: value.storyboard, product }).map(d => d.id), expected);
  assert.equal(sectionDesignAxes(run, { storyboard: value.storyboard, product }).hasAxes, false);
  const html = renderToStaticMarkup(React.createElement(Picker, { mode: 'design', blocks: value.storyboard, runKey: run.key, catalogs }));
  assert.equal((html.match(/aria-pressed=/g) || []).length, 2);
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
  assert.match(html, /사진과 글 칸 카드/); assert.match(html, /제목, 설명, 사진/);
  assert.doesNotMatch(html, /aria-label="(?:배치|꾸밈)"|액자 사진/);
  assert.equal((html.match(/<small>기본<\/small>/g) || []).length, 1);
  const count = uniqueGenerationCutCount(value.storyboard, product), original = structuredClone(value.storyboard);
  const withoutDesign = blocks => blocks.map(({ pageDesignId, ...block }) => block);
  for (const id of expected) {
    const next = changeTemplateSectionDesign(value.storyboard, run.key, id, { product });
    assert.equal(validateStoryboardV4(next), next);
    assert.equal(uniqueGenerationCutCount(next, product), count);
    assert.deepEqual(withoutDesign(next), withoutDesign(original));
    assert.ok(next.filter(block => block.pageSectionKey === run.key).every(block => block.pageDesignId === id));
  }
  assert.deepEqual(value.storyboard, original);
});

test('WOC11 22 full-width codepoints fit both description boxes across all page font styles', () => {
  const line = '가'.repeat(22);
  assert.equal(measureTextWidth(line, 10000, 35, catalog.textMetrics, 0), 770);
  assert.equal(measureTextWidth('가'.repeat(23), 10000, 35, catalog.textMetrics, 0), 805);
  for (const templateId of ['emo', 'show']) for (const fontStyle of Object.keys(catalog.fontStyles)) {
    const value = input(templateId), run = pointRun(value);
    value.analysis.pageStyle.fontStyle = fontStyle;
    value.analysis.partDescriptionCopy = details.candidates.map(c => ({ point: c.label, desc: `${line}\n${line}` }));
    for (const id of ids) {
      const page = assemblePage({ ...value, storyboard: changeTemplateSectionDesign(value.storyboard, run.key, id, { product }) });
      const descriptions = page.filter(block => block.pageSectionKey === run.key).flatMap(block => block.elements).filter(el => /items\.\d+\.desc$/.test(el.copyRole));
      assert.equal(descriptions.length, 3);
      for (const el of descriptions) {
        assert.equal(el.text, `${line}\n${line}`);
        assert.equal(el.style.font, resolvePageTokens(value.analysis.pageStyle).fonts.body.font);
        assert.equal(wrapText(line, el.w, el.style.size, catalog.textMetrics, el.style.tracking).length, 1);
        assert.ok(measureTextWidth(line, 10000, el.style.size, catalog.textMetrics, el.style.tracking) <= el.w);
        assert.equal(el.h, el.style.lineHeight * 2);
      }
    }
  }
});
