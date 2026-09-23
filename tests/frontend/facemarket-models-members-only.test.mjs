import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { eventually, findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';

/* =============================================================
   /models 모델 리스트는 등록된 셀러와 모델만 본다(2026-09-23 오너 결정).
   판정은 서버(GET /v1/facemarket/catalog-access)가 한다. 비로그인, 자격 없는 로그인 계정,
   판정 실패는 모두 안내 화면을 본다. 세션과 자격을 확인하는 동안에는 둘 다 그리지 않는다.
   볼 수 있는 사람에게 안내 화면이 잠깐 비치면 안 되기 때문이다.
   ============================================================= */

const textOf = node => node == null || typeof node === 'boolean' ? ''
  : typeof node !== 'object' ? String(node)
  : Array.isArray(node) ? node.map(textOf).join('')
  : textOf(node.props?.children);
const named = name => node => node?.type?.name === name;

const bodyHarness = (getCatalogAccess) => modelComponentHarness({
  entry: '/src/features/facemarket-landing/pages/ModelsPage.jsx', exportName: 'ModelsBody',
  initialStates: [], honorHookDependencies: true, api: { getCatalogAccess },
});
const commit = (h) => { const tree = h.render(); h.runtime.effects.forEach(effect => effect()); return tree; };
const gateHarness = () => modelComponentHarness({
  entry: '/src/features/facemarket-landing/sections/MembersOnlyGate.jsx', exportName: 'MembersOnlyGate',
  initialStates: [], api: {},
});

test('비로그인은 자격을 묻지도 않고 회원 전용 안내를 봐요', async () => {
  const calls = [];
  const h = await bodyHarness(async () => { calls.push('access'); return { allowed: true }; });
  h.runtime.session = null;
  try {
    const tree = commit(h);
    assert.ok(findTree(tree, named('MembersOnlyGate')));
    assert.equal(findTree(tree, named('BrowseSection')), null);
    assert.deepEqual(calls, []);
  } finally { await h.close(); }
});

test('등록된 셀러와 모델은 서버 판정을 받은 뒤 목록을 봐요', async () => {
  let resolve;
  const h = await bodyHarness(() => new Promise((done) => { resolve = done; }));
  h.runtime.session = { user: { id: 'seller-1' } };
  try {
    const pending = commit(h);
    assert.equal(findTree(pending, named('BrowseSection')), null, '판정 전에 목록을 그리면 안 돼요');
    assert.equal(findTree(pending, named('MembersOnlyGate')), null, '판정 전에 안내가 비치면 안 돼요');
    resolve({ allowed: true, role: 'seller' });
    await eventually(() => findTree(h.render(), named('BrowseSection')), '판정 뒤 목록이 떠야 해요');
    assert.equal(findTree(h.render(), named('MembersOnlyGate')), null);
  } finally { await h.close(); }
});

for (const [label, getCatalogAccess] of [
  ['로그인만 한 계정', async () => ({ allowed: false, role: null })],
  ['판정 조회가 실패한 경우', async () => { throw new Error('network'); }],
]) {
  test(`${label}은 목록 대신 안내를 봐요`, async () => {
    const h = await bodyHarness(getCatalogAccess);
    h.runtime.session = { user: { id: 'user-1' } };
    try {
      commit(h);
      await eventually(() => findTree(h.render(), named('MembersOnlyGate')), '안내가 떠야 해요');
      assert.equal(findTree(h.render(), named('BrowseSection')), null);
    } finally { await h.close(); }
  });
}

test('세션을 확인하는 동안에는 안내도 목록도 그리지 않아요', async () => {
  const h = await bodyHarness(async () => ({ allowed: true }));
  h.runtime.session = null;
  h.runtime.loading = true;
  try {
    const tree = commit(h);
    assert.equal(findTree(tree, named('MembersOnlyGate')), null);
    assert.equal(findTree(tree, named('BrowseSection')), null);
  } finally { await h.close(); }
});

test('안내 화면은 누가 볼 수 있는지 말하고, 홈으로 돌려보내요', async () => {
  // '로그인하고 보기'는 로그인만 하면 누구나 본다는 뜻으로 읽혀서 뺐어요(2026-09-23 오너).
  const h = await gateHarness();
  h.runtime.session = null;
  try {
    const tree = h.render();
    const text = textOf(tree);
    assert.match(text, /등록된 셀러와 모델만/);
    assert.doesNotMatch(text, /로그인/);
    const home = findTree(tree, node => node.type === 'Link' && node.props.to === '/');
    assert.ok(home, '홈으로 가는 버튼이 없어요');
    assert.equal(textOf(home), '홈으로 돌아가기');
    assert.match(home.props.className, /btn-primary/);
    assert.ok(findTree(tree, node => node.type === 'Link' && node.props.to === '/apply'), '모델 지원 링크가 없어요');
    assert.equal(findTree(tree, node => node.type === 'button'), null, '로그인 버튼이 남아 있어요');
  } finally { await h.close(); }
});

test('안내 그림은 꾸밈용이고 실제 파일이 있어요', async () => {
  const h = await gateHarness();
  h.runtime.session = null;
  try {
    const img = findTree(h.render(), node => node.type === 'img');
    assert.ok(img, '그림이 없어요');
    assert.equal(img.props.alt, '');
    assert.ok(img.props.width && img.props.height, '레이아웃이 흔들리지 않게 크기를 적어요');
    assert.ok(existsSync(new URL(`../../public${img.props.src}`, import.meta.url)), `${img.props.src} 파일이 없어요`);
  } finally { await h.close(); }
});

const findAll = (node, match, out = []) => {
  if (node == null || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach(child => findAll(child, match, out)); return out; }
  if (match(node)) out.push(node);
  findAll(node.props?.children, match, out);
  return out;
};

test('뒤 배경은 목록 화면 모양 그대로, 사진은 전부 모자이크예요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/sections/MembersOnlyGate.jsx', exportName: 'ListBackdrop',
    initialStates: [], api: {},
  });
  try {
    const tree = h.render();
    assert.equal(tree.props['aria-hidden'], 'true', '배경은 화면 읽기에서 빠져야 해요');
    assert.equal(tree.props.inert, '', '배경은 누르거나 초점이 갈 수 없어야 해요');
    assert.equal(findAll(tree, named('MosaicImage')).length, 8);
    assert.equal(findAll(tree, node => node.type === 'img').length, 0, '선명한 사진을 그대로 그리면 안 돼요');
    assert.equal(findAll(tree, named('MosaicText')).length, 8, '이름도 모자이크여야 해요');
    assert.doesNotMatch(textOf(tree), /○/, '이름 글자가 화면에 그대로 남으면 안 돼요');
    assert.match(textOf(tree), /등록 모델 리스트/);
  } finally { await h.close(); }
});

