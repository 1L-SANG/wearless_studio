import { normalizeSellingPoint, SELLING_POINTS_MAX } from '../features/analysis/sellingPoints.js';

export function stripFeatureClientFields(analysis) {
  if (!analysis) return analysis;
  const { featureRun: _run, dismissedAiPoints: _dismissed, pointsTicket: _ticket, ...payload } = analysis;
  return payload;
}

export function isFeatureRunPending(record) {
  return !!record && !['applied', 'failed', 'committing', 'committed'].includes(record.stage);
}

export function mergeFeaturePoints({ sellingPoints = [], aiSuggestedPoints = [], dismissedAiPoints = [], early = [], late = [], decisions = [] }) {
  const points = [...sellingPoints];
  let ai = [...aiSuggestedPoints];
  const norm = normalizeSellingPoint;
  const has = (list, point) => list.some((item) => norm(item) === norm(point));
  const dismissed = (point) => has(dismissedAiPoints, point);
  // 셀러 소유 여부는 적용 전 칩에서 고정한다. 앞 refine이 표식을 옮겨도 뒷 판정은 같다.
  const live = early.map((point) => has(points, point) && has(ai, point) && !dismissed(point));
  const refined = new Set();
  const changedPoints = [];
  for (const row of [...decisions].sort((a, b) => a.late - b.late)) {
    const point = norm(late[row.late]);
    if (!point) continue;
    if (row.action === 'refine') {
      if (!live[row.early] || refined.has(row.early)) continue;
      refined.add(row.early);
      if (has(points, point) || dismissed(point)) continue;
      const previous = early[row.early];
      const index = points.findIndex((item) => norm(item) === norm(previous));
      if (index < 0) continue;
      points[index] = point;
      ai = ai.filter((item) => norm(item) !== norm(previous));
    } else if (row.action === 'new') {
      if (has(points, point) || dismissed(point) || points.length >= SELLING_POINTS_MAX) continue;
      points.push(point);
    } else continue;
    if (!has(ai, point)) ai.push(point);
    changedPoints.push(point);
  }
  return { sellingPoints: points, aiSuggestedPoints: ai, dismissedAiPoints, changedPoints };
}

function sanitizeFeatures(result) {
  const ready = result?.detailRecommendations?.status === 'ready';
  return { ...result, detailRecommendationsHandoff: ready ? result.detailRecommendationsHandoff ?? null : null };
}

