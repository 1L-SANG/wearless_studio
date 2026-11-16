import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithEsbuild } from 'vite';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { withStoryboardSpaceSetExamples, STORYBOARD_SPACE_SETS, inferStoryboardSpaceSet } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { cutTypeDisplay } from '../../src/lib/storyboardTaxonomy.js';
import { signatureCardForBlock } from '../../src/lib/signatureCutPool.js';
import { filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { storyboardSpaceSetsFor } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { generationExampleImageSources } from '../../src/lib/generationExamples.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';

const catalogs = { ...withStoryboardSpaceSetExamples({ genExamples: raw }),
  detailProduct: { clothingType: 'outer' }, shotTypes: [{ value: 'full' }, { value: 'medium' }],
  productShotTypes: [{ value: 'ghost' }, { value: 'detail' }] };
const first = { id: 'first', taxonomyVersion: 4, source: 'ai', pageSectionKey: 'show.first',
  pageSectionKind: 'first', pageDesignId: 'P01-SHOW-01', sectionId: 'first', slotKey: 'first.hero',
  contentRole: 'hero', cutType: 'styling', shot: 'full', direction: 'front' };
const tabs = tree => nodes(tree).find(node => node.type === 'underline-tabs');
const chips = tree => nodes(tree).find(node => node.type === 'photo-segment') || (() => { const g = guide(tree); return g?.props.studioStyle ? { props: { value: g.props.studioStyle, options: g.props.studioOptions, onChange: g.props.onStudioStyleChange } } : undefined; })();
const guide = tree => nodes(tree).find(node => node.type === 'mood-guide');
const photos = tree => nodes(tree).filter(node => node.props?.['data-example-id']);

async function galleryHarness(props) {
  const h = hookHarness();
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, filterSpaceSetsForModel, storyboardSpaceSetsFor, generationExampleImageSources, Icon: 'icon',
  });
  let current = props;
  return { render(next = {}) { current = { ...current, ...next }; h.render(Gallery, current); return h.render(Gallery, current); }, close: h.close };
}

test('R3 single-photo slots show permitted large tabs at the top and hide the row for one category', async () => {
  const h = await storyboardPhotoPanelHarness({ block: first, catalogs, clothingType: 'outer', exampleGender: 'men' });
  try {
    const tree = h.render(), top = tabs(tree);
    assert.ok(top);
    assert.deepEqual(top.props.options.map(option => option.label), ['스타일링컷', '스튜디오컷']);
    assert.equal(top.props.value, 'styling');
    assert.ok(nodes(tree).indexOf(top) < nodes(tree).indexOf(guide(tree)));
  } finally { h.close(); }
  const review = { ...first, pageSectionKey: 'emo.review', pageSectionKind: 'review', pageDesignId: 'REVIEW-EMO-01', slotKey: 'review.background' };
  const one = await storyboardPhotoPanelHarness({ block: review, catalogs, clothingType: 'outer', exampleGender: 'men' });
  try { assert.equal(tabs(one.render()), undefined); assert.ok(guide(one.render())); } finally { one.close(); }
  const ordinary = await storyboardPhotoPanelHarness({ block: { ...first, pageSectionKey: 'show.wear_fit', pageSectionKind: 'wear',
    pageSectionUsage: 'fit', pageDesignId: 'WEAR-FIT-COLUMN-01', slotKey: 'wear_fit.front', cutType: 'horizon' },
    catalogs, clothingType: 'outer', exampleGender: 'men' });
  try { assert.equal(tabs(ordinary.render()), undefined); assert.ok(chips(ordinary.render())); } finally { ordinary.close(); }
});

