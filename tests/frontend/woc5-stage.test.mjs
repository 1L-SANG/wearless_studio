import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { assembleStoryboardPreview } from '../../src/features/storyboard/storyboardPreview.js';
import { addStageAddition, stageAdditions } from '../../src/lib/pageTemplates/stageAdditions.js';
import { storyboardSpaceSetsFor, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';
import { storyboardStage } from './helpers/storyboardStageHarness.mjs';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';
let vite, Stage;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
});
test('WOC5 opening pale ghost keeps its own position and never joins the hero location instance', () => {
  const value = input(), hero = value.storyboard[0];
  const set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(s => s.setType === 'styling');
  hero.spaceGroupId = spaceSetGroupId(set.id, 'first'); hero.exampleId = set.members[0].exampleId;
  const similar = createSimilarCutBlock(hero, 'hero-similar');
  value.storyboard.splice(1, 0, similar);
  const html = render(value, 'first');
  assert.ok(html.indexOf('고스트컷 넣기') < html.indexOf('2번째 컷 사진 바꾸기'));
  assert.doesNotMatch(html, />장소 바꾸기</);
});
test('WOC5 a pale-only continuation retains the real set replacement target', async () => {
  const value = input(), run = sectionRuns(value.storyboard).find(r => r.kind === 'wear');
  const set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(s => s.setType === 'styling' && s.members.length === 3);
  const members = createSpaceSetMembers(set, run.blocks[0], { spaceGroupId: spaceSetGroupId(set.id, 'continued'), members: set.members.slice(0, 2) });
  const group = members.flatMap((b, i) => [b, createSimilarCutBlock(b, `similar-${i}`)]), selected = [];
  const tree = await storyboardStage(group, { catalogs: { detailProduct: value.product }, onReplaceSet: b => selected.push(b) });
  const buttons = nodes(tree).filter(n => n.type === 'button' && textOf(n) === '장소 바꾸기');
  assert.equal(buttons.length, 2); buttons.forEach(button => button.props.onClick());
  assert.ok(selected.every(block => block === group[0] && block.sectionId === run.sectionId));
});
test('WOC5 pale fabric uses exactly the crop geometry that adding its bound detail will render', () => {
  const value = input(); value.storyboard = value.storyboard.filter(b => b.pageSectionKind !== 'fabric');
  const base = value.product.colors.find(c => c.isBase);
  base.images = [{ slot: 'Front', src: '/wide-fabric.jpg', width: 1600, height: 900 }];
  value.analysis.detailRecommendations = { version: 1, status: 'ready', candidates: [{ id: 'dt1_pale', kind: 'fabric', informationGroup: 'fabric',
    label: '원단', rank: 1, photoUse: 'standalone', visibility: 'clear', direction: 'front', sourceSlot: 'Front', sourceIndex: 0, region: { x: .2, y: .3, w: .3, h: .2 } }] };
  const key = sectionRuns(value.storyboard).find(r => r.kind === 'points').key, item = stageAdditions(value.storyboard, key, value)[0];
  assert.ok(item);
  const html = render(value, 'points'), tile = html.match(/aria-label="원단 확대 넣기">([\s\S]*?)<\/button>/)?.[1];
  const next = addStageAddition(value.storyboard, key, item, { ...value, makeId: () => 'expected-fabric' });
  const geometry = assembleStoryboardPreview({ storyboard: next, product: value.product, analysis: value.analysis }).flatMap(b => b.elements).find(e => e.sourceBlockId === 'expected-fabric');
  assert.match(tile, new RegExp(`viewBox="0 0 ${geometry.w} ${geometry.h}"`));
  assert.match(tile, new RegExp(`<image[^>]*width="${geometry.crop.iw}"[^>]*height="${geometry.crop.ih}"[^>]*x="${-geometry.crop.ox}"[^>]*y="${-geometry.crop.oy}"`));
});
after(() => vite?.close());
const input = () => {
  const value = structuredClone(JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json')).cases.find(c => c.name === 'emo-2-mono').input);
  value.storyboard.forEach(b => { if (b.pageSectionKind === 'color_product') b.productPresentation = 'ghost'; if (b.pageSectionKind === 'first') b.pageDesignId = 'P01-D11'; });
  return value;
};
function render(value, kind) {
  const catalogs = { detailProduct: value.product, analysis: value.analysis || {}, genExamples: [] };
  const run = sectionRuns(value.storyboard).find(r => r.kind === kind);
  return renderToStaticMarkup(React.createElement(Stage, { run, blocks: value.storyboard, page: assembleStoryboardPreview({ ...value, catalogs }), catalogs,
    photoOrdinals: new Map(value.storyboard.filter(b => b.source !== 'text').map((b, i) => [b.id, i + 1])) }));
}
test('WOC5 stage shows pale optional positions without count captions and replaces them with removable photos', () => {
  for (const [kind, label] of [['first', '고스트컷'], ['fabric', '제품컷']]) {
    const value = input(), key = sectionRuns(value.storyboard).find(r => r.kind === kind).key;
    const html = render(value, kind);
    assert.match(html, new RegExp(`aria-label="${label} 넣기"`));
    assert.doesNotMatch(html, /1컷 늘어나요|컷 수 그대로|더 넣을 수 있어요/);
    value.storyboard = addStageAddition(value.storyboard, key, stageAdditions(value.storyboard, key, value)[0], value);
    const added = render(value, kind);
    assert.doesNotMatch(added, new RegExp(`aria-label="${label} 넣기"`));
    assert.match(added, new RegExp(`aria-label="${label} 빼기"`));
  }
});
test('WOC5 header uses the approved full-width sentence and stage no longer offers a generic cut picker', () => {
  const shell = readFileSync('src/features/storyboard/Storyboard.jsx', 'utf8');
  assert.match(shell, /className=\{styles.introCopy\}>예시 이미지들로 상세페이지 초안이 구성됐어요\. 원하는 느낌으로 구성해보세요\./);
  const stage = readFileSync('src/features/storyboard/StoryboardStage.jsx', 'utf8');
  assert.doesNotMatch(stage, /＋ 컷 추가|더 넣을 수 있어요|onAddPhoto/);
});
