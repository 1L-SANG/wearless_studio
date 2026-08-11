export function createDraftSyncSingleFlight(runSync) {
  let inFlight = null;
  let inFlightRevision = null;
  let result = null;
  let resultRevision = null;
  let projectId = null;

  const revisionOf = (draft) => draft?.updatedAt || null;

  const start = (draft, options, revision) => {
    // A completed result is reusable only for the exact draft revision it saved.  After the
    // login-return timeout the user can edit the restored draft while the old promotion keeps
    // running; treating that old success as universal would silently drop those newer edits.
    result = null;
    resultRevision = null;
    inFlightRevision = revision;

    const flight = Promise.resolve()
      .then(() => runSync(draft, {
        ...options,
        projectId: options.projectId ?? projectId ?? undefined,
      }))
      .then((value) => {
        projectId = value.projectId;
        result = value;
        resultRevision = revision;
        return value;
      }, (error) => {
        if (error?.projectId) projectId = error.projectId;
        throw error;
      })
      .finally(() => {
        if (inFlight === flight) {
          inFlight = null;
          inFlightRevision = null;
        }
      });

    inFlight = flight;
    return flight;
  };

  const sync = (draft, options = {}) => {
    const revision = revisionOf(draft);
    if (result && revision === resultRevision) return Promise.resolve(result);
    if (inFlight) {
      if (revision === inFlightRevision) return inFlight;

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
