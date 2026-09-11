import { createServer } from 'vite';
export { findTree } from './facemarketHarness.mjs';

export const flush = () => new Promise((resolve) => setImmediate(resolve));

export function treeText(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (Array.isArray(node)) return node.map(treeText).join(' ');
  if (typeof node === 'object') return treeText(node.props?.children);
  return String(node);
}

export async function adminUsageReportsHarness(api = {}) {
  const key = `__adminUsageReports${Math.random().toString(36).slice(2)}`;
  const runtime = { api, slots: [], effects: [], cleanups: [], index: 0, calls: [] };
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({
    configFile: false, logLevel: 'silent',
    root: new URL('../../..', import.meta.url).pathname,
    server: { middlewareMode: true }, ssr: { noExternal: true },
    esbuild: { jsx: 'automatic' }, appType: 'custom',
    plugins: [{
      name: 'admin-usage-reports-test', enforce: 'pre',
      resolveId(id) {
        if (id === 'react') return '\0reports-react';
        if (['react/jsx-runtime', 'react/jsx-dev-runtime'].includes(id)) return '\0reports-jsx';
        if (id === '@/lib/api/facemarket.js') return '\0reports-api';
        if (id === '@/lib/api/httpAdapter.js') return '\0reports-http';
        if (id === '@/lib/supabase.js') return '\0reports-auth';
        if (id.startsWith('@/')) return new URL(`../../../src/${id.slice(2)}`, import.meta.url).pathname;
      },
      load(id) {
        if (id === '\0reports-jsx') return `export const jsx=(type,props,key)=>typeof type==='function'?{...type(props),key}:{type,props,key}; export const jsxs=jsx; export const jsxDEV=jsx;`;
        if (id === '\0reports-react') return `const r=${access};
          const unchanged=(a,b)=>a&&b&&a.length===b.length&&b.every((v,i)=>Object.is(v,a[i]));
          export const useState=initial=>{const i=r.index++; if(!(i in r.slots)) r.slots[i]=typeof initial==='function'?initial():initial;
            return [r.slots[i],v=>{r.slots[i]=typeof v==='function'?v(r.slots[i]):v;}];};
          export const useRef=initial=>{const i=r.index++; return r.slots[i]??=( {current:initial} );};
          export const useCallback=(fn,deps)=>{const i=r.index++;if(!unchanged(r.slots[i]?.deps,deps))r.slots[i]={fn,deps};return r.slots[i].fn;};
          export const useEffect=(fn,deps)=>{const i=r.index++;if(!unchanged(r.slots[i],deps)){r.slots[i]=deps;r.effects.push(()=>{r.cleanups[i]?.();r.cleanups[i]=fn();});}};`;
        if (id === '\0reports-api') return `const api=${access}.api;
          export const adminListUsageReports=(...args)=>api.adminListUsageReports(...args);
          export const adminUpdateUsageReportStatus=(...args)=>api.adminUpdateUsageReportStatus(...args);`;
        if (id === '\0reports-http') return `export const http=async(path,options)=>{${access}.calls.push({path,options});return {items:[],nextCursor:null};};`;
        if (id === '\0reports-auth') return 'export const supabase={};';
      },
    }],
  });
  return {
    runtime,
    async api() { return server.ssrLoadModule('/src/lib/api/facemarket.js'); },
    async component() {
      const module = await server.ssrLoadModule('/src/features/admin/AdminUsageReports.jsx');
      return () => {
        runtime.index = 0; runtime.effects = [];
        const tree = module.AdminUsageReports();
        runtime.effects.forEach((effect) => effect());
        return tree;
      };
    },
    async close() {
      runtime.cleanups.forEach((cleanup) => cleanup?.());
      await server.close(); delete globalThis[key];
    },
  };
}
