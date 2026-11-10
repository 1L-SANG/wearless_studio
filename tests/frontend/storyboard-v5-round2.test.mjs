import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { loadStoryboardComponent, textOf, readStoryboard } from './helpers/storyboardComponents.mjs';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { stageAdditions, addStageAddition } from '../../src/lib/pageTemplates/stageAdditions.js';

let vite, Stage, Preview, Continue, Picker, SetPanel, Gallery, catalog, seedStoryboard, sectionRuns, changeDesign, spaceSets, groupId, safeName, setExamples;
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = templateId => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId } });
const html = (component, props) => renderToStaticMarkup(React.createElement(component, props));
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
  ({ StoryboardPreview: Preview } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));
  ({ StoryboardContinue: Continue } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardContinue.jsx'));
  ({ StoryboardSectionPicker: Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
  ({ StoryboardSpaceSetPanel: SetPanel } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSpaceSetPanel.jsx'));
  ({ MoodGuide: Gallery } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardExampleGallery.jsx'));
  ({ pageTemplatesCatalog: catalog } = await vite.ssrLoadModule('/src/lib/pageTemplates/catalog.js'));
  ({ seedStoryboard } = await vite.ssrLoadModule('/src/lib/pageTemplates/seed.js'));
  ({ sectionRuns } = await vite.ssrLoadModule('/src/lib/pageTemplates/sectionRuns.js'));
  ({ changeTemplateSectionDesign: changeDesign } = await vite.ssrLoadModule('/src/lib/pageTemplates/sectionActions.js'));
  ({ storyboardSpaceSetsFor: spaceSets, spaceSetGroupId: groupId, withStoryboardSpaceSetExamples: setExamples } = await vite.ssrLoadModule('/src/lib/storyboardSpaceSetCatalog.js'));
  ({ spaceSetDisplayName: safeName } = await vite.ssrLoadModule('/src/lib/spaceSetDisplayNames.js'));
});
after(async () => { await vite?.close(); });
const stageProps = (templateId, kind, usage) => {
  const stageAnalysis = analysis(templateId);
  if (kind === 'fabric') stageAnalysis.detailRecommendations = { version: 1, status: 'ready', candidates: [
    { id: 'weave', kind: 'fabric', label: '원단', informationGroup: 'fabric', rank: 1, visibility: 'clear', photoUse: 'standalone', direction: 'front' },
  ] };
  const blocks = seedStoryboard(product, stageAnalysis);
  const runs = sectionRuns(blocks), run = runs.find(run => run.kind === kind && (!usage || run.usage === usage));
  return { run, number: kind === 'intro' ? 0 : 1, blocks, page: [], catalogs: { detailProduct: product },
    photoOrdinals: new Map(blocks.filter(block => block.source !== 'text').map((block, i) => [block.id, i + 1])), onShuffle() {} };
};

test('R2-1 stage, photo stacks and every set panel expose only seller set names', () => {
  const sets = spaceSets({ gender: 'women', clothingType: 'top' });
  const props = stageProps('emo', 'wear', 'styling');
  const set = sets.find(set => set.setType === 'styling');
  props.run = { ...props.run, blocks: props.run.blocks.map(block => ({ ...block, spaceGroupId: groupId(set.id, 'round2') })) };
  const markups = [html(Stage, props)];
  for (const clothingType of ['top', 'bottom']) for (const cut of ['styling', 'horizon']) {
    const markup = html(Gallery, { catalogs: setExamples({ genExamples: [] }), cut, shot: 'full', clothingType, gender: 'women' });
    assert.match(markup, /컷 펼치기/); markups.push(markup);
  }
  for (const [mode, usage, cutType] of [['add', 'styling', 'styling'], ['replace', 'styling', 'styling'], ['replace', 'fit', 'horizon'], ['replace', 'styling', 'horizon']]) {
    markups.push(html(SetPanel, { mode, usage, cutType, gender: 'women', clothingType: 'top', onChoose() {} }));
  }
  assert.ok(markups[0].includes(safeName(set)));
  for (const markup of markups) {
    assert.doesNotMatch(markup, /PASS|Attrangs|(?:A2|D5) ·/);
    for (const item of sets) if (item.name !== safeName(item)) assert.ok(!markup.includes(item.name.replaceAll('&', '&amp;')), item.name);
  }
});

test('R2-2 the invisible similar action cannot add height between a photo and its caption', () => {
  const css = [readFileSync(new URL('../../src/styles/app.css', import.meta.url), 'utf8'), readStoryboard('StoryboardLayout.module.css')].join('\n');
  // These selectors match the actual .fx-sheen button with both module classes.
  const matches = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*\bposition\s*:\s*(?:absolute|relative)[^{}]*)\}/g)]
    .filter(([, selector]) => ['.fx-sheen .btn-primary', '.similar', '.photoCard .photoFrame > .similar'].includes(selector.trim()))
    .map(([, selector, body], order) => ({ specificity: (selector.match(/\./g) || []).length, order, position: body.match(/position\s*:\s*(\w+)/)[1] }))
    .sort((a, b) => a.specificity - b.specificity || a.order - b.order);
  assert.equal(matches.at(-1)?.position, 'absolute');
});

