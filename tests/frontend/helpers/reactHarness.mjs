import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithEsbuild } from 'vite';

// Render real JSX and call its handlers without starting a browser or adding a DOM dependency.
export async function componentModule(path, names, bindings = {}) {
  const source = readFileSync(new URL(`../../../${path}`, import.meta.url), 'utf8')
    .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\n/gm, '')
    .replace(/^export \{[^}]+\} from ['"][^'"]+['"];?\n/gm, '')
    .replace(/^export default [^;]+;?$/gm, '').replace(/^export /gm, '');
  const { code } = await transformWithEsbuild(source, path, { jsx: 'transform' });
  return new Function('React', ...Object.keys(bindings), `${code}\nreturn {${names.join(',')}};`)(React, ...Object.values(bindings));
}

export function hooks() {
  const slots = [], effects = [];
  let cursor = 0;
  const runtime = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], (value) => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    },
    useRef(initial) {
      const i = cursor++;
      return slots[i] ||= { current: initial };
    },
    useEffect(fn, deps) {
      const i = cursor++, previous = slots[i];
      if (!previous || !deps || deps.some((value, n) => value !== previous[n])) effects.push(fn);
      slots[i] = deps;
    },
    render(fn, props) { cursor = 0; return fn(props); },
    flushEffects() { return effects.splice(0).map((fn) => fn()); },
  };
  return runtime;
}
