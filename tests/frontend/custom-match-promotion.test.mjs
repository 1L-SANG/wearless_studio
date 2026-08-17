import test from 'node:test';
import assert from 'node:assert/strict';

import {
  clearCustomMatchPromotionTask,
  applyPromotedMatchSelection,
  getCustomMatchPromotionTask,
  promoteCustomMatch,
  startCustomMatchPromotion,
  stripLocalCustomMatch,
} from '../../src/lib/customMatchPromotion.js';

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

test('내 옷 사진은 병렬 업로드하되 assetIds 는 입력 순서를 유지한다', async () => {
  const releases = new Map();
  const started = [];
  let addedAssetIds = null;
  const api = {
    uploadPhoto(_projectId, payload) {
      started.push(payload.filename);
      return new Promise((resolve) => releases.set(payload.filename, resolve));
    },
    async addCustomMatchItem(_projectId, body) {
      addedAssetIds = body.assetIds;
      return { analysis: { matchClothing: [{ id: 'custom_srv', isCustom: true }] } };
    },
    clearCustomMatchDraft() {},
  };

  const promotion = promoteCustomMatch(api, 'proj-parallel', { ...DRAFT, selected: false });
  await Promise.resolve();
  assert.deepEqual(started, ['p1.png', 'p2.png'], '첫 업로드 완료 전 두 요청이 모두 시작된다');
  releases.get('p2.png')({ assetId: 'asset-second' });
  releases.get('p1.png')({ assetId: 'asset-first' });
  await promotion;
  assert.deepEqual(addedAssetIds, ['asset-first', 'asset-second']);
});

test('콘티는 같은 승격 완료 프라미스를 구독하고 완료 콜백은 한 번 실행된다', async () => {
  const projectId = 'proj-background-task';
  clearCustomMatchPromotionTask(projectId);
  let releaseUpload;
  let settled = 0;
  const api = fakeApi();
  api.uploadPhoto = () => new Promise((resolve) => { releaseUpload = resolve; });

  const task = startCustomMatchPromotion(api, projectId, { ...DRAFT, uploads: [DRAFT.uploads[0]] }, {
    onSettled: () => { settled += 1; },
  });
  assert.equal(task.status, 'pending');
  assert.equal(getCustomMatchPromotionTask(projectId)?.promise, task.promise);
  releaseUpload({ assetId: 'asset-bg' });
  const result = await task.promise;
  assert.equal(result.promoted, true);
  assert.equal(task.status, 'settled');
  assert.equal(settled, 1);
  clearCustomMatchPromotionTask(projectId);
});

test('승격된 선택은 자동 매칭 컷에 반영하되 사용자가 고른 컷은 보존한다', () => {
  const colors = [{ id: 'base', isBase: true, swatchId: 'black' }];
  const matches = [{ id: 'custom_srv', isCustom: true, selected: true, isCompatible: true }];
  const automatic = { id: 'auto', source: 'ai', cutType: 'horizon', colorId: 'base', matchIds: ['seed'] };
  const user = { id: 'user', source: 'ai', cutType: 'styling', colorId: 'base', matchIds: ['mine'], matchIdsOrigin: 'user' };
  const product = { id: 'product', source: 'ai', cutType: 'product', colorId: 'base', matchIds: [] };

  const next = applyPromotedMatchSelection([automatic, user, product], colors, matches);
  assert.deepEqual(next[0].matchIds, ['custom_srv']);
  assert.equal(next[1], user);
  assert.equal(next[2], product);
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
  assert.deepEqual(await promoteCustomMatch(api, 'p', null),
                   { promoted: false, attempted: false });
  assert.deepEqual(await promoteCustomMatch(api, 'p', { uploads: [] }),
                   { promoted: false, attempted: false });
  assert.equal(api.calls.uploads.length, 0);
});

test('등록 실패는 fail-open — 예외를 밖으로 던지지 않는다(확정 흐름 보존)', async () => {
  const boom = Object.assign(new Error('server down'), { status: 500 });
  const api = fakeApi({ addError: boom });
  const out = await promoteCustomMatch(api, 'p', DRAFT);
  assert.equal(out.promoted, false);
  assert.equal(out.error, boom);
});

test('409(이미 등록됨)는 재시도 합류 — 실패로 치지 않는다(경고 없음)', async () => {
  const dup = Object.assign(new Error('exists'), { status: 409 });
  const api = fakeApi({ addError: dup });
  const out = await promoteCustomMatch(api, 'p', DRAFT);
  assert.equal(out.promoted, true, '이미 등록돼 있으므로 성공으로 간주 — 토스트 안 띄운다');
  assert.equal(out.attempted, true);
});