test('모자이크는 작은 캔버스를 픽셀 그대로 키워서 만들어요', async () => {
  const h = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/sections/MembersOnlyGate.jsx', exportName: 'MosaicImage',
    initialStates: [], api: {},
  });
  try {
    const canvas = h.render({ src: '/models/women/w1.webp' });
    assert.equal(canvas.type, 'canvas');
    assert.ok(canvas.props.width <= 12 && canvas.props.height <= 16, '칸이 촘촘하면 얼굴이 읽혀요');
    assert.equal(canvas.props.width * 4, canvas.props.height * 3, '카드와 같은 3:4 비율이어야 해요');
  } finally { await h.close(); }
  const text = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/sections/MembersOnlyGate.jsx', exportName: 'MosaicText',
    initialStates: [], api: {},
  });
  try {
    const canvas = text.render({ text: '김○○' });
    assert.equal(canvas.type, 'canvas');
    assert.ok(canvas.props.width <= 10 && canvas.props.height <= 3, '칸이 촘촘하면 이름이 읽혀요');
    assert.equal(textOf(canvas), '', '이름 글자를 DOM 에 두지 않아요');
  } finally { await text.close(); }
  const css = readFileSync(new URL('../../src/features/facemarket-landing/sections/MembersOnlyGate.module.css', import.meta.url), 'utf8');
  assert.equal(css.match(/image-rendering:\s*pixelated/g)?.length, 2, '사진과 이름 둘 다 픽셀 그대로 키워요');
});

test('비로그인 안내는 실제 등록 모델 목록을 불러오지 않아요', () => {
  // 모자이크를 해도 원본 사진이 브라우저로 내려오면 가린 의미가 없어요. 배경은 예시 모델만 써요.
  const source = readFileSync(new URL('../../src/features/facemarket-landing/sections/MembersOnlyGate.jsx', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /fetchPublicModels|getPublicModels|\/public\/models/);
  assert.match(source, /BROWSE_MODELS/);
});
