/* 관리자 출처 추적 화면(2026-09-26).

   이 화면에서 가장 큰 사고는 '유사도' 후보를 '확정'처럼 보여 주는 것이다 — 담당자가 엉뚱한
   셀러에게 연락하게 된다. 신뢰도 문구와 라우트·API 배선을 여기서 잠근다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { findTree } from './helpers/facemarketHarness.mjs';
import {
  TRACE_MAX_BYTES, confidenceView, evidenceText, licenseStatusLabel, targetLabel,
  validateTraceFile, watermarkView,
} from '../../src/features/admin/adminTrace.js';

const root = new URL('../../', import.meta.url);
const read = name => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');
const text = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node) ? node.map(text).join(' ') : typeof node === 'object' ? text(node.props?.children) : String(node);

test('워터마크 일치만 확정 문구를 쓰고, 유사도는 눈으로 확인하라고 말한다', () => {
  assert.equal(confidenceView('high').label, '워터마크 일치');
  assert.match(confidenceView('medium').hint, /눈으로 대조/);
  assert.match(confidenceView('low').hint, /참고만/);
  assert.equal(confidenceView('???').label, '유사도 참고'); // 모르는 값은 가장 약한 쪽으로
  for (const level of ['medium', 'low']) {
    assert.doesNotMatch(confidenceView(level).label, /일치|확정/);
  }
});

test('워터마크 판독 결과 세 갈래', () => {
  assert.match(watermarkView({ status: 'matched', code: '2468ace1', votes: 3 }).title, /2468ace1/);
  assert.match(watermarkView({ status: 'unregistered', code: 'deadbeef' }).title, /원장에 없어요/);
  assert.equal(watermarkView({ status: 'not_found' }).tone, 'none');
  assert.equal(watermarkView(null).tone, 'none');
});

test('근거 문구 — 워터마크·구간·컷을 구분한다', () => {
  assert.equal(evidenceText({ watermark: true, phashDistance: 0, matchedKind: 'strip', region: { y0: 900, y1: 1700 } }),
    '워터마크 + 페이지 구간 900~1700px · 차이 0/64');
  assert.equal(evidenceText({ watermark: false, phashDistance: 6, matchedKind: 'cut' }), '생성 컷 · 차이 6/64');
  assert.equal(evidenceText({ watermark: true, phashDistance: null }), '워터마크');
  assert.equal(licenseStatusLabel('deleted'), '삭제됨');
  assert.equal(targetLabel({ target: 'publication', publicationKind: 'zip' }), '배포본 · ZIP');
  assert.equal(targetLabel({ target: 'cut' }), '생성 컷');
});

test('업로드 전 거르기 — 서버 상한과 같다', () => {
  assert.equal(TRACE_MAX_BYTES, 60 * 1024 * 1024);
  assert.equal(validateTraceFile(null), '이미지 파일을 골라 주세요.');
  assert.match(validateTraceFile({ type: 'application/pdf', size: 10 }), /이미지 파일만/);
  assert.match(validateTraceFile({ type: 'image/jpeg', size: TRACE_MAX_BYTES + 1 }), /60MB/);
  assert.equal(validateTraceFile({ type: 'image/png', size: 1234 }), '');
});

test('라우트·내비·API 배선', () => {
  const app = read('src/apps/admin/App.jsx');
  assert.ok(app.includes('path="trace" element={<AdminTrace />}'));
  assert.ok(read('src/features/admin/AdminShell.jsx').includes("{ to: '/trace', label: '출처 추적'"));
  const api = read('src/lib/api/facemarket.js');
  const fn = api.split('export async function adminTraceImage(')[1].split('\n}\n')[0];
  assert.ok(fn.includes("form.append('image'"));
  assert.ok(fn.includes("_authFetch('/v1/facemarket/admin/trace', { method: 'POST', body: form })"));
});

async function harness(api) {
  const key = `__adminTrace${Math.random().toString(36).slice(2)}`;
  const runtime = { api, slots: [], index: 0 };
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({ configFile: false, logLevel: 'silent', root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true, watch: null }, ssr: { noExternal: true }, esbuild: { jsx: 'automatic' }, appType: 'custom',
    plugins: [{ name: 'admin-trace-test', enforce: 'pre', resolveId(id) {
      if (id === 'react') return '\0t-react';
      if (['react/jsx-runtime', 'react/jsx-dev-runtime'].includes(id)) return '\0t-jsx';
      if (id === '@/lib/api/facemarket.js') return '\0t-api';
      if (id.startsWith('@/')) return new URL(`../../src/${id.slice(2)}`, import.meta.url).pathname;
    }, load(id) {
      if (id === '\0t-jsx') return 'export const jsx=(type,props,key)=>typeof type===\'function\'?{...type(props),key}:{type,props,key}; export const jsxs=jsx; export const jsxDEV=jsx; export const Fragment=\'frag\';';
      if (id === '\0t-react') return `const r=${access};
        export const useState=initial=>{const i=r.index++;if(!(i in r.slots))r.slots[i]=typeof initial==='function'?initial():initial;return[r.slots[i],v=>{r.slots[i]=typeof v==='function'?v(r.slots[i]):v;}];};
        export const useRef=initial=>{const i=r.index++;return r.slots[i]??={current:initial};};`;
      if (id === '\0t-api') return `export const adminTraceImage=(...args)=>${access}.api.adminTraceImage(...args);`;
    }}],
  });
  const module = await server.ssrLoadModule('/src/features/admin/AdminTrace.jsx');
  const render = () => { runtime.index = 0; return module.AdminTrace(); };
  return { render, close: async () => { await server.close(); delete globalThis[key]; } };
}

test('업로드 → 추적 결과를 신뢰도 배지와 마스킹된 셀러로 보여준다', async () => {
  const calls = [];
  const file = { name: 'found.jpg', type: 'image/jpeg', size: 2048 };
  const h = await harness({ adminTraceImage: async f => { calls.push(f); return {
    image: { width: 860, height: 1000 }, stats: { fingerprintsScanned: 64, elapsedMs: 120 },
    watermark: { status: 'matched', code: '2468ace1', votes: 3 },
    candidates: [{ target: 'publication', publicationKind: 'long_png', confidence: 'high', publicationId: 'pub-1',
      projectTitle: '니트 상세', createdAt: '2026-09-26T12:00:00+00:00',
      seller: { id: 'seller-1', emailMasked: 'se***@example.com', nameMasked: '김*러' },
      model: { displayName: '김*연' }, license: { id: 'lic-1', status: 'active' },
      evidence: { watermark: true, phashDistance: 0, matchedKind: 'strip', region: { y0: 0, y1: 800 } },
      verifyUrl: 'https://ai.wearless.kr/verify/p/pub-1' }],
  }; } });
  try {
    let tree = h.render();
    const input = findTree(tree, n => n.type === 'input' && n.props?.type === 'file');
    input.props.onChange({ target: { files: [file] } });
    tree = h.render();
    const go = findTree(tree, n => n.type === 'button' && text(n) === '추적하기');
    await go.props.onClick();
    tree = h.render();
    assert.deepEqual(calls, [file]);
    const shown = text(tree);
    assert.match(shown, /워터마크 확인 · 코드 2468ace1/);
    assert.match(shown, /워터마크 일치/);
    assert.match(shown, /se\*\*\*@example\.com/);
    assert.match(shown, /김\*연/);
    const link = findTree(tree, n => n.type === 'a');
    assert.equal(link.props.href, 'https://ai.wearless.kr/verify/p/pub-1');
  } finally { await h.close(); }
});

test('서버 오류는 알림으로, 파일 없으면 버튼이 잠긴다', async () => {
  const h = await harness({ adminTraceImage: async () => { throw new Error('관리자만 가능해요.'); } });
  try {
    let tree = h.render();
    assert.equal(findTree(tree, n => n.type === 'button' && text(n) === '추적하기').props.disabled, true);
    findTree(tree, n => n.type === 'input' && n.props?.type === 'file').props.onChange({ target: { files: [{ name: 'a.png', type: 'image/png', size: 10 }] } });
    tree = h.render();
    await findTree(tree, n => n.type === 'button' && text(n) === '추적하기').props.onClick();
    tree = h.render();
    assert.equal(text(findTree(tree, n => n.props?.role === 'alert')), '관리자만 가능해요.');
  } finally { await h.close(); }
});
