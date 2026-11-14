import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage, collectOverlayRegions } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { getBlockExportElements, getBlockRenderHeight } from '../../src/features/editor/editorGeometry.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';
import { inferStoryboardSpaceSet } from '../../src/lib/storyboardSpaceSetCatalog.js';
const fixture = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url)));
const input = name => structuredClone(fixture.cases.find(item => item.name === `woc20-${name}`).input);
const blocks = value => assemblePage(value).filter(block => block.pageSectionKey);
const all = value => blocks(value).flatMap(block => block.elements);

for (const tid of ['emo', 'show']) {
  test(`WOC20 ${tid} FAQ combines A heading, B aligned Q/A and C separation`, () => {
    const pending = input(`${tid}-faq-pending`), inside = blocks(pending)[0];
    const label = inside.elements.find(el => el.copyRole === 'label');
    const title = inside.elements.find(el => el.copyRole === 'title');
    assert.deepEqual([label.text, label.y, label.w, label.style.size, label.h], ['Q&A', 68, 860, 32, 39]);
    assert.deepEqual([title.y, title.style.size, title.style.weight, title.h, title.style.align], [117, 44, 600, 57, 'center']);
    for (let index = 0; index < 3; index++) {
      const q = inside.elements.find(el => el.copyRole === `items.${index}.q`);
      const a = inside.elements.find(el => el.copyRole === `items.${index}.a`);
      const bubble = inside.elements.find(el => el.type === 'shape' && el.groupId === q.groupId);
      const markers = inside.elements.filter(el => el.groupId === q.groupId && el.templateCopyDecoration);
      assert.deepEqual(markers.map(el => [el.text, el.w, el.x]), [['Q.', 34, q.x - 34], ['A.', 34, a.x - 34]]);
      assert.equal(q.x, a.x); assert.equal(q.w, a.w); assert.equal(q.w, bubble.w - 40 - 34);
      assert.ok(q.w <= 657);
      assert.deepEqual([q.style.size, q.style.weight, q.style.color, q.style.lineHeight], [22, 400, catalog.templates.find(t => t.id === tid).colors.muted, 36]);
      assert.deepEqual([a.style.size, a.style.weight, a.style.color, a.style.lineHeight], [26, 600, catalog.templates.find(t => t.id === tid).colors.ink, 42]);
    }
    const plan = collectOverlayRegions(pending).find(plan => plan.avoidFaces);
    assert.deepEqual(plan.region, { left: .07, top: 68 / 1250, right: .93, bottom: 174 / 1250 });
    const outside = blocks(input(`${tid}-faq-outside`))[0];
    assert.equal(outside.h - inside.h, 246);
    assert.deepEqual(outside.elements.filter(el => ['label', 'title'].includes(el.copyRole)).map(el => [el.y, el.style.size, el.h]), [[120, 32, 39], [171, 37, 49]]);
    assert.equal(outside.elements.find(el => el.type === 'image').y, 246);
    assert.ok(!outside.elements.some(el => el.type === 'shape' && el.opacity === .3));
    assert.equal(getBlockRenderHeight(outside, { forExport: true }), outside.h);
  });

  for (const count of [1, 3]) test(`WOC20 ${tid} ${count} colors uses the section title and actual swatches`, () => {
    const value = input(`${tid}-colors-${count}`), elements = all(value);
    const title = elements.find(el => el.copyRole === 'title');
    assert.equal(title.text, tid === 'emo' ? `${count}가지 색상` : `${count} COLOR${count === 1 ? '' : 'S'}`);
    assert.equal(title.style.size, 37);
    assert.equal(title.style.weight, tid === 'emo' ? 600 : 400);
    assert.equal(title.style.font, tid === 'emo' ? 'Pretendard' : 'Cormorant');
    assert.equal(title.style.color, catalog.templates.find(t => t.id === tid).colors.ink);
    const row = elements.filter(el => el.type === 'shape' && el.shape === 'circle' && el.w === 37);
    assert.equal(row.length, tid === 'emo' ? count : 0);
    if (tid === 'emo') {
      assert.deepEqual(row.map(el => el.fill), value.product.colors.map(color => catalog.swatchHex[color.swatchId]));
      assert.equal(row[0].x + row.at(-1).x + 37, 1000);
      assert.equal(row[0].y - title.y - title.h, 14);
      assert.equal(elements.find(el => el.type === 'image').y - row[0].y - 37, 30);
      assert.deepEqual(elements.filter(el => el.type === 'text' && el.y < elements.find(el => el.type === 'image').y).map(el => el.text), ['Color', `${count}가지 색상`]);
    } else {
      assert.equal(title.style.numeric, 'lining');
      assert.equal(elements.some(el => el.copyRole === 'label'), false);
    }
  });

  test(`WOC20 ${tid} only published rotation sets keep direction captions`, () => {
    const value = input(`${tid}-wear-sets`);
    const captions = all(value).filter(el => el.copyRole?.startsWith('cap.'));
    assert.ok(captions.some(el => el.text));
    // Each photo's section is emitted in original order; captions follow those photos.
    for (const block of blocks(value)) {
      const photo = block.elements.find(el => el.type === 'image');
      const card = value.storyboard.find(card => card.id === photo?.sourceBlockId);
      for (const caption of block.elements.filter(el => el.copyRole?.startsWith('cap.')))
        assert.equal(Boolean(caption.text), inferStoryboardSpaceSet(card.spaceGroupId)?.setType === 'horizon-rotation');
    }
    assert.ok(all(input(`${tid}-wear-loose`)).filter(el => el.copyRole?.startsWith('cap.')).every(el => el.text === ''));
  });

  for (const lines of [0, 1, 2, 3]) test(`WOC20 ${tid} part-only descriptions accept ${lines} lines appropriately`, () => {
    const value = input(`${tid}-part-lines-${lines}`);
    const desc = all(value).filter(el => /^items\.\d+\.desc$/.test(el.copyRole));
    assert.ok(desc.length);
    assert.ok(desc.every(el => Boolean(el.text) === (lines === 1 || lines === 2)));
  });

  test(`WOC20 ${tid} blank review bubbles disappear only from output`, () => {
    const block = blocks(input(`${tid}-review-empty`))[0];
    assert.equal(block.elements.find(el => el.copyRole === 'lead').text, '먼저 입어 본 분들의 후기');
    assert.equal(block.elements.filter(el => el.opacity === .96).length, 3);
    assert.equal(getBlockExportElements(block).filter(el => el.opacity === .96).length, 0);
    const filled = blocks(input(`${tid}-review-filled`))[0];
    assert.equal(getBlockExportElements(filled).filter(el => el.opacity === .96).length, 1);
    const group = block.elements.find(el => el.opacity === .96).groupId;
    const manual = { id: 'seller-shape', type: 'shape', x: 0, y: 0, w: 10, h: 10 };
    block.elements.push(manual);
    assert.ok(getBlockExportElements(block).includes(manual));
    block.elements.push({ id: 'seller-copy', type: 'text', groupId: group, text: '직접 쓴 글', y: 0, h: 40 });
    assert.equal(getBlockExportElements(block).filter(el => el.opacity === .96).length, 1);
  });
}

