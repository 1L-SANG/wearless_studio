import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as signatureCutPool from '../../src/lib/signatureCutPool.js';
import { repickExampleForDirection } from '../../src/lib/generationExamples.js';

const {
  SIGNATURE_CUTS,
  isSignatureExampleId,
  signatureCutsFor,
  signatureCutById,
  signatureCutMatches,
  signatureGenderFor,
  pickSignatureCut,
  repairSignatureCutSelections,
} = signatureCutPool;

/* 시그니처 컷은 생성예시 카탈로그와 분리된 전용 풀이다(오너 확정 2026-08-17).
   정본 규칙: documents/genexamples_release_contract.md 와 별개 — 갤러리 나열·자동 배정·
   발행 게이트 어디에도 이 풀이 섞이면 안 된다. */

test('풀은 성별과 적용 의류로 나뉘고 id 는 sig_ 접두를 쓴다', () => {
  assert.equal(SIGNATURE_CUTS.length, 17);
  for (const cut of SIGNATURE_CUTS) {
    assert.ok(isSignatureExampleId(cut.id), `${cut.id} 는 sig_ 접두여야 한다`);
    assert.ok(['men', 'women'].includes(cut.gender));
    assert.ok(Array.isArray(cut.applicableClothingTypes));
    assert.match(cut.thumb, /^\/assets\/signature\/thumb\//);
    assert.match(cut.url, /^\/assets\/signature\//);
  }
});

test('상품 조건별로 정확한 시그니처 풀만 돌려준다', () => {
  const ids = (context) => signatureCutsFor(context).map((cut) => cut.id);

  assert.deepEqual(ids({ gender: 'women', clothingType: 'bottom', subCategory: 'slacks' }), [
    'sig_women_bottom_pants_01',
    'sig_women_bottom_pants_02',
  ]);
  assert.deepEqual(ids({ gender: 'women', clothingType: 'bottom', subCategory: 'mini_skirt' }), [
    'sig_women_bottom_skirt_01',
    'sig_women_bottom_skirt_02',
  ]);
  assert.deepEqual(ids({ gender: 'women', clothingType: 'bottom', customCategory: '플리츠 치마' }), [
    'sig_women_bottom_skirt_01',
    'sig_women_bottom_skirt_02',
  ]);
  assert.deepEqual(ids({
    gender: 'women', clothingType: 'bottom', subCategory: 'slacks', customCategory: '플리츠 치마',
  }), [
    'sig_women_bottom_skirt_01',
    'sig_women_bottom_skirt_02',
  ]);
  assert.deepEqual(ids({ gender: 'women', clothingType: 'bottom' }), [
    'sig_women_bottom_pants_01',
    'sig_women_bottom_pants_02',
  ]);
  assert.deepEqual(ids({ gender: 'men', clothingType: 'bottom', subCategory: 'mini_skirt' }), [
    'sig_men_bottom_pants_01',
    'sig_men_bottom_pants_02',
  ]);
  assert.deepEqual(ids({ gender: 'women', clothingType: 'top' }), [
    'sig_women_01', 'sig_women_02', 'sig_women_03', 'sig_women_04',
  ]);
  assert.deepEqual(ids({ gender: 'men', clothingType: 'outer' }), [
    'sig_men_01', 'sig_men_02', 'sig_men_03', 'sig_men_04', 'sig_men_05',
  ]);
  assert.deepEqual(ids({ gender: 'women', clothingType: 'dress' }), [
    'sig_women_dress_01', 'sig_women_dress_02',
  ]);
  assert.deepEqual(ids({ gender: 'men', clothingType: 'dress' }), [
    'sig_men_01', 'sig_men_02', 'sig_men_03', 'sig_men_04', 'sig_men_05',
  ]);
});

test('시그니처 성별은 서버와 같이 대상 성별 첫 값만 정본으로 쓴다', () => {
  assert.equal(signatureGenderFor({ clothingType: 'bottom', targetGenders: ['men', 'women'] }), 'men');
  assert.equal(signatureGenderFor({ clothingType: 'bottom', targetGenders: ['women', 'men'] }), 'women');
  assert.equal(signatureGenderFor({ clothingType: 'bottom', targetGenders: [] }), 'women');
  assert.equal(signatureGenderFor({ clothingType: 'dress', targetGenders: ['men'] }), 'women');
});

test('성별 불명이면 women 풀, 하의 세부 분류가 없으면 바지 풀로 떨어진다', () => {
  assert.deepEqual(
    signatureCutsFor({ clothingType: 'bottom' }),
    signatureCutsFor({ gender: 'women', clothingType: 'bottom', subCategory: 'slacks' }),
  );
  assert.deepEqual(
    signatureCutsFor({ gender: 'unknown', clothingType: 'top' }),
    signatureCutsFor({ gender: 'women', clothingType: 'top' }),
  );
});

test('같은 프로젝트는 항상 같은 컷을 받는다(진입할 때마다 바뀌면 안 된다)', () => {
  const context = {
    gender: 'women', clothingType: 'bottom', subCategory: 'mini_skirt', projectId: 'proj-A',
  };
  const first = pickSignatureCut(context);
  for (let i = 0; i < 5; i += 1) {
    assert.equal(pickSignatureCut(context)?.id, first?.id);
  }
  assert.equal(first?.id, 'sig_women_bottom_skirt_02');

  const dressContext = {
    gender: 'women', clothingType: 'dress', projectId: 'dress-project',
  };
  assert.equal(pickSignatureCut(dressContext)?.id, 'sig_women_dress_01');
});

test('뽑힌 컷은 요청한 상품 조건의 풀 안에서만 나온다', () => {
  for (const projectId of ['p1', 'p2', 'p3', 'p4', 'p5', 'p6']) {
    const context = { gender: 'women', clothingType: 'bottom', subCategory: 'midi_skirt', projectId };
    const picked = pickSignatureCut(context);
    assert.equal(signatureCutMatches(picked.id, context), true);
  }
});

test('시그니처 카드 방향 변경은 일반 생성예시로 갈아 끼우지 않는다', () => {
  const block = {
    id: 'dress-signature',
    source: 'ai',
    hookSlotRole: 'signature',
    cutType: 'horizon',
    shot: 'medium',
    direction: 'front',
    exampleId: 'sig_women_dress_01',
    exampleSelectionOrigin: 'auto',
  };
  const ordinaryCatalog = [{
    id: 'ex_horizon_women_dress_medium_profile_01',
    cutType: 'horizon',
    shot: 'medium',
    direction: 'side',
    sideStyle: 'profile',
    gender: 'women',
    clothingType: 'dress',
    applicableClothingTypes: ['dress'],
    variants: ['all'],
    thumb: '/ordinary.webp',
  }];

  assert.equal(repickExampleForDirection(block, ordinaryCatalog, {
    clothingType: 'dress', gender: 'women', direction: 'side', sideStyle: 'profile',
  }), null);
  assert.equal(repickExampleForDirection(
    { ...block, hookSlotRole: undefined }, ordinaryCatalog,
    { clothingType: 'dress', gender: 'women', direction: 'side', sideStyle: 'profile' },
  ), null);
});

test('불일치 시그니처 선택만 결정적으로 보정하고 다른 셀러 편집은 보존한다', () => {
  assert.equal(typeof repairSignatureCutSelections, 'function');
  const context = {
    gender: 'women', clothingType: 'bottom', subCategory: 'long_skirt', projectId: 'repair-project',
  };
  const mismatched = {
    id: 'signature',
    exampleId: 'sig_women_01',
    exampleSelectionOrigin: 'user',
    colorId: 'seller-color',
    matchIds: ['seller-match'],
    pose: 'seller-pose',
  };
  const compatible = {
    ...mismatched,
    id: 'compatible',
    exampleId: 'sig_women_bottom_skirt_01',
  };
  const wrongShape = {
    ...mismatched,
    id: 'wrong-shape',
    exampleId: 'sig_women_bottom_pants_02',
  };
  const ordinary = { id: 'ordinary', exampleId: 'ex_horizon_women_bottom_medium_01' };

  const repaired = repairSignatureCutSelections(
    [mismatched, wrongShape, compatible, ordinary],
    context,
  );
  const expected = pickSignatureCut(context);

  assert.equal(repaired.changed, true);
  assert.deepEqual(repaired.repairedIds, ['signature', 'wrong-shape']);
  assert.equal(repaired.blocks[0].exampleId, expected.id);
  assert.equal(repaired.blocks[0].thumb, expected.thumb);
  assert.equal(repaired.blocks[0].exampleSelectionOrigin, 'user');
  assert.equal(repaired.blocks[0].colorId, 'seller-color');
  assert.deepEqual(repaired.blocks[0].matchIds, ['seller-match']);
  assert.equal(repaired.blocks[0].pose, 'seller-pose');
  assert.equal(repaired.blocks[1].exampleId, expected.id);
  assert.equal(repaired.blocks[2], compatible);
  assert.equal(repaired.blocks[3], ordinary);

  const upper = repairSignatureCutSelections(
    [{ ...mismatched, exampleId: 'sig_women_bottom_pants_01' }],
    { gender: 'women', clothingType: 'top', projectId: 'repair-project' },
  );
  assert.match(upper.blocks[0].exampleId, /^sig_women_0[1-4]$/);

  const dressContext = {
    gender: 'women', clothingType: 'dress', projectId: 'dress-repair-project',
  };
  const dress = repairSignatureCutSelections(
    [{ ...mismatched, id: 'dress', exampleId: 'sig_women_02' }],
    dressContext,
  );
  assert.equal(dress.changed, true);
  assert.match(dress.blocks[0].exampleId, /^sig_women_dress_0[12]$/);
  assert.equal(signatureCutMatches(dress.blocks[0].exampleId, dressContext), true);
});

test('생성예시 카탈로그에 sig_ 항목이 섞여 있지 않다', () => {
  const catalog = JSON.parse(readFileSync(new URL('../../src/data/genExamples.json', import.meta.url), 'utf8'));
  const items = Array.isArray(catalog) ? catalog : (catalog.examples || Object.values(catalog)[0]);
  assert.equal(items.filter((item) => isSignatureExampleId(item?.id)).length, 0);
});

test('썸네일 조회는 카탈로그 없이도 풀에서 해결된다', () => {
  const cut = SIGNATURE_CUTS[0];
  assert.equal(signatureCutById(cut.id)?.thumb, cut.thumb);
  assert.equal(signatureCutById('ex_styling_women_top_full_01'), null);
});

test('보드는 시그니처 슬롯에만 풀을 배정하고 사용자 선택은 보존한다', () => {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  assert.match(source, /repairSignatureCutSelections\(assignment\.blocks/);
  assert.match(source, /const signatureGender = signatureGenderFor\(\{\s*clothingType,\s*targetGenders: a\?\.targetGenders/);
  assert.match(source, /gender: signatureGender,\s*clothingType,\s*subCategory: a\?\.subCategory/);
  assert.match(source, /prepareStoryboardEntry\(prefetched, undefined, initialProjectId\)/);
  assert.match(source, /prepareStoryboardEntry\(entry, usePending \? pending : board, pid\)/);
  assert.match(source, /const normalized = sbStable\(normalizedBlocks\) !== sbStable\(sourceBlocks\)/);
  assert.match(source, /normalized,\s*assignment,\s*signatureRepair,/);
  assert.match(source, /prepared\.signatureRepair\.changed/);
  assert.match(source, /signatureGender=\{signatureGenderNow\}/);
  assert.match(source, /<SignatureCutGallery[\s\S]*clothingType=\{clothingType\}/);
  // 썸네일 관문이 카탈로그보다 풀을 먼저 본다 — 이 순서가 깨지면 자리표시자가 뜬다.
  assert.match(source, /signatureCutById\(exampleId\)\?\.thumb\s*\n\s*\|\| \(catalogs\?\.genExamples/);
});
