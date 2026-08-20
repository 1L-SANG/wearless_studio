import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

test('F1 Storyboard 첫 렌더는 업로드 훅을 실행해도 ReferenceError 없이 로딩 화면을 그린다', async (t) => {
  const vite = await createServer({
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true },
    appType: 'custom',
  });
  t.after(() => vite.close());

  const { Storyboard } = await vite.ssrLoadModule('/src/features/storyboard/Storyboard.jsx');
  const { useAppStore } = await vite.ssrLoadModule('/src/store/useAppStore.js');
  useAppStore.setState({ projectId: 'project-f1' });

  const html = renderToString(
    React.createElement(
      MemoryRouter,
      { initialEntries: ['/create/storyboard'] },
      React.createElement(Storyboard, { toastOverride: { push() {} } }),
    ),
  );

  assert.match(html, /role="status"/);
});

test('F2 콘티 전환 상태는 실제 내 옷 업로드가 있을 때만 승격 시작으로 표시한다', async () => {
  const { storyboardTransitionState } = await import(
    '../../src/features/product-input/storyboardTransition.js'
  );

  assert.deepEqual(storyboardTransitionState({ customMatch: { uploads: [] } }), {
    showMannequinTransition: true,
    customMatchPromotionStarted: false,
  });
  assert.equal(storyboardTransitionState({
    customMatch: { uploads: [{ filename: 'mine.jpg' }] },
  }).customMatchPromotionStarted, true);
});

