/* 확정(CTA) 이후의 상품 사진 업로드를 화면 전환에서 떼어낸다.

   문제였던 것: CTA 는 프로젝트 생성 → **사진 전부 R2 업로드** → 상품·분석 저장까지 끝난 뒤에야
   콘티보드로 넘어갔다. 업로드는 사진 용량에 정비례하고, 오너 실측(2026-08-17)으로
   2장 4.9MB=44초 · 4장 5.6MB=75초 · 6장 14.2MB=211초였다. 그 시간 동안 화면은 그대로 멈춰 있다.

   구조는 내 옷 승격(customMatchPromotion)과 같다: 입력 화면이 시작한 프라미스를 콘티보드가
   구독한다. 다른 점은 **진행률**이다 — 사진은 몇 장 중 몇 장이 올라갔는지 셀러가 알아야
   기다림이 답답하지 않다(오너 요구 4안).

   완료된 task 도 세션 동안 남긴다. 콘티보드가 조금 늦게 마운트돼도 결과를 놓치지 않는다. */

const tasks = new Map();
const MAX_TRACKED = 8;

const snapshot = (task) => ({
  status: task.status,
  total: task.total,
  done: task.done,
  error: task.error || null,
});

function notify(task) {
  const state = snapshot(task);
  for (const listener of task.listeners) {
    try { listener(state); } catch { /* 구독자 오류가 다른 구독자를 막지 않는다 */ }
  }
}

/** 승격 시작 — `run({ onPhotoProgress })` 이 실제 업로드·저장을 수행한다.
 *  같은 프로젝트로 두 번 부르면 진행 중 task 를 그대로 돌려준다(중복 업로드 방지). */
export function startProductPhotoPromotion(projectId, total, run) {
  if (!projectId) return null;
  const current = tasks.get(projectId);
  if (current?.status === 'pending') return current;

  const task = {
    status: 'pending',
    total: Number.isFinite(total) ? Math.max(0, total) : 0,
    done: 0,
    error: null,
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
      task.status = 'settled';
      task.done = task.total;
      notify(task);
      return result;
    }, (error) => {
      task.status = 'failed';
      task.error = error;
      notify(task);
      throw error;
    });

  tasks.set(projectId, task);
  if (tasks.size > MAX_TRACKED) {
    const oldest = tasks.keys().next().value;
    if (oldest !== projectId) tasks.delete(oldest);
  }
  return task;
}

/** 프로젝트 id 는 승격 도중에 생긴다. 그 전까지 임시 키로 담아 둔 task 를 실제 id 로 옮긴다. */
export function adoptProductPhotoPromotion(temporaryKey, projectId) {
  if (!temporaryKey || !projectId || temporaryKey === projectId) return null;
  const task = tasks.get(temporaryKey);
  if (!task) return null;
  tasks.delete(temporaryKey);
  tasks.set(projectId, task);
  return task;
}

//: 아직 projectId 가 없는 동안 쓰는 임시 키.
export const NEW_PROJECT_KEY = '__pending_project__';

export function getProductPhotoPromotionTask(projectId) {
  return projectId ? tasks.get(projectId) ?? null : null;
}

/** 콘티보드가 기다릴 프라미스. 없으면(이미 끝난 세션·복원 진입) 즉시 통과한다 —
 *  사진은 그때 이미 서버에 있으므로 여기서 막을 이유가 없다. */
export function productPhotosReady(projectId) {
  const task = getProductPhotoPromotionTask(projectId);
  return task ? task.promise : Promise.resolve(null);
}

export function clearProductPhotoPromotionTask(projectId) {
  if (projectId) tasks.delete(projectId);
}
