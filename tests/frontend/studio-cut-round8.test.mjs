import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import policy from '../../server/app/data/horizon_background_policy.json' with { type: 'json' };
import { STORYBOARD_SPACE_SETS, storyboardSpaceSetsFor, withStoryboardSpaceSetExamples, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';
import * as generation from '../../src/lib/generationExamples.js';
import * as identity from '../../src/lib/identityScope.js';
import { spaceSetDisplayName } from '../../src/lib/spaceSetDisplayNames.js';
import { isSourceBasedDetailRecipe } from '../../src/lib/detailRecommendations.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardStage } from './helpers/storyboardStageHarness.mjs';
import { ensureSections } from '../../src/lib/sections.js';
import { getTemplate } from '../../src/lib/pageTemplates/catalog.js';

const set = STORYBOARD_SPACE_SETS.find(set => set.id === 'horizon-sequence-figma-s14-v2');
const product = { clothingType: 'outer', colors: [{ id: 'blue', name: '스카이블루', swatchId: 'blue', isBase: true,
  images: [{ slot: 'Front', src: '/front.webp' }] }] };
const colors = [{ id: 'blue', label: '스카이블루', hex: '#98BDE7' }];
const members = createSpaceSetMembers(set, { source: 'ai', taxonomyVersion: 4, sectionRole: 'studio', contentRole: 'fit',
  sectionId: 'wear', pageSectionKey: 'show.wear_styling', pageSectionKind: 'wear', pageSectionUsage: 'fit',
  pageDesignId: 'WEAR-FIT-COLUMN-01', colorId: 'blue', outerClosureState: 'open' }, {
  spaceGroupId: spaceSetGroupId(set.id, 'round8'), members: set.members.slice(0, 2),
}).map(block => ({ ...block, horizonBackgroundMode: 'garment-tone' }));
const evidence = backgroundStatus => ({ version: 1, clothingType: 'outer', colors: [{ colorId: 'blue', status: 'ready',
  backgroundStatus, backgroundPolicyVersion: policy.version }] });
const catalog = withStoryboardSpaceSetExamples({ genExamples: raw });
const byClass = (tree, name) => nodes(tree).find(node => node.props?.className === name);
const button = (tree, label) => nodes(tree).find(node => node.type === 'button' && textOf(node) === label);

for (const kind of ['review', 'faq']) test(`R8-1 legacy ${kind} minimal photo survives frontend entry normalization`, () => {
  const section = getTemplate('emo').sections.find(section => section.kind === kind);
  const meta = { taxonomyVersion: 4, sectionId: 'legacy', pageSectionKey: `emo.${kind}`, pageSectionKind: kind, pageDesignId: section.designId };
  const photo = { ...meta, id: 'legacy-photo', source: 'ai', slotKey: `${kind}.background`, contentRole: 'coordination', sectionRole: 'styling',
    cutType: 'styling', shot: 'full', direction: 'front', exampleId: 'ss_set_02_men_top_indoor_concrete_byslim_28635_01',
    spaceGroupId: spaceSetGroupId('set-02-men-top-indoor-concrete-byslim-28635', 'legacy'), spaceVariation: 'subtle', spaceSetMemberOrder: 1 };
  const blocks = [{ ...meta, id: 'text', source: 'text', cutType: null, contentRole: null, sectionRole: null, slotKey: null }, photo];
  const normalized = ensureSections(blocks).find(block => block.id === photo.id);
  assert.equal(normalized.exampleId, 'ss_set_02_men_top_indoor_concrete_byslim_28635_01');
  assert.equal(normalized.cutType, 'styling'); assert.equal(normalized.spaceGroupId, photo.spaceGroupId);
});

async function setPanel() {
  const h = hookHarness(), changes = [];
  const Panel = await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx', 'StoryboardSpaceSetPanel', {
    ...h.hooks, ...identity, storyboardSpaceSetsFor, Icon: 'icon',
  });
  let props = { mode: 'replace', currentMembers: members, cutType: 'horizon', gender: 'men', clothingType: 'outer', product,
    colorOpts: colors, onChoose() {}, onChangeSpaceBackground: mode => changes.push(mode) };
  return { changes, render(next = {}) { props = { ...props, ...next }; h.render(Panel, props); return h.render(Panel, props); }, close: h.close };
}

test('R8-2 unavailable background turns the chip, explanation and card mark off without changing saved intent; pending restores them', async t => {
  const h = await setPanel(); t.after(h.close); const before = structuredClone(members);
  let tree = h.render(), chip = button(tree, '배경색 맞추기'); assert.equal(chip.props['aria-pressed'], true);
  tree = h.render({ garmentColorEvidence: evidence('unavailable') }); chip = button(tree, '배경색 맞추기');
  assert.equal(chip.props['aria-disabled'], true);
  assert.equal(chip.props['aria-pressed'], false);
  assert.equal(textOf(byClass(tree, 'optionExplanation').props.children[0]), '');
  assert.ok(!byClass(tree, 'cardTone'));
  const reason = nodes(tree).find(node => node.type === 'choice-reason' && node.props.reason);
  assert.ok(reason); assert.equal(reason.props.id, chip.props['aria-describedby']);
  await chip.props.onClick(); assert.deepEqual(h.changes, []); assert.deepEqual(members, before);
  tree = h.render({ garmentColorEvidence: null }); assert.equal(button(tree, '배경색 맞추기').props['aria-pressed'], true);
  assert.ok(byClass(tree, 'cardTone'));
  tree = h.render({ garmentColorEvidence: evidence('ready') }); assert.equal(button(tree, '배경색 맞추기').props['aria-pressed'], true);
  assert.deepEqual(members, before);
});

