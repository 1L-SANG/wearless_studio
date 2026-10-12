import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeFeaturePoints, createFeatureRun, isFeatureRunPending, stripFeatureClientFields } from '../../src/lib/featureRun.js';
import { isAnalysisFastEntryEnabled } from '../../src/lib/analysisFastEntry.js';
import { applySellingPointEdit } from '../../src/features/analysis/sellingPoints.js';

const early = ['편안한 핏', '부드러운 원단'];
const late = ['여유로운 실루엣', '단추 여밈'];
const decisions = [{ late: 0, action: 'refine', early: 0 }, { late: 1, action: 'new', early: null }];
const base = () => ({ sellingPoints: [...early], aiSuggestedPoints: [...early], dismissedAiPoints: [] });
const merge = (patch = {}) => mergeFeaturePoints({ ...base(), early, late, decisions, ...patch });

test('스위치는 정확한 문자열 1과 true에서만 켠다', () => {
  for (const value of ['1', 'true']) assert.equal(isAnalysisFastEntryEnabled(value), true);
  for (const value of [undefined, false, true, 'TRUE', '0', ' true']) assert.equal(isAnalysisFastEntryEnabled(value), false);
});
test('AI 칩은 제자리에서 다듬고 새 칩만 추가하고 손대지 않은 칩은 남긴다', () => {
  const result = merge();
  assert.deepEqual(result.sellingPoints, [late[0], early[1], late[1]]);
  assert.deepEqual(result.changedPoints, late);
});
test('셀러가 고친 칩이나 지운 칩을 refine으로 되살리지 않는다', () => {
  for (const sellingPoints of [['내가 쓴 핏'], []]) {
    const result = merge({ sellingPoints, aiSuggestedPoints: [], dismissedAiPoints: [early[0]],
      late: ['새 핏', '다른 핏'], decisions: [{ late: 0, action: 'refine', early: 0 }, { late: 1, action: 'refine', early: 0 }] });
    assert.deepEqual(result.sellingPoints, sellingPoints);
    assert.deepEqual(result.changedPoints, []);
  }
});
test('살아 있는 AI 칩의 중복 refine도 첫 문구만 반영한다', () => {
  assert.deepEqual(merge({ late: ['첫 핏', '둘째 핏'], decisions: [{ late: 0, action: 'refine', early: 0 }, { late: 1, action: 'refine', early: 0 }] }).sellingPoints, ['첫 핏', early[1]]);
});
test('정규화한 중복과 지운 문구를 추가하지 않는다', () => {
  assert.deepEqual(merge({ late: [early[0].normalize('NFD') + '\u200b', '지운 특징'], dismissedAiPoints: ['지운 특징'], decisions: [{ late: 0, action: 'new' }, { late: 1, action: 'new' }] }).changedPoints, []);
});
test('8칩 상한에서 새 특징은 추가하지 않고 refine은 허용한다', () => {
  const points = [...early, ...Array.from({ length: 6 }, (_, i) => `셀러 ${i}`)];
  const result = merge({ sellingPoints: points });
  assert.equal(result.sellingPoints.length, 8);
  assert.equal(result.sellingPoints[0], late[0]);
  assert.deepEqual(result.changedPoints, [late[0]]);
});
test('편집과 삭제는 이 실행에서 버린 AI 문구 목록을 채운다', () => {
  assert.deepEqual(applySellingPointEdit({ ...base(), index: 0, text: '내 문구' }).dismissedAiPoints, [early[0]]);
  assert.deepEqual(applySellingPointEdit({ ...base(), index: 0, text: '' }).dismissedAiPoints, [early[0]]);
});
test('서버 전송은 실행 기록과 칩 출처의 클라이언트 목록을 제거한다', () => {
  assert.deepEqual(stripFeatureClientFields({ featureRun: { pointsTicket: 'secret' }, dismissedAiPoints: ['text'], sellingPoints: ['보존'] }), { sellingPoints: ['보존'] });
});

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const drain = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
function fixture(options = {}) {
  let time = 0, analysis = base(), current = true;
  const timers = new Map(); let timerId = 0;
  const writes = [], calls = [], events = [], details = [];
  const request = options.request || (() => Promise.resolve({ points: { status: 'ready', items: late }, detailRecommendations: { status: 'ready' }, detailRecommendationsHandoff: { signature: 'detail' }, featureTicket: 'feature' }));
  const run = createFeatureRun({ now: () => time, random: options.random || (() => 0),
    setTimer: (fn, delay) => { timers.set(++timerId, { fn, at: time + delay }); return timerId; }, clearTimer: (id) => timers.delete(id),
    readAnalysis: () => analysis, isCurrent: () => current,
    saveAnalysis: async (patch) => { analysis = { ...analysis, ...patch }; writes.push(structuredClone(patch)); },
    requestFeatures: (input) => { calls.push({ ...input, at: time }); return request(input); },
    requestMerge: options.merge || (async () => ({ decisions })),
    onEvent: (code) => events.push(code), onLateDetails: async (pid, handoff) => details.push([pid, handoff]),
  });
  return { run, writes, calls, events, details, get analysis() { return analysis; },
    change(patch) { analysis = { ...analysis, ...patch }; }, invalidate() { current = false; },
    async tick(ms) { const end = time + ms; await drain(); while ([...timers.values()].some((t) => t.at <= end)) { const [id, timer] = [...timers].filter(([,t]) => t.at <= end).sort((a,b) => a[1].at-b[1].at)[0]; time = timer.at; timers.delete(id); timer.fn(); await drain(); } time = end; await drain(); },
  };
}
const start = (f) => f.run.start({ runId: 'run_test_123', photoSignature: 'sha256', startedAt: 0, photos: [], product: {} });
const core = (f, points = early) => f.run.receiveCore({ ...base(), aiSuggestedPoints: points, sellingPoints: points, pointsTicket: 'core' });

