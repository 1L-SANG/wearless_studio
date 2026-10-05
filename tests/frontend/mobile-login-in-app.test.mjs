import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React, { useState, useRef, useEffect } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';

const source = readFileSync(new URL('../../src/features/auth/Login.jsx', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '').replace(/^export /gm, '')
  .replace(/import\.meta\.env\.VITE_SUPABASE_URL/g, "'https://example.test'");
const { code } = await transformWithEsbuild(source, 'Login.jsx', { jsx: 'transform' });
function renderLogin({ facemarket = false, admin = false, ua = '' } = {}) {
  const origin = facemarket ? 'https://facemarket.wearless.kr' : 'https://ai.wearless.kr';
  const path = facemarket ? '/model/register' : '/create/input';
  const bindings = {
    React, useState, useRef, useEffect, IS_FACEMARKET: facemarket, IS_ADMIN: admin,
    PG_REVIEW_LOGIN_ENABLED: false, styles: new Proxy({}, { get: (_, name) => name }),
    useAuth: () => ({ session: null }), readPostLogin: () => path,
    navigator: { userAgent: ua }, window: { location: { origin, pathname: path, search: '' } },
    Icon: () => null, Modal: ({ children }) => React.createElement('div', null, children),
  };
  const LoginGate = new Function(...Object.keys(bindings), `${code}; return LoginGate;`)(...Object.values(bindings));
  return { html: renderToStaticMarkup(React.createElement(LoginGate)), origin, path };
}
for (const facemarket of [false, true]) {
  for (const ua of ['iPhone KAKAOTALK', 'Android Instagram']) {
    test(`${facemarket ? 'FaceMarket' : 'seller'} ${ua} offers Kakao first and Google-blocked guidance`, () => {
      const { html, origin, path } = renderLogin({ facemarket, ua });
      assert.ok(html.indexOf('카카오로 계속하기') < html.indexOf('Google로 계속하기'));
      assert.ok(html.includes('구글 로그인이 막혀 있어요.'));
      if (ua.includes('KAKAOTALK')) {
        assert.ok(html.includes('다른 브라우저로 열기'));
        assert.ok(html.includes(`kakaotalk://web/openExternal?url=${encodeURIComponent(origin + path)}`));
      } else assert.ok(!html.includes('kakaotalk://web/openExternal'));
    });
  }
  test(`${facemarket ? 'FaceMarket' : 'seller'} regular browser keeps Google first without in-app hint`, () => {
    const { html } = renderLogin({ facemarket, ua: 'iPhone Safari' });
    assert.ok(html.indexOf('Google로 계속하기') < html.indexOf('카카오로 계속하기'));
    assert.ok(!html.includes('구글 로그인이 막혀 있어요.'));
  });
}
test('admin in-app login remains unchanged', () => {
  const { html } = renderLogin({ admin: true, ua: 'KAKAOTALK' });
  assert.ok(!html.includes('구글 로그인이 막혀 있어요.'));
});
