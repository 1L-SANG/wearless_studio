import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server.js';
import { Routes, createRoutesFromChildren, matchRoutes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';

const root = new URL('../../', import.meta.url);
const pathFor = (name) => fileURLToPath(new URL(name, root));
const read = (name) => readFileSync(pathFor(name), 'utf8');

let vite;
before(async () => {
  vite = await createServer({
    configFile: false,
    root: pathFor('.'),
    logLevel: 'silent',
    server: { middlewareMode: true },
    resolve: { alias: { '@': pathFor('src') } },
    esbuild: { jsx: 'automatic' },
    define: {
      'import.meta.env.VITE_SUPABASE_URL': JSON.stringify('https://legal-test.supabase.co'),
      'import.meta.env.VITE_SUPABASE_ANON_KEY': JSON.stringify('legal-test-public-key'),
    },
    plugins: [{
      name: 'capture-legal-redirect-effect',
      enforce: 'pre',
      transform(source, id) {
        if (!id.endsWith('/LegalRedirect.jsx')) return;
        return source.replace("from 'react'", "from 'virtual:legal-effect'");
      },
      resolveId(id) { if (id === 'virtual:legal-effect') return '\0legal-effect'; },
      load(id) {
        if (id === '\0legal-effect') return 'export const effects = []; export const useEffect = (effect) => effects.push(effect);';
      },
    }],
  });
});
after(async () => { await vite?.close(); });
const load = (pathname) => vite.ssrLoadModule(`/${pathname}`);
const render = (element, pathname = '/') => renderToStaticMarkup(
  React.createElement(StaticRouter, { location: pathname }, element),
);
const hrefs = (html) => [...html.matchAll(/<a\b[^>]*href="([^"]+)"/g)].map((match) => match[1]);

const LEGAL_PATHS = ['/terms', '/privacy', '/refund', '/model-license-terms'];

async function registeredRoutes(appPath) {
  const { default: App } = await load(appPath);
  let routes;
  function CaptureRoutes() {
    const tree = App();
    const routeTree = tree.type === Routes ? tree
      : React.Children.toArray(tree.props.children).find((child) => child.type === Routes);
    routes = createRoutesFromChildren(routeTree.props.children);
    return null;
  }
  render(React.createElement(CaptureRoutes));
  return routes;
}

test('manifest의 모든 공개 법무 문서는 제목으로 시작한다', () => {
  const manifest = JSON.parse(read('public/legal/manifest.json'));

  for (const { slug } of manifest) {
    const pathname = `public/legal/${slug}.md`;
    assert.equal(existsSync(pathFor(pathname)), true, `${pathname} 파일이 필요해요`);
    assert.match(read(pathname).split(/\r?\n/, 1)[0], /^# /, `${pathname} 첫 줄은 제목이어야 해요`);
  }
});

test('셀러 공개 법무 경로는 로그인 가드 없이 대표 도메인 이동 화면을 연다', async () => {
  const routes = await registeredRoutes('src/apps/seller/App.jsx');
  const { LegalRedirect } = await load('src/features/legal/LegalRedirect.jsx');
  for (const path of LEGAL_PATHS) {
    const matches = matchRoutes(routes, path);
    assert.ok(matches, `${path} 공개 경로가 필요해요`);
    assert.equal(matches.at(-1).route.element.type, LegalRedirect);
    assert.ok(matches.every(({ route }) => route.element?.type?.name !== 'RequireAuth'));
    assert.ok(hrefs(render(matches.at(-1).route.element, path)).includes(`https://wearless.kr${path}`));
  }
  assert.equal(matchRoutes(routes, '/verify/p/publication-1').at(-1).route.element.type.name, 'PublicVerifyPublication');
});

test('셀러 법무 이동 화면은 모든 이전 경로에서 새 문서로 replace하고 수동 링크를 제공한다', async () => {
  const { LegalRedirect } = await load('src/features/legal/LegalRedirect.jsx');
  const { effects } = await vite.ssrLoadModule('virtual:legal-effect');
  const previousWindow = globalThis.window;
  try {
    for (const path of LEGAL_PATHS) {
      const destinations = [];
      globalThis.window = { location: { replace: (url) => destinations.push(url) } };
      effects.length = 0;
      const html = render(React.createElement(LegalRedirect, { to: `https://wearless.kr${path}` }), path);
      for (const effect of effects) effect();
      assert.deepEqual(destinations, [`https://wearless.kr${path}`]);
      assert.deepEqual(hrefs(html), [`https://wearless.kr${path}`]);
    }
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test('Vercel은 ai 호스트의 네 이전 법무 주소만 영구 이동하고 FaceMarket을 보존한다', () => {
  const config = JSON.parse(read('vercel.json'));
  const redirects = config.redirects || [];
  for (const path of LEGAL_PATHS) {
    const rules = redirects.filter((rule) => rule.source === path);
    assert.equal(rules.length, 1, `${path} 영구 이동이 필요해요`);
    assert.equal(rules[0].destination, `https://wearless.kr${path}`);
    assert.equal(rules[0].permanent, true);
    assert.deepEqual(rules[0].has, [{ type: 'host', value: 'ai.wearless.kr' }]);
  }
  assert.equal(redirects.length, 4);
  assert.ok(config.rewrites.some((rule) => rule.destination === '/facemarket.html'));
});

test('셀러 푸터의 네 법무 링크는 대표 도메인이고 FaceMarket 푸터는 자체 경로를 유지한다', async () => {
  const { SiteFooter } = await load('src/features/shell/SiteFooter.jsx');
  const { FooterSection } = await load('src/features/facemarket-landing/sections/FooterSection.jsx');
  assert.deepEqual(hrefs(render(React.createElement(SiteFooter))), [
    'https://wearless.kr/terms', 'https://wearless.kr/privacy',
    'https://wearless.kr/refund', 'https://wearless.kr/model-license-terms',
  ]);
  const modelLinks = hrefs(render(React.createElement(FooterSection)));
  for (const path of ['/terms', '/privacy', '/license-agreement', '/seller-terms', '/answers']) {
    assert.ok(modelLinks.includes(path), `FaceMarket의 ${path} 링크를 유지해야 해요`);
  }
});

test('FaceMarket 앱은 자체 법무 문서를 공개 라우트로 유지한다', async () => {
  const routes = await registeredRoutes('src/apps/facemarket/App.jsx');
  const { LegalPage } = await load('src/features/legal/LegalPage.jsx');
  for (const [path, slug] of [
    ['/terms', 'terms-model'], ['/privacy', 'privacy-model'],
    ['/license-agreement', 'license-agreement'], ['/seller-terms', 'seller-license-terms'],
    ['/answers', 'answers'],
  ]) {
    const matches = matchRoutes(routes, path);
    assert.equal(matches.at(-1).route.element.type, LegalPage);
    assert.equal(matches.at(-1).route.element.props.slug, slug);
    assert.ok(matches.every(({ route }) => route.element?.type?.name !== 'RequireAuth'));
  }
});

test('공용 사업자 정보는 올바른 등록번호 형식을 제공한다', async () => {
  const { COMPANY_INFO_LINES } = await load('src/lib/companyInfo.js');
  assert.match(COMPANY_INFO_LINES.join(' '), /\b\d{3}-\d{2}-\d{5}\b/);
});

test('법무 화면과 두 푸터는 공용 사업자 정보를 사용한다', () => {
  for (const pathname of [
    'src/features/legal/LegalPage.jsx',
    'src/features/shell/SiteFooter.jsx',
    'src/features/facemarket-landing/sections/FooterSection.jsx',
  ]) {
    assert.match(read(pathname), /@\/lib\/companyInfo\.js/);
  }
});

test('FaceMarket 모델 레이아웃은 얇은 법무 푸터를 함께 렌더한다', () => {
  const layout = read('src/features/facemarket-shell/FacemarketModelLayout.jsx');
  assert.match(layout, /FooterSection/);
  assert.match(layout, /<FooterSection compact\s*\/>/);
});

test('공용 결제 화면은 셀러·모델 경로 모두 대표 도메인의 약관과 환불 정책으로 연결한다', async () => {
  const { Pricing } = await load('src/features/pricing/Pricing.jsx');
  // Pricing 은 세션을 읽는다 — 요금제가 공개 라우트가 되면서(비로그인도 가격을 본다)
  // 결제 버튼이 '로그인하고 …' 로 갈리기 때문이다. useAuth 는 프로바이더 밖에서 throw 하므로
  // 라우트 엘리먼트만 떼어 렌더하는 이 테스트도 앱과 같은 컨텍스트를 둘러 줘야 한다.
  // SSR(renderToStaticMarkup)에서는 effect 가 돌지 않아 세션 부트스트랩·OAuth 교환은
  // 일어나지 않는다 — session=null(비로그인) 상태로 굳는다. 법무 링크는 세션과 무관하다.
  const { AuthProvider } = await load('src/features/auth/AuthProvider.jsx');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['pricingPlans'], [{
    id: 'basic', code: 'basic', kind: 'subscription', name: '베이직', credits: 100, price: 10000,
  }]);
  try {
    for (const appPath of ['src/apps/seller/App.jsx', 'src/apps/facemarket/App.jsx']) {
      const routes = await registeredRoutes(appPath);
      const element = matchRoutes(routes, '/pricing').at(-1).route.element;
      assert.equal(element.type, Pricing, `${appPath}의 실제 결제 경로를 검증해야 해요`);
      const html = render(
        React.createElement(QueryClientProvider, { client },
          React.createElement(AuthProvider, null, element)),
        '/pricing',
      );
      assert.match(html, /구독은 해지할 때까지 매달 자동 결제돼요/);
      assert.match(html, /결제하면/);
      assert.deepEqual(hrefs(html), [
        'https://wearless.kr/refund', 'https://wearless.kr/terms', 'https://wearless.kr/refund',
      ]);
    }
  } finally { client.clear(); }
});

test('두 진입 문서는 llms.txt를 대체 문서로 알린다', () => {
  for (const pathname of ['seller.html', 'facemarket.html']) {
    assert.match(read(pathname), /<link rel="alternate" type="text\/plain" href="\/llms\.txt"\s*\/>/);
  }
});