test('특징이 먼저 도착해도 핵심 적용 뒤에만 합치고 정착한다', async () => {
  const f = fixture(); await start(f); await drain();
  assert.equal(f.run.getRecord().stage, 'received');
  assert.equal(f.run.getRecord().coreReceived, false);
  await core(f); await drain();
  assert.equal(f.run.getRecord().stage, 'applied');
  assert.deepEqual(f.analysis.sellingPoints, [late[0], early[1], late[1]]);
  assert.equal(isFeatureRunPending(f.run.getRecord()), false);
});
test('칩 편집 중에는 적용을 미루고 닫은 뒤 최신 셀러 칩을 사용한다', async () => {
  const f = fixture(); f.run.setEditing(true); await start(f); await core(f); await drain();
  assert.equal(f.run.getRecord().stage, 'decided');
  f.change({ sellingPoints: ['셀러 핏', early[1]], aiSuggestedPoints: [early[1]] });
  await f.run.setEditing(false); await drain();
  assert.deepEqual(f.analysis.sellingPoints, ['셀러 핏', early[1], late[1]]);
});
test('회차나 사진이 바뀌면 결과를 로컬 저장 전에 버린다', async () => {
  const result = deferred(); const f = fixture({ request: () => result.promise });
  await start(f); await core(f); const writes = f.writes.length; f.invalidate(); result.resolve({ points: { status: 'ready', items: late } }); await drain();
  assert.equal(f.writes.length, writes);
});
test('분석 시작의 30초 기한에서 기다림만 끝내고 고정 전 늦은 결과는 적용한다', async () => {
  const response = deferred(); const f = fixture({ request: () => response.promise }); await start(f); await core(f);
  let settled = false; f.run.wait().then(() => { settled = true; }); await f.tick(29999); assert.equal(settled, false);
  await f.tick(1); assert.equal(settled, true); assert.equal(f.run.getRecord().deadlineAt, 30000);
  response.resolve({ points: { status: 'ready', items: late }, featureTicket: 'feature' }); await drain();
  assert.equal(f.run.getRecord().stage, 'applied');
});
test('busy는 요청 횟수에 포함하지 않고 3초 8초 15초 간격으로 재시도한다', async () => {
  let n = 0; const f = fixture({ request: async () => { if (++n <= 3) throw Object.assign(new Error(), { status: 429, code: 'analysis_busy' }); return { points: { status: 'empty', items: [] }, detailRecommendations: { status: 'ready' } }; } });
  await start(f); await core(f); await f.tick(26000);
  assert.deepEqual(f.calls.map((c) => c.at), [0, 3000, 11000, 26000]);
  assert.equal(f.run.getRecord().attempts, 1);
});
for (const code of ['feature_extraction_failed', 'network']) test(`${code} 실패는 3회까지만 재시도한다`, async () => {
  const f = fixture({ request: async () => { throw Object.assign(new Error(), { status: code === 'network' ? undefined : 502, code }); } });
  await start(f); await core(f); await f.tick(4000);
  assert.equal(f.calls.length, 3); assert.equal(f.run.getRecord().stage, 'failed');
  assert.equal(isFeatureRunPending(f.run.getRecord()), false);
});
test('rate_limited는 재시도하지 않는다', async () => {
  const f = fixture({ request: async () => { throw Object.assign(new Error(), { status: 429, code: 'rate_limited' }); } }); await start(f); await core(f); await f.tick(30000); assert.equal(f.calls.length, 1);
});
test('empty와 정상 디테일은 다시 요청하지 않는다', async () => {
  const f = fixture({ request: async () => ({ points: { status: 'empty', items: [] }, detailRecommendations: { status: 'ready' } }) }); await start(f); await core(f); await drain(); assert.equal(f.calls.length, 1); assert.equal(f.run.getRecord().stage, 'applied');
});
test('디테일 형식 실패 1회 재시도는 최초 문구를 유지하고 unavailable 인계를 보관하지 않는다', async () => {
  let n = 0; const f = fixture({ request: async () => ++n === 1 ? { points: { status: 'ready', items: late }, featureTicket: 'first', detailRecommendations: { status: 'unavailable' }, detailRecommendationsHandoff: { signature: 'bad' } } : { points: { status: 'ready', items: ['바뀐 문구'] }, featureTicket: 'second', detailRecommendations: { status: 'ready' }, detailRecommendationsHandoff: { signature: 'good' } } });
  await start(f); await core(f); await f.tick(1000); assert.equal(f.calls.length, 2);
  assert.deepEqual(f.analysis.sellingPoints, [late[0], early[1], late[1]]);
  assert.equal(f.analysis.detailRecommendationsHandoff.signature, 'good');
  assert.equal(JSON.stringify(f.writes).includes('bad'), false);
});
test('문구가 비었으면 합치기 요청을 생략한다', async () => {
  let calls = 0; const f = fixture({ merge: async () => { calls++; return { decisions }; } }); await start(f); await core(f, []); await drain(); assert.equal(calls, 0); assert.deepEqual(f.analysis.sellingPoints, late);
});
test('합치기 2회 실패는 기존 칩을 보존하고 디테일만 사용한다', async () => {
  let calls = 0; const f = fixture({ merge: async () => { calls++; throw new Error(); } }); await start(f); await core(f); await f.tick(1000);
  assert.equal(calls, 2); assert.deepEqual(f.analysis.sellingPoints, early); assert.equal(f.analysis.detailRecommendationsHandoff.signature, 'detail');
});

