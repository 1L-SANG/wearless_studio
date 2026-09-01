import test from 'node:test';
import assert from 'node:assert/strict';

import { facemarketRootTarget } from '../../src/features/facemarket-landing/facemarketRootTarget.js';

// 소스에 진짜 제어문자를 박아두면 편집기·린터가 조용히 지운다. 코드로 만든다.
const TAB = String.fromCharCode(9);
const NEWLINE = String.fromCharCode(10);

test('복귀 경로가 없으면 랜딩을 그린다', () => {
  assert.equal(facemarketRootTarget(null), null);
  assert.equal(facemarketRootTarget(undefined), null);
  assert.equal(facemarketRootTarget(''), null);
  assert.equal(facemarketRootTarget('   '), null);
});

test('facemarket 도메인의 화면이면 그리로 보낸다', () => {
  assert.equal(facemarketRootTarget('/model/register'), '/model/register');
  assert.equal(facemarketRootTarget('/model/license'), '/model/license');
  assert.equal(facemarketRootTarget('  /model  '), '/model');
  assert.equal(facemarketRootTarget('/verify/abc-123'), '/verify/abc-123');
});

test('루트 자기 자신이면 랜딩을 그린다 — 리다이렉트 루프 금지', () => {
  assert.equal(facemarketRootTarget('/'), null);
});

test('앱 밖으로 튀는 값은 무시한다', () => {
  // sessionStorage 는 같은 오리진의 다른 스크립트도 쓸 수 있다. 여기서 막지 않으면
  // 로그인 복귀가 외부 사이트로 향하는 통로가 된다.
  assert.equal(facemarketRootTarget('https://example.com/phish'), null);
  assert.equal(facemarketRootTarget('//example.com/phish'), null);
  assert.equal(facemarketRootTarget('model/register'), null);
  assert.equal(facemarketRootTarget('javascript:alert(1)'), null);
  assert.equal(facemarketRootTarget(42), null);
});

test('역슬래시로 위장한 프로토콜 상대 URL 도 막는다', () => {
  // URL 파서는 http(s) 에서 역슬래시를 슬래시로 읽는다 — '/\evil.com' 은 '//evil.com' 이다.
  assert.equal(facemarketRootTarget('/\\evil.com'), null);
  assert.equal(facemarketRootTarget('/\\/evil.com'), null);
  assert.equal(facemarketRootTarget('\\\\evil.com'), null);
  assert.equal(facemarketRootTarget('/model\\..\\evil.com'), null);
});

test('인코딩으로 슬래시를 숨긴 값도 막는다', () => {
  assert.equal(facemarketRootTarget('/%2f%2fevil.com'), null);
  assert.equal(facemarketRootTarget('/%2F%2Fevil.com'), null);
  assert.equal(facemarketRootTarget('/%5Cevil.com'), null);
});

test('제어문자가 섞인 값은 통째로 버린다', () => {
  // URL 파서가 탭·개행을 지우고 나면 우리가 검사한 문자열과 실제 이동 경로가 달라진다.
  assert.equal(facemarketRootTarget('/' + TAB + '/evil.com'), null);
  assert.equal(facemarketRootTarget('/' + NEWLINE + '/evil.com'), null);
  assert.equal(facemarketRootTarget('/model' + NEWLINE + '/register'), null);
});

test('말도 안 되게 긴 값은 판정하지 않고 버린다', () => {
  assert.equal(facemarketRootTarget('/model/' + 'a'.repeat(600)), null);
  // 상한 아래의 평범한 경로는 그대로 지난다 — 길이 검사가 정상 경로를 먹지 않게.
  const ordinary = '/model/' + 'a'.repeat(400);
  assert.equal(facemarketRootTarget(ordinary), ordinary);
});

test('쿼리·해시가 붙어도 화이트리스트 판정은 경로로 한다', () => {
  // openLogin 은 '/model/register' 같은 값을 심는다. 하드닝이 정상 경로를 막으면 안 된다.
  assert.equal(facemarketRootTarget('/model/register?step=2'), '/model/register?step=2');
  assert.equal(facemarketRootTarget('/model/license#issue'), '/model/license#issue');
});

test('셀러 스튜디오 경로는 통과시키지 않는다 — 등록 전용 도메인이다', () => {
  // 플래그를 심는 쪽에는 도메인 가드가 없다. shell.jsx 의 TopNav 로그인은 '/create/input',
  // ProductInput 은 '/create/storyboard', Editor 는 '/editor/:id' 를 심는데 그 TopNav 는
  // facemarket 에서도 렌더된다. 통과시키면 모델 등록하러 온 사람에게 상품 입력·편집기가 뜬다.
  assert.equal(facemarketRootTarget('/create/input'), null);
  assert.equal(facemarketRootTarget('/create/storyboard'), null);
  assert.equal(facemarketRootTarget('/editor/abc-123'), null);
  assert.equal(facemarketRootTarget('/library'), null);
  assert.equal(facemarketRootTarget('/pricing'), null);
});

test('접두사만 겹치는 경로는 화이트리스트를 통과하지 못한다', () => {
  // '/model' 로 시작한다고 다 통과시키면 '/models-evil' 같은 값이 새 라우트를 타고 들어온다.
  assert.equal(facemarketRootTarget('/models-evil'), null);
  assert.equal(facemarketRootTarget('/modelx'), null);
  assert.equal(facemarketRootTarget('/verifying'), null);
  // 경계값: 뿌리 자신과 그 하위는 통과한다.
  assert.equal(facemarketRootTarget('/model'), '/model');
  assert.equal(facemarketRootTarget('/model/'), '/model/');
});
