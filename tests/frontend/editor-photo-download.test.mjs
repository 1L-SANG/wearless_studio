import test from 'node:test';
import assert from 'node:assert/strict';
import * as exports from '../../src/features/editor/editorExport.js';

test('selected-photo download fetches original asset bytes, not a crop or thumbnail', async () => {
  assert.equal(typeof exports.downloadEditorPhoto, 'function');
  const calls = [], saved = [];
  const blob = new Blob(['original photo'], { type: 'image/webp' });
  await exports.downloadEditorPhoto({ src: '/v1/assets/photo/file', crop: { ox: 10 }, cutType: 'front' }, 'shirt', {
    fetchImage: async url => { calls.push(url); return new Response(blob, { headers: { 'content-type': 'image/webp' } }); },
    save: (data, name) => saved.push({ data, name }),
  });
  assert.match(calls[0], /^\/v1\/assets\/photo\/bytes\?e=/);
  assert.equal(saved[0].name, 'shirt-front.webp');
  assert.equal(await saved[0].data.text(), 'original photo');
});

test('failed or non-image responses never save an apparent photo download', async () => {
  assert.equal(typeof exports.downloadEditorPhoto, 'function');
  for (const response of [new Response('', { status: 403 }), new Response('<html>login</html>', { headers: { 'content-type': 'text/html' } })]) {
    let saves = 0;
    await assert.rejects(() => exports.downloadEditorPhoto({ src: '/original' }, 'shirt', {
      fetchImage: async () => response, save: () => { saves++; },
    }));
    assert.equal(saves, 0);
  }
});
