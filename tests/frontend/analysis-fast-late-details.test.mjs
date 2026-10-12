import test from 'node:test';
import assert from 'node:assert/strict';
import { createLateDetailCatalog } from '../../src/features/storyboard/storyboardLateDetails.js';
test('준비 중 먼저 온 늦은 추천을 초기 로드의 옛 카탈로그와 합쳐 보존한다', () => {
  const late = createLateDetailCatalog(); const ready = { status: 'ready', candidates: [{ id: 'late' }] };
  late.remember('p', ready);
  assert.equal(late.merge('p', null), null);
  const result = late.merge('p', { detailRecommendations: { status: 'unavailable' }, other: 'unchanged' });
  assert.deepEqual(result.detailRecommendations, ready); assert.equal(result.other, 'unchanged');
  assert.equal(late.merge('different', { detailRecommendations: null }).detailRecommendations, null);
});
