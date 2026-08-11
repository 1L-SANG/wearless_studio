export function createDraftSyncSingleFlight(runSync) {
  let inFlight = null;
  let result = null;
  let projectId = null;

  return {
    sync(draft, options = {}) {
      if (result) return Promise.resolve(result);
      if (inFlight) return inFlight;

      inFlight = Promise.resolve()
        .then(() => runSync(draft, {
          ...options,
          projectId: options.projectId ?? projectId ?? undefined,
        }))
        .then((value) => {
          projectId = value.projectId;
          result = value;
          return value;
        }, (error) => {
          if (error?.projectId) projectId = error.projectId;
          throw error;
        })
        .finally(() => { inFlight = null; });

      return inFlight;
    },
    reset() {
      if (inFlight) return false;
      result = null;
      projectId = null;
      return true;
    },
    retryFrom(existingProjectId) {
      if (inFlight) return false;
      result = null;
      projectId = existingProjectId || projectId;
      return true;
    },
  };
}
