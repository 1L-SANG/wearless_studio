import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { createServer } from 'vite';

async function harness(t, path, exportName, { accountPlan = 'pro', topupBonusEnabled = false, request = {}, result = {}, items = [] } = {}) {
  const key = `__creditPayment${Math.random().toString(36).slice(2)}`;
  const previousWindow = globalThis.window, previousDocument = globalThis.document;
  globalThis.window = new EventTarget();
  globalThis.document = { body: {} };
  const runtime = {
    frame: null, account: { plan: accountPlan, topupBonusEnabled }, toasts: [], balances: [], invalidations: [],
    params: new URLSearchParams('paymentKey=key&orderId=order&amount=26000'),
    toast: { push: message => runtime.toasts.push(message) },
    api: {
      createBankTransferRequest: async () => ({ request }),
      confirmTossPayment: async () => result,
      adminListBankTransfers: async () => ({ items }),
      adminConfirmBankTransfer: async () => result,
      adminRejectBankTransfer: async () => ({}),
    },
    queryClient: { invalidateQueries: options => runtime.invalidations.push(options) },
  };
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const root = new URL('../..', import.meta.url).pathname;
  const vite = await createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
    configFile: false, root, logLevel: 'silent', appType: 'custom', ssr: { noExternal: true },
    server: { middlewareMode: true, watch: null, hmr: false }, esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'credit-payment-behavior', enforce: 'pre',
      resolveId(id) {
        const stubs = {
          react: 'react', 'react/jsx-runtime': 'jsx', 'react/jsx-dev-runtime': 'jsx', 'react-dom': 'portal',
          '@/lib/api/index.js': 'api', '@/lib/api/facemarket.js': 'admin', '@/store/useAppStore.js': 'store',
          '@/components/ui.jsx': 'ui', '@tanstack/react-query': 'query', 'react-router-dom': 'router',
          '@/lib/creditReturn.js': 'return', '@/lib/flowSession.js': 'flow',
        };
        if (stubs[id]) return `\0credit-payment-${stubs[id]}`;
        if (id.startsWith('@/components/admin-ui/')) return '\0credit-payment-admin-ui';
        if (id.endsWith('.module.css')) return '\0credit-payment-css';
        if (id.startsWith('@/')) return resolve(root, 'src', id.slice(2));
      },
      load(id) {
        if (id === '\0credit-payment-react') return `
          const r=${access};
          export const Fragment='Fragment';
          export const useState=initial=>{const f=r.frame,i=f.cursor++; if(!(i in f.hooks))f.hooks[i]=typeof initial==='function'?initial():initial;return[f.hooks[i],value=>f.hooks[i]=typeof value==='function'?value(f.hooks[i]):value];};
          export const useRef=initial=>{const f=r.frame,i=f.cursor++;return f.hooks[i]||=( {current:initial});};
          export const useId=()=>'credit-payment';
          const same=(a,b)=>a&&b&&a.length===b.length&&b.every((v,i)=>Object.is(v,a[i]));
          export const useCallback=(value,deps)=>{const f=r.frame,i=f.cursor++;if(!same(f.hooks[i]?.deps,deps))f.hooks[i]={value,deps};return f.hooks[i].value;};
          export const useEffect=(effect,deps)=>{const f=r.frame,i=f.cursor++;if(same(f.hooks[i]?.deps,deps))return;f.effects.push(()=>{f.hooks[i]?.cleanup?.();f.hooks[i]={deps,cleanup:effect()};});};
        `;
        if (id === '\0credit-payment-jsx') return "export const jsx=(type,props)=>({type,props});export const jsxs=jsx;export const jsxDEV=jsx;export const Fragment='Fragment';";
        if (id === '\0credit-payment-portal') return 'export const createPortal=children=>children;';
        if (id === '\0credit-payment-api') return `export const api=${access}.api;`;
        if (id === '\0credit-payment-admin') return `export const {adminListBankTransfers,adminConfirmBankTransfer,adminRejectBankTransfer}=${access}.api;`;
        if (id === '\0credit-payment-store') return `const state={account:${access}.account,syncCredits:value=>${access}.balances.push(value)};export const useAppStore=selector=>selector(state);useAppStore.getState=()=>({projectId:null});`;
        if (id === '\0credit-payment-ui') return `export const Button='Button';export const Icon='Icon';export const useToast=()=>${access}.toast;`;
        if (id === '\0credit-payment-admin-ui') return 'export const Badge="Badge",Button="Button",Card="Card",CardContent="CardContent",Input="Input",Skeleton="Skeleton",Textarea="Textarea",Table="Table",TableBody="TableBody",TableCell="TableCell",TableHead="TableHead",TableHeader="TableHeader",TableRow="TableRow";';
        if (id === '\0credit-payment-query') return `export const useQueryClient=()=>${access}.queryClient;`;
        if (id === '\0credit-payment-router') return `const navigate=()=>{};export const Link='Link';export const useNavigate=()=>navigate;export const useLocation=()=>({pathname:'/payments/success'});export const useSearchParams=()=>[${access}.params];`;
        if (id === '\0credit-payment-return') return 'export const readCreditReturn=()=>null;export const clearCreditReturn=()=>{};';
        if (id === '\0credit-payment-flow') return 'export const authorizeFlowContinuation=()=>{};';
        if (id === '\0credit-payment-css') return 'export default new Proxy({}, {get: (_, key) => key});';
      },
    }],
  });
  const Component = (await vite.ssrLoadModule(path))[exportName];
  const frames = new Map();
  function render(component = Component, props = {}) {
    const frame = frames.get(component) || { hooks: [] };
    frames.set(component, frame); frame.cursor = 0; frame.effects = []; runtime.frame = frame;
    const tree = component(props);
    for (const effect of frame.effects) effect();
    return tree;
  }
  function nodes(tree, predicate) {
    if (Array.isArray(tree)) return tree.flatMap(node => nodes(node, predicate));
    if (!tree || typeof tree !== 'object') return [];
    if (typeof tree.type === 'function' && tree.type.name !== 'CopyButton') return nodes(render(tree.type, tree.props), predicate);
    return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)];
  }
  function text(tree) {
    if (Array.isArray(tree)) return tree.map(text).join('');
    if (tree == null || typeof tree === 'boolean') return '';
    if (typeof tree !== 'object') return String(tree);
    if (typeof tree.type === 'function' && tree.type.name !== 'CopyButton') return text(render(tree.type, tree.props));
    return text(tree.props?.children);
  }
  t.after(async () => {
    for (const frame of frames.values()) for (const hook of frame.hooks) hook?.cleanup?.();
    await vite.close(); delete globalThis[key];
    globalThis.window = previousWindow; globalThis.document = previousDocument;
  });
  return { runtime, render: props => render(Component, props), nodes, text };
}

