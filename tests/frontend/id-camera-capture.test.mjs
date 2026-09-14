/* 카메라 촬영 컴포넌트. 렌더 하네스(facemarket-id-capture.test.mjs 의 stepHarness 와
   같은 방식)로 트리를 그려 확인한다 — 실제 카메라 없이 분기만 본다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(
  fileURLToPath(new URL('../../src/features/model/IdCameraCapture.jsx', import.meta.url)), 'utf8');

// 주석 안의 문장은 코드가 아니다 — 아래 매칭들이 파일 상단 설명 주석에 적힌 단어만
// 보고 통과하면(=실제로는 안 그런데 통과) 회귀를 못 잡는다. 블록·라인 주석을 지운
// 코드에 대고만 검증한다.
const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');

test('후면 카메라를 요청한다', () => {
  assert.match(code, /facingMode/, '후면 카메라를 지정해야 신분증을 찍는다');
  assert.match(code, /environment/);
});

test('수동 셔터가 항상 있다 — 자동은 편의이지 관문이 아니다', () => {
  assert.match(code, /수동|직접 찍기|촬영/, '수동 촬영 버튼이 있어야 한다');
  assert.ok(!/disabled=\{[^}]*autoReady/.test(code),
    '자동 판정이 수동 셔터를 막으면 자동이 안 잡히는 사용자가 갇힌다');
});

test('카메라를 못 쓰면 부모에게 알린다 (폴백 경로)', () => {
  assert.match(code, /onUnavailable/, '권한 거부·미지원 시 파일 선택으로 내려갈 수 있어야 한다');
});

test('촬영 결과는 마스킹을 거친다 — 원본 프레임을 넘기지 않는다', () => {
  // JPEG 인코딩(canvas.toBlob('image/jpeg', …))은 burnGuideMask(idDocumentMasking.js,
  // 이미 검증됨) 안에 있다. 이 컴포넌트가 증명해야 할 건 "그 함수에 위임한다"는 것과
  // "원본 스트림/비디오/프레임을 onCaptured 로 그대로 흘려보내지 않는다"는 것뿐이다.
  assert.match(code, /burnGuideMask\(/, '가이드 기준 마스킹(burnGuideMask)에 위임해야 한다');
  assert.ok(!/onCaptured\w*(?:\.current)?\??\.?\(\s*(?:stream|video|frame)\b/.test(code),
    '원본 스트림/비디오 엘리먼트를 그대로 넘기면 안 된다');
});

test('onCaptured 는 마스킹된 blob 하나만 받는다 — {blob,width,height} 객체가 아니다', () => {
  // 이전 계약(브리핑 원안)은 onCaptured({ blob, width, height }) 였다. 정정 1은
  // onCaptured(blob) 하나로 좁혔다 — 마스킹 전 blob 이 부모 state 를 한 틱이라도
  // 거치는 걸 막으려는 것이므로, 인자가 다시 객체로 넓어지면 그 취지가 깨진다.
  // burnGuideMask(...).then(callbackParam => { ... onCaptured(무언가) ... }) 형태에서
  // callbackParam 과 onCaptured 에 실제로 넘어가는 인자가 같은 식별자인지 비교한다.
  const start = code.indexOf('burnGuideMask(');
  assert.ok(start >= 0, 'burnGuideMask 호출을 찾을 수 없다');
  const window_ = code.slice(start, start + 600);
  const thenMatch = window_.match(/\.then\(\s*\(?\s*(\w+)\s*\)?\s*=>/);
  assert.ok(thenMatch, 'burnGuideMask(...).then(blob => …) 콜백을 찾을 수 없다');
  const callMatch = window_.match(/onCaptured\w*(?:\.current)?\??\.?\(([^)]*)\)/);
  assert.ok(callMatch, 'onCaptured 호출을 찾을 수 없다');
  const arg = callMatch[1].trim();
  assert.equal(arg, thenMatch[1],
    `onCaptured 는 마스킹된 blob 하나만 받아야 한다 (받은 인자: "${arg}")`);
});

test('스트림을 반드시 정리한다', () => {
  assert.match(code, /getTracks\(\)[\s\S]{0,80}stop\(\)/,
    '언마운트에서 트랙을 멈추지 않으면 카메라 표시등이 계속 켜져 있다');
});

test('가이드 오버레이는 비디오 전용 래퍼 안에만 있다 — 다른 형제는 밖에 있어야 한다', () => {
  // guideRectPercent 의 퍼센트는 "가장 가까운 position 조상"을 기준으로 해석된다.
  // 힌트·에러 문구·셔터 버튼처럼 높이가 바뀌는 형제가 그 조상 안에 같이 있으면,
  // 그 형제가 나타나거나 사라질 때마다(15초 힌트, 캡처 에러) 조상의 높이가 바뀌어
  // 가이드가 비디오와 어긋난다(모바일 감사에서 실측된 결함). 그래서 비디오와
  // 가이드만 담는 전용 래퍼가 있어야 하고, 셔터 버튼·힌트 문단은 그 밖에 있어야
  // 한다 — 나중에 누가 형제를 래퍼 안으로 다시 옮기면 이 테스트가 잡아야 한다.
  const start = code.indexOf('idCameraViewport');
  assert.ok(start >= 0, 'idCameraViewport 래퍼를 찾을 수 없다');
  const match = code.match(/<div className=\{s\.idCameraViewport\}[^]*?<\/div>/);
  assert.ok(match, '<div className={s.idCameraViewport}>...</div> 래퍼를 찾을 수 없다');
  const wrapper = match[0];
  assert.match(wrapper, /<video/, '래퍼 안에 video 가 있어야 한다');
  assert.match(wrapper, /idCameraGuide/, '래퍼 안에 가이드가 있어야 한다');
  assert.ok(!/직접 찍기/.test(wrapper), '셔터 버튼은 래퍼 밖에 있어야 한다');
  assert.ok(!/idCameraHint/.test(wrapper), '안내 문단은 래퍼 밖에 있어야 한다');
  assert.ok(!/s\.error/.test(wrapper), '캡처 에러 문단은 래퍼 밖에 있어야 한다');
  assert.ok(!/showManualHint/.test(wrapper), '15초 힌트 문단은 래퍼 밖에 있어야 한다');
});