test('정착 Promise는 applied 기록의 로컬 저장 완료까지 기다린다', async () => {
  let analysis = base(); const blocked = deferred(); let blockedWrite = false;
  const run = createFeatureRun({ readAnalysis: () => analysis, isCurrent: () => true,
    saveAnalysis: async (patch) => { analysis = { ...analysis, ...patch }; if (patch.featureRun.stage === 'applied') { blockedWrite = true; await blocked.promise; } },
    requestFeatures: async () => ({ points: { status: 'ready', items: late }, featureTicket: 'f' }), requestMerge: async () => ({ decisions }) });
  await run.start({ runId: 'test_run_123', photoSignature: 'sig' }); await run.receiveCore({ ...base(), pointsTicket: 'p' }); await drain();
  assert.equal(blockedWrite, true); let resolved = false; run.wait().then(() => { resolved = true; }); await drain(); assert.equal(resolved, false);
  blocked.resolve(); await drain(); assert.equal(resolved, true);
});

for (const stage of ['received', 'decided', 'applied', 'committing', 'committed', 'failed']) test(`${stage} 새로고침은 단계별로 재개하고 기한을 늘리지 않는다`, async () => {
  let mergeCalls = 0; const f = fixture({ merge: async () => { mergeCalls++; return { decisions }; } });
  const saved = { runId: 'restore_run_123', photoSignature: 'sha256', startedAt: 0, deadlineAt: 30000, attempts: 1, coreReceived: true, early,
    features: { points: { status: 'ready', items: late }, featureTicket: 'feature' }, pointsTicket: 'core', stage, decisions };
  await f.run.resume(saved); await drain();
  assert.equal(f.run.getRecord().deadlineAt, 30000); assert.equal(f.calls.length, 0);
  assert.equal(mergeCalls, stage === 'received' ? 1 : 0);
  assert.equal(f.run.getRecord().stage, ['received', 'decided'].includes(stage) ? 'applied' : stage);
});
test('requested 새로고침은 기한이 지난 뒤에도 남은 시도로 재요청한다', async () => {
  const f = fixture(); await f.tick(35000);
  await f.run.resume({ runId: 'restore_run_123', photoSignature: 'sha256', startedAt: 0, deadlineAt: 30000, attempts: 1, coreReceived: true, early, pointsTicket: 'p', stage: 'requested' }); await drain();
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0].attempt, 2); assert.equal(f.run.getRecord().deadlineAt, 30000);
});
for (const time of [0, 35000]) test(`3회 요청을 쓴 requested 복원은 실패를 저장하고 진행 표시와 기다림을 끝낸다 (${time}ms)`, async () => {
  const f = fixture(); await f.tick(time);
  const stages = [];
  const unsubscribe = f.run.subscribe((record) => stages.push(record?.stage));
  const saved = { runId: 'restore_run_123', photoSignature: 'sha256', startedAt: 0, deadlineAt: 30000,
    attempts: 3, coreReceived: true, early, pointsTicket: 'p', stage: 'requested' };
  f.change({ featureRun: saved }); await f.run.resume(saved);
  await drain(); unsubscribe();
  assert.equal(f.calls.length, 0);
  assert.equal(f.analysis.featureRun?.stage, 'failed');
  assert.equal(f.writes.at(-1)?.featureRun.stage, 'failed');
  assert.equal(isFeatureRunPending(f.analysis.featureRun), false);
  assert.equal(stages.includes('failed'), true);
  assert.equal(await f.run.wait(), 'settled');
  assert.deepEqual(f.analysis.sellingPoints, early);
});
for (const coreReceived of [false, true]) test(`3회 요청을 쓴 복원은 이미 받은 특징을 새 요청 없이 정착시킨다 (핵심 수신 ${coreReceived})`, async () => {
  const f = fixture();
  const saved = { runId: 'restore_run_123', photoSignature: 'sha256', startedAt: 0, deadlineAt: 30000,
    attempts: 3, coreReceived, early, pointsTicket: 'p', stage: 'requested',
    features: { points: { status: 'ready', items: late }, featureTicket: 'f',
      detailRecommendations: { status: 'ready' }, detailRecommendationsHandoff: { signature: 'detail' } } };
  f.change({ featureRun: saved }); await f.run.resume(saved);
  await drain();
  if (!coreReceived) {
    assert.equal(f.run.getRecord().stage, 'received');
    assert.deepEqual(f.analysis.sellingPoints, early);
    await core(f); await drain();
  }
  assert.equal(f.calls.length, 0);
  assert.equal(f.run.getRecord().stage, 'applied');
  assert.equal(f.analysis.featureRun.stage, 'applied');
  assert.deepEqual(f.analysis.sellingPoints, [late[0], early[1], late[1]]);
  assert.equal(f.analysis.detailRecommendationsHandoff.signature, 'detail');
  assert.equal(await f.run.wait(), 'settled');
});
test('기한까지 10초 미만이면 형식 실패를 재요청하지 않고 문구만 적용한다', async () => {
  const response = deferred(); const f = fixture({ request: () => response.promise }); await start(f); await core(f); await f.tick(21000);
  response.resolve({ points: { status: 'ready', items: late }, featureTicket: 'f', detailRecommendations: { status: 'unavailable' }, detailRecommendationsHandoff: 'bad' }); await drain();
  assert.equal(f.calls.length, 1); assert.equal(f.run.getRecord().stage, 'applied'); assert.equal(f.analysis.detailRecommendationsHandoff, undefined);
});
test('문구 empty와 디테일 unavailable은 형식 실패로 한 번만 재요청한다', async () => {
  const f = fixture({ request: async () => ({ points: { status: 'empty', items: [] }, detailRecommendations: { status: 'unavailable' } }) }); await start(f); await core(f); await f.tick(1000);
  assert.equal(f.calls.length, 2); assert.equal(f.run.getRecord().stage, 'applied'); assert.deepEqual(f.analysis.sellingPoints, early);
});