test('WOC20 empty FAQ markers and checklist markers follow the complete copy group', () => {
  const value = input('emo-faq-pending'); value.copyResults = [];
  const block = blocks(value)[0];
  assert.equal(block.elements.filter(el => el.templateCopyDecoration).length, 6);
  assert.equal(getBlockExportElements(block).filter(el => el.templateCopyDecoration).length, 0);
  const q = block.elements.find(el => el.copyRole === 'items.0.q'); q.text = '직접 쓴 질문';
  assert.equal(getBlockExportElements(block).filter(el => el.opacity === .96).length, 1);
  const checklist = structuredClone(fixture.cases.find(item => item.name === 'emo-2-mono').input);
  checklist.copyResults = [];
  const list = blocks(checklist).find(block => block.pageSectionKind === 'for_you');
  assert.ok(list.elements.some(el => el.templateCopyDecoration === '✓'));
  assert.equal(getBlockExportElements(list).filter(el => el.templateCopyDecoration === '✓').length, 0);
});

test('WOC20 completion replaces the pending FAQ with the taller measured block and preserves download height', () => {
  const value = input('emo-faq-pending'); value.cutResults = [];
  const assembled = assemblePage(value);
  const source = value.storyboard.find(card => card.source !== 'text');
  const emptyJob = { cuts: {}, copy: {}, status: 'running' };
  const skeleton = decorateGenBlocks(assembled, emptyJob, {});
  const live = fillGenBlocks(skeleton, { ...emptyJob, cuts: { [source.id]: { url: 'photo:live', done: true } } });
  const finalInput = structuredClone(value);
  finalInput.cutResults = [{ blockId: source.id, imageUrl: 'photo:final', width: 1000, height: 1250,
    overlayTextPositions: { [`${source.pageSectionKey}:${source.slotKey}:heading`]: 'outside' } }];
  const final = assemblePage(finalInput);
  const merged = mergeServerBlocks(live, final);
  assert.deepEqual(merged, final);
  const faq = merged.find(block => block.pageSectionKind === 'faq');
  assert.equal(faq.h - assembled.find(block => block.pageSectionKind === 'faq').h, 246);
  assert.equal(getBlockRenderHeight(faq, { forExport: true }), faq.h);
});

