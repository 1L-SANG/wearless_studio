import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { withUploadedSrcs } from '../../src/lib/draftPromotionProduct.js';

test('public analysis preserves base packet and transports other color views separately', async t => {
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
  let photoBytes = null;
  let failOptional = false;
  const summary = { version: 1, clothingType: 'top', colors: [{ colorId: 'red', status: 'unavailable', reason: 'insufficient_evidence' }] };
  const handoff = { signature: 'signed' };
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith('https://test.invalid/')) {
      if (failOptional && String(url).includes('red-')) return new Response('unavailable', { status: 503 });
      return new Response(new Blob([photoBytes || String(url)], { type: 'image/png' }));
    }
    sent = options.body;
    return new Response(JSON.stringify({ data: { clothingType: 'top', garmentColorEvidence: summary, garmentColorEvidenceHandoff: handoff } }), { headers: { 'Content-Type': 'application/json' } });
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
  assert.deepEqual(sent.getAll('colorSlots'), ['Front', 'Back']);
  assert.deepEqual(sent.getAll('colorIds'), ['red', 'red']);
  assert.equal(sent.getAll('colorImages').length, 2);
  assert.equal(JSON.parse(sent.get('productContext')).colors.find(c => c.id === 'base').isBase, true);
  assert.deepEqual(result.garmentColorEvidence, summary);
  assert.deepEqual(result.garmentColorEvidenceHandoff, handoff);
  // Two 16-MiB base photos must remain usable when an optional two-view color would
  // make the total 64 MiB. Omit the whole optional color instead of failing AG-01.
  photoBytes = new Uint8Array(16 * 1024 * 1024);
  await httpAdapter.publicAnalyze({ colors: [
    { id: 'base', isBase: true, images: [photo('Front', 'front'), photo('Back', 'back')] },
    { id: 'red', images: [photo('Front', 'red-front'), photo('Back', 'red-back')] },
  ] });
  assert.equal(sent.getAll('images').length, 2);
  assert.equal(sent.getAll('images').reduce((sum, item) => sum + item.size, 0), 32 * 1024 * 1024);
  assert.deepEqual(sent.getAll('colorImages'), []);
  const requestBytes = (await new Request('https://test.invalid/size-check', { method: 'POST', body: sent }).arrayBuffer()).byteLength;
  assert.ok(requestBytes < 60 * 1024 * 1024);
  photoBytes = null;
  failOptional = true;
  await httpAdapter.publicAnalyze({ colors: [
    { id: 'base', isBase: true, images: [photo('Front', 'front')] },
    { id: 'red', images: [photo('Front', 'red-front')] },
  ] });
  assert.equal(sent.getAll('images').length, 1);
  assert.deepEqual(sent.getAll('colorImages'), []);
});

test('measured-color handoff is server-promoted after upload and remains optional', async () => {
  const events = [];
  const signed = { signature: 'opaque-server-handoff' };
  const api = {
    createProject: async () => ({ id: 'p' }),
    uploadPhoto: async () => { events.push('upload'); return { assetId: 'asset', url: 'https://test/photo.png' }; },
    saveProduct: async () => { events.push('product'); },
    saveAnalysis: async (_id, analysis) => {
      events.push('analysis');
      assert.equal('garmentColorEvidence' in analysis, false);
      assert.equal('garmentColorEvidenceHandoff' in analysis, false);
    },
    promoteGarmentColorEvidence: async (id, value) => { events.push('color'); assert.equal(id, 'p'); assert.deepEqual(value, signed); },
    patchProject: async () => { events.push('project'); },
  };
  globalThis.__garmentColorHarness = {
    api, withUploadedSrcs, isUploadablePhotoMime: () => true,
    createDraftSyncSingleFlight: sync => ({ sync }),
    draftPromotionSession: { read: () => ({}), rememberProject() {}, rememberAsset() {} },
    startCustomMatchPromotion: () => null, stripLocalCustomMatch: analysis => analysis,
    invalidateStoryboardEntryPrefetch() {},
  };
  const oldWarn = console.warn;
  try {
    const source = readFileSync(new URL('../../src/lib/draftSync.js', import.meta.url), 'utf8').replace(/^import .+;\r?$/gm, '');
    const declarations = `const { ${Object.keys(globalThis.__garmentColorHarness).join(', ')} } = globalThis.__garmentColorHarness;\n`;
    const module = await import(`data:text/javascript;base64,${Buffer.from(declarations + source).toString('base64')}`);
    const draft = { product: { colors: [{ images: [{ id: 'local', src: 'blob:photo' }] }] },
      photos: [{ imageId: 'local', mime: 'image/png' }],
      analysis: { garmentColorEvidence: { colors: [{ observedHex: '#ff0000' }] }, garmentColorEvidenceHandoff: signed } };
    await module.promoteDraftToProject(draft);
    assert.deepEqual(events, ['upload', 'product', 'analysis', 'color', 'project']);
    events.length = 0;
    api.promoteGarmentColorEvidence = async () => { events.push('color-failed'); throw new Error('expired'); };
    console.warn = () => {};
    await module.promoteDraftToProject(draft);
    assert.deepEqual(events, ['upload', 'product', 'analysis', 'color-failed', 'project']);
  } finally {
    console.warn = oldWarn;
    delete globalThis.__garmentColorHarness;
  }
});
