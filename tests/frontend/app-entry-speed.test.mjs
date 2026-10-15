import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const clean = (source) => source.replace(/^import [\s\S]*?;\n/gm, '').replace(/^export /gm, '');

test('seller Google Fonts CSS preloads, switches on load, and has a noscript fallback', () => {
  const html = read('seller.html');
  const liveHead = html.replace(/<noscript>[\s\S]*?<\/noscript>/g, '');
  const fonts = [...liveHead.matchAll(/<link\b[^>]*href="https:\/\/fonts.googleapis.com\/css2[^>]*>/g)];
  assert.equal(fonts.length, 1);
  const link = fonts[0][0];
  assert.match(link, /rel="preload"/);
  assert.match(link, /as="style"/);
  const onload = link.match(/onload="([^"]+)"/)?.[1];
  assert.ok(onload);
  const loaded = { rel: 'preload', onload };
  new Function(onload).call(loaded);
  assert.equal(loaded.rel, 'stylesheet');
  assert.equal(loaded.onload, null);
  const href = link.match(/href="([^"]+)"/)[1];
  assert.ok(html.match(/<noscript>([\s\S]*?)<\/noscript>/)?.[1].includes(`href="${href}"`));
  assert.doesNotMatch(liveHead, /<link\b(?=[^>]*fonts.googleapis.com\/css2)(?=[^>]*rel="stylesheet")[^>]*>/);
  assert.match(liveHead, /rel="preconnect" href="https:\/\/fonts.googleapis.com"/);
  assert.match(liveHead, /rel="preconnect" href="https:\/\/fonts.gstatic.com" crossorigin/);
});

function startSeller(mode, baseUrl) {
  const links = [];
  const source = clean(read('src/apps/seller/main.jsx'))
    .replaceAll('import.meta.env.VITE_API_MODE', JSON.stringify(mode) ?? 'undefined')
    .replaceAll('import.meta.env.VITE_API_BASE_URL', JSON.stringify(baseUrl) ?? 'undefined');
  vm.runInNewContext(source, {
    URL, App: {}, mountApp() {},
    document: { createElement: (tag) => ({ tag }), head: { appendChild: (link) => links.push(link) } },
  });
  return links;
}

test('seller preconnects anonymously only to an absolute HTTP API origin in http mode', () => {
  for (const [mode, baseUrl, expected] of [
    ['http', 'https://api.example.com/v1/', 'https://api.example.com'],
    ['http', 'http://localhost:8000/v1', 'http://localhost:8000'],
    ['mock', 'https://api.example.com', null],
    [undefined, 'https://api.example.com', null],
    ['http', undefined, null], ['http', '', null], ['http', '/api', null],
    ['http', '//api.example.com', null], ['http', 'invalid', null],
    ['http', 'data:text/plain,api', null], ['http', 'ftp://api.example.com', null],
  ]) {
    const links = startSeller(mode, baseUrl);
    assert.equal(links.length, expected ? 1 : 0, `${mode}: ${baseUrl}`);
    if (expected) {
      assert.equal(links[0].rel, 'preconnect');
      assert.equal(links[0].href, expected);
      assert.equal(links[0].crossOrigin, 'anonymous');
    }
  }
});

test('input placeholder preserves the real heading and contains no focusable controls or card copy', async () => {
  const path = 'src/features/product-input/ProductInputFrame.jsx';
  assert.ok(existsSync(new URL(`../../${path}`, import.meta.url)), 'input frame is shared with the placeholder');
  const shell = read('src/features/shell/shell.jsx');
  const head = shell.slice(shell.indexOf('export function PageHead('), shell.indexOf('/* CTA footer'));
  const { code } = await transformWithEsbuild(`${clean(head)}\n${clean(read(path))}`, 'InputFrame.jsx', { jsx: 'transform' });
  const { ProductInputHeading, ProductInputPlaceholder } = new Function('React', `${code}; return { ProductInputHeading, ProductInputPlaceholder };`)(React);
  for (const mobile of [false, true]) {
    const markup = renderToStaticMarkup(React.createElement(ProductInputPlaceholder, { mobile }));
    const heading = renderToStaticMarkup(React.createElement(ProductInputHeading, { mobile }));
    assert.ok(markup.startsWith(`<div class="wizard${mobile ? ' pi-handoff' : ''}" aria-busy="true"`));
    assert.ok(markup.includes(heading));
    assert.match(heading, /의류 이미지를 올려주세요/);
    assert.ok(heading.includes(mobile ? '사진만 올리면 PC에서 이어서 만들어요.' : '사진 몇 장만으로 경험해보세요.<br/>'));
    assert.doesNotMatch(markup, /<(input|button|textarea|select|a)\b|tabindex=/i);
    const card = markup.slice(markup.indexOf('class="merged-card'));
    assert.match(card, /aria-hidden="true"/);
    assert.doesNotMatch(card, />[^<\s][^<]*</);
  }
  const product = read('src/features/product-input/ProductInput.jsx');
  assert.match(product, /<ProductInputHeading mobile=\{mobile\}/);
  assert.match(product.slice(product.indexOf('if (!product || !catalogs) return'), product.indexOf('const mobileEditingLocked')), /<ProductInputPlaceholder mobile=\{mobile\}/);
});

