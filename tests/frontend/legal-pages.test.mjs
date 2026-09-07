import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const pathFor = (name) => fileURLToPath(new URL(name, root));
const read = (name) => readFileSync(pathFor(name), 'utf8');

test('manifest의 모든 공개 법무 문서는 제목으로 시작한다', () => {
  const manifest = JSON.parse(read('public/legal/manifest.json'));

  for (const { slug } of manifest) {
    const pathname = `public/legal/${slug}.md`;
    assert.equal(existsSync(pathFor(pathname)), true, `${pathname} 파일이 필요해요`);
    assert.match(read(pathname).split(/\r?\n/, 1)[0], /^# /, `${pathname} 첫 줄은 제목이어야 해요`);
  }
});

test('셀러 앱은 네 법무 문서를 로그인 없이 여는 라우트를 등록한다', () => {
  const app = read('src/apps/seller/App.jsx');
  const routes = [
    ['terms', 'terms-seller'],
    ['privacy', 'privacy-seller'],
    ['refund', 'refund'],
    ['model-license-terms', 'seller-license-terms'],
  ];

  for (const [path, slug] of routes) {
    assert.match(app, new RegExp(`path=["']${path}["'][^>]+slug=["']${slug}["']`));
  }
});

test('FaceMarket 앱은 다섯 법무 문서를 공개 라우트로 등록한다', () => {
  const app = read('src/apps/facemarket/App.jsx');
  const routes = [
    ['terms', 'terms-model'],
    ['privacy', 'privacy-model'],
    ['license-agreement', 'license-agreement'],
    ['seller-terms', 'seller-license-terms'],
    ['answers', 'answers'],
  ];

  for (const [path, slug] of routes) {
    assert.match(app, new RegExp(`path=["']${path}["'][^>]+slug=["']${slug}["']`));
  }
});

test('사업자 정보는 한 파일에서 올바른 등록번호 형식을 제공한다', () => {
  const companyInfo = read('src/lib/companyInfo.js');
  assert.match(companyInfo, /\b\d{3}-\d{2}-\d{5}\b/);
});

test('법무 화면과 두 푸터는 공용 사업자 정보를 사용한다', () => {
  for (const pathname of [
    'src/features/legal/LegalPage.jsx',
    'src/features/shell/SiteFooter.jsx',
    'src/features/facemarket-landing/sections/FooterSection.jsx',
  ]) {
    assert.match(read(pathname), /@\/lib\/companyInfo\.js/);
  }
});

test('FaceMarket 모델 레이아웃은 얇은 법무 푸터를 함께 렌더한다', () => {
  const layout = read('src/features/facemarket-shell/FacemarketModelLayout.jsx');
  assert.match(layout, /FooterSection/);
  assert.match(layout, /<FooterSection compact\s*\/>/);
});

test('결제 화면은 탭별 환불 고지와 결제 동의 링크를 제공한다', () => {
  const pricing = read('src/features/pricing/Pricing.jsx');
  assert.match(pricing, /구독은 해지할 때까지 매달 자동 결제돼요/);
  assert.match(pricing, /추가 구매 크레딧은 소멸하지 않아요/);
  assert.match(pricing, /결제하면[\s\S]+이용약관[\s\S]+환불 정책[\s\S]+동의하는 것으로 봐요/);
  assert.match(pricing, /to="\/terms"/);
  assert.match(pricing, /to="\/refund"/);
});

test('두 진입 문서는 llms.txt를 대체 문서로 알린다', () => {
  for (const pathname of ['seller.html', 'facemarket.html']) {
    assert.match(read(pathname), /<link rel="alternate" type="text\/plain" href="\/llms\.txt"\s*\/>/);
  }
});