test('R2-3 everyday wear offers a same-place pale cut and separate set addition without count captions', () => {
  for (const [kind, usage] of [['first'], ['fabric'], ['wear', 'fit'], ['review']]) {
    const markup = html(Stage, stageProps('emo', kind, usage));
    assert.doesNotMatch(markup, />＋ 컷 추가<|장소 세트 추가|12컷까지|컷 더 넣을/);
    assert.match(markup, /예시 이미지를 클릭해서 변경할 수 있어요/);
  }
  const markup = html(Stage, stageProps('emo', 'wear', 'styling'));
  assert.match(markup, /aria-label="같은 장소 1컷 더 넣기"/);
  assert.match(markup, /＋ 장소 세트 추가/);
  assert.doesNotMatch(markup, /＋ 컷 추가|12컷까지|컷 더 넣을 수 있어요|1컷 늘어나요|컷 수 그대로/);
});
test('R2-4 no stage exposes the old shuffle action', () => assert.doesNotMatch(html(Stage, stageProps('emo', 'first')), /예시 셔플/));
test('R2-5 the floating action has no copywriting switch', () => {
  const markup = html(Continue, { count: 7, credits: 88, copyOn: false });
  assert.doesNotMatch(markup, /카피라이팅|role="switch"/); assert.match(markup, /다음: 마네킹컷 선택/);
});
test('R2-6 paired designs have seller names and enter through the pale slot without another generated card', () => {
  assert.equal(Object.keys(catalog.designs).length, 32);
  for (const design of Object.values(catalog.designs)) assert.ok(typeof design.displayName === 'string' && design.displayName.trim() && design.displayName.length <= 24, design.id);
  const blocks = seedStoryboard(product, analysis('emo'));
  const key = blocks[0].pageSectionKey, context = { product, analysis: analysis('emo') };
  assert.equal(catalog.designs['P01-D09-G'].pickerHidden, true);
  assert.equal(changeDesign(blocks, key, 'P01-D09-G', context), blocks, 'hidden paired layouts cannot be selected as a design card');
  const addition = stageAdditions(blocks, key, context).find(item => item.kind === 'firstGhost');
  assert.ok(addition);
  const changed = addStageAddition(blocks, key, addition, context);
  assert.notEqual(changed, blocks);
  assert.equal(changed[0].pageDesignId, 'P01-D09-G');
  assert.deepEqual(changed.map(block => block.id), blocks.map(block => block.id));
  assert.equal(changed.filter(block => block.source === 'ai').length, blocks.filter(block => block.source === 'ai').length);
});
test('R2-6 each section instance has exactly one default label and a named, fitted preview per candidate', () => {
  for (const templateId of ['emo', 'show']) {
    const blocks = seedStoryboard(product, analysis(templateId));
    for (const run of sectionRuns(blocks)) {
      const markup = html(Picker, { mode: 'design', runKey: run.key, blocks,
        catalogs: { detailProduct: product, analysis: analysis(templateId), genExamples: [] } });
      assert.equal((markup.match(/<small>기본<\/small>/g) || []).length, 1, run.key);
      assert.doesNotMatch(markup, /<strong>디자인 \d/);
      assert.ok(markup.includes('height="100%"'), run.key);
      if (run.kind === 'fabric') assert.match(markup, /aria-pressed="true"/, 'the active text-only fallback is selected');
    }
  }
});
test('R2-7 a design preview fits all section parts in its bounded viewport', () => {
  const markup = html(Preview, { fit: true, blocks: [{ id: 'a', h: 2000, elements: [] }, { id: 'b', h: 3000, elements: [] }] });
  assert.match(markup, /viewBox="0 0 1000 5000"/); assert.match(markup, /width="100%"/); assert.match(markup, /height="100%"/);
  assert.match(markup, /preserveAspectRatio="xMidYMid meet"/);
});
test('D193 add entries include repeatable worn kinds and exclude existing singleton/text kinds', () => {
  const blocks = seedStoryboard(product, analysis('emo'));
  const markup = html(Picker, { mode: 'add', blocks, catalogs: { detailProduct: product, analysis: analysis('emo'), genExamples: [] } });
  assert.match(markup, /data-default-design=/); assert.doesNotMatch(markup, /상품 소개|추천 대상|첫 장/);
  assert.match(markup, /카페, 골목, 집처럼 장소에서 찍은 사진/); assert.match(markup, /단색 배경에서 앞, 옆, 뒤를 찍은 사진/);
});
test('R2-9 hidden text sections have their name without Section zero', () => {
  const markup = html(Stage, stageProps('emo', 'intro'));
  assert.match(markup, /상품 소개/); assert.doesNotMatch(markup, /Section 0/);
});
test('R2-10 incomplete preview geometry never produces NaN or Infinity transforms', () => {
  const blocks = [{ id: 'missing', elements: [] }, { id: 'nan', h: NaN, elements: [] }, { id: 'zero', h: 0, elements: [] }, { id: 'valid', h: 100, elements: [] }];
  const markup = html(Preview, { blocks });
  assert.doesNotMatch(markup, /NaN|Infinity|undefined/); assert.match(markup, /viewBox="0 0 1000 100"/);
  assert.doesNotMatch(html(Preview, { blocks: [] }), /NaN|Infinity/);
  assert.doesNotThrow(() => html(Preview, { blocks: [null, { id: 'no-elements', h: 100 }] }));
});
test('R2-11 the floating action is mounted outside transformed page ancestors', async () => {
  const body = {}, portals = [];
  const Action = await loadStoryboardComponent('StoryboardContinue.jsx', 'StoryboardContinue', {
    Toggle: 'toggle', document: { body }, createPortal: (child, target) => { portals.push(target); return child; },
  });
  const tree = Action({ count: 7, credits: 88 });
  assert.deepEqual(portals, [body]); assert.match(textOf(tree), /구성 7컷/);
});