for (const [bonusCredits, totalCredits] of [[25, 525], [50, 550], [0, 500]]) {
  test(`계좌이체 완료 머리는 서버 지급량 ${totalCredits}을 한 번 표시하고 영수증은 입금 정보만 담는다`, async t => {
    const request = {
      id: 'b1', kind: 'topup', amount: 26000, credits: 500, bonusCredits, totalCredits, payerName: '홍길동',
      createdAt: '2026-10-06T14:59:00+09:00', expiresAt: '2026-10-09T14:59:00+09:00',
    };
    const h = await harness(t, '/src/features/pricing/BankTransferModal.jsx', 'BankTransferModal', { request });
    const props = {
      plan: { code: 'topup_500', name: '500 크레딧', kind: 'topup', price: 26000, credits: 500 },
      info: { enabled: true, bank: '국민은행', account: '123-456-789', holder: '정일상' }, onClose() {},
    };
    h.nodes(h.render(props), node => node.type === 'input' && node.props.id?.endsWith('-payerName'))[0].props.onChange({ target: { value: '홍길동' } });
    await h.nodes(h.render(props), node => node.type === 'form')[0].props.onSubmit({ preventDefault() {} });
    const tree = h.render(props);
    const creditLine = h.nodes(tree, node => node.props.className === 'sumSub')[0];
    assert.equal(h.text(creditLine), `${totalCredits} 크레딧${bonusCredits ? ` · 보너스 ${bonusCredits} 포함` : ''}`);
    assert.equal(h.nodes(creditLine, node => node.type === 'small').length, 0);
    const receipt = h.nodes(tree, node => node.props.className === 'receipt')[0];
    assert.deepEqual(h.nodes(receipt, node => node.type === 'dt').map(h.text),
      ['입금 금액', '입금 계좌', '입금자명', '입금 기한']);
    const text = h.text(tree);
    assert.equal((text.match(/보너스 \d+ 포함/g) || []).length, bonusCredits ? 1 : 0);
    if (bonusCredits === 25) assert.doesNotMatch(text, /550 크레딧/);
    assert.deepEqual(h.nodes(tree, node => node.props.className === 'deadlinePart').map(h.text),
      ['10/9(금)', '14:59까지', '10/9(금)', '14:59까지']);
  });
}

test('관리자 상품 표시와 지급 완료 알림은 서버 총량과 보너스를 쓴다', async t => {
  const item = { id: 'b1', kind: 'topup', planCode: 'topup_500', amount: 26000, credits: 500, bonusCredits: 25, totalCredits: 525, payerName: '홍길동', status: 'requested' };
  const h = await harness(t, '/src/features/admin/AdminBankTransfers.jsx', 'AdminBankTransfers', { items: [item], result: { credits: 525, baseCredits: 500, bonusCredits: 25, totalCredits: 525 } });
  h.render(); await new Promise(setImmediate);
  assert.match(h.text(h.render()), /525 크레딧보너스 25 포함/);
  h.nodes(h.render(), node => node.type === 'Button' && node.props.children === '입금 확인')[0].props.onClick();
  await h.nodes(h.render(), node => node.type === 'form')[0].props.onSubmit({ preventDefault() {} });
  assert.deepEqual(h.runtime.toasts, ['지급 완료 · 525 크레딧 · 보너스 25 포함']);
});

test('토스 결제 완료 화면은 서버 지급 총량과 보너스를 표시하고 서버 잔액을 반영한다', async t => {
  const h = await harness(t, '/src/features/payments/PaymentResult.jsx', 'PaymentSuccess', { result: { credits: 525, baseCredits: 500, bonusCredits: 25, totalCredits: 525, available: 1035 } });
  h.render(); await new Promise(setImmediate);
  const text = h.text(h.render());
  assert.match(text, /크레딧 525이 추가됐어요/);
  assert.match(text, /보너스 25 포함/);
  assert.match(text, /현재 잔액 1,035/);
  assert.deepEqual(h.runtime.balances, [1035]);
});
