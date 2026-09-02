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

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

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
  const paths = importPaths(read('src/App.jsx'));
  for (const path of paths) {
    assert.ok(!path.includes('features/model/'), `모델 화면이 셀러 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('facemarket-landing'), `랜딩이 셀러 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('facemarket-shell'), `랜딩 껍데기가 셀러 번들로 들어온다: ${path}`);
    assert.ok(!path.includes('routes/modelSectionRoutes'), `모델 라우트가 셀러 번들로 들어온다: ${path}`);
  }
});

test('모델 앱(AppFacemarket.jsx)은 셀러 전용 화면을 import 하지 않는다', () => {
  const paths = importPaths(read('src/AppFacemarket.jsx'));
  for (const sellerOnly of SELLER_ONLY) {
    assert.ok(!paths.includes(sellerOnly), `셀러 화면이 모델 번들로 들어온다: ${sellerOnly}`);
  }
});

test('진입점 두 개가 각자 자기 앱을 마운트한다', () => {
  assert.match(read('src/main.jsx'), /import App from '@\/App\.jsx'/);
  assert.match(read('src/mainFacemarket.jsx'), /import AppFacemarket from '@\/AppFacemarket\.jsx'/);
  // 부트스트랩은 한 벌이어야 한다 — 두 진입점이 같은 mountApp 을 쓴다.
  for (const entry of ['src/main.jsx', 'src/mainFacemarket.jsx']) {
    assert.match(read(entry), /from '@\/mountApp\.jsx'/, entry);
  }
  // 문서가 무는 진입점도 서로 달라야 한다.
  assert.match(read('index.html'), /src="\/src\/main\.jsx"/);
  assert.match(read('facemarket.html'), /src="\/src\/mainFacemarket\.jsx"/);
});
