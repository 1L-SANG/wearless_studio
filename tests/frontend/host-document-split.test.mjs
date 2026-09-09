/* 호스트별 문서 배급이 한 쌍으로 유지되는지.

   공유 미리보기(og:*)와 <title> 은 정적 head 가 전부고 크롤러는 JS 를 실행하지 않는다.
   그래서 문서를 두 벌 내고(vite rollupOptions.input) Vercel 이 호스트로 갈라 준다
   (vercel.json rewrites 의 has: host). **둘 중 하나만 있으면 프로덕션이 깨진다.**
     · input 만 두면 → facemarket.html 이 아무에게도 안 간다(조용한 실패).
     · rewrite 만 걸면 → facemarket.wearless.kr 루트가 404 다(요란한 실패).
   눈으로는 배포 후에야 보이는 종류라 여기서 잡는다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

const FACEMARKET_HOST = 'facemarket.wearless.kr';
const ADMIN_HOST = 'admin.wearless.kr';

test('facemarket 문서가 존재하고 자기 head 를 갖는다', () => {
  const html = read('facemarket.html');
  assert.match(html, /<title>FaceMarket/);
  assert.match(html, /property="og:title" content="FaceMarket/);
  // 자기 진입점을 물어야 한다. 번들도 갈라졌으므로 셀러 진입점(main.jsx)이면 안 된다 —
  // 그러면 이 문서로 들어온 사람이 셀러 앱을 받는다(랜딩 라우트가 없어 등록으로 튕긴다).
  assert.match(html, /src="\/src\/apps\/facemarket\/main\.jsx"/);
});

test('admin 문서가 존재하고 자기 진입점을 문다', () => {
  const html = read('admin.html');
  assert.match(html, /<title>Wearless 관리자/);
  assert.match(html, /src="\/src\/apps\/admin\/main\.jsx"/);
});

test('vite 가 세 문서를 모두 진입점으로 낸다', () => {
  const config = read('vite.config.js');
  assert.match(config, /rollupOptions/);
  assert.match(config, /htmlEntry\('seller'\)/);
  assert.match(config, /htmlEntry\('facemarket'\)/);
  assert.match(config, /htmlEntry\('admin'\)/);
  // dev 사전번들 스캔 목록에도 들어가야 한다(빠지면 dev 에서 504 → 흰 화면).
  assert.match(config, /entries: \['seller\.html', 'facemarket\.html', 'admin\.html'\]/);
});

/* Vercel 은 rewrite 보다 **파일 시스템을 먼저** 본다. dist 에 index.html 이 있으면 `/`
   요청이 그 파일로 곧장 나가고 host rewrite 가 돌지 않는다 — facemarket 루트가 셀러 문서를
   받아 /create/input 으로 튕겼다(프로덕션 실측). 셀러 문서 이름이 index.html 로 돌아오면
   같은 사고가 재발하므로 여기서 막는다. */
test('루트에 응답할 정적 문서가 없어야 host rewrite 가 돈다', () => {
  assert.throws(() => read('index.html'), /ENOENT/);
  assert.match(read('seller.html'), /<title>Wearless/);
});

test('vercel 이 호스트별로 그 문서를 보낸다(admin·facemarket 먼저, 셀러 폴백)', () => {
  const vercel = JSON.parse(read('vercel.json'));
  const rewrites = vercel.rewrites;

  // 호스트 규칙(has)은 전부 catch-all 폴백보다 앞에 와야 한다.
  const adminRule = rewrites.find((r) => r.destination === '/admin.html');
  assert.deepEqual(adminRule.has, [{ type: 'host', value: ADMIN_HOST }]);

  const facemarketRule = rewrites.find((r) => r.destination === '/facemarket.html');
  assert.deepEqual(facemarketRule.has, [{ type: 'host', value: FACEMARKET_HOST }]);

  // 순서가 중요하다 — 전 경로 catch-all 이 앞에 오면 호스트 규칙에 닿지 못한다.
  const fallback = rewrites.at(-1);
  assert.equal(fallback.destination, '/seller.html');
  assert.equal(fallback.has, undefined);
  assert.ok(rewrites.indexOf(adminRule) < rewrites.length - 1);
  assert.ok(rewrites.indexOf(facemarketRule) < rewrites.length - 1);
});

/* 진입 문서 ↔ 호스트 불일치 가드(2026-09-02 리뷰). Vercel 은 실제로 존재하는 파일 경로에
   rewrite 를 태우지 않으므로 ai.wearless.kr/facemarket.html 이 그대로 열린다. */
test('남의 호스트에서 열린 진입 문서는 자기 호스트로 되돌린다', async () => {
  const host = await import('../../src/lib/host.js');
  assert.equal(typeof host.redirectToOwnDocumentHost, 'function');
});

/* 파비콘도 호스트별로 갈린다(2026-09-08). 구형 사파리는 SVG 파비콘을 안 읽고 /favicon.ico 로
   되돌아가는데, facemarket 호스트는 `/(.*)` rewrite 가 모든 경로를 문서로 보내므로 그 요청이
   HTML 을 받아 아이콘이 빈다(실측: 200 text/html). 그래서 ① 문서에 PNG·ico 폴백을 걸고
   ② 그 호스트 한정 /favicon.ico rewrite 를 catch-all 앞에 둔다. 둘 중 하나만 있으면 다시 빈다. */
test('facemarket 문서는 자기 아이콘을 SVG·PNG·ico 로 건다', () => {
  const html = read('facemarket.html');
  assert.match(html, /rel="icon" type="image\/svg\+xml" href="\/assets\/brand\/facemarket-mark\.svg"/);
  assert.match(html, /rel="icon" type="image\/png"[^>]*href="\/assets\/brand\/facemarket-icon-32\.png"/);
  assert.match(html, /rel="icon" type="image\/x-icon" href="\/assets\/brand\/facemarket-favicon\.ico"/);
  assert.match(html, /rel="apple-touch-icon"[^>]*href="\/assets\/brand\/facemarket-icon-180\.png"/);
  const svg = html.match(/<link[^>]*type="image\/svg\+xml"[^>]*>/)[0];
  assert.match(svg, /sizes="any"/);
  assert.ok(html.indexOf('type="image/png"') < html.indexOf(svg));
  assert.ok(html.indexOf('type="image/x-icon"') < html.indexOf(svg));
});

test('셀러 문서는 Wearless 아이콘 그대로다 — facemarket 아이콘이 새면 안 된다', () => {
  const html = read('seller.html');
  assert.match(html, /rel="icon" type="image\/svg\+xml" href="\/assets\/brand\/logo\.svg"/);
  assert.doesNotMatch(html, /facemarket-(icon|favicon)/);
});

test('facemarket 호스트의 /favicon.ico 만 실제 아이콘으로 가고, 그 호스트 catch-all 보다 앞이다', () => {
  const rewrites = JSON.parse(read('vercel.json')).rewrites;
  const icon = rewrites.find((r) => r.source === '/favicon.ico');
  assert.deepEqual(icon.has, [{ type: 'host', value: FACEMARKET_HOST }]);
  assert.equal(icon.destination, '/assets/brand/facemarket-favicon.ico');

  const docRule = rewrites.findIndex((r) => r.destination === '/facemarket.html');
  assert.ok(rewrites.indexOf(icon) < docRule, '문서 catch-all 이 먼저면 아이콘 요청이 HTML 을 받는다');
});
