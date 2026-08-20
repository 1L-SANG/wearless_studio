export function createDraftSyncSingleFlight(runSync) {
  let inFlight = null;
  let inFlightRevision = null;
  let result = null;
  let resultRevision = null;
  let projectId = null;
  let flightCallbacks = null;
  // 기억한 project 신원의 세대. '새 제작'이 올리면 이전 세대의 flight 는 계속 돌되(업로드를
  // 끊으면 그 사진이 유실된다) 그 완료가 신원을 다시 등록하지 못한다 — 다음 상품이 앞
  // 프로젝트에 덮어쓰던 사고를 막는다.
  let identityEpoch = 0;

  const revisionOf = (draft) => draft?.updatedAt || null;

  const registerCallbacks = (options, callbacks = flightCallbacks) => {
    if (!callbacks) return null;
    const entry = {
      onProjectReady: options?.onProjectReady,
      onPhotoProgress: options?.onPhotoProgress,
      projectReadyNotified: false,
    };
    callbacks.add(entry);
    if (projectId && entry.onProjectReady) {
      entry.projectReadyNotified = true;
      try { entry.onProjectReady(projectId); } catch { /* 호출자 오류가 승격을 막지 않는다 */ }
    }
    return entry;
  };

  const notifyProjectReady = (nextProjectId, callbacks, epoch = identityEpoch) => {
    if (!nextProjectId) return;
    // 신원 승계는 같은 세대에서만. 구독자 통지는 세대와 무관하다 — 그 flight 를 기다리던
    // 화면은 '새 제작'과 상관없이 자기 프로젝트를 계속 봐야 한다.
    if (epoch === identityEpoch) projectId = nextProjectId;
    for (const entry of callbacks) {
      if (entry.projectReadyNotified || !entry.onProjectReady) continue;
      entry.projectReadyNotified = true;
      try { entry.onProjectReady(nextProjectId); } catch { /* 다른 합류자까지 계속 알린다 */ }
    }
  };

  const notifyPhotoProgress = (progress, callbacks) => {
    for (const entry of callbacks) {
      try { entry.onPhotoProgress?.(progress); } catch { /* 다른 합류자까지 계속 알린다 */ }
    }
  };

  const start = (draft, options, revision) => {
    // A completed result is reusable only for the exact draft revision it saved.  After the
    // login-return timeout the user can edit the restored draft while the old promotion keeps
    // running; treating that old success as universal would silently drop those newer edits.
    result = null;
    resultRevision = null;
    inFlightRevision = revision;
    const epoch = identityEpoch;
    const callbacks = new Set();
    flightCallbacks = callbacks;
    registerCallbacks(options, callbacks);
    const {
      onProjectReady: _onProjectReady,
      onPhotoProgress: _onPhotoProgress,
      ...runOptions
    } = options;

    const flight = Promise.resolve()
      .then(() => runSync(draft, {
        ...runOptions,
        projectId: options.projectId ?? projectId ?? undefined,
        onProjectReady: (id) => notifyProjectReady(id, callbacks, epoch),
        onPhotoProgress: (progress) => notifyPhotoProgress(progress, callbacks),
      }))
      .then((value) => {
        notifyProjectReady(value.projectId, callbacks, epoch);
        // 세대가 끊긴 flight 의 결과는 캐시하지 않는다 — 다음 상품이 재사용하면 안 된다.
        if (epoch === identityEpoch) {
          result = value;
          resultRevision = revision;
        }
        return value;
      }, (error) => {
        if (error?.projectId) notifyProjectReady(error.projectId, callbacks, epoch);
        throw error;
      })
      .finally(() => {
        if (inFlight === flight) {
          inFlight = null;
          inFlightRevision = null;
          flightCallbacks = null;
        }
      });

    inFlight = flight;
    return flight;
  };

  const sync = (draft, options = {}) => {
    const revision = revisionOf(draft);
    if (result && revision === resultRevision) {
      if (projectId) {
        try { options.onProjectReady?.(projectId); } catch { /* 결과 재사용은 유지 */ }
      }
      return Promise.resolve(result);
    }
    if (inFlight) {
      if (revision === inFlightRevision) {
        registerCallbacks(options);
        return inFlight;
      }

      // The older request still owns project creation.  Let it settle, then persist this newer
      // revision into the same remembered project instead of joining and returning stale data.
      const olderFlight = inFlight;
      return olderFlight.catch(() => null).then(() => sync(draft, options));
    }
    return start(draft, options, revision);
  };

  return {
    sync,
    reset() {
      if (inFlight) return false;
      result = null;
      resultRevision = null;
      projectId = null;
      return true;
    },
    /** '새 제작' 진입 — 도는 업로드는 그대로 두고 project 신원만 끊는다. reset 과 달리
        in-flight 여도 반드시 끊어야 한다: 다음 상품이 앞 프로젝트에 덮어쓰는 걸 막는 게
        목적이고, 그 상황이 바로 앞 승격이 아직 도는 중인 경우다. */
    forgetProject() {
      identityEpoch += 1;
      result = null;
      resultRevision = null;
      projectId = null;
    },
    retryFrom(existingProjectId) {
      if (inFlight) return false;
      result = null;
      resultRevision = null;
      projectId = existingProjectId || projectId;
      return true;
    },
  };
}
