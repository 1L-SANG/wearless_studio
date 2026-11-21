import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { assemblePage, collectOverlayRegions } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog as catalog, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { baseColorOf, resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';

const fixture = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url)));
const ids = { review: ['I09-D06', 'I09-SCRIM-01'], faq: ['T08-D07', 'T08-SCRIM-01'] };
function inputFor(kind, designId = ids[kind][0]) {
  const input = structuredClone(fixture.cases.find(item => item.name === 'emo-2-point').input);
  input.storyboard = input.storyboard.filter(card => card.pageSectionKind === kind);
  input.storyboard.forEach(card => { card.pageDesignId = designId; });
  const source = input.storyboard.find(card => card.source !== 'text');
  const key = `emo.${kind}:${kind}.background:heading`;
  input.cutResults.find(cut => cut.blockId === source.id).overlayTextColors = { [key]: '#111111' };
  return input;
}
const assembled = input => assemblePage(input).filter(block => block.pageSectionKey).flatMap(block => block.elements);

for (const kind of ['review', 'faq']) {
  test(`D216 ${kind} default keeps one 4:5 photo under a full scrim and small alternating white bubbles`, () => {
    const input = inputFor(kind), elements = assembled(input);
    const [photo] = elements.filter(element => element.type === 'image');
    assert.equal(elements.filter(element => element.type === 'image').length, 1);
    assert.equal(photo.w, 1000); assert.equal(photo.h, 1250);
    const scrim = elements.find(element => element.type === 'shape' && element.fill === '#000000');
    assert.ok(scrim, 'the entire photo is darkened independently of the photo contents');
    assert.deepEqual([scrim.x, scrim.y, scrim.w, scrim.h, scrim.opacity], [0, photo.y, 1000, 1250, .3]);
    const bubbles = elements.filter(element => element.type === 'shape' && element.opacity === .96);
    assert.equal(bubbles.length, 3);
    for (const [index, bubble] of bubbles.entries()) {
      assert.equal(bubble.fill, '#ffffff');
      assert.ok(bubble.w <= 731);
      assert.equal(index % 2 ? bubble.x + bubble.w : bubble.x, index % 2 ? 930 : 70);
      const heading = elements.find(el => el.copyRole === (kind === 'review' ? 'lead' : 'title'));
      assert.ok(bubble.y > photo.y && bubble.y >= heading.y + heading.h && bubble.y + bubble.h <= photo.y + 1162.5);
      if (index) assert.ok(bubble.y > bubbles[index - 1].y + bubbles[index - 1].h);
      if (kind === 'review') {
        const star = elements.find(el => el.copyRole === `items.${index}.stars`);
        const copy = elements.find(el => el.copyRole === `items.${index}.text`);
        assert.ok(star.y >= bubble.y && star.y + star.h < copy.y);
      }
      assert.equal(bubble.boxShadow, '0px 3px 12px rgba(0, 0, 0, 0.18)');
    }
    assert.equal(bubbles.at(-1).y + bubbles.at(-1).h, photo.y + 1162.5);
    assert.equal(elements.find(element => element.copyRole === 'label').style.color, catalog.templates[0].colors.ink);
    const text = elements.find(element => element.copyRole === (kind === 'review' ? 'items.0.text' : 'items.0.q'));
    assert.equal(text.style.color, catalog.templates[0].colors[kind === 'faq' ? 'muted' : 'ink']);
    assert.equal(collectOverlayRegions(input).length, kind === 'faq' ? 1 : 0, 'FAQ measures faces while other fixed headings skip measurement');
    if (kind === 'faq') assert.equal(collectOverlayRegions(input)[0].avoidFaces, true);
  });

  test(`D216 ${kind} gradient alternative preserves the same photo and fixed white copy`, () => {
    const input = inputFor(kind, ids[kind][1]);
    assert.ok(catalog.designs[ids[kind][1]], 'the candidate remains available alongside the bubble design');
    const elements = assembled(input);
    assert.equal(elements.filter(element => element.type === 'image').length, 1);
    const scrim = elements.find(element => element.type === 'shape');
    assert.deepEqual([scrim.x, scrim.y, scrim.w, scrim.h], [0, elements.find(el => el.type === 'image').y + (kind === 'review' ? 312.5 : 525), 1000, kind === 'review' ? 937.5 : 725]);
    if (kind === 'review') assert.match(scrim.fill, /^linear-gradient\(180deg, rgba\(0, 0, 0, 0\) 0%, rgba\(0, 0, 0, 0\.56\) [\d.]+%, rgba\(0, 0, 0, 0\.72\) 100%\)$/);
    else assert.equal(scrim.fill, 'linear-gradient(180deg, rgba(0, 0, 0, 0) 0%, rgba(0, 0, 0, 0.72) 100%)');
    assert.equal(elements.filter(element => element.type === 'shape').length, 1);
    assert.ok(elements.filter(element => element.type === 'text' && element.copyRole !== 'label'
      && !element.copyRole?.endsWith('.stars')).every(element => element.style.color === '#ffffff'));
    const stars = elements.filter(element => element.copyRole?.endsWith('.stars'));
    assert.equal(stars.length, kind === 'review' ? 3 : 0);
    assert.ok(stars.every(star => star.text === '★★★★★' && star.style.color === '#ffffff'));
    assert.deepEqual(collectOverlayRegions(input), []);
  });
}

