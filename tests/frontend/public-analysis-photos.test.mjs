import test from 'node:test';
import assert from 'node:assert/strict';

import { selectPublicAnalysisPhotos } from '../../src/lib/publicAnalysisPhotos.js';

test('public analysis selects Front then Back before remaining images', () => {
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
    ['front-1', 'back-1', 'front-2', 'detail-1'],
  );
});
