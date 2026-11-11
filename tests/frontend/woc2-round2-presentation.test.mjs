import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';

const postcss = createRequire(import.meta.resolve('vite'))('postcss');
const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const layout = postcss.parse(read('src/features/storyboard/StoryboardLayout.module.css'));
const tokens = postcss.parse(read('src/styles/tokens.css'));
const rule = selector => layout.nodes.find(node => node.type === 'rule' && node.selector === selector);
const declarations = selector => Object.fromEntries((rule(selector)?.nodes || []).filter(node => node.type === 'decl').map(node => [node.prop, node.value]));
let vite, Stage, List, styles, seed, add, assemble, runs, defaultPageStyle, crops, previewCuts;
const colors = [{ id: 'ivory', name: '아이보리', swatchId: 'white', isBase: true },
  { id: 'navy', name: '네이비' }, { id: 'mint', name: '민트' }, { id: 'charcoal', name: '차콜' }];
const photo = (id, kind = 'wear', extra = {}) => ({ id, source: 'ai', taxonomyVersion: 4, cutType: 'styling', shot: 'full',
  contentRole: 'coordination', direction: 'front', thumb: `/${id}.webp`, exampleId: 'unclassified', colorId: 'ivory',
  pageSectionKey: `emo.${kind}`, pageSectionKind: kind, ...(kind === 'wear' ? { pageSectionUsage: 'styling' } : {}), ...extra });
function stageTree(kind, photos, options = {}) {
  const run = { key: `emo.${kind}`, kind, usage: kind === 'wear' ? 'styling' : undefined, blocks: photos };
  return Stage({ run, number: 1, blocks: photos, page: [], catalogs: { detailProduct: { colors } },
    photoOrdinals: new Map(photos.map((block, i) => [block.id, i + 1])), ...options });
}
const withClass = (tree, className) => nodes(tree).filter(node => String(node.props?.className || '').split(' ').includes(className));
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', logLevel: 'silent' });
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
  ({ StoryboardPageList: List } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPageList.jsx'));
  ({ default: styles } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardLayout.module.css'));
  ({ seedStoryboard: seed, wornCropRegions: crops } = await vite.ssrLoadModule('/src/lib/pageTemplates/seed.js'));
  ({ addTemplateSection: add } = await vite.ssrLoadModule('/src/lib/pageTemplates/sectionActions.js'));
  ({ assemblePage: assemble } = await vite.ssrLoadModule('/src/lib/pageTemplates/assemble.js'));
  ({ sectionRuns: runs } = await vite.ssrLoadModule('/src/lib/pageTemplates/sectionRuns.js'));
  ({ defaultPageStyle } = await vite.ssrLoadModule('/src/lib/pageTemplates/pageStyle.js'));
  ({ storyboardPreviewCuts: previewCuts } = await vite.ssrLoadModule('/src/features/storyboard/storyboardPreview.js'));
});
after(async () => { await vite?.close(); });

