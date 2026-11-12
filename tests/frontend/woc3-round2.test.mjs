import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignChoices, defaultSectionDesignId } from '../../src/lib/pageTemplates/designChoices.js';
import { changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const postcss = createRequire(import.meta.resolve('vite'))('postcss');
const tokenCss = postcss.parse(read('src/styles/tokens.css'));
const layoutCss = postcss.parse(read('src/features/storyboard/StoryboardLayout.module.css'));
const cssValue = (selector, prop) => layoutCss.nodes.find(node => node.selector === selector)?.nodes.find(node => node.prop === prop)?.value;
function token(value) {
  const key = value.match(/^var\((--[\w-]+)\)$/)?.[1];
  if (!key) return value;
  let out; tokenCss.walkDecls(key, decl => { out ??= decl.value; });
  assert.ok(out, `defined token ${key}`); return token(out);
}
const rgb = value => { const hex = token(value).replace('#', ''); assert.match(hex, /^[\da-f]{6}$/i); return [0, 2, 4].map(at => parseInt(hex.slice(at, at + 2), 16)); };
const luminance = values => values.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
  .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
const attrs = str => Object.fromEntries([...str.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, key, val]) => [key, val]));
const fixture = JSON.parse(read('tests/fixtures/page_templates_cases.json'));
const product = { name: '데일리 셔츠', clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = templateId => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId } });
let vite, Preview, Picker, buildDraft, normalizeAnalysisFit;
const locationDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'location');
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ StoryboardPreview: Preview } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));
  ({ StoryboardSectionPicker: Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
  ({ buildDraft } = await vite.ssrLoadModule('/src/mock/db.js'));
  ({ normalizeAnalysisFit } = await vite.ssrLoadModule('/src/lib/fitAxes.js'));
});
after(async () => {
  await vite?.close();
  if (locationDescriptor) Object.defineProperty(globalThis, 'location', locationDescriptor); else delete globalThis.location;
});

for (const [kind, designId] of [['review', 'I09-SCRIM-01'], ['faq', 'T08-SCRIM-01']]) {
  test(`R2-2 ${kind} wireframe shows a distinct lower scrim and light copy lines`, () => {
    const input = structuredClone(fixture.cases.find(item => item.name === 'emo-2-point').input);
    input.storyboard = input.storyboard.filter(block => block.pageSectionKind === kind).map(block => ({ ...block, pageDesignId: designId }));
    const page = assemblePage(input).filter(block => block.pageSectionKind === kind);
    const html = renderToStaticMarkup(React.createElement(Preview, { blocks: page, wireframe: true }));
    const stops = [...html.matchAll(/<stop\b([^>]+)>/g)].map(match => attrs(match[1]));
    assert.equal(stops.length, 2); assert.equal(stops[0]['stop-opacity'], '0');
    assert.equal(stops[1]['stop-opacity'], '0.72');
    const end = rgb(stops[1]['stop-color']), photo = rgb(cssValue('.previewWirePhoto', 'fill'));
    const painted = end.map((v, i) => v * .72 + photo[i] * .28);
    assert.ok(contrast(end, photo) >= 1.8);
    assert.ok(contrast(painted, photo) >= 1.8, `actual composited scrim ${contrast(painted, photo)}`);
    const textElements = page.flatMap(block => block.elements).filter(el => el.type === 'text');
    const lines = [...html.matchAll(/<rect\b([^>]+)>/g)].map(match => attrs(match[1])).filter(el => el.class?.includes('previewTextLine'));
    assert.ok(lines.length >= textElements.length);
    assert.ok(lines.every(el => el.class.includes('previewTextOnScrim')));
    const ink = rgb(cssValue('.previewTextOnScrim', 'fill'));
    assert.ok(contrast(ink, painted) >= 2); assert.ok(contrast(ink, end) >= 2);
    assert.match(page[0].elements.find(el => el.fill?.startsWith('linear-gradient')).fill, /rgba\(0, 0, 0, 0.72\)/);
    const realPreview = renderToStaticMarkup(React.createElement(Preview, { blocks: page }));
    assert.doesNotMatch(realPreview, /sb-wire-scrim-end|previewTextOnScrim/);
  });
}
test('R2-2 bubble design wireframes retain their existing photo, bubble and copy-line paints', () => {
  for (const kind of ['review', 'faq']) {
    const input = structuredClone(fixture.cases.find(item => item.name === 'emo-2-point').input);
    input.storyboard = input.storyboard.filter(block => block.pageSectionKind === kind);
    const html = renderToStaticMarkup(React.createElement(Preview, { blocks: assemblePage(input), wireframe: true }));
    assert.doesNotMatch(html, /linearGradient|previewTextOnScrim/);
    assert.match(html, /previewWirePhoto/); assert.match(html, /previewWireShape/); assert.match(html, /previewTextLine/);
  }
});

