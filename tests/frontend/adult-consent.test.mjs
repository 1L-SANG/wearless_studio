import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const login = read('../../src/features/auth/Login.jsx');
const loginCss = read('../../src/features/auth/Login.module.css');
const completion = read('../../src/features/auth/SignupCompletion.jsx');
const completionCss = read('../../src/features/auth/SignupCompletion.module.css');
const signupConsent = read('../../src/lib/signupConsent.js');
const sellerApp = read('../../src/apps/seller/App.jsx');
const facemarketApp = read('../../src/apps/facemarket/App.jsx');
const adminApp = read('../../src/apps/admin/App.jsx');
const modelApply = read('../../src/features/model/ModelApply.jsx');

test('seller login modal splits login and signup — consent lives only in the signup tab', () => {
  // 2026-09-07 오너: 로그인마다 동의는 틀렸다. 동의는 가입 행위 안에서 받는다.
  assert.match(login, /const \[mode, setMode\] = useState\('login'\)/);
  assert.match(login, /const isSignup = IS_SELLER && mode === 'signup';/);
  assert.match(login, /role="tablist"/);
  assert.equal(login.match(/role="tab"/g)?.length, 2);
  // 체크박스는 가입 탭에서만 렌더된다
  assert.match(login, /\{isSignup && \(\s*\n\s*<label className=\{styles\.consent\}>/);
  assert.match(login, /만 19세 이상이며 <a href="\/terms" target="_blank"/);
  assert.match(login, /<a href="\/privacy" target="_blank"/);
  // 체크 전에는 두 소셜 버튼이 잠긴다
  assert.match(login, /const blocked = isSignup && !signupConsent;/);
  assert.equal(login.match(/disabled=\{pending !== null \|\| blocked\}/g)?.length, 2);
  assert.match(login, /isSignup \? 'Google로 가입하기' : 'Google로 계속하기'/);
  assert.match(login, /isSignup \? '카카오로 가입하기' : '카카오로 계속하기'/);
  // 로그인 탭에는 동의가 없고, 신규를 가입 탭으로 보내는 안내만 있다
  assert.match(login, /\{IS_SELLER && mode === 'login' && \(/);
  assert.doesNotMatch(login, /계속하면 서비스 약관에 동의하는 것으로 간주됩니다/);
});

test('checkbox sits on the first text line instead of being eyeballed', () => {
  // 오너 지적(9/8): 체크 위치가 애매하다 → 줄 높이 기준으로 계산해 첫 줄 중앙에 건다.
  for (const css of [loginCss, completionCss]) {
    assert.match(css, /grid-template-columns: 16px 1fr;/);
    assert.match(css, /margin: calc\(\(1\.55em - 16px\) \/ 2\) 0 0;/);
  }
});

test('signup consent survives the OAuth round trip without asking twice', () => {
  assert.match(login, /import \{ markSignupConsent \} from '@\/lib\/signupConsent\.js';/);
  assert.match(login, /if \(isSignup\) markSignupConsent\(\);/);
  assert.match(signupConsent, /sessionStorage/);
  // 가입 탭에서 동의한 사람에게는 완료 화면을 띄우지 않고 조용히 기록만 남긴다
  assert.match(completion, /if \(res\.needsConsent && !res\.accepted && hasFreshSignupConsent\(\)\)/);
  assert.match(completion, /clearSignupConsent\(\);/);
});

test('signup completion continues the login modal instead of taking over the screen', () => {
  assert.match(sellerApp, /import \{ SignupCompletion \} from '@\/features\/auth\/SignupCompletion\.jsx';/);
  assert.match(sellerApp, /<SignupCompletion \/>/);
  assert.doesNotMatch(facemarketApp, /SignupCompletion/);
  assert.doesNotMatch(adminApp, /SignupCompletion/);
  // 버튼은 '가입 완료' 하나뿐 — 로그아웃 버튼은 없고 우측 위 X 가 그 역할을 한다
  assert.match(completion, /revised \? '동의하고 계속하기' : '가입 완료'/);
  assert.equal(completion.match(/<Button /g)?.length, 1);
  assert.match(completion, /<Icon name="x"/);
  assert.match(completion, /className=\{styles\.close\} onClick=\{\(\) => signOut\?\.\(\)\}/);
  assert.match(completion, /aria-label="나가기\(로그아웃\)"/);
  // 로그인 창과 같은 크기의 같은 창 — 기본 Modal(폭 460px)을 쓰고, 내용이 우측에서 넘어온다.
  assert.match(completion, /<Modal>/);
  assert.doesNotMatch(completion, /<Modal (wide|narrow)/);
  assert.doesNotMatch(completion, /onClose=/); // Esc·바깥 클릭으로 닫히면 동의를 건너뛸 수 있다
  assert.match(completionCss, /animation: slideNext/);
  assert.match(completionCss, /@keyframes slideNext \{\s*\n\s*from \{ opacity: 0; transform: translateX\(38px\); \}/);
  assert.match(completionCss, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(completion, /if \(!state\?\.needsConsent\) return null;/);
  assert.match(completion, /만 19세 이상이며, 위 이용약관과 개인정보 처리방침에 동의합니다\./);
});

test('login copy follows the owner-approved lines for each product', () => {
  // 2026-09-08 오너 지정 카피
  assert.match(login, /끝판왕 AI 상세페이지 서비스,<br \/>팔리는 상세페이지를 만드세요\./);
  assert.match(login, /Wearless가 처음이라면\? <button type="button" className=\{styles\.linkBtn\}/);
  assert.match(loginCss, /\.linkBtn \{[^}]*text-decoration: underline;/s);
  // FaceMarket 은 랜딩 히어로(오너 확정)와 같은 결
  assert.match(login, /내 얼굴이 쇼핑몰에서 일하는 곳,<br \/>모델 등록을 시작하세요\./);
  assert.doesNotMatch(login, /마네킹컷 생성을 시작하세요/);
});

test('model application eligibility and attestation use age 19', () => {
  assert.equal(modelApply.match(/만 19세/g)?.length, 2);
  assert.doesNotMatch(modelApply, /만 18세/);
  assert.match(modelApply, /만 19세 이상이며, 제공한 모든 정보가 사실이고 정확함을 확인합니다\./);
});
