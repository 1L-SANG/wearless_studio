/* 두 도메인 앱이 서로의 화면을 import 하지 않는지.

   ai.wearless.kr 이 받는 JS 에 모델 등록 화면(생체정보 동의·신분증·얼굴 사진)이 실려 있으면
   안 된다 — 화면에 안 뜨는 것과 파일에 없는 것은 다르다. 그 경계는 딱 두 줄로 무너진다:
   App.jsx 가 모델 화면을 하나 import 하거나, AppFacemarket.jsx 가 셀러 화면을 하나 import
   하면 rollup 이 그 그래프를 통째로 그쪽 진입 청크에 넣는다.

   빌드를 돌려 청크를 뜯는 대신 **소스의 import 문**을 본다: 빌드는 느리고, 어차피 경계를
   깨는 건 사람이 쓰는 import 한 줄이라 여기서 잡는 게 원인에 가깝다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

// 정적 import를 재귀로 따라 CSS까지 확인한다. 간접 공용 import로 새는 경우도 막는다.
function sourceGraph(entry, seen = new Set()) {
  if (seen.has(entry)) return seen;
  seen.add(entry);
  if (!/\.(?:jsx?|css)$/.test(entry)) return seen;
  const source = read(entry);
  const paths = [...source.matchAll(/^(?:import\s+(?:[^;]*?\s+from\s+)?|export\s+[^;]*?\s+from\s+)['"]([^'"]+)['"]/gm)].map((match) => match[1]);
  paths.push(...[...source.matchAll(/(?:import\(\s*|@import\s+(?:url\(\s*)?)['"]([^'"]+)['"]/g)].map((match) => match[1]));
  for (const path of paths) {
    const target = path.startsWith('@/') ? `src/${path.slice(2)}`
      : path.startsWith('.') ? resolve(fileURLToPath(root), dirname(entry), path).slice(fileURLToPath(root).length) : null;
    if (target) sourceGraph(target, seen);
  }
  return seen;
}

test('팔레트 C와 셀러 토큰 덮어쓰기는 FaceMarket 진입 그래프에 들어가지 않는다', () => {
  const seller = sourceGraph('src/apps/seller/main.jsx');
  const facemarket = sourceGraph('src/apps/facemarket/main.jsx');
  for (const file of ['src/styles/sellerDesign.css', 'src/styles/wl-light.css']) {
    assert.ok(seller.has(file), `셀러가 자기 팔레트를 읽는다: ${file}`);
    assert.ok(!facemarket.has(file), `셀러 팔레트가 FaceMarket으로 새었다: ${file}`);
  }
  const overrides = read('src/styles/sellerDesign.css');
  assert.match(overrides, /--link:\s*#3182F6/i);
  assert.match(overrides, /--ring-flow-1:\s*(?:#8FC6F6|var\(--wl-sky\))/i);
  assert.match(read('src/styles/tokens.css'), /--link:\s*#0099ff/i);
  assert.match(read('src/styles/app.css'), /\.orb-bg \.l1[^\n]*animation: orbA 12s/);
  assert.match(read('src/styles/app.css'), /\.orb-bg \.l2[^\n]*animation: orbB 17s/);
  assert.match(read('src/features/facemarket-landing/sections/HeroSection.jsx'), /className="orb-bg"/);
});

/* import 문에 등장하는 경로만 뽑는다 — 주석에 파일명을 적는 건 자유여야 한다
   (이 레포는 주석에 경로를 자주 남긴다). */
function importPaths(source) {
  return [...source.matchAll(/^import\s[^;]*?from\s+'([^']+)';/gm)].map((match) => match[1]);
}

const SELLER_ONLY = [
  '@/features/shell/ChromeLayout.jsx',
  '@/features/product-input/ProductInput.jsx',
  '@/features/mannequin/Mannequin.jsx',
  '@/features/storyboard/Storyboard.jsx',
  '@/features/generating/Generating.jsx',
  '@/features/library/Library.jsx',
  '@/features/editor/lazyEditor.js',
];

test('셀러 앱(App.jsx)은 모델·랜딩 화면을 import 하지 않는다', () => {
  const paths = importPaths(read('src/apps/seller/App.jsx'));
  for (const path of paths) {
    assert.ok(!path.includes('features/model/'), `모델 화면이 셀러 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('facemarket-landing'), `랜딩이 셀러 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('facemarket-shell'), `랜딩 껍데기가 셀러 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('facemarket/modelSectionRoutes'), `모델 라우트가 셀러 번들로 들어온다: ${path}`);
  }
});

test('모델 앱(AppFacemarket.jsx)은 셀러 전용 화면을 import 하지 않는다', () => {
  const paths = importPaths(read('src/apps/facemarket/App.jsx'));
  for (const sellerOnly of SELLER_ONLY) {
    assert.ok(!paths.includes(sellerOnly), `셀러 화면이 모델 번들로 들어온다: ${sellerOnly}`);
  }
});

test('관리자 앱(admin/App.jsx)은 셀러·모델 등록·랜딩 화면을 import 하지 않는다', () => {
  const paths = importPaths(read('src/apps/admin/App.jsx'));
  for (const path of paths) {
    assert.ok(!path.includes('features/model/'), `모델 등록 화면이 admin 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('facemarket-landing'), `랜딩이 admin 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('facemarket-shell'), `랜딩 껍데기가 admin 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('facemarket/modelSectionRoutes'), `모델 라우트가 admin 번들로 들어온다: ${path}`);
    for (const sellerOnly of SELLER_ONLY) {
      assert.ok(path !== sellerOnly, `셀러 화면이 admin 번들로 들어온다: ${path}`);
    }
  }
});

test('진입점 세 개가 각자 자기 앱을 마운트한다', () => {
  assert.match(read('src/apps/seller/main.jsx'), /import App from '\.\/App\.jsx'/);
  assert.match(read('src/apps/facemarket/main.jsx'), /import AppFacemarket from '\.\/App\.jsx'/);
  assert.match(read('src/apps/admin/main.jsx'), /import AppAdmin from '\.\/App\.jsx'/);
  // 부트스트랩은 프로바이더 한 벌(AppProviders.renderApp)을 공유한다. 스타일 조립만
  // admin 이 갈라진다(Tailwind 레이어 때문에) — 그래서 admin 만 mountAdminApp 을 거친다.
  assert.match(read('src/apps/seller/main.jsx'), /from '\.\.\/mountApp\.jsx'/);
  assert.match(read('src/apps/facemarket/main.jsx'), /from '\.\.\/mountApp\.jsx'/);
  assert.match(read('src/apps/admin/main.jsx'), /from '\.\/mountAdminApp\.jsx'/);
  // 문서가 무는 진입점도 서로 달라야 한다.
  assert.match(read('seller.html'), /src="\/src\/apps\/seller\/main\.jsx"/);
  assert.match(read('facemarket.html'), /src="\/src\/apps\/facemarket\/main\.jsx"/);
  assert.match(read('admin.html'), /src="\/src\/apps\/admin\/main\.jsx"/);
});
