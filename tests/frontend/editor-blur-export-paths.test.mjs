import test from 'node:test';
import assert from 'node:assert/strict';
import { blurExportHarness } from './helpers/editorBlurExportHarness.mjs';

const paths = [
  ['one block PNG', ({ api, block }) => api.exportBlockPng(block, 'shirt', 0)],
  ['all blocks long PNG', ({ api, block }) => api.exportLongPng([block, block], 'shirt')],
  ['all blocks ZIP', ({ api, block }) => api.exportBlocksZip([block], 'shirt')],
  ['storefront split JPEG', ({ api, block }) => api.exportStorefrontDownload([block], 'shirt', null, { preset: 'smartstore', delivery: 'split', format: 'jpeg' })],
  ['storefront single PNG', ({ api, block }) => api.exportStorefrontDownload([block], 'shirt', null, { preset: 'high', delivery: 'single', format: 'png' })],
];
for (const [name, run] of paths) test(`${name} bakes pixels after safe bytes and before probes/SVG`, async t => {
  const harness = blurExportHarness(t);
  await run(harness);
  assert.ok(harness.events.indexOf('bytes') < harness.events.indexOf('pixel-bake'));
  assert.ok(harness.events.indexOf('pixel-bake') < harness.events.indexOf('probe'));
  assert.ok(harness.events.indexOf('probe') < harness.events.indexOf('svg'));
  assert.ok(harness.events.includes('encode'));
  assert.equal(harness.saves.length, 1);
});

for (const destination of ['detail publication', 'mall registration publication']) test(`${destination} uploads the same baked capture`, async t => {
  const harness = blurExportHarness(t);
  let uploaded;
  const result = await harness.publishDetailPage([harness.block], { projectId: 'p', productName: 'shirt', preset: 'smartstore' }, {
    api: {
      createDetailPublication: async () => ({ publicationId: 'pub', uploads: [{ index: 1, uploadToken: 'token', uploadUrl: 'https://local.invalid/upload' }] }),
      completeDetailPublication: async () => ({ files: [{ url: 'https://local.invalid/file' }] }),
    },
    readDimensions: async () => ({ width: 860, height: 8 }),
    fetchImpl: async (_url, options) => { uploaded = options.body; return new Response(''); },
  });
  assert.equal(await uploaded.text(), 'baked output');
  assert.equal(result.files[0].blob, uploaded);
  assert.ok(harness.events.indexOf('pixel-bake') < harness.events.indexOf('svg'));
});

test('capture integration fails if bake is omitted, rather than accepting an SVG-only blur', async t => {
  const harness = blurExportHarness(t, { omitBake: true });
  await assert.rejects(harness.api.exportBlockPng(harness.block, 'shirt', 0), /baked pixels/);
  assert.equal(harness.saves.length, 0);
});
