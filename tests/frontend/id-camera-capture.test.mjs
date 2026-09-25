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

test('R7 capture is manual only and the shutter is accessible', () => {
  assert.doesNotMatch(code, /idCardDetector|setInterval|paused|showManualHint|sampleRef/);
  assert.match(code, /aria-label="촬영하기"/);
  assert.match(code, /주민등록증을 네모 안에 맞추고 촬영해 주세요./);
  assert.doesNotMatch(code, /style=\{/);
});

test('촬영과 앨범 맞추기 화면 모두 다음 단계의 필수 가림을 안내한다', () => {
  assert.match(code, /촬영 후 화면을 터치해서 뒷자리를 가릴 가림막 박스를 만들어 주세요./);
  const fit = readFileSync(
    fileURLToPath(new URL('../../src/features/model/IdGalleryFit.jsx', import.meta.url)), 'utf8');
  assert.match(fit, /다음 화면을 터치해서 뒷자리를 가릴 가림막 박스를 만들어 주세요./);
});

test('프레임 크기가 세션 도중 바뀌어도(회전) 가이드를 다시 계산한다(최종리뷰 I3)', () => {
  // 브라우저는 video 트랙의 실제 프레임 크기가 바뀌면(가장 흔한 경우가 세로→가로 회전)
  // <video> 에 resize 를 쏘지 loadedmetadata 를 다시 쏘지 않는다. 이 리스너가 없으면
  // frameSize 가 낡아 가이드(화면에 그리는 박스)와 capture() 가 실제로 마스킹하는
  // 박스(video.videoWidth/Height 기준)가 어긋난다 — R10a/R13 이 이미 두 번 고친 결함이
  // 세 번째 문으로 들어오는 것이다.
  assert.match(code, /addEventListener\('loadedmetadata'/, 'loadedmetadata 리스너가 있어야 한다');
  assert.match(
    code,
    /addEventListener\('resize'/,
    'resize 리스너가 없으면 회전 뒤 가이드와 실제 마스킹 좌표가 어긋난다',
  );
  assert.match(code, /removeEventListener\('loadedmetadata'/, 'loadedmetadata 리스너를 정리해야 한다');
  assert.match(
    code,
    /removeEventListener\('resize'/,
    'resize 리스너도 같은 클린업에서 정리해야 한다 — 안 하면 언마운트 뒤에도 setFrameSize 가 불릴 수 있다',
  );
});

import { modelComponentHarness, findTree, eventually } from './helpers/facemarketHarness.mjs';

test('수동 셔터는 프레임을 한 번만 인코딩해 로컬 가림 단계에 넘겨요', async () => {
  const h = await modelComponentHarness({ entry: '/src/features/model/IdCameraCapture.jsx', exportName: 'default', initialStates: [true, { width: 1920, height: 1080 }, false], api: {} });
  const captured = [], calls = []; let encode;
  const blob = new Blob(['masked'], { type: 'image/jpeg' });
  try {
    const tree = h.render({ onCaptured: value => captured.push(value), busy: false });
    const video = { videoWidth: 1920, videoHeight: 1080 };
    h.runtime.refs[0].current = video;
    h.runtime.refs[1].current = { getContext: () => ({ drawImage: (...args) => calls.push(['draw', ...args]), fillRect: (...args) => calls.push(['mask', ...args]) }), toBlob: done => { encode = done; } };
    const shutter = findTree(tree, n => n.props['aria-label'] === '촬영하기');
    assert.equal(shutter.props.disabled, false);
    assert.deepEqual(captured, []);
    shutter.props.onClick(); shutter.props.onClick();
    assert.deepEqual(calls.map(c => c[0]), ['draw']);
    assert.deepEqual(calls[0], ['draw', video, 224, 76, 1473, 929, 0, 0, 1473, 929]);
    encode(blob);
    await eventually(() => captured.length === 1, '완성된 사진을 전달해요');
    assert.equal(captured[0], blob);
  } finally { await h.close(); }
});

test('카메라 권한 응답이 화면을 닫은 뒤 도착하면 스트림을 바로 멈춰요', async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  let grant, stopped = 0;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: () => new Promise(resolve => { grant = resolve; }) } } });
  const h = await modelComponentHarness({ entry: '/src/features/model/IdCameraCapture.jsx', exportName: 'default', initialStates: [], api: {} });
  try {
    h.render();
    const cleanup = h.runtime.effects[3]();
    cleanup();
    grant({ getTracks: () => [{ stop: () => { stopped++; } }] });
    await eventually(() => stopped === 1, '늦게 도착한 스트림을 정리해요');
    assert.equal(h.runtime.states[0], false);
  } finally { await h.close(); if (previous) Object.defineProperty(globalThis, 'navigator', previous); else delete globalThis.navigator; }
});
