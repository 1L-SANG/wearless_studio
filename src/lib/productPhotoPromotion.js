/* 확정(CTA) 이후의 상품 사진 업로드를 화면 전환에서 떼어낸다.

   문제였던 것: CTA 는 프로젝트 생성 → **사진 전부 R2 업로드** → 상품·분석 저장까지 끝난 뒤에야
   콘티보드로 넘어갔다. 업로드는 사진 용량에 정비례하고, 오너 실측(2026-08-17)으로
   2장 4.9MB=44초 · 4장 5.6MB=75초 · 6장 14.2MB=211초였다. 그 시간 동안 화면은 그대로 멈춰 있다.

   구조는 내 옷 승격(customMatchPromotion)과 같다: 입력 화면이 시작한 프라미스를 콘티보드가
   구독한다. 다른 점은 **진행률**이다 — 사진은 몇 장 중 몇 장이 올라갔는지 셀러가 알아야
   기다림이 답답하지 않다(오너 요구 4안).

   완료된 task 도 세션 동안 남긴다. 콘티보드가 조금 늦게 마운트돼도 결과를 놓치지 않는다. */

import { createPromotionTaskRegistry } from './promotionTaskRegistry.js';

const snapshot = (task) => ({
  status: task.status,
  total: task.total,
  done: task.done,
  error: task.error || null,
});

const registry = createPromotionTaskRegistry({ snapshot });
const failureListeners = new Set();

function notifyFailure(task) {
  if (!task || task.failureNotified || !failureListeners.size) return;
  task.failureNotified = true;
  for (const listener of failureListeners) {
    try { listener(task.projectId, task.error); } catch { /* 다른 실패 리스너까지 계속 알린다 */ }
  }
}

function recoverAfterFailure(task) {
  if (!task || task.recoveryStarted) return;
  task.recoveryStarted = true;
  const recover = task.recoverDraftSlot ?? (async (snapshot) => {
    const { draftSlot } = await import('./draftSlot.js');
    draftSlot.resume();
    if (snapshot) draftSlot.queue(snapshot);
  });
  void Promise.resolve().then(() => recover(task.draftSlotSnapshot)).catch(() => {});
}

export function onProductPhotoPromotionFailure(listener) {
  failureListeners.add(listener);
  for (const task of registry.values()) {
    if (task.status === 'failed') notifyFailure(task);
  }
  return () => failureListeners.delete(listener);
}

function notify(task) {
  const state = snapshot(task);
  for (const listener of task.listeners) {
    try { listener(state); } catch { /* 구독자 오류가 다른 구독자를 막지 않는다 */ }
  }
  registry.notify(task.projectId);
}

/** 승격 시작 — `run({ onPhotoProgress })` 이 실제 업로드·저장을 수행한다.
 *  같은 프로젝트로 두 번 부르면 진행 중 task 를 그대로 돌려준다(중복 업로드 방지). */
export function startProductPhotoPromotion(projectId, run, {
  recoverDraftSlot = null,
  draftSlotSnapshot = null,
} = {}) {
  if (!projectId) return null;
  const current = registry.get(projectId);
  if (current?.status === 'pending') return current;

  const task = {
    projectId,
    status: 'pending',
    total: 0,
    done: 0,
    error: null,
    failureNotified: false,
    recoveryStarted: false,
    recoverDraftSlot,
    draftSlotSnapshot,
    listeners: new Set(),
    promise: null,
  };
  task.subscribe = (listener) => {
    task.listeners.add(listener);
    listener(snapshot(task));
    return () => task.listeners.delete(listener);
  };
  task.promise = Promise.resolve()
    .then(() => run({
      onPhotoProgress: ({ done, total: nextTotal } = {}) => {
        if (Number.isFinite(nextTotal)) task.total = Math.max(0, nextTotal);
        if (Number.isFinite(done)) task.done = Math.max(0, done);
        notify(task);
      },
    }))
    .then((result) => {
      // 워치독이 먼저 failed 로 바꿨더라도 실제 요청이 뒤늦게 성공하면 정착 상태를 회복한다.
      // 그렇지 않으면 서버 저장은 끝났는데 화면은 영원히 재시도 오류로 남는다.
      if (task.status === 'settled') return result;
      task.status = 'settled';
      task.error = null;
      task.done = task.total;
      notify(task);
      return result;
    }, (error) => {
      if (task.status !== 'pending') throw error;
      task.status = 'failed';
      task.error = error;
      notify(task);
      recoverAfterFailure(task);
      notifyFailure(task);
      throw error;
    });

  registry.set(projectId, task);
  return task;
}

/** 프로젝트 id 는 승격 도중에 생긴다. 그 전까지 임시 키로 담아 둔 task 를 실제 id 로 옮긴다. */
export function adoptProductPhotoPromotion(temporaryKey, projectId) {
  if (!temporaryKey || !projectId || temporaryKey === projectId) return null;
  const task = registry.get(temporaryKey);
  if (task) task.projectId = projectId;
  return registry.move(temporaryKey, projectId);
}

//: 아직 projectId 가 없는 동안 쓰는 임시 키.
export const NEW_PROJECT_KEY = '__pending_project__';

export function getProductPhotoPromotionTask(projectId) {
  return projectId ? registry.get(projectId) : null;
}

export function subscribeProductPhotoPromotion(projectId, listener) {
  return registry.subscribe(projectId, listener);
}

/** 콘티보드가 기다릴 프라미스. 없으면(이미 끝난 세션·복원 진입) 즉시 통과한다 —
 *  사진은 그때 이미 서버에 있으므로 여기서 막을 이유가 없다. */