test('적용 저장 도중 편집이 열리면 저장 전에 막고 확정 뒤 다시 계산한다', async () => {
  let analysis = base(); const block = deferred(); let saveStarted = false;
  const run = createFeatureRun({ readAnalysis: () => analysis,
    saveAnalysis: async (patch, { isCurrent }) => {
      if (patch.featureRun.stage === 'applied') { saveStarted = true; await block.promise; }
      if (!isCurrent()) return false;
      analysis = { ...analysis, ...patch };
    }, requestFeatures: async () => ({ points: { status: 'ready', items: late }, featureTicket: 'f' }), requestMerge: async () => ({ decisions }) });
  await run.start({ runId: 'test_run_123', photoSignature: 's' }); await run.receiveCore({ ...base(), pointsTicket: 'p' }); await drain();
  assert.equal(saveStarted, true); await run.setEditing(true); block.resolve(); await drain();
  assert.deepEqual(analysis.sellingPoints, early);
  analysis = { ...analysis, sellingPoints: ['셀러 핏', early[1]], aiSuggestedPoints: [early[1]] };
  await run.setEditing(false); await drain(); assert.deepEqual(analysis.sellingPoints, ['셀러 핏', early[1], late[1]]);
});

test('적용 저장 대기 중 삭제하거나 편집을 빨리 끝내도 셀러 변경을 덮지 않는다', async () => {
  for (const sellingPoints of [[], ['셀러 핏']]) {
    let analysis = base(); const block = deferred(); let writes = 0;
    const run = createFeatureRun({ readAnalysis: () => analysis,
      saveAnalysis: async (patch, { isCurrent }) => {
        if (patch.featureRun.stage === 'applied' && writes++ === 0) await block.promise;
        if (!isCurrent()) return false;
        analysis = { ...analysis, ...patch };
      }, requestFeatures: async () => ({ points: { status: 'ready', items: late }, featureTicket: 'f' }), requestMerge: async () => ({ decisions }) });
    await run.start({ runId: 'test_run_123', photoSignature: 's' }); await run.receiveCore({ ...base(), pointsTicket: 'p' }); await drain();
    analysis = { ...analysis, sellingPoints, aiSuggestedPoints: [], dismissedAiPoints: early };
    block.resolve(); await drain();
    assert.deepEqual(analysis.sellingPoints, [...sellingPoints, late[1]]);
  }
});
test('고정 전에 받은 디테일도 분석 저장 성공 뒤 늦은 승격으로 한 번 보존한다', async () => {
  const f = fixture(); f.run.setEditing(true); await start(f); await core(f); await drain();
  assert.equal(f.run.getRecord().stage, 'decided'); f.run.freeze('project', { deadlineExceeded: true }); f.run.committed(); await drain();
  assert.deepEqual(f.details, [['project', { signature: 'detail' }]]);
  await f.run.setEditing(false); await drain(); assert.equal(f.details.length, 1);
});