test('R8-2 unavailable evidence suppresses stage head tone marks while pending and ready preserve them', async () => {
  const before = structuredClone(members);
  for (const [garmentColorEvidence, want] of [[evidence('unavailable'), false], [null, true], [evidence('ready'), true]]) {
    const tree = await storyboardStage(members, { catalogs: { detailProduct: product, garmentColorEvidence }, colorOpts: colors });
    const marks = nodes(tree).find(node => node.type === 'set-head-marks'); assert.ok(marks);
    assert.equal(marks.props.state.backgroundOn, want);
  }
  assert.deepEqual(members, before);
});

test('R8-2 unavailable singleton hides its caption tone while preserving the stored choice for pending recovery', async () => {
  const single = [members[0]], before = structuredClone(single);
  for (const [garmentColorEvidence, want] of [[evidence('unavailable'), false], [null, true], [evidence('ready'), true]]) {
    const tree = await storyboardStage(single, { catalogs: { detailProduct: product, garmentColorEvidence }, colorOpts: colors });
    assert.equal(nodes(tree).some(node => node.type === 'set-caption-tone'), want);
  }
  assert.deepEqual(single, before);
});

async function editorGallery() {
  const h = hookHarness();
  const Guide = await loadStoryboardComponent('StoryboardExampleGallery.jsx', 'MoodGuide', {
    ...h.hooks, ...generation, ...identity, storyboardSpaceSetsFor, spaceSetDisplayName, isSourceBasedDetailRecipe,
    Icon: 'icon', MineImageTab: 'mine-images', ShotSegment: 'shot-segment', gallerySeedFactory: () => 'r8',
  });
  let props = { catalogs: catalog, cut: 'styling', shot: 'full', clothingType: 'top', gender: 'men',
    identityKind: 'virtual', onExampleChange() {}, gallerySeedFactory: () => 'r8' };
  return { render(next = {}) { props = { ...props, ...next }; h.render(Guide, props); return h.render(Guide, props); }, close: h.close };
}

for (const [name, cut, exampleId] of [
  ['minimal', 'styling', 'ss_set_02_men_top_indoor_concrete_byslim_28635_01'],
  ['horizon', 'horizon', 'ss_horizon_zara_s02_01_v2'],
]) test(`R8-3 editor cafe to ${name} uses studio photos for both framing buttons`, async t => {
  const h = await editorGallery(); t.after(h.close);
  let tree = h.render();
  const places = nodes(tree).find(node => typeof node.type === 'function' && node.type.name === 'PlaceTabs'); assert.ok(places);
  places.props.onChange('cafe'); tree = h.render();
  tree = h.render({ cut, exampleId });
  const frame = nodes(tree).find(node => node.props?.['aria-label'] === '구도'); assert.ok(frame);
  assert.equal(button(frame, '전신').props.disabled, false);
  assert.equal(button(frame, '상반신').props.disabled, false);
  const list = nodes(tree).find(node => node.props?.role === 'region' && node.props['aria-label'] === '생성예시 갤러리');
  assert.ok(nodes(list).some(node => node.type === 'button' && node.props.className?.includes('sb-excell')));
});

test('R8-3 storyboard cafe to minimal has usable full and medium framing without a hidden place filter', async t => {
  const h = hookHarness(); t.after(h.close);
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, ...generation, ...identity, storyboardSpaceSetsFor, Icon: 'icon',
  });
  const props = { block: { id: 'storyboard-control', pageSectionKind: 'first' }, catalogs: catalog,
    cut: 'styling', clothingType: 'top', gender: 'men', identityKind: 'virtual' };
  const render = extra => { const next = { ...props, ...extra }; h.render(Gallery, next); return h.render(Gallery, next); };
  let tree = render(); nodes(tree).find(node => node.type === 'photo-filters').props.onPlace('cafe'); tree = render();
  tree = render({ studioStyle: 'minimal', exampleId: 'ss_set_02_men_top_indoor_concrete_byslim_28635_01' });
  const framing = nodes(tree).find(node => node.type === 'photo-framing'); assert.ok(framing);
  assert.equal(framing.props.options.find(option => option.value === 'full').disabled, false);
  assert.equal(framing.props.options.find(option => option.value === 'medium').disabled, false);
  assert.ok(nodes(tree).some(node => node.props?.['data-photo-choice']));
});

test('R8-4 repeated studio segments reference unique real tooltip IDs without whitespace', async () => {
  const Segment = await loadStoryboardComponent('StoryboardChoiceControls.jsx', 'PhotoSegment', {
    useId: React.useId, useRef: React.useRef, useEffect: React.useEffect,
  });
  const props = { label: '스튜디오컷 종류', value: 'horizon', onChange() {}, options: [
    { value: 'horizon', label: '호리존컷' }, { value: 'minimal', label: '미니멀컷', disabled: true, disabledReason: '조건 확인' },
  ] };
  const html = renderToStaticMarkup(React.createElement(React.Fragment, null, React.createElement(Segment, props), React.createElement(Segment, props)));
  const references = [...html.matchAll(/aria-describedby="([^"]+)"/g)].map(match => match[1]);
  assert.equal(references.length, 2);
  assert.ok(references.every(id => !/\s/.test(id)), `description IDs must be single tokens: ${references.join(' | ')}`);
  assert.equal(new Set(references).size, 2);
  for (const id of references) assert.ok(html.includes(`id="${id}" role="tooltip"`));
});
