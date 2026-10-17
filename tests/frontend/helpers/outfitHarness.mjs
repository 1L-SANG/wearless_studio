import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

export const nodes = (node) => Array.isArray(node) ? node.flatMap(nodes)
  : !node || typeof node !== 'object' ? [] : [node, ...nodes(node.props?.children)];
export const textOf = (node) => node == null || typeof node === 'boolean' ? '' : Array.isArray(node)
  ? node.map(textOf).join('') : typeof node === 'object' ? textOf(node.props?.children) : String(node);
export const drain = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

// Actual modules, a small hook renderer and inert DOM boundaries; no browser process.
export async function outfitHarness(api = {}) {
  const key = `__outfit${Math.random().toString(36).slice(2)}`;
  const runtime = { api, slots: [], cleanups: [], effects: [], index: 0 };
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({ configFile: false, logLevel: 'silent',
    root: fileURLToPath(new URL('../../..', import.meta.url)),
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom', ssr: { noExternal: true },
    plugins: [{ name: 'outfit-behavior', enforce: 'pre',
      resolveId(id) {
        if (['react', 'react-dom', 'lucide-react', '@/lib/api/index.js', '@/features/shell/shell.jsx'].includes(id)) return `\0outfit-${id}`;
        if (id.startsWith('@/')) return fileURLToPath(new URL(`../../../src/${id.slice(2)}`, import.meta.url));
      },
      load(id) {
        if (id === '\0outfit-react') return `const r=${access};
          const same=(a,b)=>a&&b&&a.length===b.length&&b.every((v,i)=>Object.is(v,a[i]));
          export const useState=initial=>{const i=r.index++;if(!(i in r.slots))r.slots[i]=typeof initial==='function'?initial():initial;return [r.slots[i],v=>{r.slots[i]=typeof v==='function'?v(r.slots[i]):v;}];};
          export const useRef=initial=>{const i=r.index++;return r.slots[i]??={current:initial};};
          export const useEffect=(fn,deps)=>{const i=r.index++;if(!same(r.slots[i],deps)){r.slots[i]=deps;r.effects.push(()=>{r.cleanups[i]?.();r.cleanups[i]=fn();});}};
          export const useMemo=(fn,deps)=>{const i=r.index++;if(!same(r.slots[i]?.deps,deps))r.slots[i]={value:fn(),deps};return r.slots[i].value;};
          export const useCallback=(fn,deps)=>useMemo(()=>fn,deps);export const useId=()=>{const i=r.index++;return 'outfit-test-'+i;};
          const React={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:{...props,children}})};export default React;`;
        if (id === '\0outfit-react-dom') return 'export const createPortal=x=>x;export const flushSync=fn=>fn();';
        if (id === '\0outfit-lucide-react') return "export const Plus='plus',X='x',ChevronRight='chev-right',UserRound='model-icon',Shirt='shirt',Footprints='shoe',ShoppingBag='bag',Gem='accessory',ChevronDown='chev-down',ChevronUp='chev-up',Sparkles='sparkles',Info='info',Search='search',ArrowRight='arrow-right';";
        if (id === '\0outfit-@/lib/api/index.js') return `export const api=${access}.api;export const isMockMode=true;`;
        if (id === '\0outfit-@/features/shell/shell.jsx') return "export const WizardCTA='wizard-cta';";
      },
    }],
  });
  return { runtime,
    load: (path) => server.ssrLoadModule(path),
    render(fn, props, attachRefs) { runtime.index = 0; runtime.effects = []; const tree = fn(props); attachRefs?.(tree); runtime.effects.splice(0).forEach(effect => effect()); return tree; },
    async close() { runtime.cleanups.forEach(cleanup => cleanup?.()); await server.close(); delete globalThis[key]; },
  };
}
