import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { hasImageSource } from '../../src/features/editor/editorImageSource.js';

const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const panels = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
const slice = (text, start, end) => text.slice(text.indexOf(start), text.indexOf(end, text.indexOf(start)));
const evaluate = (code, scope) => new Function('scope', `with (scope) { ${code} }`)(scope);
const React = { createElement: (type, props, ...children) => ({ type, props, children }) };
const nodes = (tree, predicate) => Array.isArray(tree) ? tree.flatMap(child => nodes(child, predicate))
  : tree && typeof tree === 'object' ? [...(predicate(tree) ? [tree] : []), ...nodes(tree.children, predicate)] : [];
const { code } = await transformWithEsbuild(slice(panels, 'function VaryPanel(', '/* ---------- AI ---------- */') + '\nreturn VaryPanel;', 'vary.jsx', { loader: 'jsx' });
const Panel = evaluate(code, { React, useRef: value => ({ current: value }), hasImageSource, Button: 'Button', EmptyState: 'EmptyState' });

for (const kind of ['worn', 'detail', 'ghost']) test(`현재 이미지 수정의 ${kind} 사진은 허용된 버튼과 요청만 제공한다`, async () => {
  let request;
  const source = { id: kind, src: `/${kind}.jpg`, cutType: kind === 'worn' ? 'styling' : 'product', ...(kind === 'detail' ? { detailReshoot: true } : {}) };
  const tree = Panel({ catalogs: { creditCosts: { editorImage: 2 } }, source, onGenerate: value => { request = value; } });
  const buttons = nodes(tree, node => node.type === 'Button');
  if (kind === 'ghost') {
    assert.equal(buttons.length, 0);
    assert.match(JSON.stringify(tree), /제품컷은 새 이미지 추가에서 원본 사진을 골라 만들어요\./);
    return;
  }
  assert.equal(buttons.length, 1);
  assert.match(JSON.stringify(tree), kind === 'worn' ? /비슷한 컷 생성 · 2 크레딧/ : /같은 부위 다시 촬영 · 2 크레딧/);
  if (kind === 'detail') assert.match(JSON.stringify(tree), /같은 부위를 원본으로 다시 촬영해요/);
  await buttons[0].props.onClick();
  assert.equal('changes' in request, false);
  assert.equal('variationMode' in request, kind === 'worn');
  if (kind === 'worn') assert.equal(request.variationMode, 'similar');
});

test('varyGenerate는 폐기된 변경값과 배경 참조를 요청에서 빼고 재촬영 진행 문구를 쓴다', async () => {
  let request; const messages = [];
  const scope = { hasImageSource, projectId: 'project', crypto: { randomUUID: () => 'key' },
    varySubmission: { current: null }, unconfirmedVarySubmissions: { current: new Map() }, varyTarget: null,
    uid: () => 'loading', setWardrobe: () => {}, setTab: () => {}, genCount: { current: 0 }, setGenDot: () => {},
    toast: { push: message => messages.push(message) }, syncCredits: () => {}, placeGeneratedImage: () => {}, handleImageJobFailure: () => {},
    api: { generateImage: async (_project, value) => { request = value; return { data: { src: '/new.jpg' }, credits: 9 }; } },
  };
  const generate = evaluate(slice(editor, '  const varyGenerate =', '  const varyImage =') + '\nreturn varyGenerate;', scope);
  await generate({ source: { src: '/detail.jpg', cutType: 'product' }, changes: [], refBg: 'old', refBgAssetId: 'old' });
  for (const key of ['changes', 'refBg', 'refBgAssetId']) assert.equal(key in request, false, key);
  assert.ok(messages.includes('같은 부위를 다시 촬영하고 있어요'));
});

test('의류와 캔버스 사진의 재촬영 여부는 서버 표시 또는 원래 제품 디테일 블록으로 판정한다', () => {
  const storyboard = [{ id: 'detail', cutType: 'product', shot: 'detail' }, { id: 'ghost', cutType: 'product', shot: 'ghost' }];
  const wardrobe = { base: [{ id: 'flag', src: '/flag.jpg', cutType: 'product', detailReshoot: true },
    { id: 'bound', src: '/bound.jpg', cutType: 'product', sourceBlockId: 'detail' },
    { id: 'ghost', src: '/ghost.jpg', cutType: 'product', sourceBlockId: 'ghost' }] };
  const source = (varyTarget, selectedElObj) => evaluate(slice(editor, '  const varySource =', '  // setBlockFocused') + '\nreturn varySource;',
    { wardrobe, varyTarget, selectedElObj, hasImageSource, wardrobeContext: { current: { storyboard } } });
  assert.equal(source({ id: 'flag' }).detailReshoot, true);
  assert.equal(source({ id: 'bound' }).detailReshoot, true);
  assert.equal(source({ id: 'ghost' }).detailReshoot, false);
  for (const src of ['/flag.jpg', '/bound.jpg']) assert.equal(source(null, { id: 'canvas', type: 'image', src }).detailReshoot, true);
  assert.equal(source(null, { id: 'canvas', type: 'image', src: '/canvas-only.jpg', sourceBlockId: 'detail' }).detailReshoot, true);
  assert.equal(source(null, { id: 'canvas', type: 'image', src: '/other.jpg', sourceBlockId: 'ghost' }).detailReshoot, false);
});

for (const { name, image, slots, expected } of [
  { name: '에디터 고스트를 디테일 칸에 넣어도 다시 촬영을 제공하지 않는다',
    image: { id: 'editor-ghost', src: '/editor-ghost.jpg', cutType: 'product' }, slots: ['detail'], expected: false },
  { name: '서버 표시가 있는 에디터 디테일은 어느 칸에서도 다시 촬영을 제공한다',
    image: { id: 'editor-detail', src: '/editor-detail.jpg', cutType: 'product', detailReshoot: true },
    slots: ['detail', 'ghost', null], expected: true },
  { name: '서버 목록에 없는 상세페이지 디테일은 원래 칸에서 다시 촬영을 제공한다',
    image: { id: 'page-detail', src: '/page-detail.jpg', cutType: 'product', sourceBlockId: 'detail' },
    slots: ['detail'], expected: true },
]) test(name, async () => {
  const storyboard = [{ id: 'detail', cutType: 'product', shot: 'detail' }, { id: 'ghost', cutType: 'product', shot: 'ghost' }];
  const sourceFor = (varyTarget, selectedElObj) => evaluate(slice(editor, '  const varySource =', '  // setBlockFocused') + '\nreturn varySource;',
    { wardrobe: { base: [image] }, varyTarget, selectedElObj, hasImageSource, wardrobeContext: { current: { storyboard } } });
  const sources = slots.map(sourceBlockId => sourceFor(null, { id: 'canvas', type: 'image', src: image.src, cutType: 'product', sourceBlockId }));
  sources.push(sourceFor({ id: image.id, group: 'base' }, null));
  for (const source of sources) {
    assert.equal(source.detailReshoot, expected);
    let request;
    const tree = Panel({ catalogs: { creditCosts: { editorImage: 2 } }, source, onGenerate: value => { request = value; } });
    const buttons = nodes(tree, node => node.type === 'Button');
    assert.equal(buttons.length, expected ? 1 : 0);
    if (expected) {
      assert.match(JSON.stringify(tree), /같은 부위 다시 촬영 · 2 크레딧/);
      await buttons[0].props.onClick();
      assert.equal(request.source.src, image.src);
      assert.equal('variationMode' in request, false);
    } else assert.match(JSON.stringify(tree), /제품컷은 새 이미지 추가에서 원본 사진을 골라 만들어요\./);
  }
});
