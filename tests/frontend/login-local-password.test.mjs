import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(
  new URL('../../src/features/auth/Login.jsx', import.meta.url),
  'utf8',
);

test('login uses the shared auth provider for password sign-in', () => {
  assert.match(src, /signInWithPassword/);
  assert.doesNotMatch(src, /supabase\.auth\./);
});

test('local QA remains available independently of the temporary seller review switch', () => {
  assert.match(src, /VITE_SUPABASE_URL/);
  assert.match(src, /127\.0\.0\.1|localhost/);
  assert.match(src, /IS_LOCAL_SUPABASE \|\| \(IS_SELLER && PG_REVIEW_LOGIN_ENABLED\)/);
});
