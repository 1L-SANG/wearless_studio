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