test('F3 내 옷 승격은 구독이 먼저여도 나중에 등록된 태스크의 pending과 settle을 전달한다', async () => {
  const mod = await import('../../src/lib/customMatchPromotion.js');
  const projectId = 'project-f3';
  mod.clearCustomMatchPromotionTask(projectId);
  const seen = [];
  const off = mod.subscribeCustomMatchPromotion(projectId, (state) => {
    seen.push(state?.status ?? 'none');
  });
  let finish;
  const api = {
    uploadPhoto: async () => ({ assetId: 'asset-1' }),
    addCustomMatchItem: async () => {
      await new Promise((resolve) => { finish = resolve; });
      return { analysis: { matchClothing: [{ id: 'mine', isCustom: true }] } };
    },
    clearCustomMatchDraft() {},
  };
  const task = mod.startCustomMatchPromotion(api, projectId, {
    uploads: [{ filename: 'mine.jpg', mime: 'image/jpeg', blob: new Blob() }],
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(seen.slice(0, 2), ['none', 'pending']);
  finish();
  await task.promise;
  assert.equal(seen.at(-1), 'settled');
  off();
  mod.clearCustomMatchPromotionTask(projectId);
});

test('F7 상품 사진 진행률 구독은 같은 프로젝트 키의 재시도 태스크로 자동 교체된다', async () => {
  const mod = await import('../../src/lib/productPhotoPromotion.js');
  const projectId = 'project-f7';
  mod.clearProductPhotoPromotionTask(projectId);
  const seen = [];
  const off = mod.subscribeProductPhotoPromotion(projectId, (state) => {
    seen.push(state?.status ?? 'none');
  });
  const failed = mod.startProductPhotoPromotion(projectId, async () => {
    throw new Error('first failed');
  });
  await assert.rejects(failed.promise, /first failed/);
  const retry = mod.startProductPhotoPromotion(projectId, async ({ onPhotoProgress }) => {
    onPhotoProgress({ done: 1, total: 1 });
  });
  await retry.promise;

  assert.deepEqual(seen.filter((status) => status !== 'none'), [
    'pending', 'failed', 'pending', 'pending', 'settled',
  ]);
  off();
  mod.clearProductPhotoPromotionTask(projectId);
});

test('F4 새로고침 복구는 세션과 draft가 있으면 승격을 마친 뒤에만 보드 로드를 허용한다', async () => {
  const mod = await import('../../src/lib/productPhotoPromotion.js');
  const calls = [];
  let finish;
  const recovery = mod.resumeProductPhotoPromotionForStoryboard('project-f4', {
    readSession: () => ({ projectId: 'project-f4' }),
    loadDraft: async () => ({ product: { name: 'knit' }, photos: [{ imageId: 'photo-1' }] }),
    retry: async () => {
      calls.push('retry');
      await new Promise((resolve) => { finish = resolve; });
      calls.push('recovered');
      return true;
    },
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  calls.push('before-finish');
  finish();
  const result = await recovery;
  calls.push('load-board');

  assert.deepEqual(calls, ['retry', 'before-finish', 'recovered', 'load-board']);
  assert.deepEqual(result, { promotionObserved: true, recoveryAttempted: true, recovered: true });
});

test('F11 상품 사진 승격은 진행률이 멈추면 실패로 전환되어 무한 대기를 끝낸다', async () => {
  const mod = await import('../../src/lib/productPhotoPromotion.js');
  const projectId = 'project-f11';
  mod.clearProductPhotoPromotionTask(projectId);
  mod.startProductPhotoPromotion(projectId, async () => new Promise(() => {}));

  const outcome = await Promise.race([
    mod.productPhotosReady(projectId, { stallMs: 10 }).then(
      () => ({ status: 'resolved' }),
      (error) => ({ status: 'rejected', error }),
    ),
    new Promise((resolve) => setTimeout(() => resolve({ status: 'still-pending' }), 30)),
  ]);
  assert.equal(outcome.status, 'rejected');
  assert.match(outcome.error.message, /진행이 멈췄습니다/);
  assert.equal(mod.getProductPhotoPromotionTask(projectId).status, 'failed');
  mod.clearProductPhotoPromotionTask(projectId);
});

test('F11 워치독 뒤 살아 있는 단일비행에는 재합류하지 않고, 늦은 성공은 settled로 회복한다', async () => {
  const mod = await import('../../src/lib/productPhotoPromotion.js');
  const projectId = 'project-f11-late';
  mod.clearProductPhotoPromotionTask(projectId);
  let finish;
  const task = mod.startProductPhotoPromotion(projectId, async () => (
    new Promise((resolve) => { finish = resolve; })
  ));
  await assert.rejects(mod.productPhotosReady(projectId, { stallMs: 10 }), /진행이 멈췄습니다/);

  let reruns = 0;
  assert.equal(await mod.retryProductPhotoPromotionFromDraft(projectId, {
    loadDraft: async () => ({ product: { name: 'knit' }, photos: [{ imageId: 'photo-1' }] }),
    resetRetry: () => false,
    promote: async () => { reruns += 1; },
    finishDraft: async () => {},
  }), false);
  assert.equal(reruns, 0);

  finish({ projectId });
  await task.promise;
  assert.equal(mod.getProductPhotoPromotionTask(projectId).status, 'settled');
  mod.clearProductPhotoPromotionTask(projectId);
});

test('F5 지연 정리는 승격을 시작한 revision과 현재 draft가 같을 때만 삭제한다', async () => {
  const { clearDraftIfCurrent } = await import('../../src/lib/draftStore.js');
  let current = { updatedAt: 'new-revision', product: { name: 'new product' } };
  let cleared = 0;
  const io = {
    load: async () => current,
    clear: async () => { current = null; cleared += 1; },
  };

  assert.equal(await clearDraftIfCurrent('old-revision', io), false);
  assert.equal(current.product.name, 'new product');
  assert.equal(cleared, 0);
  assert.equal(await clearDraftIfCurrent('new-revision', io), true);
  assert.equal(current, null);
  assert.equal(cleared, 1);

  current = { updatedAt: 'old-revision', product: { name: 'old product' } };
  assert.equal(await clearDraftIfCurrent('old-revision', {
    ...io,
    getPending: () => ({ updatedAt: 'queued-new-revision', product: { name: 'queued new product' } }),
  }), false);
  assert.equal(current.product.name, 'old product');
  assert.equal(cleared, 1, '아직 디스크에 쓰이지 않은 새 draft도 지우면 안 된다');

  let pending = null;
  current = { updatedAt: 'old-revision', product: { name: 'old product' } };
  assert.equal(await clearDraftIfCurrent('old-revision', {
    ...io,
    getPending: () => pending,
    waitForSaves: async () => { pending = { updatedAt: 'new-during-wait' }; },
  }), false);
  assert.equal(current.product.name, 'old product');
  assert.equal(cleared, 1, '정리 대기 중 생긴 새 draft도 지우면 안 된다');
});

test('F8 화면이 없을 때 난 업로드 실패도 draft 슬롯을 복구하고 다음 리스너에게 한 번 전달한다', async () => {
  const mod = await import('../../src/lib/productPhotoPromotion.js');
  const projectId = 'project-f8';
  mod.clearProductPhotoPromotionTask(projectId);
  let recovered = 0;
  const task = mod.startProductPhotoPromotion(
    projectId,
    async () => { throw new Error('background upload failed'); },
    { recoverDraftSlot: () => { recovered += 1; } },
  );
  await assert.rejects(task.promise, /background upload failed/);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(recovered, 1);

  const delivered = [];
  const off = mod.onProductPhotoPromotionFailure((id, error) => {
    delivered.push([id, error.message]);
  });
  assert.deepEqual(delivered, [[projectId, 'background upload failed']]);
  off();
  const late = [];
  const offLate = mod.onProductPhotoPromotionFailure((id) => late.push(id));
  assert.deepEqual(late, [], '같은 실패는 한 번 전달한 뒤 다시 재생하지 않는다');
  offLate();
  mod.clearProductPhotoPromotionTask(projectId);
});

test('F9 프리페치는 pending 승격 성공 뒤에만 실행되고 실패하면 실행하지 않는다', async () => {
  const photo = await import('../../src/lib/productPhotoPromotion.js');
  const { prefetchStoryboardAfterProductPhotos } = await import(
    '../../src/features/storyboard/storyboardEntryPrefetch.js'
  );
  photo.clearProductPhotoPromotionTask(photo.NEW_PROJECT_KEY);
  let finish;
  const pending = photo.startProductPhotoPromotion(photo.NEW_PROJECT_KEY, async () => {
    await new Promise((resolve) => { finish = resolve; });
  });
  const calls = [];
  const waiting = prefetchStoryboardAfterProductPhotos('project-f9', {
    prefetch: async (id) => { calls.push(id); return 'prefetched'; },
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(calls, []);
  finish();
  await pending.promise;
  assert.equal(await waiting, 'prefetched');
  assert.deepEqual(calls, ['project-f9']);
  photo.clearProductPhotoPromotionTask(photo.NEW_PROJECT_KEY);

  const failed = photo.startProductPhotoPromotion(photo.NEW_PROJECT_KEY, async () => {
    throw new Error('prefetch must stop');
  });
  const skipped = prefetchStoryboardAfterProductPhotos('project-f9-failed', {
    prefetch: async (id) => { calls.push(id); },
  });
  await assert.rejects(failed.promise, /prefetch must stop/);
  assert.equal(await skipped, null);
  assert.deepEqual(calls, ['project-f9']);
  photo.clearProductPhotoPromotionTask(photo.NEW_PROJECT_KEY);
});

test('F10 사진 편집은 콘티 프리페치 캐시를 무효화하고 승격을 기다린 로드는 초기 캐시를 재사용하지 않는다', async () => {
  const { createStoryboardEntryPrefetchCache } = await import(
    '../../src/features/storyboard/storyboardEntryPrefetchCache.js'
  );
  const {
    invalidateStoryboardForProductPhotoEdit,
  } = await import('../../src/features/product-input/storyboardTransition.js');
  const { shouldReuseInitialStoryboardEntry } = await import(
    '../../src/features/storyboard/storyboardEntryReuse.js'
  );
  const cache = createStoryboardEntryPrefetchCache();
  await cache.prefetch('project-f10', async () => ['stale-entry']);
  assert.deepEqual(cache.peek('project-f10'), ['stale-entry']);
  invalidateStoryboardForProductPhotoEdit('project-f10', cache.invalidate);
  assert.equal(cache.peek('project-f10'), null);

  const initialEntry = { projectId: 'project-f10', raw: ['fresh-entry'] };
  assert.equal(shouldReuseInitialStoryboardEntry({
    usePending: false,
    promotionObserved: true,
    initialEntry,
    projectId: 'project-f10',
    entry: initialEntry.raw,
  }), false);
  assert.equal(shouldReuseInitialStoryboardEntry({
    usePending: false,
    promotionObserved: false,
    initialEntry,
    projectId: 'project-f10',
    entry: initialEntry.raw,
  }), true);
});

test('정리1 상품 사진 total은 시작 인자가 아니라 실제 진행률 콜백만 소유한다', async () => {
  const mod = await import('../../src/lib/productPhotoPromotion.js');
  const projectId = 'project-cleanup-total';
  mod.clearProductPhotoPromotionTask(projectId);
  let report;
  let finish;
  const runner = async ({ onPhotoProgress }) => {
    report = onPhotoProgress;
    await new Promise((resolve) => { finish = resolve; });
  };
  const task = mod.startProductPhotoPromotion.length === 2
    ? mod.startProductPhotoPromotion(projectId, runner)
    : mod.startProductPhotoPromotion(projectId, 99, runner);
  await Promise.resolve();
  assert.equal(mod.getProductPhotoPromotionTask(projectId).total, 0);
  report({ done: 1, total: 3 });
  assert.equal(mod.getProductPhotoPromotionTask(projectId).total, 3);
  finish();
  await task.promise;
  mod.clearProductPhotoPromotionTask(projectId);
});

test('정리2·3 업로드 로딩은 공용 ProgressBar와 결합된 status 영역을 실제로 렌더한다', async (t) => {
  const vite = await createServer({
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true },
    appType: 'custom',
  });
  t.after(() => vite.close());
  const { StoryboardLoadingState } = await vite.ssrLoadModule(
    '/src/features/storyboard/Storyboard.jsx'
  );
  const html = renderToString(React.createElement(StoryboardLoadingState, {
    photoUpload: { done: 1, total: 4 },
  }));

  assert.match(html, /role="status" aria-busy="true"/);
  assert.match(html, /class="progress"/);
  assert.match(html, /사진 4장 중 1장 올렸어요/);
  assert.doesNotMatch(html, /sb-upload-progress-track|sb-upload-progress-bar/);
});
