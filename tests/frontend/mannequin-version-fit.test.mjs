import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import * as versionFit from '../../src/features/mannequin/cutFitProfile.js';

const {
  analysisForCutFitProfile,
  cutFitProfileRecord,
} = versionFit;

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
};

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('버전 핏 기록은 null 사진 그대로와 기록 없는 옛 컷을 구분한다', () => {
  const current = { fitProfile: { axes: { fit: 'over' } } };
  const photoAsIs = { fitProfileRecorded: true, fitProfile: null };
  const legacy = {};

  assert.deepEqual(cutFitProfileRecord(photoAsIs), { recorded: true, profile: null });
  assert.equal(analysisForCutFitProfile(current, photoAsIs).fitProfile, null);
  assert.equal(analysisForCutFitProfile(current, legacy), current);
});

test('썸네일 선택과 진행은 선택 버전의 fitProfile을 저장한다', () => {
  const source = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
    'utf8',
  );
  const choose = source.slice(
    source.indexOf('const chooseCut = (cutId) =>'),
    source.indexOf('const applyToneRenderToSelectedCut'),
  );
  const proceed = source.slice(
    source.indexOf('const continueWithSelectedVersion = async'),
    source.indexOf('const onContinueCurrentVersion ='),
  );

  assert.match(choose, /cutFitProfileRecord\(cut\)/);
  assert.match(choose, /api\.saveAnalysis\(projectId, \{ fitProfile: record\.profile \}\)/);
  assert.match(proceed, /const profile = record\.recorded \? record\.profile : legacyProfile/);
  assert.match(proceed, /await selectMannequin\(selected\.id\)/);
  assert.match(proceed, /await api\.saveAnalysis\(projectId, \{ fitProfile: profile \}\)/);
});

test('재시도 소진 패널에서도 현재 버전 진행 버튼이 노출된다', () => {
  const source = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
    'utf8',
  );

  assert.match(source, /selected && \(!showWaitPanel \|\| waitFailure\)/);
  assert.match(source, /onClick=\{waitFailure \? onContinueCurrent : onContinue\}/);
  assert.match(source, /`현재 버전으로 진행 · \$\{aiCutCount/);
});

test('유료 재생성은 기존 저장과 현재 버전·기준 핏 재확정이 모두 끝나야 시작한다', async () => {
  const run = versionFit.regenerateWithPersistedSelectedVersion;
  assert.equal(typeof run, 'function', '유료 재생성 전 선택 저장을 보장하는 경계가 필요하다');

  const pendingPersistence = deferred();
  const selectionPersistence = deferred();
  const fitPersistence = deferred();
  const selectedBaseline = { version: 2, axes: { fit: 'regular' }, source: 'auto' };
  const adjustedProfile = { version: 2, axes: { fit: 'over' }, source: 'seller' };
  const events = [];

  const result = run({
    pendingPersistence: pendingPersistence.promise,
    selectedCut: {
      id: 'older-cut',
      fitProfileRecorded: true,
      fitProfile: selectedBaseline,
    },
    adjustedProfile,
    persistSelection: async (cutId) => {
      events.push(['selection', cutId]);
      await selectionPersistence.promise;
    },
    persistFitProfile: async (profile) => {
      events.push(['fit', profile]);
      await fitPersistence.promise;
    },
    regenerate: async (profile) => {
      events.push(['regenerate', profile]);
      return 'generated';
    },
  });

  await flush();
  assert.deepEqual(events, [], '기존 선택·핏 저장을 먼저 기다린다');
  pendingPersistence.resolve();
  await flush();
  assert.deepEqual(events, [['selection', 'older-cut']]);
  selectionPersistence.resolve();
  await flush();
  assert.deepEqual(events, [
    ['selection', 'older-cut'],
    ['fit', selectedBaseline],
  ]);
  fitPersistence.resolve();

  assert.equal(await result, 'generated');
  assert.deepEqual(events, [
    ['selection', 'older-cut'],
    ['fit', selectedBaseline],
    ['regenerate', adjustedProfile],
  ]);
  assert.equal(events.filter(([event]) => event === 'fit').length, 1);
});

for (const failedStep of ['selection', 'fit']) {
  test(`현재 버전 ${failedStep} 저장이 실패하면 유료 재생성을 호출하지 않는다`, async () => {
    const run = versionFit.regenerateWithPersistedSelectedVersion;
    assert.equal(typeof run, 'function', '유료 재생성 전 선택 저장을 보장하는 경계가 필요하다');
    const failure = new Error(`${failedStep} persistence failed`);
    let regenerateCalls = 0;

    await assert.rejects(run({
      pendingPersistence: Promise.resolve(),
      selectedCut: {
        id: 'older-cut',
        fitProfileRecorded: true,
        fitProfile: { version: 2, axes: { fit: 'regular' } },
      },
      adjustedProfile: { version: 2, axes: { fit: 'over' } },
      persistSelection: async () => {
        if (failedStep === 'selection') throw failure;
      },
      persistFitProfile: async () => {
        if (failedStep === 'fit') throw failure;
      },
      regenerate: async () => {
        regenerateCalls += 1;
      },
    }), failure);

    assert.equal(regenerateCalls, 0);
  });
}

test('기록 없는 예전 컷은 기준 핏 저장 없이 기존 재생성 경로를 유지한다', async () => {
  const run = versionFit.regenerateWithPersistedSelectedVersion;
  assert.equal(typeof run, 'function', '유료 재생성 전 선택 저장을 보장하는 경계가 필요하다');
  const adjustedProfile = { version: 2, axes: { fit: 'over' } };
  let fitSaves = 0;

  const result = await run({
    pendingPersistence: Promise.resolve(),
    selectedCut: { id: 'legacy-cut' },
    adjustedProfile,
    persistSelection: async (cutId) => assert.equal(cutId, 'legacy-cut'),
    persistFitProfile: async () => { fitSaves += 1; },
    regenerate: async (profile) => profile,
  });

  assert.equal(fitSaves, 0);
  assert.equal(result, adjustedProfile);
});

test('저장을 기다리는 동안 화면을 떠나면 유료 재생성을 시작하지 않는다', async () => {
  const pending = deferred();
  let current = true;
  const calls = [];
  const result = versionFit.regenerateWithPersistedSelectedVersion({
    pendingPersistence: pending.promise,
    selectedCut: { id: 'v1', fitProfileRecorded: true, fitProfile: null },
    adjustedProfile: { axes: { fit: 'over' } },
    persistSelection: async () => calls.push('selection'),
    persistFitProfile: async () => calls.push('fit'),
    regenerate: async () => calls.push('paid'),
    isCurrent: () => current,
  });
  current = false;
  pending.resolve();
  assert.equal(await result, false);
  assert.deepEqual(calls, []);
});

test('이전 저장이 실패해도 현재 선택을 다시 확정하고 사진 그대로 null을 보존한다', async () => {
  const fits = [];
  assert.equal(await versionFit.regenerateWithPersistedSelectedVersion({
    pendingPersistence: Promise.reject(new Error('previous save failed')),
    selectedCut: { id: 'v1', fitProfileRecorded: true, fitProfile: null },
    adjustedProfile: { axes: { fit: 'over' } },
    persistSelection: async id => assert.equal(id, 'v1'),
    persistFitProfile: async profile => fits.push(profile),
    regenerate: async profile => { assert.equal(profile.axes.fit, 'over'); return true; },
  }), true);
  assert.deepEqual(fits, [null]);
});