test('고정 뒤 끝난 합치기는 분석 저장 성공 전 디테일을 승격하지 않는다', async () => {
  const merged = deferred(); const f = fixture({ merge: () => merged.promise });
  await start(f); await core(f); await drain();
  const waiting = f.run.wait(); await f.tick(30000); assert.equal(await waiting, 'deadline');
  f.run.freeze('project', { deadlineExceeded: true });
  merged.resolve({ decisions }); await drain();
  assert.deepEqual(f.details, []);
  f.run.committed(); await drain();
  assert.deepEqual(f.details, [['project', { signature: 'detail' }]]);
  assert.deepEqual(f.analysis.sellingPoints, early);
});

test('로그인 복귀 재요청이 고정 전에 바로 끝나도 칩은 보존하고 저장 성공 뒤 디테일만 승격한다', async () => {
  const f = fixture();
  await f.run.resume({ runId: 'restore_run_123', photoSignature: 'sha256', startedAt: 0, deadlineAt: 30000,
    attempts: 1, coreReceived: true, early, pointsTicket: 'core', stage: 'requested' }, {}, { promotion: true });
  await drain();
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0].attempt, 2);
  assert.deepEqual(f.analysis.sellingPoints, early);
  assert.equal(f.analysis.detailRecommendationsHandoff, undefined);
  assert.deepEqual(f.details, []);
  f.run.freeze('project'); await f.run.setEditing(false); await drain(); assert.deepEqual(f.details, []);
  f.run.committed(); await drain();
  assert.deepEqual(f.details, [['project', { signature: 'detail' }]]);
  assert.deepEqual(f.analysis.sellingPoints, early);
});