test('R2-1 responsive width has one ordered theme owner, including repeated theme classes', () => {
  const definitions = [];
  for (const [file, root] of [['tokens', tokens], ['layout', layout]]) root.walkDecls('--sb-toc-thumb', decl => {
    definitions.push({ file, selector: decl.parent.selector, media: decl.parent.parent.type === 'atrule' ? decl.parent.parent.params : null, value: decl.value });
  });
  assert.ok(definitions.length >= 2);
  assert.ok(definitions.every(def => def.file === 'tokens' && def.selector === '.sb-v5-theme'), 'a local or descendant theme must not reset the responsive width');
  assert.equal(definitions[0].media, null);
  assert.equal(definitions.at(-1).media, '(width < 1360px)');
  const values = new Map(); tokens.walkDecls(decl => { if (decl.parent.type === 'rule' && decl.parent.selector === '.sb-v5-theme' && decl.parent.parent.type !== 'atrule') values.set(decl.prop, decl.value); });
  const resolve = value => value.startsWith('var(') ? resolve(values.get(value.slice(4, -1))) : parseFloat(value);
  for (const width of [1280, 1359, 1360, 1440, 1920]) {
    const applicable = definitions.filter(def => !def.media || width < 1360);
    const thumb = resolve(applicable.at(-1).value), column = thumb + resolve(values.get('--sb-toc-tools-space'));
    assert.deepEqual([thumb, column], width < 1360 ? [172, 216] : [184, 228]);
    // Both the body and the page match the same media-aware theme rule.
    assert.equal(resolve(applicable.at(-1).value), thumb);
  }
});
test('R2-2 title gaps fit the measured longest current name at 184px without truncating 172px', () => {
  const title = declarations('.sectionTitle'), count = declarations('.sectionTitle small'), name = declarations('.sectionTitle strong');
  // 총괄 실측(11/05, 크롬 1440, 줄바꿈 없는 폭): Section 52.5, 스튜디오 착용컷 80.5, 10컷 22.6px.
  assert.equal(title.gap, 'var(--sp-3)'); assert.equal(count['margin-left'], '0');
  const required = 53 + 81 + 23 + 3 + 3;
  assert.ok(required <= 184 - 20); assert.ok(required > 172 - 20);
  assert.equal(name['min-width'], '0'); assert.equal(title['flex-wrap'], 'wrap');
  assert.doesNotMatch(rule('.sectionTitle strong').toString(), /ellipsis|nowrap/);
});
for (const kind of ['first', 'points', 'color_product']) test(`R2-3 ${kind} has no empty group-header strip`, () => {
  const photos = [photo('a', kind), photo('b', kind, { colorId: 'navy' })];
  assert.equal(withClass(stageTree(kind, photos), styles.groupHead).length, 0);
});
test('R2-3 header spacing is retained for blank groups sharing a row with a named group', () => {
  const tree = stageTree('wear', [photo('independent'), photo('group-a', 'wear', { spaceGroupId: 'same' }), photo('group-b', 'wear', { spaceGroupId: 'same' })]);
  const headers = withClass(tree, styles.groupHead);
  assert.equal(headers.length, 2); assert.equal(textOf(headers[0]), ''); assert.match(textOf(headers[1]), /장소 바꾸기/);
  const emptyRow = stageTree('wear', [photo('single')]);
  assert.equal(withClass(emptyRow, styles.groupHead).length, 0);
  assert.equal(declarations('.addSlot')['margin-top'], '0');
  assert.ok(withClass(tree, styles.addWithHeading).length === 1);
  assert.equal(declarations('.addWithHeading')['margin-top'], 'calc(var(--sb-group-head-height) + var(--sp-8))');
  assert.ok(rule('.groupHead .pill'), 'the compact group replacement pill keeps its own dimensions');
});
test('WOC8 color-product captions show color dots, names and ordinals without group headings', () => {
  const photos = colors.map((color, i) => photo(`front${i}`, 'color_product', { colorId: color.id, cutType: 'product' }));
  photos.push(photo('back', 'color_product', { cutType: 'product', direction: 'back', slotKey: 'color_product.back' }));
  const tree = stageTree('color_product', photos);
  assert.equal(withClass(tree, styles.groupHead).length, 0);
  assert.deepEqual(withClass(tree, styles.captionColorName).map(textOf), ['아이보리','네이비','민트','차콜','아이보리']);
  assert.ok(withClass(tree, styles.captionColor).every(dot => dot.type === 'svg' && dot.props['aria-hidden']));
  assert.doesNotMatch(textOf(tree), /두 번째 색/);
});
test('R2-4 everyday extra colors use their names and added base-color groups retain the existing label', () => {
  const tree = stageTree('wear', colors.map((color, i) => photo(`color${i}`, 'wear', { colorId: color.id })));
  const headings = withClass(tree, styles.groupHead).map(textOf);
  for (const name of ['네이비','민트','차콜']) assert.ok(headings.includes(name));
  assert.doesNotMatch(textOf(tree), /두 번째 색/);
  const added = stageTree('wear', [photo('other', 'wear', { colorId: 'navy' }), photo('base')]);
  assert.match(textOf(added), /추가한 컷/);
});
test('R2-5 outline add is the outlined plus pill while overview remains a text action', () => {
  const markup = renderToStaticMarkup(React.createElement(List, { runs: [], page: [], sectionRefs: { current: new Map() } }));
  assert.match(markup, />＋ 섹션 추가</); assert.match(markup, /outlineAdd/);
  const addRule = declarations('.outlineHead button.outlineAdd');
  assert.equal(addRule['border-radius'], 'var(--r-pill)'); assert.equal(addRule['text-decoration'], 'none');
  assert.match(addRule.border || '', /^1px solid var\(/);
  assert.equal(declarations('.outlineHead button, .back')['text-decoration'], 'underline');
});
test('WOC4 place captions stay absent for both unknown and known locations while ordinals remain', () => {
  const block = photo('hero', 'first');
  const unknown = stageTree('first', [block]);
  assert.equal(withClass(unknown, styles.captionColor)[0].type, 'svg');
  assert.equal(withClass(unknown, styles.captionColorName).length, 1);
  assert.doesNotMatch(textOf(unknown), /기타/); assert.match(textOf(unknown), /1번째 컷/);
  const known = stageTree('first', [block], { catalogs: { genExamples: [{ id: 'unclassified', cutType: 'styling', placeType: 'cafe' }] } });
  assert.doesNotMatch(textOf(known), /카페/); assert.match(textOf(known), /1번째 컷/);
});
test('R2-7 worn crops clip the exact assembled area inside a contained, letterboxed stage viewport', () => {
  const originalRegions = {
    top: [['shoulder','어깨선',[0.44,0.20,0.30,0.16]],['neckline','목선이 앉는 모양',[0.36,0.20,0.28,0.17]],['length','입었을 때 기장',[0.30,0.27,0.40,0.24]]],
    outer: [['shoulder','어깨선',[0.44,0.20,0.30,0.16]],['silhouette','입었을 때 실루엣',[0.28,0.20,0.44,0.34]],['length','입었을 때 기장',[0.30,0.40,0.40,0.24]]],
    bottom: [['waistline','입었을 때 허리선',[0.34,0.36,0.32,0.14]],['silhouette','입었을 때 실루엣',[0.30,0.40,0.40,0.50]],['length','입었을 때 기장',[0.30,0.72,0.40,0.24]]],
    dress: [['shoulder','어깨선',[0.44,0.20,0.30,0.16]],['silhouette','입었을 때 실루엣',[0.30,0.30,0.40,0.40]],['length','입었을 때 기장',[0.30,0.60,0.40,0.26]]],
  };
  assert.deepEqual(crops, originalRegions);
  for (const clothingType of Object.keys(originalRegions)) {
    const product = { clothingType, colors: [colors[0]] }, analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle() };
    const original = seed(product, analysis), blocks = add(original, product, analysis, 'points');
    const run = runs(blocks).find(run => run.kind === 'points'), catalogs = { detailProduct: product };
    const page = assemble({ storyboard: blocks, product, analysis, cutResults: previewCuts(blocks, catalogs) });
    const images = page.filter(item => item.pageSectionKey === run.key).flatMap(item => item.elements).filter(el => el.pointItemKind === 'wornCrop');
    const tree = Stage({ run, number: 2, blocks, page, catalogs, photoOrdinals: new Map(blocks.map((b,i)=>[b.id,i+1])) });
    const previews = withClass(tree, styles.cropPreview);
    assert.equal(previews.length, 3, clothingType); assert.equal(images.length, 3);
    for (const [index, preview] of previews.entries()) {
      const el = images[index];
      assert.equal(preview.props.viewBox, `0 0 ${el.w} ${el.h}`);
      assert.equal(preview.props.preserveAspectRatio, 'xMidYMid meet');
      const clip = nodes(preview).find(node => node.type === 'svg' && node !== preview);
      assert.ok(clip, 'the transformed viewBox alone does not clip its letterbox');
      assert.equal(clip.props.overflow, 'hidden'); assert.equal(clip.props.width, el.w); assert.equal(clip.props.height, el.h);
      const image = nodes(clip).find(node => node.type === 'image');
      assert.equal(image.props.href, el.src); assert.equal(image.props.x, -el.crop.ox); assert.equal(image.props.y, -el.crop.oy);
      assert.equal(image.props.width, el.crop.iw); assert.equal(image.props.height, el.crop.ih);
    }

  }
  assert.equal(declarations('.photo .cropPreview').background, 'var(--bg-2)');
});
