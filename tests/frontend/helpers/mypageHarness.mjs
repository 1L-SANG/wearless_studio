import { createServer } from 'vite';

export function findTree(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const child of node) { const found = findTree(child, predicate); if (found) return found; }
    return null;
  }
  return predicate(node) ? node : findTree(node.props?.children, predicate);
}
export async function loadEarningsHarness(api = {}) {
  const key = `__mypage${Math.random().toString(36).slice(2)}`;
  const runtime = { api, states: [], effects: [], index: 0, hash: '#usage', navigations: [] };
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({ configFile: false, logLevel: 'silent',
    root: new URL('../../..', import.meta.url).pathname, server: { middlewareMode: true },
    resolve: { alias: { '@': new URL('../../../src', import.meta.url).pathname } },
    ssr: { noExternal: true, external: ['lucide-react'] }, esbuild: { jsx: 'automatic' }, appType: 'custom',
    plugins: [{ name: 'mypage-harness', enforce: 'pre',
      resolveId(id) {
        if (['react/jsx-runtime', 'react/jsx-dev-runtime'].includes(id)) return '\0mp-jsx';
        if (id === 'react') return '\0mp-react';
        if (id === 'react-router-dom') return '\0mp-router';
        if (id.endsWith('/lib/api/facemarket.js')) return '\0mp-api';
        if (id.endsWith('.module.css')) return '\0mp-css';
      },
      load(id) {
        if (id === '\0mp-jsx') return 'export const jsx=(type,props)=>({type,props}); export const jsxs=jsx; export const jsxDEV=jsx;';
        if (id === '\0mp-react') return `const r=${access};
          export const useState = initial => { const i=r.index++; if(!(i in r.states)) r.states[i]=typeof initial==='function'?initial():initial;
            return [r.states[i],value=>{r.states[i]=typeof value==='function'?value(r.states[i]):value;}]; };
          export const useCallback = fn => fn;
          export const useRef = value => { const i=r.index++; if(!(i in r.states)) r.states[i]={current:value}; return r.states[i]; };
          export const useMemo = fn => fn();
          export const useEffect = fn => r.effects.push(fn);`;
        if (id === '\0mp-router') return `const r=${access}; export const Navigate='Navigate'; export const Link='Link';
          export const useLocation=()=>({hash:r.hash}); export const useNavigate=()=>to=>{r.navigations.push(to); if(to.hash) r.hash=to.hash;};`;
        if (id === '\0mp-api') return `const api=${access}.api;
          export const getSettlementSummary=()=>api.getSettlementSummary?.() ?? Promise.resolve({monthCount:0,monthAmount:0,totalAmount:0,totalCount:0});
          export const listSettlements=()=>api.listSettlements?.() ?? Promise.resolve([]);
          export const reportUsage=(...args)=>api.reportUsage(...args);
          export const pauseMyModel=(...args)=>api.pauseMyModel(...args);
          export const resumeMyModel=(...args)=>api.resumeMyModel(...args);
          export const revokeLicense=(...args)=>api.revokeLicense(...args);
          export const updateLicenseTerms=(...args)=>api.updateLicenseTerms(...args);`;
        if (id === '\0mp-css') return 'export default new Proxy({}, {get:(_,key)=>key});';
      },
    }],
  });
  const module = await server.ssrLoadModule('/src/features/model/mypage/MyPageEarnings.jsx');
  const render = (component = module.useMyPageSettlements, props = 'm1') => { runtime.index=0; runtime.effects=[]; return component(props); };
  return { server, module, runtime, render,
    async load() { render(); runtime.effects[0](); await new Promise(resolve => setImmediate(resolve)); return render(); },
    async close() { await server.close(); delete globalThis[key]; },
  };
}
