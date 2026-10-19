import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { baseProductPhoto, currentDraftVisuals, startVisualPolling } from '../../src/lib/draftVisuals.js';

function harness() {
  const slots = []; let index = 0; const effects = []; const timers = new Map();
  const queries = []; const replies = []; let listener; let nextId = 0; let dirty = false;
  const context = { user: 'seller', enabled: true, mobile: false, token: 'token', bindings: { product: { sourceAssetIds: ['remote-front'] } } };
  const source = readFileSync(new URL('../../src/features/analysis/useDraftVisuals.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export function', 'function');
  const bindings = {
    useState(initial) {
      const i = index++; if (!(i in slots)) slots[i] = initial;
      return [slots[i], (value) => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; dirty = true; }];
    },
    useEffect(fn, deps) {
      const i = index++; const prior = slots[i];
      if (!prior || deps.some((value, n) => !Object.is(value, prior.deps[n]))) {
        slots[i] = { deps };
        effects.push(() => { prior?.cleanup?.(); slots[i].cleanup = fn(); });
      }
    },
    api: { getDraftVisuals(token, options) {
      queries.push({ token, options });
      return new Promise((resolve, reject) => replies.push({ resolve, reject }));
    } },
    draftSlot: { getUserId: () => context.user, getToken: () => context.token,
      isVisualsEnabled: () => context.enabled, getVisualSources: () => context.bindings, recoverVisualHandoff() {}, onVisuals(fn) { listener = fn; return () => { listener = null; }; } },
    isMobileDevice: () => context.mobile, baseProductPhoto, currentDraftVisuals,
    startVisualPolling: (options) => startVisualPolling({ ...options,
      setTimer: (fn, ms) => { timers.set(++nextId, { fn, ms }); return nextId; }, clearTimer: (id) => timers.delete(id) }),
  };
  const hook = new Function(...Object.keys(bindings), `${source}; return useDraftVisuals;`)(...Object.values(bindings));
  const product = { colors: [{ isBase: true, images: [{ id: 'local-front', src: 'raw', slot: 'Front' }] }] };
  const render = (props = {}) => { index = 0; dirty = false; return hook({ projectId: null, product, ...props }); };
  return { context, queries, replies, timers, render,
    effects() { effects.splice(0).forEach((fn) => fn()); },
    change() { listener?.(); },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
    dirty: () => dirty,
  };
}

test('draft hook stops on source replacement, account change and unmount, ignoring late results', async () => {
  const h = harness();
  h.render(); h.effects();
  assert.equal(h.queries[0].options.expectedUserId, 'seller');
  h.replies.shift().resolve({ product: { state: 'processing', sourceAssetIds: ['remote-front'], front: { tidyStatus: 'processing' } } });
  await new Promise(setImmediate);
  assert.equal([...h.timers.values()][0].ms, 5000);
  assert.equal(h.render().product.state, 'processing');
  const replaced = { colors: [{ isBase: true, images: [{ id: 'new-front', src: 'new', slot: 'Front' }] }] };
  assert.equal(h.render({ product: replaced }).product, null);
  h.effects();
  assert.equal(h.timers.size, 0);
  h.context.user = 'new-seller'; h.change();
  assert.equal(h.render({ product: replaced }).product, null);
  h.effects();
  assert.equal(h.queries.at(-1).options.expectedUserId, 'new-seller');
  h.unmount();
  while (h.replies.length) h.replies.shift().resolve({ product: { state: 'ready', sourceAssetIds: ['remote-front'] } });
  const before = h.dirty(); await new Promise(setImmediate);
  assert.equal(h.dirty(), before);
  assert.equal(h.timers.size, 0);
});

test('draft hook never polls for guests, mobile, missing token or saved projects', () => {
  for (const options of [{ user: null }, { enabled: false }, { enabled: undefined }, { mobile: true }, { token: null }, { projectId: 'saved' }]) {
    const h = harness(); Object.assign(h.context, options);
    h.render({ projectId: options.projectId || null }); h.effects();
    assert.equal(h.queries.length, 0); h.unmount();
  }
});
