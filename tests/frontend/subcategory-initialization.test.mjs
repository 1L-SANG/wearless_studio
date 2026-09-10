import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { normalizeAnalysisFit } from '../../src/lib/fitAxes.js';

for (const subCategory of ['leggings', 'mini_skirt', 'midi_skirt', 'long_skirt']) {
  test(`male analysis cannot retain hidden ${subCategory}`, () => {
    const original = {
      clothingType: 'bottom', subCategory, targetGenders: ['men'],
      fitProfile: { category: 'skirt', gender: 'women', axes: { length: 'mini' } },
    };
    const normalized = normalizeAnalysisFit(original);
    assert.equal(normalized.subCategory, null);
    assert.deepEqual(normalized.targetGenders, ['men']);
    assert.equal(normalized.fitProfile, null);
    assert.equal(original.subCategory, subCategory);
    assert.equal(normalizeAnalysisFit(normalized), normalized);
  });
}

for (const category of ['skirt', 'dress']) {
  for (const length of ['mini', 'midi', 'long']) {
    const initial = {
      clothingType: category === 'skirt' ? 'bottom' : 'dress',
      subCategory: `${length}_${category}`, targetGenders: ['women'], fitProfile: null,
    };
    test(`AI-selected ${initial.subCategory} seeds the first generation length`, () => {
      const normalized = normalizeAnalysisFit(initial);
      assert.equal(normalized.fitProfile.category, category);
      assert.equal(normalized.fitProfile.axes.length, length);
      assert.equal(normalized.fitProfile.source, 'auto');
      assert.equal(initial.fitProfile, null);
      assert.equal(normalizeAnalysisFit(normalized), normalized);
    });
    for (const selectedLength of ['long', null]) {
      test(`${initial.subCategory} preserves a later seller length of ${selectedLength}`, () => {
        const changed = { ...initial, fitProfile: {
          category, gender: 'women', axes: { length: selectedLength }, source: 'seller', version: 2,
        } };
        assert.equal(normalizeAnalysisFit(changed), changed);
        assert.equal(changed.fitProfile.axes.length, selectedLength);
      });
    }
  }
}

test('legacy skirt remains usable without inventing a length', () => {
  const legacy = { clothingType: 'bottom', subCategory: 'skirt', targetGenders: ['women'], fitProfile: null };
  assert.equal(normalizeAnalysisFit(legacy), legacy);
});

test('public AI responses are normalized before the input form receives them', async t => {
  const root = new URL('../..', import.meta.url).pathname;
  const server = await createServer({
    configFile: false, root, logLevel: 'silent',
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, hmr: false },
    plugins: [{
      name: 'subcategory-api-test', enforce: 'pre',
      resolveId(id) {
        if (id.endsWith('/lib/supabase.js')) return '\0subcategory-auth';
      },
      load(id) {
        if (id === '\0subcategory-auth') return 'export const supabase={auth:{getSession:async()=>({data:{session:null}})}};';
      },
    }],
  });
  const previousFetch = globalThis.fetch;
  let ai;
  globalThis.fetch = async url => {
    if (url === 'https://test.invalid/photo') return new Response(new Blob(['photo'], { type: 'image/png' }));
    assert.ok(String(url).endsWith('/v1/public/analyze'));
    return new Response(JSON.stringify({ data: ai }), { headers: { 'Content-Type': 'application/json' } });
  };
  t.after(async () => { globalThis.fetch = previousFetch; await server.close(); });
  const { httpAdapter } = await server.ssrLoadModule('/src/lib/api/httpAdapter.js');
  const product = { colors: [{ isBase: true, images: [{ slot: 'Front', src: 'https://test.invalid/photo' }] }] };
  ai = { clothingType: 'dress', subCategory: 'midi_dress', targetGenders: ['women'] };
  const dress = await httpAdapter.publicAnalyze(product);
  assert.equal(dress.subCategory, 'midi_dress');
  assert.equal(dress.fitProfile.axes.length, 'midi');
  ai = { clothingType: 'bottom', subCategory: 'leggings', targetGenders: ['men'] };
  const male = await httpAdapter.publicAnalyze(product);
  assert.equal(male.subCategory, null);
  assert.deepEqual(male.targetGenders, ['men']);
});
