import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { assembleStoryboardPreview } from '../../src/features/storyboard/storyboardPreview.js';
import { withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { signatureCardForBlock } from '../../src/lib/signatureCutPool.js';
import { garmentColorOptions } from '../../src/lib/garmentColors.js';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';

let vite, PageList, Themes, Continue, PhotoPanel, Gallery;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, logLevel: 'silent' });
  ({ StoryboardPageList: PageList } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPageList.jsx'));
  ({ StoryboardThemePicker: Themes } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardThemePicker.jsx'));
  ({ StoryboardContinue: Continue } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardContinue.jsx'));
  ({ StoryboardPhotoPanel: PhotoPanel } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPhotoPanel.jsx'));
  ({ StoryboardPhotoGallery: Gallery } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPhotoGallery.jsx'));
});
after(() => vite?.close());
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
const read = file => readFileSync(`src/features/storyboard/${file}`, 'utf8');
const product = { clothingType: 'top', colors: [
  { id: 'base', isBase: true, name: '아이보리', hex: '#eee9dd', images: [{ slot: 'Front', src: '/front.jpg' }, { slot: 'Back', src: '/back.jpg' }] },
  { id: 'blue', name: '블루', hex: '#abc', images: [{ slot: 'Back', src: '/blue-back.jpg' }] },
  { id: 'black', name: '블랙', hex: '#222', images: [] },
] };
const analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle() };
const catalogs = { ...withStoryboardSpaceSetExamples({ genExamples: raw }), detailProduct: product, analysis,
  shotTypes: [{ value: 'full', label: '전신' }, { value: 'medium', label: '상반신' }],
  productShotTypes: [{ value: 'ghost', label: '전체컷' }, { value: 'detail', label: '디테일' }] };
const board = () => seedStoryboard(product, analysis);
const panelProps = (blocks = board()) => ({ blocks, block: blocks.find(item => item.pageSectionKind === 'color_product'),
  catalogs, clothingType: 'top', exampleGender: 'women', colorOpts: [], detailColorOpts: [], onChange() {} });

test('WOC5 section tools stay on the top left and keyboard arrows remain reachable', () => {
  const blocks = board(), runs = sectionRuns(blocks);
  const html = render(PageList, { runs, page: assembleStoryboardPreview({ storyboard: blocks, catalogs }), sectionRefs: { current: new Map() } });
  assert.doesNotMatch(html, /aria-label="Section 1 (?:순서 바꾸기|위로|아래로)"/);
  assert.equal((html.match(/aria-label="섹션 순서와 삭제"/g) || []).length, runs.length - 1);
  assert.match(html, /data-move-action="up"/);
  assert.doesNotMatch(html, /tabindex="-1"[^>]*data-move-action|data-move-action="(?:up|down)"[^>]*tabindex="-1"/);
  const css = read('StoryboardLayout.module.css');
  const tools = css.match(/\.sectionTools \{([^}]+)\}/)?.[1];
  assert.match(tools, /left: var\(--sp-6\)/);
  assert.doesNotMatch(tools, /opacity: 0|pointer-events: none/);
  assert.match(css, /\.sectionTools button \{[^}]*width: var\(--sp-28\); height: var\(--sp-28\)/);
  assert.match(css, /\.sectionTools:hover \.sectionMoveTools, \.sectionTools:focus-within \.sectionMoveTools/);
  assert.doesNotMatch(css, /\.sectionRow:(?:hover|focus-within) \.sectionTools/);
});

test('WOC5 all three named type specimens are readable and only selection adds the ink border', () => {
  const html = render(Themes, { value: 'serif' });
  assert.match(html, />글자 스타일</);
  assert.match(html, /role="group" aria-label="글자 스타일"/);
  for (const name of ['매거진', '볼드', '다정']) assert.match(html, new RegExp(`title="${name}"`));
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
  const css = read('StoryboardChoices.module.css');
  assert.doesNotMatch(css.match(/\.themeChoice \{([^}]+)\}/)?.[1] || '', /opacity:/);
  assert.match(css, /\.themeSelected \{[^}]*1\.5px var\(--fg-1\)/);
  assert.doesNotMatch(readFileSync('src/styles/tokens.css', 'utf8'), /--sb-theme-inactive/);
  assert.doesNotMatch(read('StoryboardLayout.module.css'), /\.drawerOpen > :global\(\.page-head\) \{[^}]*opacity:/);
});

test('WOC5 intro copy spans the header grid at the approved readable size', () => {
  const css = read('StoryboardLayout.module.css');
  assert.match(css, /\.pageHeadWithTheme \{[^}]*display: grid/);
  assert.match(css, /\.introCopy \{[^}]*grid-column: 1 \/ -1[^}]*font-size: var\(--sb-fs-intro\)/);
  assert.doesNotMatch(css.match(/\.introCopy \{([^}]+)\}/)?.[1] || '', /nowrap|word-break|overflow-wrap/);
  assert.match(readFileSync('src/styles/tokens.css', 'utf8'), /--sb-fs-intro: 16px/);
});

test('WOC5 the garment color row precedes product modes and uses one labeled button per color', () => {
  const html = render(PhotoPanel, panelProps());
  assert.ok(html.indexOf('의류 색상 선택') >= 0 && html.indexOf('의류 색상 선택') < html.indexOf('촬영 방식'));
  assert.match(html, /<button[^>]*aria-label="아이보리"[^>]*>[\s\S]*?<svg[\s\S]*?아이보리<\/span><\/button>/);
  assert.match(html, /<button[^>]*aria-label="블루"/);
  assert.match(html, /<button[^>]*disabled=""[^>]*aria-label="블랙"/);
  assert.match(html, /원본 사진 필요/);
});

