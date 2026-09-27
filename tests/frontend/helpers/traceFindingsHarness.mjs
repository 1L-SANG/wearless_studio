/* 자동 발견(관리자)·모델 제보 화면 테스트 하네스(2026-09-27).
   adminUsageReportsHarness 와 같은 방식 — React 훅을 슬롯 배열로 흉내 내고, API 모듈은 테스트가
   넘긴 가짜로 바꾼다. api() 는 진짜 facemarket.js 를 불러 경로·본문 배선을 본다(http·fetch 기록). */
import { createServer } from 'vite';
export { findTree } from './facemarketHarness.mjs';

export const flush = () => new Promise((resolve) => setImmediate(resolve));

export function treeText(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (Array.isArray(node)) return node.map(treeText).join(' ');
  if (typeof node === 'object') return treeText(node.props?.children);
  return String(node);
}

const API_NAMES = ['adminListTraceFindings', 'adminUpdateTraceFinding', 'reportSighting', 'listMySightings'];

export async function traceFindingsHarness(api = {}) {
  const key = `__traceFindings${Math.random().toString(36).slice(2)}`;
  const runtime = { api, slots: [], effects: [], cleanups: [], index: 0, calls: [] };
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({
    configFile: false, logLevel: 'silent',
    root: new URL('../../..', import.meta.url).pathname,
    server: { middlewareMode: true, watch: null }, ssr: { noExternal: true },
    esbuild: { jsx: 'automatic' }, appType: 'custom',
    plugins: [{
      name: 'trace-findings-test', enforce: 'pre',
      resolveId(id, importer) {
        if (id === 'react') return '\0tf-react';
        if (['react/jsx-runtime', 'react/jsx-dev-runtime'].includes(id)) return '\0tf-jsx';
        if (id === '@/lib/api/facemarket.js' && !importer?.includes('/tests/')) return '\0tf-api';
        if (id === '@/lib/api/httpAdapter.js') return '\0tf-http';
        if (id === '@/lib/supabase.js') return '\0tf-auth';
        if (id.startsWith('@/')) return new URL(`../../../src/${id.slice(2)}`, import.meta.url).pathname;
      },
      load(id) {
        if (id === '\0tf-jsx') return `export const jsx=(type,props,key)=>typeof type==='function'?{...type(props),key}:{type,props,key}; export const jsxs=jsx; export const jsxDEV=jsx; export const Fragment='frag';`;
        if (id === '\0tf-react') return `const r=${access};
          const unchanged=(a,b)=>a&&b&&a.length===b.length&&b.every((v,i)=>Object.is(v,a[i]));
          export const useState=initial=>{const i=r.index++; if(!(i in r.slots)) r.slots[i]=typeof initial==='function'?initial():initial;
            return [r.slots[i],v=>{r.slots[i]=typeof v==='function'?v(r.slots[i]):v;}];};
          export const useRef=initial=>{const i=r.index++; return r.slots[i]??=( {current:initial} );};
          export const useCallback=(fn,deps)=>{const i=r.index++;if(!unchanged(r.slots[i]?.deps,deps))r.slots[i]={fn,deps};return r.slots[i].fn;};
          export const useEffect=(fn,deps)=>{const i=r.index++;if(!unchanged(r.slots[i],deps)){r.slots[i]=deps;r.effects.push(()=>{r.cleanups[i]?.();r.cleanups[i]=fn();});}};`;
        if (id === '\0tf-api') return `const api=${access}.api;\n` + API_NAMES
          .map(name => `export const ${name}=(...args)=>api.${name}(...args);`).join('\n');
        if (id === '\0tf-http') return `export const http=async(path,options)=>{${access}.calls.push({path,options});return {items:[],nextCursor:null};};`;
        if (id === '\0tf-auth') return `export const supabase={auth:{getSession:async()=>({data:{session:{access_token:'tok'}}})}};`;
      },
    }],
  });
  return {
    runtime,
    async api() { return server.ssrLoadModule('/src/lib/api/facemarket.js'); },
    async component(path, name) {
      const module = await server.ssrLoadModule(path);
      return (props = {}) => {
        runtime.index = 0; runtime.effects = [];
        const tree = module[name](props);
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
