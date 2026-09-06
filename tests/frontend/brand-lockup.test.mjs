/* 브랜드 락업이 도메인마다 맞게 걸리는지.

   로그인 모달은 **세 호스트가 공유하는 한 파일**이다. facemarket 쪽 브랜딩을 손대다가
   셀러(ai.wearless.kr)까지 FaceMarket 로고로 바꿔 버리면, 마네킹컷을 만들러 온 셀러가
   로그인 창에서 남의 제품을 본다 — 그 파일 주석이 "셀러 값은 바꾸지 마라"고 못박아 둔
   실패다. 화면을 눈으로 볼 수 없는 이 테스트가 그 경계를 대신 지킨다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('탭 아이콘은 admin·facemarket 만 FaceMarket 심볼, 셀러는 Wearless 오브', () => {
  for (const doc of ['admin.html', 'facemarket.html']) {
    const html = read(doc);
    assert.match(html, /rel="icon"[^>]*facemarket-mark\.svg/, `${doc} 의 탭 아이콘이 안 바뀌었다`);
  }
  assert.match(read('seller.html'), /rel="icon"[^>]*\/assets\/brand\/logo\.svg/,
    '셀러 탭 아이콘까지 바꾸면 안 된다');
});

test('탭 아이콘은 정사각 마크다 — 가로로 긴 워드마크를 걸면 16px 에서 뭉개진다', () => {
  const mark = read('public/assets/brand/facemarket-mark.svg');
  const viewBox = mark.match(/viewBox="([^"]+)"/)?.[1];
  assert.ok(viewBox, 'viewBox 가 없다');
  const [, , w, h] = viewBox.split(/\s+/).map(Number);
  assert.equal(w, h, `정사각이 아니다: ${viewBox}`);
});

test('로그인 모달은 facemarket·admin 에서만 FaceMarket 로고를 쓴다', () => {
  const source = read('src/features/auth/Login.jsx');
  assert.match(source, /FACEMARKET_LOCKUP\s*=\s*IS_FACEMARKET\s*\|\|\s*IS_ADMIN/,
    '락업 분기 조건이 없다');
  assert.ok(source.includes('facemarket-logo.svg'), 'FaceMarket 로고를 안 쓴다');
});

test('셀러 로그인 락업은 그대로다 — 오브 + wearless 워드마크 + Studio', () => {
  const source = read('src/features/auth/Login.jsx');
  assert.ok(source.includes('/assets/brand/logo.svg'), '셀러 오브가 사라졌다');
  assert.ok(source.includes('/assets/brand/wordmark.png'), '셀러 워드마크가 사라졌다');
  assert.match(source, /IS_FACEMARKET \? 'FaceMarket' : 'Studio'/, "셀러 접미사 'Studio' 가 사라졌다");
});

test('admin 사이드바가 로고를 건다', () => {
  const shell = read('src/features/admin/AdminShell.jsx');
  assert.ok(shell.includes('facemarket-logo.svg'), '사이드바가 아직 텍스트뿐이다');
  assert.match(shell, /alt="FaceMarket"/, '로고에 alt 가 없다');
});
