import test from 'node:test';
import assert from 'node:assert/strict';
import { outfitHarness, nodes, textOf, drain } from './helpers/outfitHarness.mjs';

test('existing project processing keeps the raw photo; only a prepared draft shows the new label', async () => {
  const previousWindow = globalThis.window;
  globalThis.window = { innerHeight: 900, addEventListener() {}, removeEventListener() {} };
  const h = await outfitHarness({ getProductVisuals: async () => ({ front: { tidyStatus: 'processing' } }) });
  try {
    const { OutfitBoard } = await h.load('/src/features/analysis/OutfitBoard.jsx');
    const props = { projectId: 'saved', analysis: { clothingType: 'top', matchClothing: [] },
      product: { colors: [{ isBase: true, images: [{ id: 'front', slot: 'Front', src: 'raw-front' }] }] },
      onOpenRole() {} };
    h.render(OutfitBoard, props); await drain();
    const existing = h.render(OutfitBoard, props);
    assert.ok(nodes(existing).some((node) => node.type === 'img' && node.props.src === 'raw-front'));
    assert.ok(!textOf(existing).includes('준비 중'));
    const draft = h.render(OutfitBoard, { ...props, projectId: null,
      draftVisuals: { front: { tidyStatus: 'processing' } } });
    assert.ok(textOf(draft).includes('준비 중'));
  } finally { await h.close(); globalThis.window = previousWindow; }
});