test('first entry stays fixed per pathname, redirects retain it, and later visits use the normal entrance', async () => {
  const path = 'src/features/shell/useDocumentEntry.js';
  assert.ok(existsSync(new URL(`../../${path}`, import.meta.url)));
  let state;
  const useState = (initial) => {
    if (!state) state = typeof initial === 'function' ? initial() : initial;
    return [state, (next) => { state = next; }];
  };
  const useDocumentEntry = new Function('useState', `${clean(read(path))}; return useDocumentEntry;`)(useState);
  for (const initialPath of ['/create/input', '/pricing', '/editor/project', '/']) {
    state = undefined;
    assert.equal(useDocumentEntry(initialPath), true);
    assert.equal(useDocumentEntry(initialPath), true, 'auth, data-entry cleanup and query updates keep the same entrance');
    if (initialPath === '/') {
      assert.equal(useDocumentEntry('/create/input'), true, 'RootRedirect hands first entry to the visible route');
      assert.equal(useDocumentEntry('/create/input'), true);
    }
    assert.equal(useDocumentEntry('/library'), false);
    assert.equal(useDocumentEntry(initialPath), false, 'back navigation never replays first entry');
  }
  const app = read('src/apps/seller/App.jsx');
  assert.match(app, /useDocumentEntry\(pathname\)/);
  assert.equal((app.match(/<ChromeLayout initialEntry=\{initialEntry\}/g) || []).length, 2, 'App owns entry state across shell remounts');
  const chrome = read('src/features/shell/ChromeLayout.jsx');
  assert.match(chrome, /storyboardOwnsEntrance \? '' : initialEntry/);
  assert.match(chrome, /: ' page-enter'/);
  assert.match(chrome, /key=\{pathname\}/);
  const css = read('src/styles/sellerDesign.css');
  assert.match(css, /\.app-main\.page-enter-initial\s*\{[^}]*animation:\s*sellerPageIn 240ms/, 'animate the stable main, so replacing its checking child does not restart the heading');
  const keyframes = css.match(/@keyframes sellerPageIn\s*\{([\s\S]*?)\n\}/)?.[1];
  assert.ok(keyframes);
  assert.match(keyframes, /opacity: 0/);
  assert.match(keyframes, /translateY\(4px\)/);
  assert.doesNotMatch(keyframes, /filter|blur|scale/);
  assert.doesNotMatch(css, /html\[data-entry[^{}]*page-enter-initial|page-enter-initial[^{}]*nth-child/);
  assert.match(css, /prefers-reduced-motion: reduce[\s\S]*page-enter-initial[^}]*animation: none/);
  assert.match(css, /page-enter-initial\.page-enter-landing[^}]*animation: wl-handoff-chrome 100ms/, 'landing retains its existing reduced-motion opacity fade');
  assert.match(read('src/styles/app.css'), /\.app-main\.page-enter > \*\s*\{\s*animation: fxPageIn \.52s/);
});

test('seller main reserves the viewport below the 60px nav without constraining route height', () => {
  const css = read('src/styles/sellerDesign.css');
  const rule = css.match(/\.app-shell > \.app-main\s*\{([^}]+)\}/)?.[1];
  assert.ok(rule);
  assert.match(rule, /min-height:\s*calc\(100vh - 60px\)/);
  assert.match(rule, /min-height:\s*calc\(100svh - 60px\)/);
  assert.doesNotMatch(rule, /(?:^|;)\s*(?:height|overflow):/);
});

test('ChromeLayout keeps the same entrance after landing cleanup and gives storyboard its own entrance', async () => {
  const { code } = await transformWithEsbuild(clean(read('src/features/shell/ChromeLayout.jsx')
    .replace(/^export default .*;$/gm, '')), 'ChromeLayout.jsx', { jsx: 'transform' });
  const hooks = [];
  let cursor = 0;
  let entry = 'landing';
  let entryReads = 0;
  const location = { pathname: '/create/input' };
  const bindings = {
    React,
    document: { documentElement: { getAttribute() { entryReads += 1; return entry; } } },
    useState(initial) {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial;
      return [hooks[index], (value) => { hooks[index] = value; }];
    },
    useRef: () => ({ current: null }), useEffect() {}, useLayoutEffect() {},
    useLocation: () => location, useNavigate: () => () => {}, useAuth: () => ({ session: null }),
    useAppStore: (select) => select({ loadAccount() {}, loadCatalogs() {} }), useToast: () => ({ push() {} }),
    TopNav: 'nav', SiteFooter: 'footer', Outlet: 'outlet',
  };
  const ChromeLayout = new Function(...Object.keys(bindings), `${code}; return ChromeLayout;`)(...Object.values(bindings));
  const main = (initialEntry) => {
    cursor = 0;
    return ChromeLayout({ initialEntry }).props.children.find((child) => child?.props?.className?.startsWith('app-main'));
  };
  assert.equal(main(true).props.className, 'app-main page-enter-initial page-enter-landing');
  entry = null;
  assert.equal(main(true).props.className, 'app-main page-enter-initial page-enter-landing');
  assert.equal(entryReads, 1, '600ms cleanup does not change the class or restart the animation');
  location.pathname = '/pricing';
  assert.equal(main(false).props.className, 'app-main page-enter');
  location.pathname = '/create/storyboard';
  assert.equal(main(true).props.className, 'app-main');
  assert.equal(main(false).props.className, 'app-main');
});
