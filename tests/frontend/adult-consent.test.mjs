import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const login = read('../../src/features/auth/Login.jsx');
const gate = read('../../src/features/auth/SellerConsentGate.jsx');
const sellerApp = read('../../src/apps/seller/App.jsx');
const facemarketApp = read('../../src/apps/facemarket/App.jsx');
const adminApp = read('../../src/apps/admin/App.jsx');
const modelApply = read('../../src/features/model/ModelApply.jsx');

test('login screen asks for no consent checkbox — consent is a one-time gate, not per login', () => {
  // 2026-09-07 오너 지적: 로그인마다 동의 체크는 말이 안 된다. 체크박스·게이팅 상태를 두지 않는다.
  assert.doesNotMatch(login, /sellerConsent|NEEDS_SELLER_CONSENT|type="checkbox"/);
  // 소셜 버튼 2개 + 로컬 폼 제출 1개 — 셋 다 진행 중 여부로만 잠긴다(동의 상태로 잠그지 않는다).
  assert.equal(login.match(/disabled=\{pending !== null\}/g)?.length, 3);
  // 대신 셀러에게만 "첫 로그인 때 한 번만 확인한다"는 안내 한 줄
  assert.match(login, /const IS_SELLER = !IS_FACEMARKET && !IS_ADMIN;/);
  assert.match(login, /\{IS_SELLER && \(/);
  assert.match(login, /동의를 한 번만 확인해요/);
  assert.match(login, /href="\/terms" target="_blank"/);
  assert.match(login, /href="\/privacy" target="_blank"/);
  assert.doesNotMatch(login, /계속하면 서비스 약관에 동의하는 것으로 간주됩니다/);
});

test('seller consent gate is mounted only in the seller app and records age 19 + both documents', () => {
  assert.match(sellerApp, /import \{ SellerConsentGate \} from '@\/features\/auth\/SellerConsentGate\.jsx';/);
  assert.match(sellerApp, /<SellerConsentGate \/>/);
  assert.doesNotMatch(facemarketApp, /SellerConsentGate/);
  assert.doesNotMatch(adminApp, /SellerConsentGate/);
  // 서버가 버전을 기억한다 — 게이트는 needsConsent 일 때만 그려지고, 닫기 없이 로그아웃만 준다.
  assert.match(gate, /if \(!state\?\.needsConsent\) return null;/);
  assert.match(gate, /<Modal narrow>/);
  assert.match(gate, /signOut\?\.\(\)/);
  assert.match(gate, /만 19세 이상이며, 위 이용약관과 개인정보 처리방침에 동의합니다\./);
  assert.match(gate, /WEARLESS_LEGAL_URLS\.terms/);
  assert.match(gate, /WEARLESS_LEGAL_URLS\.privacy/);
  // 게이트가 보여준 버전을 그대로 보낸다(서버가 현재 버전과 대조)
  assert.match(gate, /acceptSellerConsent\(\{ termsVersion: required\.terms, privacyVersion: required\.privacy \}\)/);
});

test('model application eligibility and attestation use age 19', () => {
  assert.equal(modelApply.match(/만 19세/g)?.length, 2);
  assert.doesNotMatch(modelApply, /만 18세/);
  assert.match(modelApply, /만 19세 이상이며, 제공한 모든 정보가 사실이고 정확함을 확인합니다\./);
});
