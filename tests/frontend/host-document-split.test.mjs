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

test('facemarket 문서가 존재하고 자기 head 를 갖는다', () => {
  const html = read('facemarket.html');
  assert.match(html, /<title>FaceMarket/);
  assert.match(html, /property="og:title" content="FaceMarket/);
  // 자기 진입점을 물어야 한다. 번들도 갈라졌으므로 셀러 진입점(main.jsx)이면 안 된다 —
  // 그러면 이 문서로 들어온 사람이 셀러 앱을 받는다(랜딩 라우트가 없어 등록으로 튕긴다).
  assert.match(html, /src="\/src\/apps\/facemarket\/main\.jsx"/);
});

test('vite 가 두 문서를 모두 진입점으로 낸다', () => {
  const config = read('vite.config.js');
  assert.match(config, /rollupOptions/);
  assert.match(config, /htmlEntry\('index'\)/);
  assert.match(config, /htmlEntry\('facemarket'\)/);
  // dev 사전번들 스캔 목록에도 들어가야 한다(빠지면 dev 에서 504 → 흰 화면).
  assert.match(config, /entries: \['index\.html', 'facemarket\.html'\]/);
});

test('vercel 이 facemarket 호스트만 그 문서로 보낸다', () => {
  const vercel = JSON.parse(read('vercel.json'));
  const [first, ...rest] = vercel.rewrites;

  assert.equal(first.destination, '/facemarket.html');
  assert.deepEqual(first.has, [{ type: 'host', value: FACEMARKET_HOST }]);

  // 순서가 중요하다 — 전 경로 catch-all 이 앞에 오면 호스트 규칙에 닿지 못한다.
  const fallback = rest.at(-1);
  assert.equal(fallback.destination, '/index.html');
  assert.equal(fallback.has, undefined);
});