test('R2-2 invalid preview paint cannot break scrim detection and only text above a gradient changes tone', () => {
  const text = id => ({ id, type: 'text', x: 70, y: 900, w: 500, h: 30, text: '예시', style: { size: 20, lineHeight: 30 } });
  const html = renderToStaticMarkup(React.createElement(Preview, { wireframe: true, blocks: [{ id: 'mixed', h: 1250, elements: [
    null, text('below-gradient'), { id: 'gradient', type: 'shape', shape: 'rect', x: 0, y: 525, w: 1000, h: 725,
      fill: 'linear-gradient(180deg, rgba(0, 0, 0, 0) 0%, rgba(0, 0, 0, 0.72) 100%)' }, text('above-gradient'),
  ] }] }));
  const lines = [...html.matchAll(/<rect\b([^>]+)>/g)].map(match => attrs(match[1])).filter(el => el.class?.includes('previewTextLine'));
  assert.equal(lines.length, 2); assert.ok(!lines[0].class.includes('previewTextOnScrim'));
  assert.ok(lines[1].class.includes('previewTextOnScrim'));
});

for (const [templateId, kind, defaultId, selectedId] of [
  ['show', 'first', 'P01-SHOW-01', 'P01-D09'], ['emo', 'review', 'I09-D06', 'I09-D06'],
]) {
  test(`R2-3 ${templateId} ${kind} starts with its default and preserves the remaining catalog order`, () => {
    const blocks = seedStoryboard(product, analysis(templateId)), run = sectionRuns(blocks).find(run => run.kind === kind);
    const designs = sectionDesignChoices(run, { storyboard: blocks, product });
    assert.equal(defaultSectionDesignId(run, blocks), defaultId); assert.equal(designs[0].id, defaultId);
    const rest = Object.keys(catalog.designs).filter(id => id !== defaultId && designs.some(design => design.id === id));
    assert.deepEqual(designs.slice(1).map(design => design.id), rest);
    if (kind === 'review') assert.deepEqual(designs.map(design => design.id), [defaultId]);
    const changed = selectedId === defaultId ? blocks : changeTemplateSectionDesign(blocks, run.key, selectedId, { product });
    const html = renderToStaticMarkup(React.createElement(Picker, { mode: 'design', runKey: run.key, blocks: changed,
      catalogs: { detailProduct: product, analysis: analysis(templateId), genExamples: [] } }));
    const cards = [...html.matchAll(/<button\b([^>]+)>([\s\S]*?)<\/button>/g)].filter(([, attrs]) => attrs.includes('aria-pressed'));
    assert.doesNotMatch(html, /<small>기본<\/small>|designCheck/);
    if (designs.length > 1) {
      assert.equal(attrs(cards[0][1])['data-design-thumbnail'], defaultId);
      assert.equal(attrs(cards.find(([, attributes]) => attrs(attributes)['data-design-thumbnail'] === selectedId)[1])['aria-pressed'], 'true');
    } else {
      assert.equal(cards.length, 0, 'one-option stage is hidden');
      assert.doesNotMatch(html, /role="group"|완성 모습|data-design-illustration=/);
    }
    assert.equal(validateStoryboardV4(changed), changed);
  });
}

for (const templateId of ['emo', 'show']) for (const flag of ['', '&mockDetail=full', '&mockDetail=unknown']) {
  test(`R2-4 ${templateId}${flag || ' default'} changes only the explicit full mock detail scenario`, () => {
    globalThis.location = { search: `?mockTemplate=${templateId}${flag}` };
    const draft = buildDraft(), rec = draft.analysis.detailRecommendations;
    assert.deepEqual(normalizeAnalysisFit(draft.analysis).detailRecommendations, rec);
    const points = draft.storyboard.filter(b => b.source === 'ai' && b.pageSectionKind === 'points' && b.shot === 'detail');
    const fabric = draft.storyboard.filter(b => b.source === 'ai' && b.pageSectionKind === 'fabric');
    if (flag === '&mockDetail=full') {
      assert.equal(rec.status, 'ready'); assert.equal(rec.candidates.length, 4);
      assert.equal(points.length, 3); assert.equal(fabric.length, 1);
      assert.equal(draft.storyboard.filter(b => b.source === 'ai' && b.shot === 'detail').length, 4);
      assert.deepEqual(new Set(rec.candidates.map(item => item.sourceSlot)), new Set(['Front', 'Detail']));
      assert.ok(rec.candidates.every(item => item.id.startsWith('dt1_') && item.photoUse === 'standalone' && item.visibility === 'clear'));
    } else {
      assert.deepEqual(rec, { version: 1, status: 'unavailable', candidates: [] });
      assert.equal(points.length, 0); assert.equal(fabric.length, 0);
    }
    assert.equal(validateStoryboardV4(draft.storyboard), draft.storyboard);
  });
}