test('R3 browsing large tabs and studio chips saves nothing in single and ordinary photo slots', async () => {
  const ordinary = { ...first, id: 'ordinary', pageSectionKey: 'emo.wear_styling', pageSectionKind: 'wear',
    pageSectionUsage: 'styling', pageDesignId: 'P02-D01', sectionId: 'wear', slotKey: 'wear_styling.g1', contentRole: 'coordination' };
  for (const block of [first, ordinary]) {
    const before = structuredClone(block), h = await storyboardPhotoPanelHarness({ block, catalogs, clothingType: 'outer', exampleGender: 'men' });
    try {
      tabs(h.render()).props.onChange('horizon');
      let tree = h.render(); assert.equal(tabs(tree).props.value, 'horizon');
      chips(tree).props.onChange('minimal'); tree = h.render();
      assert.equal(chips(tree).props.value, 'minimal');
      assert.equal(h.changes.length, 0); assert.deepEqual(h.block, before);
      assert.equal(guide(tree).props.studioStyle, 'minimal');
      const minimal = raw.find(example => example.studioStyle === 'minimal' && example.gender === 'men');
      await guide(tree).props.onExampleChange(minimal.id);
      assert.equal(h.changes.length, 1); assert.equal(h.block.exampleId, minimal.id); assert.equal(h.block.cutType, 'styling');
      assert.equal(cutTypeDisplay(h.block.cutType, h.block.exampleId).studioStyle, 'minimal');
    } finally { h.close(); }
  }
});

test('R5 styling keeps places without studio or light and studio has no place filter', async () => {
  const sig = signatureCardForBlock(first, { product: { clothingType: 'outer' }, gender: 'men' });
  const h = await galleryHarness({ block: first, catalogs, cut: 'styling', shot: 'full', clothingType: 'outer', gender: 'men', signature: sig });
  try {
    let tree = h.render(); const filters = nodes(tree).find(node => node.type === 'photo-filters');
    assert.ok(filters); assert.ok(!filters.props.tabs.some(tab => tab.value === 'studio'));
    assert.ok(!photos(tree).some(node => node.props['data-example-id'].startsWith('sig_')));
    assert.equal(filters.props.onLight, undefined);
    tree = h.render({ cut: 'styling', studioStyle: 'minimal' });
    assert.equal(nodes(tree).find(node => node.type === 'photo-filters'), undefined);
    assert.equal(nodes(tree).find(node => node.type === 'photo-filters'), undefined);
    assert.equal(photos(tree)[0].props['data-example-id'], sig.id);
    assert.ok(photos(tree).length > 1, 'studio retains all published examples without a light filter');
    tree = h.render({ cut: 'horizon', studioStyle: 'horizon' });
    assert.equal(nodes(tree).find(node => node.type === 'photo-filters'), undefined);
    assert.ok(photos(tree).length); assert.ok(photos(tree).every(node => !node.props['data-example-id'].startsWith('sig_')));
  } finally { h.close(); }
});

test('R3 signature without any minimal examples enables minimal and is the only card', async () => {
  const h = await storyboardPhotoPanelHarness({ block: first, catalogs: { ...catalogs, genExamples: [] }, clothingType: 'outer', exampleGender: 'men' });
  try {
    tabs(h.render()).props.onChange('horizon'); let tree = h.render();
    const option = chips(tree)?.props.options.find(option => option.value === 'minimal');
    assert.ok(option); assert.equal(option.disabled, false);
    assert.equal(chips(tree).props.options.find(option => option.value === 'horizon').disabled, true,
      'signature examples enable minimal only, never an empty horizon gallery');
    assert.equal(chips(tree).props.value, 'minimal');
    chips(tree).props.onChange('minimal'); tree = h.render();
    assert.equal(h.changes.length, 0);
    const props = guide(tree).props, g = await galleryHarness(props);
    try {
      const rendered = g.render(); assert.equal(photos(rendered).length, 1);
      assert.equal(photos(rendered)[0].props['data-example-id'], props.signature.id);
      assert.ok(!textOf(rendered).includes('이 조건의 장소 사진을 준비 중이에요.'));
      await photos(rendered)[0].props.onClick();
    } finally { g.close(); }
    assert.equal(h.block.cutType, 'horizon'); assert.ok(h.block.exampleId.startsWith('sig_'));
    assert.equal(h.block.refScope, 'all');
    assert.equal(h.block.spaceGroupId, null); assert.equal(Object.hasOwn(h.block, 'studioStyle'), false);
    tree = h.render(); assert.equal(tabs(tree).props.value, 'horizon'); assert.equal(chips(tree).props.value, 'minimal');
    const reopened = await galleryHarness(guide(tree).props);
    try {
      const card = photos(reopened.render())[0];
      assert.equal(card.props['data-example-id'], h.block.exampleId); assert.equal(card.props['aria-pressed'], true);
    } finally { reopened.close(); }
  } finally { h.close(); }
});

