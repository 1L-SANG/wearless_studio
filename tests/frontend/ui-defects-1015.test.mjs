import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';

const root = new URL('../../', import.meta.url);
const read = (path) => readFileSync(new URL(path, root), 'utf8');
const styles = new Proxy({}, { get: (_, key) => key });
async function load(path, names, bindings = {}) {
  const source = read(path).replace(/^import[\s\S]*?;\n/gm, '')
    .replace(/^export default .*;\n/gm, '').replace(/^export /gm, '');
  const { code } = await transformWithEsbuild(source, path, { jsx: 'transform' });
  const scope = { React, ...React, s: styles, ...bindings };
  return new Function(...Object.keys(scope), `${code}; return { ${names.join(', ')} };`)(...Object.values(scope));
}
const { Icon, Button, EmptyState, Skeleton } = await load('src/components/ui.jsx', ['Icon', 'Button', 'EmptyState', 'Skeleton']);
const { HangTag } = await load('src/features/shell/HangTag.jsx', ['HangTag']);
const render = (tree) => renderToStaticMarkup(tree);

async function screen(path, name, { states = [], query = '', failure, queries = [], pathname = '/test' } = {}) {
  let cursor = 0, queryCursor = 0;
  const effects = [], logs = [];
  const store = (select) => select({ syncCredits() {} });
  const bindings = {
    Icon, Button, EmptyState, Skeleton, HangTag, tagStyles: styles,
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (value) => { states[index] = value; }];
    },
    useRef: (current) => ({ current }), useEffect: (fn) => effects.push(fn),
    useCallback: (fn) => fn, useNavigate: () => () => {},
    useSearchParams: () => [new URLSearchParams(query)],
    useLocation: () => ({ pathname }), useParams: () => ({ licenseId: 'l', publicationId: 'p' }),
    useQueryClient: () => ({ invalidateQueries() {} }), useAppStore: store,
    useAuth: () => ({ session: null }), useToast: () => ({ push() {} }),
    useQuery: () => queries[queryCursor++] ?? { data: [], isLoading: false },
    Link: ({ to, children }) => React.createElement('a', { href: to }, children),
    console: { error: (...args) => logs.push(args), warn: (...args) => logs.push(args) },
    verifyLicensePublic: async () => { throw failure; },
    verifyPublicationPublic: async () => { throw failure; },
    api: {
      confirmTossPayment: async () => { throw failure; },
      startSubscription: async () => { throw failure; },
    },
    BANK_TRANSFER_ENABLED: false, SUBSCRIPTION_TRANSFER_ENABLED: false,
    TOSS_BILLING_CLIENT_KEY: '',
  };
  const module = await load(path, [name], bindings);
  const paint = () => { cursor = 0; queryCursor = 0; effects.length = 0; return render(module[name]()); };
  return { paint, effects, logs };
}

