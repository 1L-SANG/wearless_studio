import test from 'node:test';
import assert from 'node:assert/strict';

import {
  fetchPublicModels,
  formatValidUntil,
  formatValidity,
  fromExampleModel,
  physiqueLine,
  sellerStudioUrl,
  sizeParts,
  sizesText,
  toBrowseModel,
} from '../../src/features/facemarket-landing/data/publicModels.js';
import { BROWSE_MODELS } from '../../src/features/facemarket-landing/data/browseModels.js';

/* =============================================================
   /models 가 실모델(공개 API)과 가상 예시를 한 모양으로 맞추는 어댑터의 계약.
   명세: documents/facemarket_testcuts_profile_spec_v1.md §3.4·§6, 상세 창 구성은 2026-09-08 오너 확정.
   ============================================================= */

const ITEM = {
  id: '11111111-1111-1111-1111-111111111111',
  displayName: '정일상',
  gender: 'male',
  ageBand: '20대 초반',
  heightCm: 178,
  heightBucket: 'm_175_180',
  bodyType: 'toned',
  closeupImageUrl: 'https://assets.example/facemarket/catalog/models/x/covers/a.webp',
  fullbodyImageUrl: 'https://assets.example/facemarket/catalog/models/x/covers/b.webp',
  license: {
    allowedUse: ['상의', '아우터'],
    unitPrice: 10000,
    validUntil: '2027-09-07T00:00:00Z',
    validDays: 365,
  },
  confirmedAt: '2026-09-07T10:00:00Z',
};

test('유효기간 라벨 — 1년·2년·90일·영구', () => {
  assert.equal(formatValidity(365), '1년');
  assert.equal(formatValidity(730), '2년');
  assert.equal(formatValidity(90), '90일');
  assert.equal(formatValidity(3650), '영구');
  assert.equal(formatValidity(null), '영구');
});

test('만료 시각은 "년 월 일까지"로, 잘못된 값은 null', () => {
  assert.equal(formatValidUntil('2027-09-07T12:00:00Z'), '2027년 9월 7일까지');
  assert.equal(formatValidUntil('nope'), null);
  assert.equal(formatValidUntil(null), '영구');
});

test('카드 보조 줄 — cm 가 있으면 cm, 없으면 키 구간, 체형은 한국어 라벨', () => {
  assert.equal(physiqueLine(ITEM), '키 178cm · 잔잔한 근육');
  assert.equal(physiqueLine({ heightBucket: 'f_160_165', bodyType: 'slim' }), '키 160–165cm · 마름');
  assert.equal(physiqueLine({ bodyType: 'glamorous' }), '글래머러스');
  // 서버 enum 밖의 값은 원문을 화면에 올리지 않는다.
  assert.equal(physiqueLine({ bodyType: 'unknown_value' }), null);
  assert.equal(physiqueLine({}), null);
});

test('착용 사이즈는 단위를 붙여 칸별로, 하나도 없으면 null', () => {
  assert.deepEqual(sizeParts({ topSize: 'm', bottomSize: 30, shoeSize: 270 }), { top: 'M', bottom: '30인치', shoe: '270mm' });
  assert.equal(sizesText({ topSize: 'm', bottomSize: 30, shoeSize: 270 }), '상의 M · 하의 30인치 · 신발 270mm');
  assert.deepEqual(sizeParts({ bottomSize: 'L' }), { bottom: 'L' });
  assert.equal(sizeParts({}), null);
  assert.equal(sizesText({}), null);
});

test('셀러 스튜디오 링크는 ai.wearless.kr 에 모델 id 를 쿼리로 붙인다', () => {
  assert.equal(sellerStudioUrl('abc/1'), 'https://ai.wearless.kr/?model=abc%2F1');
});

test('실모델 → 화면 모델: 화이트리스트 필드만 담고 상세 창 줄을 채운다', () => {
  const model = toBrowseModel({
    ...ITEM, weightKg: 62.4, topSize: 'M', bottomSize: 30, shoeSize: 270,
    email: 'leak@example.com', applicantName: '실명', r2Key: 'private/x', birthdate: '2004-03-15',
  });
  assert.equal(model.kind, 'real');
  assert.equal(model.name, '정일상');
  assert.equal(model.gender, '남성');
  assert.equal(model.ageBand, '20대 초반');
  assert.equal(model.closeup, ITEM.closeupImageUrl);
  assert.equal(model.fullbody, ITEM.fullbodyImageUrl);
  assert.equal(model.height, '178cm');
  assert.equal(model.weight, '62kg');
  assert.equal(model.sizes, '상의 M · 하의 30인치 · 신발 270mm');
  assert.deepEqual(model.sizeParts, { top: 'M', bottom: '30인치', shoe: '270mm' });
  assert.equal(model.spec, '키 178cm · 잔잔한 근육');
  assert.deepEqual(model.license, {
    uses: ['상의', '아우터'],
    unitPrice: 10000,
    validity: '1년',
    validUntilText: '2027년 9월 7일까지',
  });
  assert.equal(model.verified, true);
  // 서버가 실수로 더 보내도(이메일·실명·생년월일) 화면 모델에는 안 실린다.
  assert.deepEqual(
    Object.keys(model).sort(),
    ['ageBand', 'alt', 'closeup', 'fullbody', 'gender', 'height', 'id', 'kind', 'license', 'name', 'sizeParts', 'sizes', 'spec', 'verified', 'weight'],
  );
});

test('몸무게·사이즈·나이대가 없으면 그 줄은 null 로 비운다', () => {
  const model = toBrowseModel({ ...ITEM, ageBand: undefined });
  assert.equal(model.ageBand, null);
  assert.equal(model.weight, null);
  assert.equal(model.sizes, null);
});

test('확대샷이 없는 항목은 버린다 — 카드 사진이 비면 안 된다', () => {
  assert.equal(toBrowseModel({ ...ITEM, closeupImageUrl: null }), null);
  assert.equal(toBrowseModel(null), null);
});

test('가상 예시 → 같은 모양: 확대 = 초상, 전신 = 본인 전신 예시 첫 장(없으면 null), 예시는 검증 표시 없음', () => {
  const w2 = fromExampleModel(BROWSE_MODELS.find((m) => m.id === 'w2'));
  const w1 = fromExampleModel(BROWSE_MODELS.find((m) => m.id === 'w1'));
  assert.equal(w2.kind, 'example');
  assert.equal(w2.closeup, '/models/women/w2.webp');
  assert.match(w2.fullbody, /^\/models\/women\/w2-body-types\//);
  assert.equal(w1.fullbody, null);
  assert.equal(w1.gender, '여성');
  assert.equal(w1.spec, '168cm · 49kg');
  assert.equal(w1.height, '168cm');
  assert.equal(w1.weight, '49kg');
  assert.equal(w1.sizes, null);
  assert.equal(w1.verified, false);
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

test('영구 조건을 저장한 공개 모델은 만료 미정 대신 영구로 표시해요', () => {
  const model = toBrowseModel({...ITEM,license:{...ITEM.license,validDays:null,validUntil:null}});
  assert.equal(model.license.validity, '영구');
  assert.equal(model.license.validUntilText, '영구');
});
