import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, transformWithEsbuild } from 'vite';

let vite, panelCode, libraryCode, Button, api, DB;
const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
before(async () => {
  const root = new URL('../..', import.meta.url).pathname;
  vite = await createServer({ configFile: false, root, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', resolve: { alias: { '@': `${root}/src` } }, esbuild: { jsx: 'automatic' } });
  ({ Button } = await vite.ssrLoadModule('/src/components/ui.jsx'));
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
  const compile = async (path) => (await transformWithEsbuild(read(path).replace(/^import .*;\n/gm, '').replace(/^export /gm, '').replace('default Library;', ''), path, { jsx: 'transform' })).code;
  panelCode = await compile('src/features/library/PublicationsPanel.jsx');
  libraryCode = await compile('src/features/library/Library.jsx');
});
after(() => vite?.close());
const row = (changes = {}) => ({ publicationId: 'pub1', version: 4, preset: 'smartstore', width: 860,
  sliceCount: 7, createdAt: '2026-10-23T00:00:00Z', expiresAt: null, files: [{ index: 1, url: '/01.jpg' }], ...changes });
const findAll = (tree, predicate) => !tree || typeof tree !== 'object' ? [] : [
  ...(predicate(tree) ? [tree] : []), ...React.Children.toArray(tree.props?.children).flatMap((child) => findAll(child, predicate)),
];
const Modal = ({ children }) => React.createElement('section', null, children);
const styles = new Proxy({}, { get: (_target, key) => key });
function harness({ items = [row()], list, remove = async () => {}, library = false } = {}) {
  const states = [], refs = [], effects = [], toasts = [], calls = [], props = { projectId: 'p', productName: '셔츠', onClose: () => calls.push('close') };
  let si, ri, ei, queued, tree, user = 'u';
  const useAppStore = (selector) => selector({ adoptProject: (id) => calls.push(['adopt', id]) });
  useAppStore.getState = () => ({ userId: user, projectGeneration: 0 });
  const bindings = { React, Button, Modal, styles, publicationStyles: styles, useAppStore,
    useState: (initial) => { const i = si++; if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial;
      return [states[i], (next) => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
    useRef: (initial) => { const i = ri++; refs[i] ||= { current: initial }; return refs[i]; },
    useEffect: (setup, deps) => { const i = ei++, old = effects[i];
      if (!old || !deps || deps.some((value, j) => value !== old.deps?.[j])) queued.push(() => { old?.cleanup?.(); effects[i] = { deps, cleanup: setup() }; }); },
    useAuth: () => ({ session: { user: { id: user } } }), useToast: () => ({ push: (text) => toasts.push(text) }),
    clientEvents: { getAccountGeneration: () => user },
    api: { listDetailPublications: list || (async () => ({ items })), deleteDetailPublication: async (id) => { calls.push(['delete', id]); await remove(id); } },
    buildPublicationHtml: () => '<div>상세</div>', downloadPublicationZip: async () => new Blob(['zip']),
    PublicationsPanel: 'aside', useNavigate: () => (path) => calls.push(['navigate', path]), isMobileDevice: () => false,
    authorizeFlowContinuation() {}, Skeleton: 'div', ErrorState: 'div', tagStyles: {}, HangTag: 'div',
    useQuery: ({ queryKey }) => ({ data: queryKey.length === 1 ? [{ id: 'p', title: '셔츠', status: 'done', updatedAt: '2026-10-23', publicationCount: 2 }] : [], refetch() {} }),
  };
  const Component = new Function(...Object.keys(bindings), `${library ? libraryCode : panelCode}; return ${library ? 'Library' : 'PublicationsPanel'};`)(...Object.values(bindings));
  const render = () => { si = ri = ei = 0; queued = []; tree = Component(props); queued.forEach((effect) => effect()); return tree; };
  return { render, props, calls, toasts, closeModals: () => findAll(render(), (node) => node.type === Modal).forEach((node) => node.props.onClose()),
    async ready() { render(); await Promise.resolve(); await Promise.resolve(); return render(); },
    html: () => renderToStaticMarkup(render()), changeUser: (value) => { user = value; },
    unmount: () => effects.forEach((effect) => effect.cleanup?.()) };
}
test('publication panel loading and empty copy uses paste-code wording', async () => {
  const h = harness({ items: [] });
  assert.match(h.html(), /붙여넣을 코드를 불러오는 중이에요/);
  await h.ready();
  assert.match(h.html(), /만든 코드가 없어요/);
});
test('renders expiry warning within 14 days', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-10-23T00:00:00Z'));
  const h = harness({ items: [row({ expiresAt: '2026-11-06T00:00:00Z' })] }); await h.ready();
  assert.match(h.html(), /class="warning"[^>]*>14일 뒤 지워져요/);
  assert.match(h.html(), /버전 4 · 2026.10.23 · 860px · 조각 7개/);
});
test('expiry and error colors outrank shared modal paragraph styles', () => {
  const shared = read('src/styles/app.css').match(/\.modal p\s*\{([^}]+)\}/);
  assert.ok(shared, 'evaluate the existing shared modal paragraph rule');
  const rules = [...read('src/features/library/PublicationsPanel.module.css').matchAll(/([^{}]+)\{([^{}]+)\}/g)]
    .flatMap(([, selectors, body]) => {
      const color = body.match(/\bcolor:\s*([^;]+);/)?.[1];
      return color ? selectors.split(',').map((selector) => ({ selector: selector.trim().replace(/:global\(([^)]+)\)/g, '$1'), color })) : [];
    });
  rules.push({ selector: '.modal p', color: shared[1].match(/\bcolor:\s*([^;]+);/)[1] });
  const matches = (part, node) => (!/^[a-z]/i.test(part) || part.match(/^[a-z]+/i)[0] === node.tag)
    && [...part.matchAll(/\.([\w-]+)/g)].every(([, cls]) => node.classes.includes(cls));
  const specificity = (selector) => (selector.match(/\.[\w-]+/g) || []).length * 1000
    + (selector.replace(/\.[\w-]+/g, '').match(/\b[a-z]+\b/gi) || []).length;
  const paragraph = (classes) => ({ tag: 'p', classes });
  const ancestor = (cls) => ({ tag: 'div', classes: [cls] });
  for (const [label, nodes] of [
    ['expiry', [paragraph(['warning']), ancestor('panel'), ancestor('modal')]],
    ['panel error', [paragraph([]), ancestor('error'), ancestor('panel'), ancestor('modal')]],
    ['delete error', [paragraph(['error']), ancestor('modal')]],
  ]) {
    const candidates = rules.filter(({ selector }) => {
      const parts = selector.split(/\s+/).reverse();
      if (!matches(parts[0], nodes[0])) return false;
      let at = 1;
      return parts.slice(1).every((part) => {
        while (at < nodes.length && !matches(part, nodes[at])) at++;
        return at++ < nodes.length;
      });
    }).sort((a, b) => specificity(a.selector) - specificity(b.selector));
    assert.equal(candidates.at(-1)?.color, 'var(--accent-error)', label);
  }
});
test('renders subscription keep label when expiresAt null', async () => {
  const h = harness(); await h.ready(); assert.match(h.html(), /구독 중 보관/);
});
test('delete confirm copy present', async () => {
  const h = harness(); await h.ready();
  findAll(h.render(), (node) => node.props?.children === '삭제')[0].props.onClick();
  assert.match(h.html(), /몰에 붙여 둔 상세페이지가 깨져요\. 몰에서 먼저 바꾼 뒤 지우세요\./);
  assert.match(h.html(), />취소<\/button>/); assert.match(h.html(), />지우기<\/button>/);
});
test('deletion locks both modals and removes only the confirmed version', async () => {
  let resolve;
  const h = harness({ items: [row(), row({ publicationId: 'pub2', version: 9 })], remove: () => new Promise((r) => { resolve = r; }) }); await h.ready();
  findAll(h.render(), (node) => node.props?.children === '삭제')[0].props.onClick();
  const pending = findAll(h.render(), (node) => node.props?.children === '지우기')[0].props.onClick();
  h.closeModals(); assert.equal(h.calls.includes('close'), false);
  assert.deepEqual(h.calls, [['delete', 'pub1']]);
  resolve(); await pending;
  assert.doesNotMatch(h.html(), /버전 4 ·/); assert.match(h.html(), /버전 9 ·/);
});
test('late panel lists cannot leak into another project or account', async () => {
  let resolve;
  const h = harness({ list: () => new Promise((r) => { resolve = r; }) }); h.render();
  const old = resolve; h.props.projectId = 'other'; h.changeUser('other'); h.render();
  old({ items: [row()] }); await Promise.resolve();
  assert.doesNotMatch(h.html(), /버전 4 ·/);
});
test('library publication count has a keyboard action separate from project open', async () => {
  const h = harness({ library: true }); const tree = await h.ready();
  const count = findAll(tree, (node) => node.type === 'button' && React.Children.toArray(node.props.children).join('') === '코드 2개')[0];
  assert.ok(count); let stopped = false; count.props.onClick({ stopPropagation: () => { stopped = true; } });
  assert.equal(stopped, true); assert.equal(h.calls.some((call) => Array.isArray(call) && call[0] === 'navigate'), false);
  assert.equal(findAll(h.render(), (node) => node.type === 'aside').length, 1);
  assert.equal(findAll(tree, (node) => node.type === 'button' && node.props.children === '셔츠').length, 0);
  assert.equal(findAll(tree, (node) => node.type === 'div' && node.props.className === 't')[0].props.children, '셔츠');
  findAll(tree, (node) => node.props.className === 'lib-card')[0].props.onClick();
  assert.deepEqual(h.calls.at(-1), ['navigate', '/editor/p']);
});
test('mock library counts only ready publications for each project', async () => {
  const id = DB.library[0].id;
  DB.detailPublications = [row({ projectId: id, status: 'ready' }), row({ projectId: id, status: 'ready' }),
    row({ projectId: id, status: 'pending' }), row({ projectId: id, status: 'deleted' }), row({ projectId: 'other', status: 'ready' })];
  assert.equal((await api.getLibrary())[0].publicationCount, 2);
});
test('account changes immediately hide manual HTML and deletion confirmation', async () => {
  const h = harness(); await h.ready();
  await findAll(h.render(), (node) => node.props?.children === 'HTML 복사')[0].props.onClick();
  findAll(h.render(), (node) => node.props?.children === '삭제')[0].props.onClick();
  assert.match(h.html(), /<textarea/);
  h.changeUser('another');
  const firstFrame = renderToStaticMarkup(h.render());
  assert.doesNotMatch(firstFrame, /<textarea|몰에 붙여 둔 상세페이지가 깨져요/);
});
test('library does not reopen a previous panel after switching accounts back', async () => {
  const h = harness({ library: true }); await h.ready();
  findAll(h.render(), (node) => node.type === 'button' && React.Children.toArray(node.props.children).join('') === '코드 2개')[0].props.onClick({ stopPropagation() {} });
  assert.equal(findAll(h.render(), (node) => node.type === 'aside').length, 1);
  h.changeUser('another'); h.render(); h.changeUser('u');
  assert.equal(findAll(h.render(), (node) => node.type === 'aside').length, 0);
});
