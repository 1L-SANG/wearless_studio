import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  createStoryboardEntryPrefetchCache,
  shouldRenderStoryboardLoadingFrame,
} from '../../src/features/storyboard/storyboardEntryPrefetchCache.js';

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
  assert.match(
    productInputSource,
    /storyboardPrefetchProjectRef\.current === analysisProjectId[\s\S]*?prefetchStoryboardEntry\(analysisProjectId\)/,
  );
  assert.doesNotMatch(mannequinSource, /warmStoryboardEntry/);
});

test('a ready cache hit initializes content and skips the loading-frame branch', async () => {
  const cache = createStoryboardEntryPrefetchCache({ ttlMs: 1_000 });
  const data = [[{ id: 'board-1' }], { genExamples: [] }, [], { colors: [] }, {}];
  await cache.prefetch('project-1', async () => data);

  const prefetched = cache.peek('project-1');
  assert.ok(prefetched);
  assert.equal(shouldRenderStoryboardLoadingFrame(prefetched[0], prefetched[1]), false);
  assert.match(storyboardSource, /const initialEntry = initialEntryRef\.current\?\.prepared/);
  assert.match(storyboardSource, /useState\(\(\) => initialEntry\?\.blocks \|\| null\)/);
  assert.match(storyboardSource, /useState\(\(\) => initialEntry\?\.catalogs \|\| null\)/);
  assert.match(storyboardSource, /if \(shouldRenderStoryboardLoadingFrame\(blocks, catalogs\)\) return <StoryboardLoadingFrame/);
});

test('missing, stale, invalidated, and failed entries take the loading fallback', async () => {
  let clock = 0;
  const cache = createStoryboardEntryPrefetchCache({ ttlMs: 10, now: () => clock });
  assert.equal(cache.peek('missing'), null);
  assert.equal(shouldRenderStoryboardLoadingFrame(null, null), true);

  let finishSlow;
  const slowLoad = new Promise((resolve) => { finishSlow = resolve; });
  const slow = cache.prefetch('slow', () => slowLoad);
  assert.equal(cache.peek('slow'), null);
  assert.equal(shouldRenderStoryboardLoadingFrame(null, null), true);
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
