import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(
  new URL('../../src/features/shell/shell.jsx', import.meta.url),
  'utf8',
);

test('profile menu offers a 모델 등록 entry that navigates to /model/register', () => {
  // a profile-item button whose onClick navigates to /model/register, labelled 모델 등록
  assert.match(
    source,
    /className="profile-item"[^]*?navigate\('\/model\/register'\);[^]*?모델 등록/,
  );
});

test('profile menu still closes on the 모델 등록 navigation', () => {
  // matches the existing menu-item pattern: setOpen(false) before navigate
  assert.match(
    source,
    /setOpen\(false\); navigate\('\/model\/register'\);/,
  );
});
