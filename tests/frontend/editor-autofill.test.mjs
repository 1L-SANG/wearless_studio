import assert from 'node:assert/strict';
import test from 'node:test';

import { buildRoledCutPool, autofillBlocks } from '../../src/features/editor/templates/autofill.js';

// storyboard 블록: content_role 은 블록에, 컷은 sourceBlockId 로 조인해 역할 복원.
const storyboard = [
  { id: 'sb-hero', contentRole: 'hero' },
  { id: 'sb-coord', contentRole: 'coordination' },
  { id: 'sb-detail', contentRole: 'detail' },
];
const wardrobe = {
  red: [
    { id: 'c-hero', src: 'hero.jpg', generated: true, sourceBlockId: 'sb-hero', cutType: 'styling', wardrobeGroup: 'red' },
    { id: 'c-coord', src: 'coord.jpg', generated: true, sourceBlockId: 'sb-coord', cutType: 'styling', wardrobeGroup: 'red' },
  ],
  blue: [
    { id: 'c-detail', src: 'detail.jpg', generated: true, sourceBlockId: 'sb-detail', cutType: 'product', wardrobeGroup: 'blue' },
    { id: 'c-upload', src: 'mine.jpg', userUploaded: true, wardrobeGroup: 'misc' }, // 생성컷 아님 → 제외
    { id: 'c-dupe', src: 'hero.jpg', generated: true, sourceBlockId: 'sb-hero' },   // 같은 src → 중복 제외
  ],
};

const slot = (id, roleHint) => ({ id, type: 'image', frameSlot: true, checkerboard: true, src: null, ...(roleHint ? { roleHint } : {}) });
const block = (id, els) => ({ id, elements: els });

test('buildRoledCutPool: 생성컷만·src중복 제거하고 sourceBlockId 로 역할 복원', () => {
  const pool = buildRoledCutPool(wardrobe, storyboard);
  assert.equal(pool.length, 3); // hero, coord, detail (upload/dupe 제외)
  const byId = Object.fromEntries(pool.map((c) => [c.id, c]));
  assert.equal(byId['c-hero'].role, 'hero');
  assert.equal(byId['c-hero'].sectionRole, 'hooking');
  assert.equal(byId['c-detail'].role, 'detail');
  assert.equal(byId['c-detail'].sectionRole, 'product');
  assert.equal(byId['c-coord'].role, 'coordination');
});

test('autofillBlocks: roleHint 정확 매칭', () => {
  const pool = buildRoledCutPool(wardrobe, storyboard);
  const out = autofillBlocks([block('b', [slot('s1', 'hero'), slot('s2', 'detail')])], pool);
  assert.equal(out[0].elements[0].src, 'hero.jpg');
  assert.equal(out[0].elements[1].src, 'detail.jpg');
  // 조인 메타도 슬롯에 반영
  assert.equal(out[0].elements[0].cutType, 'styling');
  assert.equal(out[0].elements[0].sourceBlockId, 'sb-hero');
});

test('autofillBlocks: 같은 섹션역할 완화 매칭 (benefit→hooking→hero)', () => {
  // benefit(hooking) 컷 없음, hero(hooking) 컷 있음 → 섹션 완화로 hero 채움
  const pool = buildRoledCutPool({ red: [wardrobe.red[0]] }, storyboard); // hero 하나만
  const out = autofillBlocks([block('b', [slot('s1', 'benefit')])], pool);
  assert.equal(out[0].elements[0].src, 'hero.jpg');
});

test('autofillBlocks: 역할·섹션 모두 불일치면 any 컷으로 채움 (채움 우선)', () => {
  const pool = buildRoledCutPool({ red: [wardrobe.red[0]] }, storyboard); // hero(hooking)만
  const out = autofillBlocks([block('b', [slot('s1', 'detail')])], pool); // detail(product) 매칭 없음
  assert.equal(out[0].elements[0].src, 'hero.jpg'); // any 티어
});

test('autofillBlocks: roleHint 없는 슬롯도 any 로 채움', () => {
  const pool = buildRoledCutPool(wardrobe, storyboard);
  const out = autofillBlocks([block('b', [slot('s1')])], pool);
  assert.ok(out[0].elements[0].src); // 뭔가 채워짐
});

test('autofillBlocks: 컷 부족하면 라운드로빈 재사용', () => {
  // hero 컷 1개, hero 슬롯 3개 → 재사용
  const pool = buildRoledCutPool({ red: [wardrobe.red[0]] }, storyboard);
  const out = autofillBlocks([block('b', [slot('s1', 'hero'), slot('s2', 'hero'), slot('s3', 'hero')])], pool);
  assert.equal(out[0].elements[0].src, 'hero.jpg');
  assert.equal(out[0].elements[1].src, 'hero.jpg');
  assert.equal(out[0].elements[2].src, 'hero.jpg');
});

test('autofillBlocks: 컷 여러 개면 라운드로빈으로 분산', () => {
  // hero 컷 2개, hero 슬롯 3개 → c1, c2, c1
  const pool = [
    { id: 'h1', src: 'h1.jpg', role: 'hero', sectionRole: 'hooking' },
    { id: 'h2', src: 'h2.jpg', role: 'hero', sectionRole: 'hooking' },
  ];
  const out = autofillBlocks([block('b', [slot('s1', 'hero'), slot('s2', 'hero'), slot('s3', 'hero')])], pool);
  assert.equal(out[0].elements[0].src, 'h1.jpg');
  assert.equal(out[0].elements[1].src, 'h2.jpg');
  assert.equal(out[0].elements[2].src, 'h1.jpg');
});

test('autofillBlocks: 컷 0개면 빈 슬롯 그대로 (폴백)', () => {
  const out = autofillBlocks([block('b', [slot('s1', 'hero')])], []);
  assert.equal(out[0].elements[0].src, null);
});

test('autofillBlocks: 이미 채워진 슬롯·frameSlot 아닌 요소·텍스트는 안 건드림', () => {
  const pool = buildRoledCutPool(wardrobe, storyboard);
  const filled = { id: 'f', type: 'image', frameSlot: true, src: 'keep.jpg', roleHint: 'hero' };
  const nonSlot = { id: 'n', type: 'image', src: 'plain.jpg' }; // frameSlot 아님
  const text = { id: 't', type: 'text', text: 'DETAIL' };
  const out = autofillBlocks([block('b', [filled, nonSlot, text])], pool);
  assert.equal(out[0].elements[0].src, 'keep.jpg');
  assert.equal(out[0].elements[1].src, 'plain.jpg');
  assert.equal(out[0].elements[2].text, 'DETAIL');
});