test('시도했는데 진짜 실패면 attempted+promoted=false 로 화면이 경고할 수 있다', async () => {
  const boom = Object.assign(new Error('down'), { status: 500 });
  const out = await promoteCustomMatch(fakeApi({ addError: boom }), 'p', DRAFT);
  assert.equal(out.attempted, true);
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

// ── 하의 상품 + 매칭 상의 (2026-08-15 전수조사 확정 결함) ─────────────────────
import { mergeMatchSelection } from '../../src/lib/api/matchSelection.js';

const CUSTOM_TOP = { id: 'custom_srv', isCustom: true, clothingType: 'top', isCompatible: true };
const SEED_TOP = { id: 'seed_top', isCustom: false, clothingType: 'top', isCompatible: true };

test('clothingType 불명이면 타입 필터를 생략한다 — 승격 직후 선택이 살아남는다', () => {
  // 승격 직후 캐시엔 clothingType 이 없다(draftSync 가 product 로 미러하며 제거).
  // 예전엔 없으면 'bottom' 으로 굳어 매칭 상의가 전부 탈락했다.
  const out = mergeMatchSelection(
    [CUSTOM_TOP, SEED_TOP],
    [{ id: 'custom_srv', selected: true, selOrder: 1 }],
    undefined,
  );
  const custom = out.find((m) => m.id === 'custom_srv');
  assert.equal(custom.selected, true, '타입 불명이어도 선택이 유지된다');
  assert.equal(custom.selOrder, 1);
});

test('하의 상품이면 매칭 상의가 정상 선택된다', () => {
  const out = mergeMatchSelection(
    [CUSTOM_TOP, SEED_TOP],
    [{ id: 'custom_srv', selected: true, selOrder: 1 }],
    'bottom',
  );
  assert.equal(out.find((m) => m.id === 'custom_srv').selected, true);
});

test('원피스는 매칭 선택이 없다 (기존 의미 보존)', () => {
  const out = mergeMatchSelection(
    [CUSTOM_TOP],
    [{ id: 'custom_srv', selected: true, selOrder: 1 }],
    'dress',
  );
  assert.equal(out.find((m) => m.id === 'custom_srv').selected, false);
});

test('상의 상품이면 매칭 상의는 타입 불일치로 탈락한다', () => {
  const out = mergeMatchSelection(
    [CUSTOM_TOP],
    [{ id: 'custom_srv', selected: true, selOrder: 1 }],
    'top',
  );
  assert.equal(out.find((m) => m.id === 'custom_srv').selected, false);
});

/* 리뷰 후속(2026-08-17): 승격 결과 소비가 콘티보드 마운트에 묶여 있어, 셀러가 화면을
   빨리 떠나면 실패 안내가 조용히 사라졌다. 알림을 태스크 층으로 올려 화면과 분리한다. */
test('실패 알림은 화면과 무관하게 프로젝트당 한 번만 전달된다', async () => {
  const mod = await import('../../src/lib/customMatchPromotion.js');
  const { startCustomMatchPromotion, onCustomMatchPromotionFailure, clearCustomMatchPromotionTask } = mod;

  const seen = [];
  const off = onCustomMatchPromotionFailure((id) => seen.push(id));
  const api = {
    uploadPhoto: async () => { throw new Error('upload boom'); },
    addCustomMatchItem: async () => ({}),
    saveAnalysis: async () => ({}),
  };
  const draft = { uploads: [{ filename: 'a.jpg', mime: 'image/jpeg', blob: new Blob(['x']) }] };

  const task = startCustomMatchPromotion(api, 'proj-fail', draft);
  await task.promise.catch(() => {});
  assert.deepEqual(seen, ['proj-fail'], '구독자가 실패를 정확히 한 번 받는다');

  off();
  clearCustomMatchPromotionTask('proj-fail');
});

test('추적하는 승격 태스크는 무한정 쌓이지 않는다', async () => {
  const mod = await import('../../src/lib/customMatchPromotion.js');
  const { startCustomMatchPromotion, getCustomMatchPromotionTask, clearCustomMatchPromotionTask } = mod;
  const api = {
    uploadPhoto: async () => ({ assetId: 'a1' }),
    addCustomMatchItem: async () => ({ analysis: { matchClothing: [] } }),
    saveAnalysis: async () => ({}),
  };
  const draft = () => ({ uploads: [{ filename: 'a.jpg', mime: 'image/jpeg', blob: new Blob(['x']) }] });

  const ids = Array.from({ length: 12 }, (_, i) => `bulk-${i}`);
  for (const id of ids) await startCustomMatchPromotion(api, id, draft())?.promise?.catch(() => {});
  // 오래된 것부터 버려지므로 처음 것은 사라지고 마지막 것은 남는다.
  assert.equal(getCustomMatchPromotionTask('bulk-0'), null);
  assert.ok(getCustomMatchPromotionTask(ids.at(-1)));
  ids.forEach(clearCustomMatchPromotionTask);
});
