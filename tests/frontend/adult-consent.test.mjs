import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const login = read('../../src/features/auth/Login.jsx');
const modelApply = read('../../src/features/model/ModelApply.jsx');

test('seller OAuth requires explicit age, terms, and privacy consent', () => {
  assert.match(login, /const \[sellerConsent, setSellerConsent\] = useState\(false\)/);
  assert.match(login, /만 19세 이상이며/);
  assert.match(login, /href="\/terms" target="_blank"/);
  assert.match(login, /href="\/privacy" target="_blank"/);
  assert.equal(login.match(/disabled=\{pending !== null \|\| \(!IS_FACEMARKET && !sellerConsent\)\}/g)?.length, 2);
  assert.doesNotMatch(login, /계속하면 서비스 약관에 동의하는 것으로 간주됩니다/);
});

test('model application eligibility and attestation use age 19', () => {
  assert.equal(modelApply.match(/만 19세/g)?.length, 2);
  assert.doesNotMatch(modelApply, /만 18세/);
  assert.match(modelApply, /만 19세 이상이며, 제공한 모든 정보가 사실이고 정확함을 확인합니다\./);
});
