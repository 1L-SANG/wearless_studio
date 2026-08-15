import test from 'node:test';
import assert from 'node:assert/strict';

import { promoteCustomMatch, stripLocalCustomMatch } from '../../src/lib/customMatchPromotion.js';

function fakeApi({ addResult, addError } = {}) {
  const calls = { uploads: [], adds: [], saves: [], cleared: 0 };
  return {
    calls,
    async uploadPhoto(projectId, payload) {
      calls.uploads.push({ projectId, ...payload });
      return { assetId: `asset-${calls.uploads.length}`, url: `u${calls.uploads.length}` };
    },
    async addCustomMatchItem(projectId, body) {
      calls.adds.push({ projectId, ...body });
      if (addError) throw addError;
      return addResult ?? {
        item: { id: 'custom_srv' },
        analysis: {
          matchClothing: [
            { id: 'custom_srv', isCustom: true, selected: false },
            { id: 'seed_1', isCustom: false, selected: false },
          ],
        },
      };
    },
    async saveAnalysis(projectId, analysis) {
      calls.saves.push({ projectId, analysis });
      return analysis;
    },
    clearCustomMatchDraft() {
      calls.cleared += 1;
    },
  };
}

const DRAFT = {
  uploads: [
    { filename: 'p1.png', mime: 'image/png', blob: 'B1' },
    { filename: 'p2.png', mime: 'image/png', blob: 'B2' },
  ],
  selected: true,
  localId: 'custom_local',
};

test('내 옷 draft 는 확정 시 재업로드되고 custom-match-item 으로 등록된다', async () => {
  const api = fakeApi();
  const out = await promoteCustomMatch(api, 'proj-1', DRAFT);

  assert.equal(out.promoted, true);
  assert.equal(api.calls.uploads.length, 2, '사진 수만큼 재업로드');
  assert.ok(api.calls.uploads.every((u) => u.purpose === 'custom_match_source'),
    '용도가 custom_match_source 여야 서버가 매칭 원본으로 취급한다');
  assert.deepEqual(api.calls.adds, [{ projectId: 'proj-1', assetIds: ['asset-1', 'asset-2'] }]);
});

test('draft 에서 선택돼 있던 내 옷은 승격본도 선택 상태로 저장된다 — 델타만 보낸다', async () => {
  const api = fakeApi();
  await promoteCustomMatch(api, 'proj-1', DRAFT);

  assert.equal(api.calls.saves.length, 1, '선택 이월 저장 1회');
  const patch = api.calls.saves[0].analysis;
  // analysis 전체를 보내면 서버 어댑터가 '추천 갱신'으로 읽고 보완 타입을 잘못 굳혀
  // 하의 상품에서 승격본까지 선택 해제된다(리뷰 확정 결함) — 델타만 보내야 한다.
  assert.deepEqual(Object.keys(patch), ['matchClothing']);
  assert.deepEqual(patch.matchClothing, [{ id: 'custom_srv', selected: true, selOrder: 1 }]);
});

test('승격이 끝나면 draft 키를 반드시 비운다 (성공·실패·409 전부)', async () => {
  const ok = fakeApi();
  await promoteCustomMatch(ok, 'p', DRAFT);
  assert.equal(ok.calls.cleared, 1, '성공 후 소거');

  const boom = fakeApi({ addError: Object.assign(new Error('down'), { status: 500 }) });
  await promoteCustomMatch(boom, 'p', DRAFT);
  assert.equal(boom.calls.cleared, 1, '실패 후에도 소거 — 다음 프로젝트로 새면 안 된다');

  const dup = fakeApi({ addError: Object.assign(new Error('exists'), { status: 409 }) });
  await promoteCustomMatch(dup, 'p', DRAFT);
  assert.equal(dup.calls.cleared, 1, '409 후에도 소거');
});

test('선택 안 된 draft 는 등록만 하고 선택 저장을 만들지 않는다', async () => {
  const api = fakeApi();
  await promoteCustomMatch(api, 'proj-1', { ...DRAFT, selected: false });
  assert.equal(api.calls.saves.length, 0);
});

test('draft 없음·빈 업로드는 아무 호출도 만들지 않는다', async () => {
  const api = fakeApi();
  assert.deepEqual(await promoteCustomMatch(api, 'p', null), { promoted: false });
  assert.deepEqual(await promoteCustomMatch(api, 'p', { uploads: [] }), { promoted: false });
  assert.equal(api.calls.uploads.length, 0);
});

test('등록 실패는 fail-open — 예외를 밖으로 던지지 않는다(확정 흐름 보존)', async () => {
  const boom = Object.assign(new Error('server down'), { status: 500 });
  const api = fakeApi({ addError: boom });
  const out = await promoteCustomMatch(api, 'p', DRAFT);
  assert.equal(out.promoted, false);
  assert.equal(out.error, boom);
});

test('409(이미 등록됨)는 재시도 합류 — 조용히 넘어간다', async () => {
  const dup = Object.assign(new Error('exists'), { status: 409 });
  const api = fakeApi({ addError: dup });
  const out = await promoteCustomMatch(api, 'p', DRAFT);
  assert.equal(out.promoted, false);
});

test('stripLocalCustomMatch 는 로컬 커스텀만 걷어내고 시드는 남긴다', () => {
  const a = stripLocalCustomMatch({
    matchClothing: [
      { id: 'custom_local', isCustom: true },
      { id: 'seed_1', isCustom: false },
    ],
    other: 'x',
  });
  assert.deepEqual(a.matchClothing.map((m) => m.id), ['seed_1']);
  assert.equal(a.other, 'x');
  assert.equal(stripLocalCustomMatch(null), null);
});