test('WOC5 single-color products omit the garment row while missing and mixed modes disable color edits', () => {
  const props = panelProps();
  assert.doesNotMatch(render(PhotoPanel, { ...props, catalogs: { ...catalogs, detailProduct: { ...product, colors: [product.colors[0]] } } }), /의류 색상 선택/);
  for (const mixed of [false, true]) {
    const blocks = board().map((block, index) => block.pageSectionKind === 'color_product'
      ? { ...block, productPresentation: mixed && index % 2 ? 'flat_lay' : null } : block);
    const html = render(PhotoPanel, panelProps(blocks));
    assert.match(html, /<button[^>]*disabled=""[^>]*aria-label="아이보리"/);
    assert.match(html, /촬영 방식을 먼저 골라 주세요/);
  }
});

test('WOC5 displayed first ghost has actual base-front source and no mode or color editing', () => {
  const props = panelProps();
  const html = render(PhotoPanel, { ...props, fixedProduct: true });
  assert.match(html, /고스트컷/);
  assert.match(html, /기준 색상/);
  assert.match(html, /src="\/front.jpg"/);
  assert.doesNotMatch(html, /aria-label="의류 색상 선택"|평면컷|자연광컷|<button/);
  const missingFront = render(PhotoPanel, { ...props, fixedProduct: true,
    catalogs: { ...catalogs, detailProduct: { ...product, colors: [{ ...product.colors[0], images: [{ slot: 'Back', src: '/back.jpg' }] }] } } });
  assert.match(missingFront, /앞면 사진이 필요해요/);
  assert.doesNotMatch(missingFront, /<img/);
});

test('WOC5 signature cards display a small visible signature label', () => {
  const block = board()[0];
  const signature = signatureCardForBlock(block, { product, analysis, gender: 'women', projectId: 'woc5' });
  assert.ok(signature);
  const html = render(Gallery, { block, catalogs, cut: block.cutType, shot: block.shot, clothingType: 'top', gender: 'women', signature });
  assert.match(html, /<span[^>]*>시그니처<\/span>/);
});

test('WOC5 generated composition count uses cuts', () => {
  const html = render(Continue, { count: 7, credits: 88 });
  assert.match(html, /구성 <strong>7<\/strong>컷/);
  assert.doesNotMatch(html, /구성컷|7<\/strong>개/);
});

test('WOC5 color buttons preserve source availability and cannot submit disabled edits', async () => {
  const Field = await loadStoryboardComponent('StoryboardGarmentColorField.jsx', 'StoryboardGarmentColorField', {
    useId: () => 'color-field', garmentColorOptions,
  });
  const chosen = [], props = { product, block: { cutType: 'product', shot: 'ghost', direction: 'front' }, value: 'base', onChange: id => chosen.push(id) };
  let tree = Field(props);
  nodes(tree).find(node => node.props?.['aria-label'] === '블루').props.onClick();
  nodes(tree).find(node => node.props?.['aria-label'] === '블랙').props.onClick();
  assert.deepEqual(chosen, ['blue']);
  tree = Field({ ...props, disabled: true });
  nodes(tree).find(node => node.props?.['aria-label'] === '아이보리').props.onClick();
  assert.deepEqual(chosen, ['blue']);
  const worn = Field({ ...props, block: { cutType: 'styling', shot: 'medium' } });
  assert.equal(nodes(worn).find(node => node.props?.['aria-label'] === '블루').props.disabled, true);
  const backOnly = Field({ ...props, value: 'blue' });
  assert.equal(nodes(backOnly).find(node => node.type === 'img').props.src, '/blue-back.jpg');
  assert.match(textOf(backOnly), /앞면 사진이 없어 뒷면을 사용해요/);
});

test('WOC5 a mixed product run blocks stale color events until a uniform mode exists', async () => {
  const blocks = board().map((block, index) => block.pageSectionKind === 'color_product'
    ? { ...block, productPresentation: index % 2 ? 'flat' : 'ghost' } : block);
  const { onChange: _onChange, ...props } = panelProps(blocks), h = await storyboardPhotoPanelHarness(props);
  try {
    let field = nodes(h.render()).find(node => node.type === 'color-dots');
    assert.equal(field.props.disabled, true);
    field.props.onChange('blue');
    assert.equal(h.changes.length, 0);
    const selected = blocks.map(block => block.pageSectionKind === 'color_product' ? { ...block, productPresentation: 'ghost' } : block);
    field = nodes(h.render({ blocks: selected, block: selected.find(block => block.id === props.block.id) }))
      .find(node => node.type === 'color-dots');
    assert.equal(field.props.disabled, false);
    field.props.onChange('blue');
    assert.equal(h.changes.length, 1);
    assert.equal(h.block.colorId, 'blue');
    assert.equal(h.block.direction, 'back');
    assert.equal(h.block.productPresentation, 'ghost');
  } finally { h.close(); }
});


test('WOC5 signature badge occupies the top left and normal type specimens exceed 4.5 contrast', () => {
  const css = read('StoryboardChoices.module.css');
  const badge = css.match(/\.signatureBadge \{([^}]+)\}/)?.[1];
  assert.match(badge, /left: var\(--sp-6\)/); assert.match(badge, /top: var\(--sp-6\)/); assert.doesNotMatch(badge, /bottom:/);
  const tokens = readFileSync('src/styles/tokens.css', 'utf8');
  const value = name => tokens.match(new RegExp(`${name}: (#[0-9a-f]{6})`, 'i'))[1];
  const light = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  for (const background of ['--bg-1', '--bg-2']) assert.ok((light(value(background)) + .05) / (light(value('--fg-1')) + .05) >= 4.5);
});
