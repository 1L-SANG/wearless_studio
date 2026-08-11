import test from 'node:test';
import assert from 'node:assert/strict';

import { createProductPhotoPreviewRegistry } from '../../src/features/product-input/productPhotoPreviewRegistry.js';

const flushPromises = () => new Promise((resolve) => setImmediate(resolve));

test('product photo preview registry keeps thumbnails separate and revokes replacements', async () => {
  const revoked = [];
  const registry = createProductPhotoPreviewRegistry({
    createThumbnail: async (url) => `blob:thumb-${url.replace(':', '-')}`,
    revokeObjectUrl: (url) => revoked.push(url),
  });
  const productImages = [{ id: 'image-1', src: 'blob:original-1', name: 'coat.jpg' }];

  registry.sync(productImages);
  await flushPromises();
  assert.equal(registry.displayUrl('image-1', 'blob:original-1'), 'blob:thumb-blob-original-1');
  assert.deepEqual(productImages, [{ id: 'image-1', src: 'blob:original-1', name: 'coat.jpg' }]);

  registry.sync([{ id: 'image-1', src: 'blob:original-2' }]);
  await flushPromises();
  assert.deepEqual(revoked, ['blob:thumb-blob-original-1', 'blob:original-1']);

  registry.release('image-1');
  assert.deepEqual(revoked, [
    'blob:thumb-blob-original-1',
    'blob:original-1',
    'blob:thumb-blob-original-2',
    'blob:original-2',
  ]);
});

test('registry revokes owned originals and thumbnails on sync removal and dispose', async () => {
  const revoked = [];
  const registry = createProductPhotoPreviewRegistry({
    createThumbnail: async (url) => `blob:thumb-${url}`,
    revokeObjectUrl: (url) => revoked.push(url),
  });

  registry.sync([
    { id: 'owned', src: 'blob:owned-original' },
    { id: 'remote', src: 'https://cdn.test/photo.jpg' },
  ]);
  await flushPromises();
  registry.sync([{ id: 'remote', src: 'https://cdn.test/photo.jpg' }]);
  registry.dispose();

  assert.deepEqual(revoked, [
    'blob:thumb-blob:owned-original',
    'blob:owned-original',
    'blob:thumb-https://cdn.test/photo.jpg',
  ]);
});
