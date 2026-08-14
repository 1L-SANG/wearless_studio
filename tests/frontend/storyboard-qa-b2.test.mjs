import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { createServer } from 'vite';

import { continueAfterStoryboardFlush } from '../../src/features/storyboard/storyboardNavigation.js';
import {
  bindStoryboardExitFlush,
  scheduleStoryboardAutosave,
  STORYBOARD_AUTOSAVE_DELAY_MS,
} from '../../src/features/storyboard/storyboardSaveLifecycle.js';

const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);

function fakeClock() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  return {
    setTimer(fn, delay) {
      const id = nextId++;
      timers.set(id, { fn, delay, due: now + delay });
      return id;
    },
    clearTimer(id) { timers.delete(id); },
    advance(ms) {
      const end = now + ms;
      while (true) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.due <= end)
          .sort((left, right) => left[1].due - right[1].due)[0];
        if (!next) break;
        const [id, timer] = next;
        timers.delete(id);
        now = timer.due;
        timer.fn();
      }
      now = end;
    },
    delays() { return [...timers.values()].map((timer) => timer.delay); },
    count() { return timers.size; },
  };
}

let vite;
let createStoryboardPersistence;

before(async () => {
  vite = await createServer({
    configFile: false,
    plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'silent',
  });
  ({ createStoryboardPersistence } = await vite.ssrLoadModule(
    '/src/features/storyboard/storyboardPersistence.js',
  ));
});

after(async () => {
  await vite?.close();
});