test('generic scrim/fixed-color geometry follows catalog data even with an unrelated design id', () => {
  const design = structuredClone(catalog.designs['I09-D06']);
  design.id = 'GENERIC-OVERLAY';
  design.layout.find(node => node.type === 'overlay').aspect = '4/5';
  design.layout.find(node => node.type === 'overlay').scrim = { color: '#123456', opacity: .4, heightRatio: .5, gradient: true };
  design.layout.find(node => node.type === 'overlay').groups.forEach(group => { group.textColor = '#ffffff'; });
  catalog.designs[design.id] = design;
  try {
    const input = inputFor('review', design.id), elements = assembled(input);
    const scrim = elements.find(element => element.fill?.startsWith('linear-gradient('));
    assert.ok(scrim);
    assert.deepEqual([scrim.y, scrim.h], [elements.find(el => el.type === 'image').y + 625, 625]);
    assert.equal(scrim.fill, 'linear-gradient(180deg, rgba(18, 52, 86, 0) 0%, rgba(18, 52, 86, 0.4) 100%)');
    assert.deepEqual(collectOverlayRegions(input), []);
  } finally { delete catalog.designs[design.id]; }
});

for (const [property, value, error] of [
  ['scrim', { color: 'black', opacity: .3 }, 'overlay scrim'],
  ['scrim', { color: '#000000', opacity: 1.1 }, 'overlay scrim'],
  ['scrim', { color: '#000000', opacity: .3, heightRatio: 0 }, 'overlay scrim'],
  ['scrim', { color: '#000000', opacity: .3, gradient: 'yes' }, 'overlay scrim'],
  ['fillOpacity', -.1, 'fill opacity'],
  ['itemGap', -1, 'list item gap'],
  ['shadow', { x: 0, y: 3, blur: -1, color: '#000000', opacity: .18 }, 'shape shadow'],
]) test(`both catalog consumers reject malformed ${property} ${JSON.stringify(value)}`, () => {
  const changed = structuredClone(catalog);
  const node = changed.designs['I09-D06'].layout.find(node => node.type === 'overlay');
  const target = property === 'scrim' ? node : node.groups[0].nodes.find(child => child.type === 'list');
  target[property] = value;
  assert.throws(() => validateCatalog(changed), new RegExp(error));
});

test('catalog rejects malformed fixed group text color', () => {
  const changed = structuredClone(catalog);
  changed.designs['I09-D06'].layout.find(node => node.type === 'overlay').groups[0].textColor = 'white';
  assert.throws(() => validateCatalog(changed), /overlay text color/);
});

test('storyboard renders catalog gradient as a gray SVG gradient in wireframe and preserves opacity', async t => {
  const vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null, hmr: false }, appType: 'custom' });
  t.after(() => vite.close());
  const { StoryboardPreview } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx');
  const block = { id: 'generic', h: 1250, elements: [
    { id: 'gradient', type: 'shape', shape: 'rect', x: 0, y: 525, w: 1000, h: 725,
      fill: 'linear-gradient(180deg, rgba(0, 0, 0, 0) 0%, rgba(0, 0, 0, 0.72) 100%)' },
    { id: 'bubble', type: 'shape', shape: 'rect', x: 70, y: 1000, w: 300, h: 60, fill: '#ffffff', opacity: .96,
      boxShadow: '0px 3px 12px rgba(0, 0, 0, 0.18)' },
  ] };
  const markup = renderToStaticMarkup(React.createElement(StoryboardPreview, { blocks: [block], wireframe: true }));
  assert.match(markup, /<linearGradient/);
  assert.match(markup, /stop-color="var\(--sb-wire-scrim-end\)" stop-opacity="0.72"/);
  assert.match(markup, /opacity="0.96"/);
  assert.doesNotMatch(markup, /rgba\(0, 0, 0/);
});
