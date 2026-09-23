/* 없는 주소 화면(src/components/NotFound.jsx). 두 사이트가 같은 문구를 쓴다(2026-09-23 오너 결정)는
   것과, 브랜드·보조 링크만 다르다는 것, 그리고 두 앱의 마지막 경로(catch-all)가 이 화면인지를 고정한다. */
import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server.js';
import { createServer } from 'vite';

const root = new URL('../..', import.meta.url).pathname;
const cacheRoot = mkdtempSync(join(tmpdir(), 'not-found-'));
let vite, NotFound;

before(async () => {
  vite = await createServer({
    configFile: false,
    root,
    cacheDir: join(cacheRoot, 'default'),
    logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } },
    esbuild: { jsx: 'automatic' },
  });
  ({ NotFound } = await vite.ssrLoadModule('/src/components/NotFound.jsx'));
});

after(async () => {
  await vite?.close();
  rmSync(cacheRoot, { recursive: true, force: true });
});

const render = (props) => renderToStaticMarkup(
  React.createElement(StaticRouter, { location: '/nowhere' }, React.createElement(NotFound, props)),
);
const COPY = ['찾는 페이지가 목록에 없어요', '주소가 바뀌었거나 사라진 페이지예요', '홈에서 다른 페이지를 찾아 주세요', '홈으로 가기', 'PAGE NOT FOUND', 'ERROR 404'];

test('두 사이트가 같은 문구를 쓰고 태그의 브랜드 이름만 다르다', () => {
  const wearless = render({ brand: 'WEARLESS' });
  const facemarket = render({ brand: 'FACEMARKET', secondary: { to: '/models', label: '모델 리스트 보기' } });
  for (const text of COPY) {
    assert.ok(wearless.includes(text), `wearless: ${text}`);
    assert.ok(facemarket.includes(text), `facemarket: ${text}`);
  }
  assert.match(wearless, />WEARLESS</);
  assert.match(facemarket, />FACEMARKET</);
  assert.match(wearless, /href="\/"[^>]*>홈으로 가기|홈으로 가기/);
});

test('보조 링크는 넘겨준 것만 그리고, 서버 렌더에서는 이전 화면 버튼이 없다', () => {
  const facemarket = render({ brand: 'FACEMARKET', secondary: { to: '/models', label: '모델 리스트 보기' } });
  assert.match(facemarket, /href="\/models"[^>]*>모델 리스트 보기</);
  assert.doesNotMatch(facemarket, /이전 화면으로/);
  // 서버 렌더(window 없음)에서는 사이트 안 이동 이력을 알 수 없어 뒤로 가기를 그리지 않는다.
  const wearless = render({ brand: 'WEARLESS' });
  assert.doesNotMatch(wearless, /이전 화면으로/);
  assert.doesNotMatch(wearless, /모델 리스트 보기/);
});

test('두 앱의 마지막 경로가 이 화면이고 셀러 쪽은 상단바(ChromeLayout) 안에 있다', () => {
  const seller = readFileSync(join(root, 'src/apps/seller/App.jsx'), 'utf8');
  const facemarket = readFileSync(join(root, 'src/apps/facemarket/App.jsx'), 'utf8');
  assert.match(seller, /<Route element=\{<ChromeLayout \/>\}>\s*<Route path="\*" element=\{<NotFound brand="WEARLESS" \/>\} \/>/);
  assert.match(facemarket, /<Route path="\*" element=\{<NotFound brand="FACEMARKET"/);
  assert.doesNotMatch(seller, /path="\*" element=\{<Navigate/);
  assert.doesNotMatch(facemarket, /path="\*" element=\{<Navigate/);
});
