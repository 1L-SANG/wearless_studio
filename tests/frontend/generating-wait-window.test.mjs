/**
 * 생성 대기 창(2026-08-07) — 화면이 서버보다 먼저 포기하지 않게 하는 계약.
 *
 * 배경(2026-08-05 실측 사고): 정상 생성이 242~285초인데 화면 상한이 300초였다. 여유가
 * 15초뿐이라 조금만 느려도 화면이 먼저 포기했고, 그때 "실패" 토스트를 띄우고 콘티보드로
 * 되돌렸다. 서버 잡은 계속 돌아 완성·차감까지 했으므로 사용자에겐 "실패했는데 크레딧은
 * 나갔다"로 보였다. 게다가 다시 누르면 서버가 같은 활성 잡에 합류시켜 또 기다리다 또 튕겼다.
 *
 * 이 파일은 그 두 가지가 되돌아오지 않게 잠근다: ① 대기 상한 ② 타임아웃 ≠ 실패.
 * 모듈이 `@/` alias 를 쓰므로 기존 프론트 테스트와 같이 소스 텍스트로 검증한다.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const httpAdapter = readFileSync(
  new URL('../../src/lib/api/httpAdapter.js', import.meta.url),
  'utf8',
);
const generating = readFileSync(
  new URL('../../src/features/generating/Generating.jsx', import.meta.url),
  'utf8',
);

test('상세페이지 대기 상한은 15분 — 서버 lease 복구(900초)와 같은 창', () => {
  const call = httpAdapter.slice(httpAdapter.indexOf('async generateDetailPage'));
  const body = call.slice(0, call.indexOf('async getProject'));
  assert.match(body, /timeoutMs: 900000/);
  assert.doesNotMatch(body, /timeoutMs: 300000/);
});

test('타임아웃은 code=job_timeout 으로 실패와 구분된다', () => {
  const poll = httpAdapter.slice(
    httpAdapter.indexOf('async function pollJob'),
    httpAdapter.indexOf('export async function uploadPhoto'),
  );
  // 상한 초과 분기에서 code 를 붙여 던진다 — 붙이지 않으면 호출부가 일반 실패와 못 가른다.
  assert.match(poll, /Date\.now\(\) - start > timeoutMs/);
  assert.match(poll, /err\.code = 'job_timeout'/);
});

test('타임아웃이면 콘티보드로 되돌리지 않는다', () => {
  const idx = generating.indexOf("e?.code === 'job_timeout'");
  assert.ok(idx > 0, 'job_timeout 분기가 있어야 한다');

  // 그 분기는 setStillRunning 으로 끝나고 navigate 를 타지 않아야 한다.
  const branch = generating.slice(idx, generating.indexOf('\n', generating.indexOf('return;', idx)));
  assert.match(branch, /setStillRunning\(true\)/);
  assert.doesNotMatch(branch, /navigate\(/);

  // navigate('/create/storyboard') 되돌림은 job_timeout 분기 **뒤에** 남아 있어야 한다
  // (그 외 진짜 실패는 기존대로 콘티로 되돌린다).
  const fallback = generating.indexOf("navigate('/create/storyboard', { replace: true })", idx);
  assert.ok(fallback > idx, '일반 실패 되돌림은 유지되어야 한다');
});

test('대기 화면은 재시도 버튼을 두지 않는다 — 다시 눌러도 같은 잡에 합류할 뿐', () => {
  const start = generating.indexOf('if (stillRunning)');
  // '장면③' 은 위쪽 useState 주석에도 있으므로 패널 시작점 **뒤에서** 찾는다.
  const panel = generating.slice(start, generating.indexOf('if (receipt)', start));
  assert.ok(panel.length > 0, 'stillRunning 패널이 있어야 한다');
  assert.match(panel, /완성됐는지 확인하기/);
  assert.doesNotMatch(panel, /다시 시도|재시도|다시 생성/);
});
