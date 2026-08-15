import test from 'node:test';
import assert from 'node:assert/strict';

import {
  rememberCustomMatchDraft, clearCustomMatchDraft, readCustomMatchDraft,
} from '../../src/mock/customMatchDraftStore.js';
import { recommendLegacyMatchClothing } from '../../src/mock/matchingRecommendation.js';

// 2026-08-14 전수조사에서 잡은 실제 킬러 경로의 재현:
// 모달이 내 옷을 추가하면 500ms 뒤 자동으로 닫히며 refreshMatchClothing 이 돌고,
// 그 재구성이 toLegacyMatchItem 화이트리스트를 지나며 승격 키(sourceAssetIds)를
// 지웠다 → 확정 시 getCustomMatchDraft() null → 서버 등록 0건 → 누끼 영원히 안 돎.

const CUSTOM = {
  id: 'custom_x',
  name: '커스텀 하의',
  thumb: 'blob:a',
  thumbnailUrl: 'blob:a',
  imageUrl: 'blob:a',
  gender: 'unisex',
  clothingType: 'bottom',
  category: '팬츠',
  fit: 'regular',
  length: 'full',
  fitCategory: 'pants',
  isCustom: true,
  isCompatible: true,
  selected: true,
  selOrder: 1,
  sourceAssetIds: ['a1', 'a2'],
};

test('킬러 경로: 목록 재구성(toLegacyMatchItem)을 지나도 승격 키가 살아남는다', () => {
  const rebuilt = recommendLegacyMatchClothing({
    clothingType: 'top',
    targetGenders: ['women'],
    current: [CUSTOM],
    defaultSelection: false,
  });
  const custom = rebuilt.find((m) => m.isCustom);
  assert.ok(custom, '커스텀 아이템은 재구성 목록에 보존된다');
  assert.deepEqual(custom.sourceAssetIds, ['a1', 'a2'],
    '승격 키(sourceAssetIds)가 화이트리스트에서 떨어지면 확정 승격이 조용히 무산된다');
});

test('전용 저장소는 목록 재구성과 무관하게 승격 키를 보존한다', () => {
  clearCustomMatchDraft();
  rememberCustomMatchDraft({ assetIds: ['a1', 'a2'] });
  // 목록이 몇 번 재구성되든 저장소는 영향받지 않는다 — 근본 방어선.
  recommendLegacyMatchClothing({ clothingType: 'top', current: [], defaultSelection: false });
  assert.deepEqual(readCustomMatchDraft(), { assetIds: ['a1', 'a2'] });
  clearCustomMatchDraft();
  assert.equal(readCustomMatchDraft(), null);
});

test('빈/잘못된 assetIds 는 저장하지 않는다', () => {
  clearCustomMatchDraft();
  rememberCustomMatchDraft({ assetIds: [] });
  assert.equal(readCustomMatchDraft(), null);
  rememberCustomMatchDraft({ assetIds: null });
  assert.equal(readCustomMatchDraft(), null);
});
