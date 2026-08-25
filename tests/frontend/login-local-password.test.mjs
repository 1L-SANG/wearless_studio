import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(
  new URL('../../src/features/auth/Login.jsx', import.meta.url),
  'utf8',
);

test('login offers a local-only email/password path via signInWithPassword', () => {
  assert.match(src, /signInWithPassword/);
});

test('the email/password form is gated to local supabase (127.0.0.1 / localhost)', () => {
  // only shown when VITE_SUPABASE_URL points at a local supabase — never in prod
  assert.match(src, /VITE_SUPABASE_URL/);
  assert.match(src, /127\.0\.0\.1|localhost/);
  // the form render is guarded by the local flag
  assert.match(src, /IS_LOCAL_SUPABASE &&/);
});