test('1-04 schedules the latest edit at 10 seconds and an undo schedules its snapshot again', () => {
  const clock = fakeClock();
  const timerRef = { current: null };
  const saved = [];
  let latest = 'first-edit';
  const schedule = () => scheduleStoryboardAutosave(timerRef, () => saved.push(latest), {
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });

  schedule();
  clock.advance(9_999);
  assert.deepEqual(saved, []);
  clock.advance(1);
  assert.deepEqual(saved, ['first-edit']);
  assert.equal(STORYBOARD_AUTOSAVE_DELAY_MS, 10_000);

  latest = 'second-edit';
  schedule();
  latest = 'undo-snapshot';
  schedule();
  clock.advance(9_999);
  assert.deepEqual(saved, ['first-edit']);
  clock.advance(1);
  assert.deepEqual(saved, ['first-edit', 'undo-snapshot']);

  assert.match(storyboardSource, /sbSkipFirstSave\.current/);
  assert.match(storyboardSource, /directSaveSnapshots\.current\.has\(blocks\)/);
  assert.match(storyboardSource, /const undoLatest = \(\) => \{[\s\S]*?setBlocks\(entry\.before\)/);
});

test('storyboard PUTs stay serialized when a newer snapshot is queued behind a delayed request', async () => {
  const calls = [];
  let releaseFirst;
  const persistence = createStoryboardPersistence({
    invalidate: () => {},
    onlineTarget: null,
    saveStoryboard: (_projectId, snapshot) => {
      calls.push(snapshot.id);
      if (snapshot.id === 'old') return new Promise((resolve) => { releaseFirst = resolve; });
      return Promise.resolve();
    },
  });
  const oldSnapshot = { id: 'old' };
  const latestSnapshot = { id: 'latest' };

  const first = persistence.saveNow('project', () => oldSnapshot);
  await new Promise(setImmediate);
  const second = persistence.saveNow('project', () => latestSnapshot);
  await new Promise(setImmediate);
  assert.deepEqual(calls, ['old']);

  releaseFirst();
  await Promise.all([first, second]);
  assert.deepEqual(calls, ['old', 'latest']);
  persistence.dispose();
});

test('background failure retries with backoff and an online event retries immediately', async () => {
  const backoffClock = fakeClock();
  let backoffCalls = 0;
  const backoff = createStoryboardPersistence({
    invalidate: () => {},
    onlineTarget: null,
    retryDelays: [1_000, 2_000],
    setTimer: backoffClock.setTimer,
    clearTimer: backoffClock.clearTimer,
    saveStoryboard: async () => {
      backoffCalls += 1;
      if (backoffCalls === 1) throw new Error('offline');
    },
  });
  const snapshot = [{ id: 'backoff' }];
  await assert.rejects(backoff.saveNow('backoff-project', () => snapshot), /offline/);
  assert.equal(backoff.pending.get('backoff-project'), snapshot);
  assert.deepEqual(backoffClock.delays(), [1_000]);
  backoffClock.advance(999);
  await backoff.saveIdle();
  assert.equal(backoffCalls, 1);
  backoffClock.advance(1);
  await backoff.saveIdle();
  assert.equal(backoffCalls, 2);
  assert.equal(backoff.pending.has('backoff-project'), false);
  backoff.dispose();

  const onlineClock = fakeClock();
  const onlineTarget = new EventTarget();
  let onlineCalls = 0;
  const online = createStoryboardPersistence({
    invalidate: () => {},
    onlineTarget,
    retryDelays: [30_000],
    setTimer: onlineClock.setTimer,
    clearTimer: onlineClock.clearTimer,
    saveStoryboard: async () => {
      onlineCalls += 1;
      if (onlineCalls === 1) throw new Error('offline');
    },
  });
  await assert.rejects(online.saveNow('online-project', () => snapshot), /offline/);
  assert.equal(onlineClock.count(), 1);
  onlineTarget.dispatchEvent(new Event('online'));
  await online.saveIdle();
  assert.equal(onlineCalls, 2);
  assert.equal(onlineClock.count(), 0);
  online.dispose();
});

test('atomic rollback clears pending so its scheduled retry becomes a no-op', async () => {
  const clock = fakeClock();
  let calls = 0;
  const persistence = createStoryboardPersistence({
    invalidate: () => {},
    onlineTarget: null,
    retryDelays: [1_000],
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    saveStoryboard: async () => {
      calls += 1;
      throw new Error('atomic failed');
    },
  });
  const snapshot = [{ id: 'atomic' }];
  await assert.rejects(persistence.saveNow('atomic-project', () => snapshot), /atomic failed/);
  assert.equal(clock.count(), 1);

  persistence.pending.delete('atomic-project');
  clock.advance(1_000);
  await persistence.saveIdle();
  assert.equal(calls, 1);
  assert.equal(clock.count(), 0);
  persistence.dispose();
});

test('a queued retry rechecks pending at PUT time and cannot overwrite a newer snapshot', async () => {
  const clock = fakeClock();
  const calls = [];
  let releaseLatest;
  const persistence = createStoryboardPersistence({
    invalidate: () => {},
    onlineTarget: null,
    retryDelays: [1_000],
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    saveStoryboard: async (_projectId, snapshot) => {
      calls.push(snapshot.id);
      if (snapshot.id === 'old') throw new Error('old failed');
      await new Promise((resolve) => { releaseLatest = resolve; });
    },
  });
  const oldSnapshot = { id: 'old' };
  const latestSnapshot = { id: 'latest' };
  await assert.rejects(persistence.saveNow('race-project', () => oldSnapshot), /old failed/);

  const latestSave = persistence.saveNow('race-project', () => latestSnapshot);
  await new Promise(setImmediate);
  clock.advance(1_000);
  releaseLatest();
  await latestSave;
  await persistence.saveIdle();

  assert.deepEqual(calls, ['old', 'latest']);
  assert.equal(persistence.lastSaved.get('race-project'), latestSnapshot);
  persistence.dispose();
});

test('pagehide flush waits for the save chain, uses keepalive, and overlapping exit signals emit one latest PUT', async () => {
  const calls = [];
  let releaseFirst;
  const persistence = createStoryboardPersistence({
    invalidate: () => {},
    onlineTarget: null,
    saveStoryboard: (_projectId, snapshot, options) => {
      calls.push({ snapshot, options });
      if (snapshot.id === 'old') return new Promise((resolve) => { releaseFirst = resolve; });
      return Promise.resolve();
    },
  });
  const oldSnapshot = { id: 'old' };
  const latestSnapshot = { id: 'latest' };
  const first = persistence.saveNow('exit-project', () => oldSnapshot);
  await new Promise(setImmediate);

  const windowTarget = new EventTarget();
  const documentTarget = new EventTarget();
  documentTarget.hidden = false;
  const cleanup = bindStoryboardExitFlush({
    windowTarget,
    documentTarget,
    getProjectId: () => 'exit-project',
    flushLatest: (projectId, options) => persistence.saveNow(
      projectId,
      () => latestSnapshot,
      options,
    ),
  });

  windowTarget.dispatchEvent(new Event('pagehide'));
  documentTarget.hidden = true;
  documentTarget.dispatchEvent(new Event('visibilitychange'));
  cleanup();
  await new Promise(setImmediate);
  assert.deepEqual(calls.map(({ snapshot }) => snapshot.id), ['old']);

  releaseFirst();
  await first;
  await persistence.saveIdle();
  assert.deepEqual(calls.map(({ snapshot }) => snapshot.id), ['old', 'latest']);
  assert.equal(calls[1].options.keepalive, true);
  persistence.dispose();

  assert.match(storyboardSource, /const flushLatest = \(pid, options = \{\}\) => \{[\s\S]*?return sbSaveNow\(pid, \(\) => latestBlocks\.current, options\)/);
  assert.match(storyboardSource, /bindStoryboardExitFlush\(\{[\s\S]*?getProjectId: \(\) => pidRef\.current,[\s\S]*?flushLatest/);
});

test('HTTP saveStoryboard forwards keepalive to the actual fetch request and mock keeps the same options contract', async () => {
  const { httpAdapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js');
  const { supabase } = await vite.ssrLoadModule('/src/lib/supabase.js');
  const originalGetSession = supabase.auth.getSession;
  const originalFetch = globalThis.fetch;
  let request;
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test-token' } } });
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return { ok: true, status: 204 };
  };
  try {
    await httpAdapter.saveStoryboard('keepalive-project', [{ id: 'latest' }], { keepalive: true });
  } finally {
    supabase.auth.getSession = originalGetSession;
    globalThis.fetch = originalFetch;
  }

  assert.equal(new URL(request.url, 'http://test.local').pathname, '/v1/projects/keepalive-project/storyboard');
  assert.equal(request.options.keepalive, true);
  assert.deepEqual(JSON.parse(request.options.body), [{ id: 'latest' }]);
  const mockSource = readFileSync(new URL('../../src/mock/api.js', import.meta.url), 'utf8');
  assert.match(mockSource, /saveStoryboard\(_projectId, blocks, \{ autoAssignment = false, keepalive: _keepalive = false \} = \{\}\)/);
});

test('next-step flush failure blocks navigation and reports a toast message', async () => {
  const navigations = [];
  const toasts = [];
  const moved = await continueAfterStoryboardFlush({
    flush: async () => { throw new Error('세트 저장에 실패했어요'); },
    navigate: () => navigations.push('/create/mannequin'),
    onFailure: (message) => toasts.push(message),
  });

  assert.equal(moved, false);
  assert.deepEqual(navigations, []);
  assert.deepEqual(toasts, ['세트 저장에 실패했어요']);
  assert.match(storyboardSource, /continueAfterStoryboardFlush\(\{[\s\S]*?flush: \(\) => saveNow\(projectId\),[\s\S]*?onFailure: \(message\) => toast\.push\(message\)/);
  assert.doesNotMatch(storyboardSource, /\{saveError && <div className="sb-save-error">/);
  assert.match(storyboardSource, /pickerSaveError && <div className="sb-save-error">/);
  assert.match(storyboardSource, /setSetPickerError\('장소 세트를 저장하지 못했어요\. 다시 시도해주세요\.'/);
});
