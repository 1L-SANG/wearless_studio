// 충돌 작업본은 탭별로 보관한다. 저장소를 못 쓰는 환경에서도 이 화면의 복구는 유지한다.
export function createConflictWorkCopies(kind, storage) {
  const copies = new Map();
  const target = () => {
    try { return storage || globalThis.sessionStorage; } catch { return null; }
  };
  const key = (id) => `wl_${kind}_conflict_${id}`;
  return {
    keep(id, copy) {
      copies.set(id, copy);
      try { target()?.setItem(key(id), JSON.stringify(copy)); } catch { /* 메모리본 유지 */ }
      return copy;
    },
    read(id) {
      if (copies.has(id)) return copies.get(id);
      try {
        const copy = JSON.parse(target()?.getItem(key(id)) || 'null');
        if (Array.isArray(copy?.blocks)) { copies.set(id, copy); return copy; }
      } catch { /* 손상된 보관본 무시 */ }
      return null;
    },
    clear(id, copy) {
      if (copy && this.read(id) !== copy) return;
      copies.delete(id);
      try { target()?.removeItem(key(id)); } catch { /* 무시 */ }
    },
  };
}

export function restoreWorkCopyOnce(restore) {
  let restored = false;
  return (...args) => {
    if (restored) return;
    restored = true;
    return restore(...args);
  };
}

export const storyboardWorkCopies = createConflictWorkCopies('storyboard');
export const editorWorkCopies = createConflictWorkCopies('editor');
