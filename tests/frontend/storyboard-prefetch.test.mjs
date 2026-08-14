import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { createStoryboardEntryPrefetchCache } from '../../src/features/storyboard/storyboardEntryPrefetchCache.js';

const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);
const mannequinSource = readFileSync(
  new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
  'utf8',
);
const productInputSource = readFileSync(
  new URL('../../src/features/product-input/ProductInput.jsx', import.meta.url),
  'utf8',
);
const saveRoutingSource = readFileSync(
  new URL('../../src/features/product-input/saveRouting.js', import.meta.url),
  'utf8',
);

test('repeated mannequin warm-ups share one project request', async () => {
  const cache = createStoryboardEntryPrefetchCache({ ttlMs: 1_000 });
  let loads = 0;
  let idleWaits = 0;
  const data = [[{ id: 'board-1' }], { genExamples: [] }, [], { colors: [] }, {}];
  const load = async () => { loads += 1; return data; };
  const waitForIdle = async () => { idleWaits += 1; };

  const first = cache.prefetch('project-1', load, waitForIdle);
  const repeated = cache.prefetch('project-1', load, waitForIdle);
  assert.equal(first, repeated);
  assert.equal(await first, data);
  assert.equal(loads, 1);
  assert.equal(idleWaits, 1);
  assert.equal(cache.peek('project-1'), data);
  // 트리거는 project id 만으로는 이르다 — 콘티 시드가 읽는 필드가 전부 저장된 뒤인
  // phase==='done' 까지 함께 게이트해야 한다 (그렇지 않으면 submit() 시작 시점의
  // analysisProjectId 만으로 미저장 상태를 캐시해 버린다).
  assert.match(
    productInputSource,
    /if \(!analysisProjectId \|\| phase !== 'done'\) return;[\s\S]*?storyboardPrefetchProjectRef\.current === analysisProjectId[\s\S]*?prefetchStoryboardEntry\(analysisProjectId\)/,
  );
  assert.match(productInputSource, /\}, \[analysisProjectId, phase\]\);/);
  assert.doesNotMatch(mannequinSource, /warmStoryboardEntry/);
});

test('edits that touch the storyboard seed invalidate the warmed prefetch', () => {
  // 시드가 읽는 필드(colors·clothingType·targetGenders·matchClothing) 목록이 실제로 이 네 키를 담고 있어야
  // 한다 — 목록이 비거나 다른 키로 바뀌면 이 어서션이 깨져 드리프트를 잡아낸다.
  assert.match(
    saveRoutingSource,
    /STORYBOARD_SEED_PATCH_KEYS = new Set\(\[['"]colors['"], ['"]clothingType['"], ['"]targetGenders['"], ['"]matchClothing['"]\]\)/,
  );
  // persistAnalysisEdit — 이 화면의 모든 분석 편집 저장이 지나는 단일 퍼널 — 이 그 키 집합을
  // 실제로 검사해 invalidateStoryboardEntryPrefetch 를 호출해야 한다. UI 핸들러 각각이 아니라
  // 이 퍼널 한 곳에 있어야 향후 편집 경로가 무효화를 빠뜨릴 수 없다.
  assert.match(
    saveRoutingSource,
    /export async function persistAnalysisEdit\(api, projectId, patch\) \{[\s\S]*?STORYBOARD_SEED_PATCH_KEYS\.has\(key\)\)\) \{\s*\n\s*invalidateStoryboardEntryPrefetch\(projectId\);/,
  );
});

test('a ready cache hit initializes content and skips the empty loading state', async () => {
  const cache = createStoryboardEntryPrefetchCache({ ttlMs: 1_000 });
  const data = [[{ id: 'board-1' }], { genExamples: [] }, [], { colors: [] }, {}];
  await cache.prefetch('project-1', async () => data);

  const prefetched = cache.peek('project-1');
  assert.ok(prefetched);
  assert.match(storyboardSource, /const initialEntry = initialEntryRef\.current\?\.prepared/);
  assert.match(storyboardSource, /useState\(\(\) => initialEntry\?\.blocks \|\| null\)/);
  assert.match(storyboardSource, /useState\(\(\) => initialEntry\?\.catalogs \|\| null\)/);
  assert.match(storyboardSource, /if \(!blocks \|\| !catalogs\) return <StoryboardLoadingState \/>/);
});

test('missing, stale, invalidated, and failed entries stay unavailable for the empty loading state', async () => {
  let clock = 0;
  const cache = createStoryboardEntryPrefetchCache({ ttlMs: 10, now: () => clock });
  assert.equal(cache.peek('missing'), null);

  let finishSlow;
  const slowLoad = new Promise((resolve) => { finishSlow = resolve; });
  const slow = cache.prefetch('slow', () => slowLoad);
  assert.equal(cache.peek('slow'), null);
  finishSlow([[{}], {}, [], {}, {}]);
  await slow;

  await cache.prefetch('stale', async () => [[{}], {}, [], {}, {}]);
  clock = 11;
  assert.equal(cache.peek('stale'), null);

  clock = 20;
  await cache.prefetch('invalidated', async () => [[{}], {}, [], {}, {}]);
  cache.invalidate('invalidated');
  assert.equal(cache.peek('invalidated'), null);

  let failedLoads = 0;
  clock = 40;
  const failed = async () => { failedLoads += 1; throw new Error('expected prefetch failure'); };
  assert.equal(await cache.prefetch('failed', failed), null);
  assert.equal(await cache.prefetch('failed', failed), null);
  assert.equal(await cache.consume('failed'), null);
  assert.equal(failedLoads, 1);

  const loadingState = storyboardSource.slice(
    storyboardSource.indexOf('function StoryboardLoadingState()'),
    storyboardSource.indexOf('function prepareStoryboardEntry'),
  );
  assert.match(loadingState, /role="status" aria-busy="true"[\s\S]*?콘티보드를 불러오는 중이에요/);
  assert.doesNotMatch(loadingState, /PageHead|sb-loading-|StoryboardLoadingFrame/);
});

test('project changes discard the previous project cache', async () => {
  const cache = createStoryboardEntryPrefetchCache({ ttlMs: 1_000 });
  await cache.prefetch('project-1', async () => [[{ id: 'one' }], {}, [], {}, {}]);
  await cache.prefetch('project-2', async () => [[{ id: 'two' }], {}, [], {}, {}]);

  assert.equal(cache.peek('project-2')[0][0].id, 'two');
  assert.equal(cache.peek('project-1'), null);
  assert.equal(cache.peek('project-2'), null, 'switching back scopes the cache and drops project-2 too');
});

test('pending storyboard restore prevents synchronous cache hydration', () => {
  assert.match(
    storyboardSource,
    /initialProjectId && !sbPending\.has\(initialProjectId\)[\s\S]*?peekStoryboardEntry\(initialProjectId\)/,
  );
  assert.match(storyboardSource, /await sbSaveIdle\(\)[\s\S]*?consumeStoryboardEntry\(pid\)/);
});
