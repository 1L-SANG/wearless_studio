import test from 'node:test';
import assert from 'node:assert/strict';

import {
  deriveSimpleAuthUnavailableReason,
  parseIdentityMethods,
} from '../../src/features/model/identityMethodConfig.js';

// 리뷰 IMPORTANT 4: 이전엔 이 파생값이 ModelRegister.jsx 안 모듈 상수(`import.meta.env` 를
// 읽는 즉시 계산)로만 있어서, IdentityMethodStep 에 넘어가는 prop 소비만 테스트할 수 있었다
// (`simpleAuthUnavailableReason` 을 손으로 만들어 넘기는 테스트는 삼항이 뒤집혀도 못 잡는다
// — prop 값은 여전히 테스트가 손으로 넣은 값 그대로이기 때문이다). 이 파일은 그 파생 로직
// 자체를 여러 입력값으로 직접 검증한다.

test('VITE_FM_IDENTITY_METHODS 를 콤마로 나누고 공백을 정리한다', () => {
  assert.deepEqual(parseIdentityMethods('mid,simple_auth'), ['mid', 'simple_auth']);
  assert.deepEqual(parseIdentityMethods(' mid , simple_auth '), ['mid', 'simple_auth']);
  assert.deepEqual(parseIdentityMethods('simple_auth'), ['simple_auth']);
});

test('값이 없으면(undefined/빈 문자열) 기본값 mid 하나로 떨어진다', () => {
  assert.deepEqual(parseIdentityMethods(undefined), ['mid']);
  assert.deepEqual(parseIdentityMethods(''), ['mid']);
  assert.deepEqual(parseIdentityMethods(null), ['mid']);
});

test('빈 조각·중복 콤마는 걸러낸다', () => {
  assert.deepEqual(parseIdentityMethods('mid,,simple_auth,'), ['mid', 'simple_auth']);
  assert.deepEqual(parseIdentityMethods(',,'), ['mid']);
});

// 핵심 계약: 삼항 방향. configUrl 이 있으면 null(막지 않음), 없으면 이유 문자열(막음).
// 방향이 뒤집히면 "설정 있는데 막힘" 또는 "설정 없는데 위젯이 열려 v1.0 경로로 조용히
// 실패" 둘 중 하나가 조용히 배포된다 — 아래 두 테스트가 그 방향을 직접 값으로 고정한다.
test('설정 URL이 있으면 간편인증을 막지 않는다(null)', () => {
  assert.equal(deriveSimpleAuthUnavailableReason('https://cx.example/config.json'), null);
});

test('설정 URL이 없으면(빈 문자열/undefined) 간편인증을 막을 이유 문구를 돌려준다', () => {
  assert.equal(typeof deriveSimpleAuthUnavailableReason(''), 'string');
  assert.match(deriveSimpleAuthUnavailableReason(''), /설정되지 않았어요/);
  assert.match(deriveSimpleAuthUnavailableReason(undefined), /설정되지 않았어요/);
});