export function productPhotosReady(projectId, {
  stallMs = 90_000,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  const task = getProductPhotoPromotionTask(projectId);
  if (!task) return Promise.resolve(null);
  if (task.status === 'failed') return Promise.reject(task.error);
  if (task.status === 'settled') return task.promise;

  return new Promise((resolve, reject) => {
    let timer = null;
    let finished = false;
    let unsubscribe = () => {};
    const finish = (callback, value) => {
      if (finished) return;
      finished = true;
      clearTimer(timer);
      unsubscribe();
      callback(value);
    };
    const armWatchdog = () => {
      clearTimer(timer);
      timer = setTimer(() => {
        if (getProductPhotoPromotionTask(projectId) !== task || task.status !== 'pending') return;
        const error = new Error('상품 사진 업로드 진행이 멈췄습니다.');
        error.code = 'product_photo_promotion_stalled';
        task.status = 'failed';
        task.error = error;
        notify(task);
        recoverAfterFailure(task);
        notifyFailure(task);
        finish(reject, error);
      }, stallMs);
    };
    unsubscribe = subscribeProductPhotoPromotion(projectId, (state) => {
      if (state?.status === 'pending') armWatchdog();
      else if (state?.status === 'failed') finish(reject, state.error);
    });
    task.promise.then(
      (value) => finish(resolve, value),
      (error) => finish(reject, error),
    );
  });
}

export function clearProductPhotoPromotionTask(projectId) {
  if (projectId) registry.delete(projectId);
}

/* 업로드가 실패로 끝난 뒤의 **제자리 재시도** — 콘티보드에서 부른다.

   실패 시점은 이미 확정(confirmProductInfo) 뒤라 입력 화면은 봉인돼 있다("의류 정보는 확정돼
   수정할 수 없어요" 반송, 재진입을 고집하면 start-new 가 draft 를 지운다). 그래서 뒤로 보내는
   안내는 갈 수 없는 곳을 가리킨다 — 복구는 여기서, 같은 draft(로컬 IndexedDB, 실패 시 지우지
   않는다)로 승격을 다시 돌리는 것뿐이다. 이미 올라간 사진은 draftPromotionSession 의 자산
   매핑이 재사용하므로 두 번 올라가지 않는다.

   io 주입은 테스트용 — 실제 임포트는 순환이 없다(draftSync·draftStore 는 이 모듈을 모른다). */
export async function retryProductPhotoPromotionFromDraft(projectId, io = {}) {
  if (!projectId) return false;
  const load = io.loadDraft
    ?? (await import('./draftStore.js')).loadDraft;
  const draftSync = io.promote && io.resetRetry && io.finishDraft ? null : await import('./draftSync.js');
  const promote = io.promote ?? draftSync.promoteDraftToProject;
  const resetRetry = io.resetRetry ?? draftSync.retryDraftPromotion;
  const finishDraft = io.finishDraft ?? (async () => {
    const store = await import('./draftStore.js');
    await store.clearDraft();
    draftSync?.resetDraftSyncSingleFlight?.();
  });

  const draft = await Promise.resolve().then(load).catch(() => null);
  if (!draft?.product || !(draft.photos || []).length) return false;   // 재시도할 재료 없음

  // 워치독은 UI의 무한 대기만 끊을 뿐 브라우저 fetch 자체를 취소할 수는 없다. 기존 단일비행이
  // 아직 살아 있으면 새 태스크가 거기에 다시 합류해 또 무한 대기하므로, 재시도를 시작하지 않고
  // 오류 화면을 연다. 원 요청이 뒤늦게 성공하면 위의 late-success 경로가 settled 로 회복한다.
  if (resetRetry(projectId) === false) return false;
  clearProductPhotoPromotionTask(projectId);
  const task = startProductPhotoPromotion(projectId,
    ({ onPhotoProgress }) => promote(draft, { projectId, onPhotoProgress }));
  try {
    await task.promise;
  } catch {
    return false;          // task 는 failed 로 남는다 — 호출측이 재시도 화면을 유지한다
  }
  // 성공 — CTA 성공 경로와 같은 정리(업로드 원본을 더 들고 있을 이유가 없다).
  void Promise.resolve().then(finishDraft).catch(() => {});
  return true;
}

/** 새로고침으로 메모리 task 만 사라진 경우 localStorage 세션과 IndexedDB draft 로 재개한다. */
export async function resumeProductPhotoPromotionForStoryboard(projectId, io = {}) {
  if (!projectId) return { promotionObserved: false, recoveryAttempted: false, recovered: false };
  const current = getProductPhotoPromotionTask(projectId);
  if (current) {
    await productPhotosReady(projectId, io).catch(() => null);
    return { promotionObserved: true, recoveryAttempted: false, recovered: current.status === 'settled' };
  }

  const readSession = io.readSession
    ?? (() => import('./draftPromotionSession.js').then(({ draftPromotionSession }) => draftPromotionSession.read()));
  const session = await Promise.resolve().then(readSession).catch(() => ({}));
  if (session?.projectId !== projectId) {
    return { promotionObserved: false, recoveryAttempted: false, recovered: false };
  }

  const load = io.loadDraft ?? (await import('./draftStore.js')).loadDraft;
  const draft = await Promise.resolve().then(load).catch(() => null);
  if (!draft?.product || !(draft.photos || []).length) {
    return { promotionObserved: false, recoveryAttempted: false, recovered: false };
  }

  const retry = io.retry ?? retryProductPhotoPromotionFromDraft;
  const recovered = await retry(projectId);
  return { promotionObserved: true, recoveryAttempted: true, recovered };
}