test('WOC20 seller-written decoration text and user groups remain content', () => {
  const value = input('emo-faq-pending'); value.copyResults = [];
  const block = blocks(value)[0];
  const marker = block.elements.find(el => el.templateCopyDecoration === 'Q.');
  marker.text = '직접 쓴 글';
  assert.ok(getBlockExportElements(block).includes(marker));
  assert.equal(getBlockExportElements(block).filter(el => el.opacity === .96).length, 1);
  const manual = { id: 'custom', type: 'shape', groupId: 'seller-group', x: 0, y: 1300, w: 100, h: 40 };
  block.elements.push(manual, { id: 'custom-copy', type: 'text', groupId: 'seller-group', text: '', x: 0, y: 1300, w: 100, h: 40 });
  assert.ok(getBlockExportElements(block).includes(manual));
});

test('WOC20 editing FAQ copy while waiting still applies measured face separation', () => {
  const value = input('emo-faq-pending'); value.cutResults = [];
  const local = decorateGenBlocks(assemblePage(value), { cuts: {}, copy: {}, status: 'running' }, {});
  const faq = local.find(block => block.pageSectionKind === 'faq');
  faq.elements.find(el => el.copyRole === 'items.0.q').text = '셀러가 고친 질문이에요';
  const source = value.storyboard.find(card => card.source !== 'text');
  const finalInput = structuredClone(value);
  finalInput.cutResults = [{ blockId: source.id, imageUrl: 'photo:final', width: 1000, height: 1250,
    overlayTextPositions: { [`${source.pageSectionKey}:${source.slotKey}:heading`]: 'outside' } }];
  const final = assemblePage(finalInput);
  const merged = mergeServerBlocks(local, final).find(block => block.pageSectionKind === 'faq');
  assert.equal(merged.elements.find(el => el.copyRole === 'items.0.q').text, '셀러가 고친 질문이에요');
  assert.equal(merged.elements.find(el => el.copyRole === 'label').y, 120);
  assert.equal(merged.elements.find(el => el.copyRole === 'title').style.size, 37);
  assert.equal(merged.elements.find(el => el.type === 'image').y, 246);
  assert.equal(merged.elements.some(el => el.opacity === .3), false);
  assert.equal(merged.h, 1616);
  assert.equal(getBlockRenderHeight(merged, { forExport: true }), 1616);
  faq.elements.find(el => el.copyRole === 'title').y += 100;
  const manual = mergeServerBlocks(local, final).find(block => block.pageSectionKind === 'faq');
  assert.equal(manual.elements.find(el => el.type === 'image').y, 0, 'seller-placed heading keeps the manual layout');
});
