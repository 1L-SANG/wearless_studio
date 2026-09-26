import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { withUploadedSrcs } from '../../src/lib/draftPromotionProduct.js';

test('public analysis sends only the base color photos and returns no garment color keys', async t => {
  const server = await createServer({
    configFile: false, root: fileURLToPath(new URL('../..', import.meta.url)), logLevel: 'silent',
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, hmr: false },
    plugins: [{ name: 'color-auth-test', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/lib/supabase.js')) return '\0color-auth'; },
      load(id) { if (id === '\0color-auth') return 'export const supabase={auth:{getSession:async()=>({data:{session:null}})}};'; },
    }],
  });
  const oldFetch = globalThis.fetch;
  let sent;
  const fetched = [];
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith('https://test.invalid/')) {
      fetched.push(String(url));
      return new Response(new Blob([String(url)], { type: 'image/png' }));
    }
    sent = options.body;
    return new Response(JSON.stringify({ data: { clothingType: 'top' } }), { headers: { 'Content-Type': 'application/json' } });
  };
  t.after(async () => { globalThis.fetch = oldFetch; await server.close(); });
  const { httpAdapter } = await server.ssrLoadModule('/src/lib/api/httpAdapter.js');
  const photo = (slot, name) => ({ slot, src: `https://test.invalid/${name}` });
  const result = await httpAdapter.publicAnalyze({ colors: [
    { id: 'red', images: [photo('Front', 'red-front'), photo('Back', 'red-back'), photo('Detail', 'red-detail')] },
    { id: 'base', isBase: true, images: [photo('Detail', 'detail'), photo('Back', 'back'), photo('Front', 'front')] },
  ] });
  assert.deepEqual(sent.getAll('slots'), ['Front', 'Back', 'Detail']);
  assert.equal(sent.getAll('images').length, 3);
  assert.equal(fetched.some(url => url.includes('red-')), false);
  for (const key of ['colorImages', 'colorSlots', 'colorIds']) assert.equal(sent.has(key), false, key);
  assert.equal('isBase' in JSON.parse(sent.get('productContext')).colors[0], false);
  assert.equal('garmentColorEvidence' in result, false);
  assert.equal('garmentColorEvidenceHandoff' in result, false);
});

test('draft promotion never promotes garment color evidence', async () => {
  const events = [];
  const api = {
    createProject: async () => ({ id: 'p' }),
    uploadPhoto: async () => { events.push('upload'); return { assetId: 'asset', url: 'https://test/photo.png' }; },
    saveProduct: async () => { events.push('product'); },
    saveAnalysis: async () => { events.push('analysis'); },
    measureGarmentColors: async () => { events.push('measure'); },
    patchProject: async () => { events.push('project'); },
  };
  globalThis.__garmentColorHarness = {
    api, withUploadedSrcs, isUploadablePhotoMime: () => true,
    createDraftSyncSingleFlight: sync => ({ sync }),
    draftPromotionSession: { read: () => ({}), rememberProject() {}, rememberAsset() {} },
    startCustomMatchPromotion: () => null, stripLocalCustomMatch: analysis => analysis,
    invalidateStoryboardEntryPrefetch() {},
  };
  try {
    const source = readFileSync(new URL('../../src/lib/draftSync.js', import.meta.url), 'utf8').replace(/^import .+;\r?$/gm, '');
    assert.doesNotMatch(source, /garmentColor|GarmentColor/);
    const declarations = `const { ${Object.keys(globalThis.__garmentColorHarness).join(', ')} } = globalThis.__garmentColorHarness;\n`;
    const module = await import(`data:text/javascript;base64,${Buffer.from(declarations + source).toString('base64')}`);
    const draft = { product: { colors: [{ images: [{ id: 'local', src: 'blob:photo' }] }] },
      photos: [{ imageId: 'local', mime: 'image/png' }], analysis: { clothingType: 'top' } };
    await module.promoteDraftToProject(draft);
    assert.deepEqual(events, ['upload', 'product', 'analysis', 'project']);
  } finally {
    delete globalThis.__garmentColorHarness;
  }
});
