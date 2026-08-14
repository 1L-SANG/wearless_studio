import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  INITIAL_REVEAL_TIMEOUT_MS,
  collectInitialRevealThumbnailUrls,
  waitForInitialReveal,
} from '../../src/features/storyboard/initialRevealGate.js';

const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);
const featureStyles = readFileSync(
  new URL('../../src/styles/features.css', import.meta.url),
  'utf8',
);

test('all initial thumbnails loaded releases the gate immediately', async () => {
  const loaded = [];
  let timerCleared = false;
  const result = await waitForInitialReveal(['/one.webp', '/two.webp'], {
    loadImage: async (url) => { loaded.push(url); },
    setTimeoutFn: () => 17,
    clearTimeoutFn: (timerId) => {
      assert.equal(timerId, 17);
      timerCleared = true;
    },
  });

  assert.equal(result.reason, 'settled');
  assert.deepEqual(loaded, ['/one.webp', '/two.webp']);
  assert.equal(timerCleared, true);
});

test('a failed thumbnail does not keep the gate closed', async () => {
  const result = await waitForInitialReveal(['/ok.webp', '/failed.webp'], {
    loadImage: async (url) => {
      if (url === '/failed.webp') throw new Error('expected image failure');
    },
    setTimeoutFn: () => 21,
    clearTimeoutFn: () => {},
  });

  assert.equal(result.reason, 'settled');
  assert.deepEqual(result.results.map(({ status }) => status), ['fulfilled', 'rejected']);
});

test('a slow thumbnail releases the gate at the 2.5 second upper bound', async () => {
  let releaseTimeout;
  let observedTimeout;
  let resolved = false;
  const resultPromise = waitForInitialReveal(['/slow.webp'], {
    loadImage: () => new Promise(() => {}),
    setTimeoutFn: (callback, delay) => {
      observedTimeout = delay;
      releaseTimeout = callback;
      return 25;
    },
    clearTimeoutFn: () => assert.fail('a fired timeout must not be cleared as settled'),
  }).then((result) => {
    resolved = true;
    return result;
  });

  await Promise.resolve();
  assert.equal(resolved, false);
  assert.equal(observedTimeout, INITIAL_REVEAL_TIMEOUT_MS);
  releaseTimeout();
  assert.equal((await resultPromise).reason, 'timeout');
});

test('an empty thumbnail list releases the gate immediately without a timer', async () => {
  let loadCalled = false;
  let timerCalled = false;
  const result = await waitForInitialReveal([], {
    loadImage: async () => { loadCalled = true; },
    setTimeoutFn: () => { timerCalled = true; },
  });

  assert.equal(result.reason, 'empty');
  assert.equal(loadCalled, false);
  assert.equal(timerCalled, false);
});

test('thumbnail collection follows rendered section and preview order', () => {
  const sections = Array.from({ length: 4 }, (_, sectionIndex) => ({
    items: Array.from({ length: 4 }, (_, imageIndex) => ({
      block: { thumb: `/s${sectionIndex + 1}-${imageIndex + 1}.webp` },
    })),
  }));

  assert.deepEqual(
    collectInitialRevealThumbnailUrls(sections, (block) => block.thumb),
    [
      '/s1-1.webp', '/s1-2.webp', '/s1-3.webp',
      '/s2-1.webp', '/s2-2.webp', '/s2-3.webp',
      '/s3-1.webp', '/s3-2.webp', '/s3-3.webp',
    ],
  );
});

test('the released gate reveals board chrome immediately and sections in capped 90ms steps', () => {
  assert.match(
    storyboardSource,
    /boardGroups\.map\(\(group, groupIndex\)[\s\S]*?style=\{\{ '--reveal-order': Math\.min\(groupIndex, 6\) \}\}/,
  );
  assert.match(
    storyboardSource,
    /sb-page sb-content-enter sb-initial-reveal\$\{initialBoardRevealed \? ' is-revealed' : ''\}/,
  );

  const revealStyles = featureStyles.slice(
    featureStyles.indexOf('/* 첫 화면 썸네일 게이트 중에도'),
    featureStyles.indexOf('.sb-page.is-atomic-saving'),
  );
  assert.match(revealStyles, /\.sb-initial-reveal > \.page-head/);
  assert.match(revealStyles, /\.sb-initial-reveal \.sb-board-tools/);
  assert.match(revealStyles, /\.sb-initial-reveal \.sb-deck/);
  assert.match(revealStyles, /transform: translate3d\(0, 8px, 0\)/);
  assert.match(revealStyles, /transition: opacity \.45s ease, transform \.45s ease/);
  assert.match(revealStyles, /--reveal-delay: calc\(var\(--reveal-order, 0\) \* 90ms\)/);
  assert.match(revealStyles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?transition: none;[\s\S]*?transition-delay: 0s/);
});