test('R3 display classifies a stored horizon signature as studio minimal without changing regular horizon', () => {
  assert.equal(cutTypeDisplay('horizon', 'sig_men_01').studioStyle, 'minimal');
  assert.equal(cutTypeDisplay('horizon', 'sig_men_01').studioLabel, '미니멀컷');
  assert.equal(cutTypeDisplay('horizon', null).studioStyle, 'horizon');
  assert.equal(cutTypeDisplay('horizon', 'ex_horizon_men_top_full_front_02').studioStyle, 'horizon');
});

test('R3 editor retry text uses the minimal signature label and retains its horizon request', async () => {
  const source = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
  const start = source.indexOf('  if (pendingGenerateRequest) {');
  const end = source.indexOf('\n  return (\n    <div>', start);
  const { code } = await transformWithEsbuild(`function Retry(){${source.slice(start, end)}}`, 'Retry.jsx', { jsx: 'transform' });
  const request = { cutType: 'horizon', exampleId: 'sig_men_01', shot: 'medium' };
  const bindings = { React, pendingGenerateRequest: request, colorOpts: [], detailColorOpts: [], catalogs, cutTypeDisplay,
    PRODUCT_PRESENTATION_OPTIONS: [], generateSubmitting: false, onConfirmPendingGenerate() {}, PanelHead: 'head', Button: 'button' };
  const Retry = new Function(...Object.keys(bindings), `${code};return Retry;`)(...Object.values(bindings));
  assert.ok(textOf(Retry()).includes('스튜디오컷 · 미니멀컷'));
  assert.equal(request.cutType, 'horizon'); assert.equal(request.exampleId, 'sig_men_01');
});

test('R3 set photos keep both chip rows hidden and studio set picker omits all light filtering', async () => {
  const set = STORYBOARD_SPACE_SETS.find(set => set.setType === 'horizon-sequence' && set.gender === 'men' && set.applicableClothingTypes.includes('top'));
  const members = set.members.map(member => ({ ...first, id: member.exampleId, exampleId: member.exampleId,
    pageSectionKey: 'show.wear_styling', pageSectionKind: 'wear', pageSectionUsage: 'fit', pageDesignId: 'WEAR-FIT-COLUMN-01',
    slotKey: 'wear_styling.g1', cutType: 'horizon', spaceGroupId: `ssg1__${set.id}__r3` }));
  const panel = await storyboardPhotoPanelHarness({ block: members[0], blocks: members, spaceContext: { set, siblings: members }, catalogs, exampleGender: 'men' });
  try { const tree = panel.render(); assert.equal(tabs(tree), undefined); assert.equal(chips(tree), undefined); } finally { panel.close(); }
  const h = hookHarness();
  const Picker = await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx', 'StoryboardSpaceSetPanel', {
    ...h.hooks, filterSpaceSetsForModel, storyboardSpaceSetsFor, inferStoryboardSpaceSet, Icon: 'icon',
  });
  const props = { mode: 'replace', cutType: 'horizon', currentMembers: members, gender: 'men', clothingType: 'top', usage: 'fit' };
  try {
    let tree = h.render(Picker, props);
    assert.equal(nodes(tree).find(node => node.type === 'photo-filters'), undefined);
    chips(tree).props.onChange('minimal'); tree = h.render(Picker, props);
    assert.equal(nodes(tree).find(node => node.type === 'photo-filters'), undefined);
    assert.ok(nodes(tree).some(node => node.props?.['data-space-set']));
  } finally { h.close(); }
});