export function createFeatureRun({ readAnalysis, saveAnalysis, requestFeatures, requestMerge,
  isCurrent = () => true, now = Date.now, random = Math.random, setTimer = setTimeout, clearTimer = clearTimeout,
  onLateDetails = async () => {}, onEvent = () => {} }) {
  let record = null, input = null, editing = false, invalid = false, deferFeatures = false;
  let processing = null, requesting = null, coreSaving = null, frozenAnalysis = null, projectId = null;
  let needsLateDetails = false, latePromotion = null, promotedHandoff = null;
  const listeners = new Set();
  const valid = () => !invalid && !!record && isCurrent(record);
  const frozen = () => ['committing', 'committed'].includes(record?.stage);
  const notify = () => { for (const listener of listeners) listener(record); };
  const sleep = (ms) => new Promise((resolve) => setTimer(resolve, ms));
  const persist = async (patch = {}, canApply = () => true, recordPatch = {}) => {
    if (!valid() || frozen()) return false;
    const snapshot = { ...record, ...recordPatch };
    const saved = await saveAnalysis({ ...patch, featureRun: snapshot }, { isCurrent: () => valid() && !frozen() && canApply() });
    return saved !== false && valid() && !frozen() && canApply();
  };
  const checkpoint = async (stage, patch, canApply) => {
    if (!valid() || frozen()) return false;
    record = { ...record, stage };
    const saved = await persist(patch, canApply);
    if (saved) notify();
    return saved;
  };
  const lateDetails = async () => {
    if (!valid()) return;
    if (record.stage !== 'committed') { needsLateDetails = true; return; }
    if (!projectId) return;
    const handoff = record.features?.detailRecommendationsHandoff;
    const key = handoff ? JSON.stringify(handoff) : null;
    if (key && key === promotedHandoff) return;
    if (latePromotion) { await latePromotion; return lateDetails(); }
    onEvent('feature_result_late');
    if (!handoff) return;
    latePromotion = Promise.resolve().then(async () => {
      try {
        await onLateDetails(projectId, handoff, { runId: record.runId, photoSignature: record.photoSignature });
        if (!valid()) return;
        promotedHandoff = key;
        record = { ...record, lateDetailRevision: (record.lateDetailRevision || 0) + 1 };
        notify();
      } catch { onEvent('feature_detail_promotion_failed'); }
    });
    try { await latePromotion; } finally { latePromotion = null; }
  };
  const chipKey = (analysis) => JSON.stringify([analysis?.sellingPoints || [], analysis?.aiSuggestedPoints || [], analysis?.dismissedAiPoints || []]);
  const apply = async () => {
    if (!valid()) return;
    if (frozen()) return lateDetails();
    if (editing || !record.coreReceived) return;
    const analysis = readAnalysis() || {};
    const beforeKey = chipKey(analysis);
    const merged = mergeFeaturePoints({ ...analysis, early: record.early, late: record.features?.points?.items,
      decisions: record.decisions || [] });
    const { changedPoints, ...patch } = merged;
    const features = record.features;
    if (features?.detailRecommendations?.status === 'ready') {
      patch.detailRecommendations = features.detailRecommendations;
      patch.detailRecommendationsHandoff = features.detailRecommendationsHandoff;
    }
    // applied를 저장하되, Promise 대기는 쓰기가 끝난 뒤 알림을 받고 정착한다.
    record = { ...record, changedPoints };
    if (!await checkpoint('applied', patch, () => !editing && [beforeKey, chipKey(patch)].includes(chipKey(readAnalysis()))) && valid() && !frozen()) record = { ...record, stage: 'decided' };
  };
  const process = () => {
    if (processing) return processing;
    processing = (async () => {
      if (!valid() || !record.coreReceived) return;
      if (frozen()) return lateDetails();
      if (record.stage === 'received') {
        const late = record.features?.points?.items || [];
        let decisions = [];
        if (!late.length) decisions = [];
        else if (!record.early.length) decisions = late.map((_, index) => ({ late: index, action: 'new', early: null }));
        else {
          for (let attempt = 0; attempt < 2; attempt++) {
            if (!valid()) return;
            if (frozen()) return lateDetails();
            try {
              const response = await requestMerge({ runId: record.runId, pointsTicket: record.pointsTicket, featureTicket: record.features.featureTicket });
              if (!valid()) return;
              if (frozen()) return lateDetails();
              decisions = response.decisions;
              if (!Array.isArray(decisions) || decisions.length !== late.length
                || new Set(decisions.map((row) => row.late)).size !== late.length
                || decisions.some((row) => !Number.isInteger(row.late) || row.late < 0 || row.late >= late.length
                  || !['new', 'refine'].includes(row.action)
                  || (row.action === 'refine' && (!Number.isInteger(row.early) || row.early < 0 || row.early >= record.early.length)))) throw new Error('invalid_decisions');
              break;
            } catch {
              decisions = [];
              if (attempt === 0 && now() + 1000 < record.deadlineAt) await sleep(1000 + random() * 500);
            }
          }
        }
        record = { ...record, decisions };
        if (!await checkpoint('decided')) return;
      }
      if (record.stage === 'decided') await apply();
    })().catch(async () => {
      if (valid() && !frozen()) await checkpoint('failed').catch(() => {});
    }).finally(() => { processing = null; if (valid() && !frozen() && record.stage === 'decided' && !editing) void process(); });
    return processing;
  };
  const request = () => {
    if (requesting) return requesting;
    requesting = (async () => {
      let busy = 0, firstRequest = true;
      while (valid() && record.attempts < 3) {
        if (!firstRequest && now() >= record.deadlineAt) {
          if (record.features) await process();
          else if (!frozen()) await checkpoint('failed');
          return;
        }
        firstRequest = false;
        record = { ...record, attempts: record.attempts + 1 };
        if (!frozen() && !await persist() && (!deferFeatures || !frozen())) return;
        if (!valid()) return;
        try {
          const received = sanitizeFeatures(await requestFeatures({ ...input, runId: record.runId, attempt: record.attempts }));
          if (!valid()) return;
          const previous = record.features;
          const features = previous?.points?.status === 'ready'
            ? { ...received, points: previous.points, featureTicket: previous.featureTicket }
            : received;
          record = { ...record, features };
          if (coreSaving) await coreSaving;
          if (!valid()) return;
          if (frozen() || deferFeatures) return lateDetails();
          if (!await checkpoint('received')) return;
          const malformed = received.detailRecommendations?.status === 'unavailable';
          if (malformed && !record.formatRetried && record.deadlineAt - now() >= 10000 && record.attempts < 3) {
            record = { ...record, formatRetried: true };
            await persist();
            await sleep(1000 + random() * 500);
            continue;
          }
          await process();
          return;
        } catch (error) {
          if (!valid()) return;
          const isBusy = error?.code === 'analysis_busy';
          if (isBusy) record = { ...record, attempts: record.attempts - 1 };
          const retryable = isBusy || !error?.status || error.status >= 500;
          const delay = isBusy ? [3000, 8000, 15000][Math.min(busy++, 2)] : record.attempts * 1000;
          if (!retryable || record.attempts >= 3 || now() + delay >= record.deadlineAt) {
            if (!frozen()) {
              if (record.features) await process();
              else await checkpoint('failed');
            }
            return;
          }
          if (!frozen()) await persist();
          await sleep(delay + random() * 500);
        }
      }
    })().catch(async () => { if (valid() && !frozen()) await checkpoint('failed').catch(() => {}); })
      .finally(() => { requesting = null; if (valid() && record.coreReceived && record.stage === 'received') void process(); });
    return requesting;
  };
  const controller = {
    async start(options) {
      input = options;
      record = { runId: options.runId, startedAt: options.startedAt ?? now(), deadlineAt: (options.startedAt ?? now()) + 30000,
        photoSignature: options.photoSignature, attempts: 0, coreReceived: false, early: [], stage: 'requested' };
      if (await persist({ dismissedAiPoints: [] })) { notify(); void request(); }
      return record;
    },
    async receiveCore(analysis) {
      if (!valid() || frozen()) return null;
      record = { ...record, early: [...(analysis.aiSuggestedPoints || [])], pointsTicket: analysis.pointsTicket };
      const { pointsTicket: _ticket, ...fields } = analysis;
      const sellingPoints = [...new Set([...(analysis.sellingPoints || []), ...record.early])].slice(0, SELLING_POINTS_MAX);
      coreSaving = (async () => {
        if (!await persist({ ...fields, sellingPoints, dismissedAiPoints: [] }, undefined, { coreReceived: true })) return null;
        record = { ...record, coreReceived: true };
        notify();
        return readAnalysis();
      })();
      let saved;
      try { saved = await coreSaving; } finally { coreSaving = null; }
      if (saved && !requesting) void process();
      return saved;
    },
    async resume(saved, options = {}, { promotion = false } = {}) {
      if (record?.runId === saved.runId) return controller;
      record = { ...saved }; input = options;
      deferFeatures = promotion && record.stage === 'requested';
      if (promotion) {
        if (record.stage === 'requested') void request();
        else if (record.stage === 'decided') await apply();
        else if (record.stage === 'received') { record = { ...record, decisions: [] }; await checkpoint('decided'); await apply(); }
        return controller;
      }
      if (record.stage === 'requested') void request();
      else if (['received', 'decided'].includes(record.stage)) void process();
      notify();
      return controller;
    },
    getRecord: () => record,
    getProjectId: () => projectId,
    getAnalysis: () => frozenAnalysis || readAnalysis(),
    subscribe(listener) { listeners.add(listener); listener(record); return () => listeners.delete(listener); },
    async setEditing(value) { editing = value; if (!value) await process(); },
    wait() {
      if (!isFeatureRunPending(record) && !processing) return Promise.resolve('settled');
      if (!record || now() >= record.deadlineAt) return Promise.resolve('deadline');
      return new Promise((resolve) => {
        const timer = setTimer(() => finish('deadline'), record.deadlineAt - now());
        const listener = () => { if (!isFeatureRunPending(record)) finish('settled'); };
        function finish(value) { clearTimer(timer); listeners.delete(listener); resolve(value); }
        listeners.add(listener);
      });
    },
    freeze(pid, { deadlineExceeded = false } = {}) {
      if (deadlineExceeded) onEvent('feature_deadline_exceeded');
      needsLateDetails ||= isFeatureRunPending(record) || !!processing;
      projectId = pid;
      frozenAnalysis ||= { ...readAnalysis() };
      if (deadlineExceeded) {
        delete frozenAnalysis.detailRecommendations;
        delete frozenAnalysis.detailRecommendationsHandoff;
      }
      record = { ...record, stage: 'committing' };
      notify();
      return frozenAnalysis;
    },
    committed() { record = { ...record, stage: 'committed' }; notify(); if (needsLateDetails) void lateDetails(); },
    invalidate() { invalid = true; notify(); },
  };
  return controller;
}

let active = null;
export const getActiveFeatureRun = () => active;
export function setActiveFeatureRun(run) {
  if (!['committing', 'committed'].includes(active?.getRecord()?.stage)) active?.invalidate();
  active = run;
  return run;
}