test('공용 Icon을 쓰는 실패 화면과 알림은 실제 등록된 경고 아이콘을 쓴다', () => {
  const names = new Set([...read('src/components/ui.jsx').matchAll(/^  (\w+): '/gm)].map((m) => m[1]));
  const visit = (dir) => readdirSync(new URL(dir, root), { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? visit(`${dir}/${entry.name}`) : /\.[jt]sx?$/.test(entry.name) ? [`${dir}/${entry.name}`] : []);
  const invalid = [];
  for (const path of visit('src')) {
    const source = read(path);
    for (const match of source.matchAll(/(?:<Icon\s+name=|\bicon=)["'](alert\w*)["']|\bicon:\s*(?:error\?\.icon\s*\|\|\s*)?["'](alert\w*)["']/g)) {
      if (!names.has(match[1] || match[2])) invalid.push(path);
    }
  }
  assert.deepEqual(invalid, []);
  assert.match(render(React.createElement(Icon, { name: 'alertCircle' })), /<circle/);
});

for (const [path, name] of [
  ['src/features/payments/PaymentResult.jsx', 'PaymentFail'],
  ['src/features/subscription/Subscription.jsx', 'SubscriptionFail'],
]) {
  test(`${name}는 취소 코드와 외부 오류 원문을 화면에서 숨기고 지원 로그에 남긴다`, async () => {
    const h = await screen(path, name, { query: 'code=PAY_PROCESS_CANCELED&message=PAY_PROCESS_CANCELED+raw+failure' });
    const html = h.paint();
    assert.doesNotMatch(html, /PAY_PROCESS_CANCELED|raw failure/);
    assert.match(html, /취소됐어요/);
    assert.match(html, /<circle/);
    h.effects.forEach((effect) => effect());
    assert.match(JSON.stringify(h.logs), /PAY_PROCESS_CANCELED/);
  });
  test(`${name}는 실패를 취소로 표시하지 않는다`, async () => {
    const h = await screen(path, name, { query: 'code=REJECT_CARD_COMPANY&message=internal' });
    const html = h.paint();
    assert.doesNotMatch(html, /취소됐어요|REJECT_CARD_COMPANY|internal/);
    assert.match(html, /못했어요/);
  });
}

for (const [path, name, pathname, query] of [
  ['src/features/payments/PaymentResult.jsx', 'PaymentSuccess', '/payments/success', 'paymentKey=k&orderId=o&amount=100'],
  ['src/features/subscription/Subscription.jsx', 'SubscriptionSuccess', '/subscription/success', 'authKey=k&customerKey=c&plan=seller'],
]) {
  test(`${name}는 승인 오류 원문을 로그에만 남기고 짧은 재시도 안내를 보여준다`, async () => {
    const failure = Object.assign(new Error('INTERNAL_ERROR private detail'), { code: 'INTERNAL_ERROR' });
    const h = await screen(path, name, { pathname, query, failure });
    h.paint();
    h.effects.forEach((effect) => effect());
    await new Promise((resolve) => setImmediate(resolve));
    const html = h.paint();
    assert.doesNotMatch(html, /INTERNAL_ERROR|private detail/);
    assert.match(html, /잠시 후 다시 시도해 주세요/);
    assert.match(html, /<circle/);
    assert.ok(h.logs.some((args) => args.includes(failure)));
  });
}

test('결제 성공의 두 행동은 공통 알약 규칙을 함께 적용받는다', async () => {
  const h = await screen('src/features/payments/PaymentResult.jsx', 'PaymentSuccess', {
    states: [{ status: 'done', credits: 100 }],
  });
  assert.match(h.paint(), /class="[^"]*modal-actions[^"]*"[^]*사용 내역 보기[^]*작업 계속하기/);
  const css = read('src/styles/app.css');
  assert.match(css, /\.modal-actions \.btn[^}]*border-radius: var\(--cta-radius\); min-height: var\(--cta-height\)/);
});

test('빈 보관함은 흰 카드 없이 공용 옷 태그와 기존 생성 버튼을 보여준다', async () => {
  const h = await screen('src/features/library/Library.jsx', 'Library');
  const html = h.paint();
  assert.match(html, /viewBox="0 0 280 230"/);
  assert.match(html, /아직 비어 있어요/);
  assert.match(html, /상품 사진으로 시작해 보세요/);
  assert.match(html, /첫 상세페이지 만들기/);
  assert.doesNotMatch(html, /class="surface"|PAGE NOT FOUND|404/);
});

for (const name of ['PublicVerify', 'PublicVerifyPublication']) {
  test(`${name}는 브라우저별 연결 오류를 한국어로 안내하고 다시 시도를 제공한다`, async () => {
    for (const message of ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.']) {
      const h = await screen(`src/features/verify/${name}.jsx`, name, { failure: new TypeError(message) });
      h.paint();
      await h.effects[0]();
      const html = h.paint();
      assert.doesNotMatch(html, /Failed to fetch|Load failed|NetworkError/);
      assert.match(html, /인터넷 연결을 확인하고 다시 시도해 주세요/);
      assert.match(html, /다시 시도<\/button>/);
    }
  });
  test(`${name}는 서버의 한국어 안내와 404 분기를 보존한다`, async () => {
    for (const status of [404, 500]) {
      const h = await screen(`src/features/verify/${name}.jsx`, name, {
        failure: Object.assign(new Error('잠시 후 다시 확인해 주세요.'), { status }),
      });
      h.paint();
      await h.effects[0]();
      const html = h.paint();
      assert.match(html, status === 404 ? /찾을 수 없는/ : /잠시 후 다시 확인해 주세요/);
    }
  });
}

test('콘티 완료 알림만 인스펙터 폭 바깥 아래에 배치한다', () => {
  const css = read('src/styles/app.css');
  assert.match(css, /@media \(min-width: 901px\)[^{]*\{\s*body:has\(\.sb-page\) \.toast-host:has\(\.mannequin-completion\)\s*\{[^}]*top: auto;[^}]*bottom: var\(--sp-24\);[^}]*right: calc\(max\(24px, \(100vw - 1132px\) \/ 2\) \+ 360px \+ 22px\)/);
});

test('크레딧 목록은 PC에서 제목 왼쪽에 맞추고 휴대폰 기본 간격은 유지한다', () => {
  const css = read('src/features/credits/CreditsHistory.module.css');
  assert.match(css, /\.list\s*\{[^}]*max-width: 560px; margin: 0 auto;/);
  assert.match(css, /@media \(min-width: 769px\)\s*\{\s*\.list\s*\{\s*margin-inline: 0;/);
});
