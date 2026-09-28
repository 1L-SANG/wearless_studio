import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { TEST_CUT_SLOTS, neighborFinish, testCutMatrix } from '../../src/features/admin/testCutMatrix.js';

const FINISHES = ['prod', 'texture', 'soft50'];
const cut = (id, skinFinish, kind, sort) => ({ id, skinFinish, kind, sort, imageUri: `/x/${id}` });

test('보정 3종을 같은 자리(확대1·확대2·전신1·전신2)끼리 줄 맞춘다', () => {
  const m = testCutMatrix([
    cut('a-f1', 'prod', 'fullbody', 3), cut('a-c1', 'prod', 'closeup', 0),
    cut('a-c2', 'prod', 'closeup', 1), cut('a-f0', 'prod', 'fullbody', 2),
    cut('b-c1', 'texture', 'closeup', 0),
    cut('legacy', null, 'closeup', 5),
  ], FINISHES);
  assert.deepEqual(TEST_CUT_SLOTS.map((s) => s.label), ['확대 1', '확대 2', '전신 1', '전신 2']);
  assert.deepEqual(m.prod.map((c) => c?.id), ['a-c1', 'a-c2', 'a-f0', 'a-f1']);
  assert.deepEqual(m.texture.map((c) => c?.id ?? null), ['b-c1', null, null, null]);
  assert.deepEqual(m.soft50, [null, null, null, null]);
});

test('크게 보기: 같은 자리에서 컷이 있는 보정만 돌고, 끝에서 처음으로 돌아간다', () => {
  const m = testCutMatrix([
    cut('a', 'prod', 'closeup', 0), cut('c', 'soft50', 'closeup', 0),
  ], FINISHES);
  assert.equal(neighborFinish(m, FINISHES, 'prod', 0, 1), 'soft50');
  assert.equal(neighborFinish(m, FINISHES, 'soft50', 0, 1), 'prod');
  assert.equal(neighborFinish(m, FINISHES, 'prod', 0, -1), 'soft50');
  assert.equal(neighborFinish(m, FINISHES, 'prod', 3, 1), null);
});

test('관리자 화면은 비교 표와 크게 보기를 쓴다(작은 가로 나열로 돌아가지 않는다)', () => {
  const src = readFileSync(new URL('../../src/features/admin/AdminModels.jsx', import.meta.url), 'utf8');
  assert.match(src, /testCutMatrix\(cuts, SKIN_FINISHES\.map/);
  assert.match(src, /<TestCutViewer/);
  assert.match(src, /grid grid-cols-4/);
  assert.doesNotMatch(src, /groupCuts/);
});
