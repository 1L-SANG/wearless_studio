import test from 'node:test';
import assert from 'node:assert/strict';

import { selectPublicAnalysisPhotos } from '../../src/lib/publicAnalysisPhotos.js';

test('public analysis prioritizes Front and Back then orders the selected evidence by source slot', () => {
  const images = [
    { id: 'front-1', slot: 'Front' },
    { id: 'front-2', slot: 'Front' },
    { id: 'detail-1', slot: 'Detail' },
    { id: 'detail-2', slot: 'Detail' },
    { id: 'detail-3', slot: 'Detail' },
    { id: 'back-1', slot: 'Back' },
  ];

  assert.deepEqual(
    selectPublicAnalysisPhotos(images).map((image) => image.id),
    ['front-1', 'front-2', 'back-1', 'detail-1'],
  );
});

const { preparePublicAnalysis, publicAnalysisForm } = await import('../../src/lib/api/publicAnalysisRequest.js');
test('두 요청은 같은 선택과 바이트를 재사용하고 슬롯도 SHA-256 서명에 포함한다', async () => {
  const bytes = new Blob(['same bytes'], { type: 'image/jpeg' });
  const product = { colors: [{ id: 'base', isBase: true, images: [{ id: 'a', slot: 'Front', src: 'local-a' }, { id: 'b', slot: 'Back', src: 'local-b' }] }] };
  let reads = 0;
  const prepared = await preparePublicAnalysis(product, { fetchBlob: async () => { reads++; return bytes; } });
  assert.equal(reads, 2);
  assert.equal(prepared.photos[0].blob, bytes);
  const core = publicAnalysisForm(product, prepared.photos, { parts: 'core', runId: 'test_run_123' });
  const features = publicAnalysisForm(product, prepared.photos, { runId: 'test_run_123', attempt: 1 });
  assert.equal(core.get('parts'), 'core'); assert.equal(features.get('parts'), null); assert.equal(features.get('attempt'), '1');
  assert.deepEqual(core.getAll('slots'), features.getAll('slots'));
  const changed = await preparePublicAnalysis({ colors: [{ ...product.colors[0], images: [{ ...product.colors[0].images[0], slot: 'Detail' }] }] }, { fetchBlob: async () => bytes });
  assert.notEqual(prepared.photoSignature, changed.photoSignature);
});
