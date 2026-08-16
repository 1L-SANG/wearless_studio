/* 편집 봉인 표식 — "에디터에 들어온 프로젝트는 앞 단계로 못 돌아간다"(오너 2026-08-15)의
   근거가 되는 저장소 계약. 지금까지 소스 정규식 두 줄로만 지켜지고 있었다(2026-08-16 리뷰). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { clearEditorEntered, hasEditorEntered, markEditorEntered } from '../../src/lib/editorEntered.js';

const withStorage = (impl, run) => {
  const had = 'localStorage' in globalThis;
  const prev = had ? globalThis.localStorage : undefined;
  globalThis.localStorage = impl;
  try { run(); } finally {
    if (had) globalThis.localStorage = prev;
    else delete globalThis.localStorage;
  }
};

const memoryStorage = () => {
  const map = new Map();
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
};

test('표식은 프로젝트별로 남고, 새로고침(같은 저장소)에서도 읽힌다', () => {
  const store = memoryStorage();
  withStorage(store, () => {
    assert.equal(hasEditorEntered('p1'), false);
    markEditorEntered('p1');
    assert.equal(hasEditorEntered('p1'), true);
    assert.equal(hasEditorEntered('p2'), false, '다른 프로젝트까지 막으면 새 제작이 불가능해진다');
    clearEditorEntered('p1');
    assert.equal(hasEditorEntered('p1'), false);
  });
});

test('projectId 가 없으면 아무것도 하지 않는다 — 빈 키로 전 프로젝트를 막지 않게', () => {
  const store = memoryStorage();
  withStorage(store, () => {
    markEditorEntered(undefined);
    markEditorEntered('');
    assert.equal(store.map.size, 0);
    assert.equal(hasEditorEntered(undefined), false);
    assert.equal(hasEditorEntered(''), false);
  });
});

test('저장소가 막힌 브라우저(사생활 모드)에서도 앱은 산다 — 가드만 꺼진다', () => {
  const blocked = {
    getItem() { throw new Error('SecurityError'); },
    setItem() { throw new Error('SecurityError'); },
    removeItem() { throw new Error('SecurityError'); },
  };
  withStorage(blocked, () => {
    assert.doesNotThrow(() => markEditorEntered('p1'));
    assert.doesNotThrow(() => clearEditorEntered('p1'));
    assert.equal(hasEditorEntered('p1'), false, '읽기 실패는 "표식 없음"으로 — 갇히지 않는다');
  });
});

test('표식 값이 손상돼 있으면 봉인하지 않는다 — 잘못 갇히는 쪽보다 열리는 쪽이 안전하다', () => {
  const store = memoryStorage();
  store.setItem('ed-entered-p1', 'yes');
  withStorage(store, () => {
    assert.equal(hasEditorEntered('p1'), false);
  });
});


test('새 제작을 시작해도 이전 프로젝트의 편집 표식은 남는다 — 보호장치가 풀리면 안 된다', () => {
  const store = readFileSync(new URL('../../src/store/useAppStore.js', import.meta.url), 'utf8');
  const begin = store.slice(store.indexOf('async beginProject()'), store.indexOf('async createProject('));
  // 지우면: 그 프로젝트를 나중에 보관함에서 다시 열었을 때 앞 단계 복귀가 열려
  // 편집분이 다음 생성으로 덮인다(2026-08-17 리뷰).
  assert.doesNotMatch(begin, /clearEditorEntered/);
});
