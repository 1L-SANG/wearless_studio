import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog as catalog, textSlotForRole, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { decorateGenBlocks, mergeServerBlocks, patchTemplateElement } from '../../src/lib/editorWaitSkeleton.js';

const fixture = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url)));
const input = name => structuredClone(fixture.cases.find(item => item.name === name).input);
const texts = blocks => blocks.flatMap(block => block.elements).filter(el => el.type === 'text');

test('catalog resolves template and seller limits while sharing the server JSON', () => {
  for (const [kind, role, emo, show] of [['fabric', 'title', 20, 16], ['fabric', 'body', 50, 56],
    ['points', 'items.0.desc', 44, 26], ['points', 'fit.tip', 34, 34], ['faq', 'items.0.a', 56, 56],
    ['review', 'items.0.text', 34, 34], ['review', 'items.0.who', 34, 34]]) {
    for (const [templateId, expected] of [['emo', emo], ['show', show]])
      assert.equal(textSlotForRole(catalog.kinds[kind], role, { templateId }).maxChars, expected);
  }
  for (const [sellerComment, expected] of [['', 120], ['가'.repeat(180), 180], ['가'.repeat(350), 300], ['🧵'.repeat(180), 180]])
    assert.equal(textSlotForRole(catalog.kinds.intro, 'body', { sellerComment }).maxChars, expected);
  for (const [kind, role, expected] of [['fabric', 'title', 'aiDraft'], ['fabric', 'body', 'aiDraft'],
    ['faq', 'items.0.a', 'aiDraft'], ['points', 'fit.tip', 'aiDraft'], ['for_you', 'items.0', 'ai']])
    assert.equal(textSlotForRole(catalog.kinds[kind], role).source, expected);
});

test('invalid per-template and seller limits are rejected', () => {
  for (const patch of [{ maxCharsByTemplate: { show: -1 } }, { maxCharsByTemplate: { missing: 20 } },
    { maxCharsFromSeller: { min: 300, max: 120 } }]) {
    const invalid = structuredClone(catalog);
    Object.assign(invalid.kinds.intro.textSlots.find(slot => slot.copyRole === 'body'), patch);
    assert.throws(() => validateCatalog(invalid), /maxChars/);
  }
});

test('legacy mocks seed and assemble without optional labels or stored page copy', () => {
  for (const templateId of ['emo', 'show']) {
    const value = input(`${templateId}-2-mono`);
    delete value.analysis.pageCopy;
    for (const name of ['partDescriptionCopy', 'featureCopy'])
      for (const row of value.analysis[name] || []) delete row.label;
    value.storyboard = seedStoryboard(value.product, value.analysis, { projectId: 'copy-b-legacy' });
    const result = assemblePage({ ...value, copyResults: [] });
    assert.ok(result.length);
    assert.equal(texts(result).filter(el => el.copyRole?.endsWith('.stars')).length, templateId === 'emo' ? 3 : 0);
  }
});

test('seller star edits survive final completion', () => {
  const value = input('emo-2-mono');
  const final = assemblePage(value);
  const waiting = decorateGenBlocks(final, { status: 'running', copy: {}, cuts: {}, live: [] }, {});
  const block = waiting.find(b => b.pageSectionKind === 'review');
  const star = block.elements.find(el => el.copyRole === 'items.0.stars');
  const edited = waiting.map(b => b.id === block.id ? patchTemplateElement(b, star.id, { text: '★★★★☆' }) : b);
  assert.equal(texts(mergeServerBlocks(edited, final)).find(el => el.copyRole === 'items.0.stars').text, '★★★★☆');
});

for (const templateId of ['emo', 'show']) test(`${templateId} known detail and manual worn-crop labels keep distinct origins`, () => {
  const value = input(`${templateId}-2-mono`);
  Object.assign(value.analysis, { sellingPoints: ['카라'], aiSuggestedPoints: [],
    partDescriptionCopy: [{ point: '카라', label: '쉬운 이름', desc: '부위 설명' }],
    featureCopy: [{ point: '카라', label: '쉬운 특징', desc: '특징 설명' }] });
  const name = value => texts(assemblePage(value)).find(el => el.copyRole === 'items.0.name').text;
  assert.equal(name(value), '쉬운 이름', 'the candidate is known AI origin despite a same-name seller feature');
  value.analysis.partDescriptionCopy = [];
  assert.equal(name(value), '카라', 'the manual selling feature does not gain a display label');
  value.analysis.aiSuggestedPoints = ['카라'];
  assert.equal(name(value), '쉬운 특징');
  const manual = input(`woc11-${templateId}-PT-PHOTO-CARD-01-new-worn`);
  const lead = manual.storyboard.find(card => card.pointItems);
  const original = lead.pointItems[0].label;
  Object.assign(manual.analysis, { sellingPoints: [original], aiSuggestedPoints: [original],
    partDescriptionCopy: [{ point: original, label: '대체 이름', desc: '부위 설명' }],
    featureCopy: [{ point: original, label: '대체 특징', desc: '특징 설명' }] });
  assert.equal(name(manual), original, 'a manual worn-crop label cannot inherit provenance from matching words');
});
