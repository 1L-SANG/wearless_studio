/* 확정 CTA 는 사진 업로드를 기다리지 않는다 (2026-08-18 오너 요구).

   종전: CTA → 프로젝트 생성 → **사진 전부 업로드** → 상품·분석 저장 → 화면 전환.
   업로드는 용량에 정비례하고 오너 실측으로 2장 4.9MB=44초 · 4장 5.6MB=75초 ·
   6장 14.2MB=211초였다. 그 시간 동안 분석 화면이 그대로 멈춰 있었다.

   지금: CTA 는 **프로젝트 신원까지만** 기다리고 즉시 콘티보드로 넘어간다. 남은 업로드·저장은
   프라미스로 이어지고, 콘티보드가 그것을 구독해 ① 진행률을 보여주고 ② 보드 시드를 읽기 전과
   마네킹 생성 전에 정착을 기다린다(시드가 상품 색상을 읽으므로 순서가 중요하다). */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  adoptProductPhotoPromotion,
  clearProductPhotoPromotionTask,
  getProductPhotoPromotionTask,
  NEW_PROJECT_KEY,
  productPhotosReady,
  startProductPhotoPromotion,
} from '../../src/lib/productPhotoPromotion.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

test('승격은 진행률을 구독자에게 알린다', async () => {
  clearProductPhotoPromotionTask('p1');
  const gate = deferred();
  const task = startProductPhotoPromotion('p1', 3, async ({ onPhotoProgress }) => {
    onPhotoProgress({ done: 0, total: 3 });
    onPhotoProgress({ done: 2, total: 3 });
    await gate.promise;
    return { projectId: 'p1' };
  });

  const seen = [];
  const unsubscribe = task.subscribe((state) => seen.push(`${state.status} ${state.done}/${state.total}`));
  await Promise.resolve();
  assert.deepEqual(seen.slice(0, 3), ['pending 0/3', 'pending 0/3', 'pending 2/3']);

  gate.resolve();
  await task.promise;
  assert.equal(seen.at(-1), 'settled 3/3', '완료 시 전량 완료로 알린다');
  unsubscribe();
  clearProductPhotoPromotionTask('p1');
});

test('실패도 구독자에게 알리고 프라미스는 거부된다', async () => {
  clearProductPhotoPromotionTask('p2');
  const task = startProductPhotoPromotion('p2', 1, async () => { throw new Error('upload down'); });
  const seen = [];
  task.subscribe((state) => seen.push(state.status));
  await assert.rejects(task.promise, /upload down/);
  assert.equal(seen.at(-1), 'failed');
  clearProductPhotoPromotionTask('p2');
});

test('같은 프로젝트로 두 번 시작해도 업로드는 한 번만 돈다', async () => {
  clearProductPhotoPromotionTask('p3');
  let runs = 0;
  const gate = deferred();
  const first = startProductPhotoPromotion('p3', 1, async () => { runs += 1; await gate.promise; return 1; });
  const second = startProductPhotoPromotion('p3', 1, async () => { runs += 1; return 2; });
  assert.equal(second, first, '진행 중이면 같은 task 를 돌려준다');
  gate.resolve();
  await first.promise;
  assert.equal(runs, 1);
  clearProductPhotoPromotionTask('p3');
});

test('프로젝트 id 가 생기면 임시 키의 task 를 그 id 로 옮긴다', async () => {
  clearProductPhotoPromotionTask(NEW_PROJECT_KEY);
  clearProductPhotoPromotionTask('p4');
  const gate = deferred();
  const task = startProductPhotoPromotion(NEW_PROJECT_KEY, 2, async () => { await gate.promise; return 'x'; });
  assert.equal(getProductPhotoPromotionTask('p4'), null);
  adoptProductPhotoPromotion(NEW_PROJECT_KEY, 'p4');
  assert.equal(getProductPhotoPromotionTask('p4'), task, '콘티보드는 실제 projectId 로 구독한다');
  assert.equal(getProductPhotoPromotionTask(NEW_PROJECT_KEY), null, '임시 키는 남지 않는다');
  gate.resolve();
  await task.promise;
  clearProductPhotoPromotionTask('p4');
});

test('추적하는 승격이 없으면 콘티보드는 기다리지 않는다', async () => {
  clearProductPhotoPromotionTask('p5');
  // 복원 진입·이미 끝난 세션 — 사진은 그때 이미 서버에 있으므로 막을 이유가 없다.
  assert.equal(await productPhotosReady('p5'), null);
  assert.equal(await productPhotosReady(null), null);
});

// ── 배선 계약 ────────────────────────────────────────────────────────────────

test('CTA 는 프로젝트 신원까지만 기다리고 화면을 넘긴다', () => {
  const src = read('../../src/features/product-input/ProductInput.jsx');
  assert.match(src, /onProjectReady: \(id\) => \{/, 'projectId 가 생긴 순간 전환해야 한다');
  assert.equal(src.includes('await promoteDraftToProject(draft)'), false,
    '승격 전체를 await 하면 업로드 시간만큼 화면이 멈춘다');
  // 업로드가 끝나기 전에 로컬 draft 를 지우면 재시도할 사진 원본이 사라진다.
  assert.match(src, /promotion\.promise[\s\S]{0,120}clearDraft\(\)/);
});

test('draftSync 는 신원 생성 직후 알리고 장수별 진행률을 보고한다', () => {
  const src = read('../../src/lib/draftSync.js');
  assert.match(src, /onProjectReady\?\.\(projectId\)/);
  assert.match(src, /onPhotoProgress\?\.\(\{ done, total: uploadable\.length \}\)/);
  // 알림은 업로드 시작 **전에** 나가야 의미가 있다.
  assert.ok(src.indexOf('onProjectReady?.(projectId)') < src.indexOf('const uploadable ='));
});

test('콘티보드는 보드를 읽기 전에 사진 정착을 기다린다', () => {
  const src = read('../../src/features/storyboard/Storyboard.jsx');
  assert.match(src, /await productPhotosReady\(pid\)/);
  // 시드가 상품 색상을 읽으므로(shapes.defaultStoryboard) 반드시 보드 GET 앞이어야 한다.
  assert.ok(src.indexOf('await productPhotosReady(pid)') < src.indexOf('loadStoryboardEntry(pid)'));
  // 수십 초 대기 중 언마운트되면 setState 를 하지 않는다.
  assert.match(src, /await productPhotosReady\(pid\)\.catch\(\(\) => null\);\s*\n\s*if \(!active\) return;/);
  // 진행률 UI 와 훅 배치(훅은 early-return 위 — 화이트스크린 방지).
  assert.match(src, /StoryboardLoadingState photoUpload=\{photoUploadProgress\}/);
  assert.ok(src.indexOf('usePhotoUploadProgress(') < src.indexOf('if (!blocks || !catalogs)'));
});


test('업로드 실패는 콘티보드에서 셀러에게 알린다', () => {
  const src = read('../../src/features/storyboard/Storyboard.jsx');
  assert.match(src, /getProductPhotoPromotionTask\(pid\)\?\.status === 'failed'/);
  assert.match(src, /사진 업로드를 끝내지 못했어요/);
});

test('프리페치도 사진 정착 뒤에 돈다 — 빈 상품으로 시드된 보드가 캐시되면 안 된다', () => {
  const src = read('../../src/features/product-input/ProductInput.jsx');
  assert.match(src, /productPhotosReady\(analysisProjectId\)[\s\S]{0,160}prefetchStoryboardEntry\(analysisProjectId\)/);
});
