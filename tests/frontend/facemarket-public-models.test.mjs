import test from 'node:test';
import assert from 'node:assert/strict';

import {
  fetchPublicModels,
  formatValidity,
  fromExampleModel,
  physiqueLine,
  toBrowseModel,
} from '../../src/features/facemarket-landing/data/publicModels.js';
import { BROWSE_MODELS } from '../../src/features/facemarket-landing/data/browseModels.js';

/* =============================================================
   /models 가 실모델(공개 API)과 가상 예시를 한 모양으로 맞추는 어댑터의 계약.
   명세: documents/facemarket_testcuts_profile_spec_v1.md §3.4·§6.
   ============================================================= */

const ITEM = {
  id: '11111111-1111-1111-1111-111111111111',
  displayName: '정일상',
  gender: 'male',
  heightCm: 178,
  heightBucket: 'm_175_180',
  bodyType: 'toned',
  closeupImageUrl: 'https://assets.example/facemarket/catalog/models/x/covers/a.webp',
  fullbodyImageUrl: 'https://assets.example/facemarket/catalog/models/x/covers/b.webp',
  license: { allowedUse: ['상의', '아우터'], unitPrice: 10000, validUntil: '2027-09-07T00:00:00Z', validDays: 365 },
  confirmedAt: '2026-09-07T10:00:00Z',
};

test('유효기간 라벨 — 1년·2년·90일·영구', () => {
  assert.equal(formatValidity(365), '1년');
  assert.equal(formatValidity(730), '2년');
  assert.equal(formatValidity(90), '90일');
  assert.equal(formatValidity(3650), '영구');
  assert.equal(formatValidity(null), null);
});

test('카드 보조 줄 — cm 가 있으면 cm, 없으면 키 구간, 체형은 한국어 라벨', () => {
  assert.equal(physiqueLine(ITEM), '키 178cm · 잔잔한 근육');
  assert.equal(physiqueLine({ heightBucket: 'f_160_165', bodyType: 'slim' }), '키 160–165cm · 마름');
  assert.equal(physiqueLine({ bodyType: 'glamorous' }), '글래머러스');
  // 서버 enum 밖의 값은 원문을 화면에 올리지 않는다.
  assert.equal(physiqueLine({ bodyType: 'unknown_value' }), null);
  assert.equal(physiqueLine({}), null);
});

test('실모델 → 화면 모델: 화이트리스트 필드만 담고 확대·전신 이미지를 나눈다', () => {
  const model = toBrowseModel({ ...ITEM, email: 'leak@example.com', applicantName: '실명', r2Key: 'private/x' });
  assert.equal(model.kind, 'real');
  assert.equal(model.name, '정일상');
  assert.equal(model.closeup, ITEM.closeupImageUrl);
  assert.equal(model.fullbody, ITEM.fullbodyImageUrl);
  assert.equal(model.spec, '키 178cm · 잔잔한 근육');
  assert.deepEqual(model.body, [{ dt: '키', dd: '178cm' }, { dt: '체형', dd: '잔잔한 근육' }]);
  assert.deepEqual(model.license, { uses: ['상의', '아우터'], unitPrice: 10000, validity: '1년' });
  // 서버가 실수로 더 보내도 화면 모델에는 안 실린다.
  assert.deepEqual(
    Object.keys(model).sort(),
    ['alt', 'body', 'closeup', 'fullbody', 'id', 'kind', 'license', 'name', 'spec'],
  );
});

test('확대샷이 없는 항목은 버린다 — 카드 사진이 비면 안 된다', () => {
  assert.equal(toBrowseModel({ ...ITEM, closeupImageUrl: null }), null);
  assert.equal(toBrowseModel(null), null);
});

test('가상 예시 → 같은 모양: 확대 = 초상, 전신 = 본인 전신 예시 첫 장(없으면 null)', () => {
  const w2 = fromExampleModel(BROWSE_MODELS.find((m) => m.id === 'w2'));
  const w1 = fromExampleModel(BROWSE_MODELS.find((m) => m.id === 'w1'));
  assert.equal(w2.kind, 'example');
  assert.equal(w2.closeup, '/models/women/w2.webp');
  assert.match(w2.fullbody, /^\/models\/women\/w2-body-types\//);
  assert.equal(w1.fullbody, null);
  assert.equal(w1.spec, '168cm · 49kg');
  assert.deepEqual(w1.body, [{ dt: '키', dd: '168cm' }, { dt: '몸무게', dd: '49kg' }]);
  assert.ok(w1.license.validity);
});

test('공개 목록 fetch — items 를 화면 모델로 바꾸고, 실패는 던진다', async () => {
  const ok = async () => ({ ok: true, status: 200, json: async () => ({ items: [ITEM, { id: 'no-image' }] }) });
  const models = await fetchPublicModels({ fetchImpl: ok });
  assert.equal(models.length, 1);
  assert.equal(models[0].name, '정일상');

  const down = async () => ({ ok: false, status: 503, json: async () => ({}) });
  await assert.rejects(() => fetchPublicModels({ fetchImpl: down }), /503/);
});
