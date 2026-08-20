export function createDraftSyncSingleFlight(runSync) {
  let inFlight = null;
  let inFlightRevision = null;
  let result = null;
  let resultRevision = null;
  let projectId = null;
  let flightCallbacks = null;

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

  const notifyProjectReady = (nextProjectId, callbacks) => {
    if (!nextProjectId) return;
    projectId = nextProjectId;
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
        onProjectReady: (id) => notifyProjectReady(id, callbacks),
        onPhotoProgress: (progress) => notifyPhotoProgress(progress, callbacks),
      }))
      .then((value) => {
        notifyProjectReady(value.projectId, callbacks);
        result = value;
        resultRevision = revision;
        return value;
      }, (error) => {
        if (error?.projectId) notifyProjectReady(error.projectId, callbacks);
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
    retryFrom(existingProjectId) {
      if (inFlight) return false;
      result = null;
      resultRevision = null;
      projectId = existingProjectId || projectId;
      return true;
    },
  };
}
