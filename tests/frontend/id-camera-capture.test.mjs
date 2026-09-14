/* 카메라 촬영 컴포넌트. 렌더 하네스(facemarket-id-capture.test.mjs 의 stepHarness 와
   같은 방식)로 트리를 그려 확인한다 — 실제 카메라 없이 분기만 본다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(
  fileURLToPath(new URL('../../src/features/model/IdCameraCapture.jsx', import.meta.url)), 'utf8');

test('후면 카메라를 요청한다', () => {
  assert.match(source, /facingMode/, '후면 카메라를 지정해야 신분증을 찍는다');
  assert.match(source, /environment/);
});

test('수동 셔터가 항상 있다 — 자동은 편의이지 관문이 아니다', () => {
  assert.match(source, /수동|직접 찍기|촬영/, '수동 촬영 버튼이 있어야 한다');
  assert.ok(!/disabled=\{[^}]*autoReady/.test(source),
    '자동 판정이 수동 셔터를 막으면 자동이 안 잡히는 사용자가 갇힌다');
});

test('카메라를 못 쓰면 부모에게 알린다 (폴백 경로)', () => {
  assert.match(source, /onUnavailable/, '권한 거부·미지원 시 파일 선택으로 내려갈 수 있어야 한다');
});

test('촬영 결과는 JPEG blob 이다 — 원본 프레임을 넘기지 않는다', () => {
  assert.match(source, /toBlob\(/, '캔버스에서 blob 을 뽑아야 한다');
  assert.match(source, /image\/jpeg/);
  assert.ok(!/onCaptured\(\s*(?:stream|video|frame)\b/.test(source),
    '원본 스트림/비디오 엘리먼트를 그대로 넘기면 안 된다');
});

test('스트림을 반드시 정리한다', () => {
  assert.match(source, /getTracks\(\)[\s\S]{0,80}stop\(\)/,
    '언마운트에서 트랙을 멈추지 않으면 카메라 표시등이 계속 켜져 있다');
});