for (const attempts of [2, 3]) test(`로그인 복귀 requested 재요청은 기한 뒤에도 3회 상한을 지킨다 (${attempts}회 사용)`, async () => {
  const response = deferred(); const f = fixture({ request: () => response.promise }); await f.tick(35000);
  await f.run.resume({ runId: 'restore_run_123', photoSignature: 'sha256', startedAt: 0, deadlineAt: 30000,
    attempts, coreReceived: true, early, pointsTicket: 'core', stage: 'requested' }, {}, { promotion: true });
  await drain();
  assert.equal(f.calls.length, attempts === 2 ? 1 : 0);
  assert.equal(f.run.getRecord().attempts, 3);
  assert.equal(f.run.getRecord().deadlineAt, 30000);
  if (attempts === 2) assert.equal(f.calls[0].attempt, 3);
});
test('로그인 복귀의 소진된 requested 기록에 특징이 있어도 칩은 고정하고 늦은 디테일만 승격한다', async () => {
  const f = fixture();
  await f.run.resume({ runId: 'restore_run_123', photoSignature: 'sha256', startedAt: 0, deadlineAt: 30000,
    attempts: 3, coreReceived: true, early, pointsTicket: 'p', stage: 'requested',
    features: { points: { status: 'ready', items: late }, featureTicket: 'f',
      detailRecommendations: { status: 'ready' }, detailRecommendationsHandoff: { signature: 'detail' } } }, {}, { promotion: true });
  await drain();
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.analysis.sellingPoints, early);
  f.run.freeze('project'); await drain();
  assert.equal(f.run.getRecord().stage, 'committing'); assert.deepEqual(f.details, []);
  f.run.committed(); await drain();
  assert.deepEqual(f.details, [['project', { signature: 'detail' }]]);
  assert.deepEqual(f.run.getAnalysis().sellingPoints, early);
  f.run.committed(); await f.run.setEditing(false); await drain(); assert.equal(f.details.length, 1);
});


test('무작위 재시도 지연이 기한을 넘으면 새 특징 요청을 보내지 않는다', async () => {
  const response = deferred();
  const f = fixture({ random: () => 1, request: () => response.promise });
  await start(f); await core(f); await f.tick(28500);
  response.reject(Object.assign(new Error(), { status: 502, code: 'feature_extraction_failed' }));
  await f.tick(1500);
  assert.equal(f.calls.length, 1); assert.equal(f.run.getRecord().stage, 'failed');
});

test('coreReceived는 핵심 체크포인트 쓰기가 끝나기 전에는 합치기를 시작하지 않는다', async () => {
  let analysis = base(), mergeCalls = 0; const coreWrite = deferred(), feature = deferred();
  const run = createFeatureRun({ readAnalysis: () => analysis,
    saveAnalysis: async (patch) => {
      if (patch.featureRun.coreReceived && !patch.featureRun.features) await coreWrite.promise;
      analysis = { ...analysis, ...patch };
    }, requestFeatures: () => feature.promise, requestMerge: async () => { mergeCalls++; return { decisions }; } });
  await run.start({ runId: 'test_run_123', photoSignature: 's' });
  const coreSaving = run.receiveCore({ ...base(), pointsTicket: 'p' });
  feature.resolve({ points: { status: 'ready', items: late }, featureTicket: 'f' }); await drain();
  assert.equal(mergeCalls, 0); assert.equal(run.getRecord().coreReceived, false);
  coreWrite.resolve(); await coreSaving; await drain(); assert.equal(mergeCalls, 1); assert.equal(run.getRecord().stage, 'applied');
});
