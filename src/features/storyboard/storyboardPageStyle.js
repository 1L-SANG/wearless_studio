import { api } from '../../lib/api/index.js';
import { persistAnalysisEdit, registerAnalysisEditSave } from '../product-input/saveRouting.js';

export function createStoryboardStyleSaveQueue(save, { projectId = null } = {}) {
  let pending = Promise.resolve();
  let queued = false, confirmed = null;
  return {
    seed(style) { if (!queued) confirmed = style; },
    confirmed() { return confirmed; },
    save(style) {
      queued = true;
      pending = pending.catch(() => {}).then(() => save(style)).then(result => {
        // Earlier successes still matter if a later queued choice fails. Never
        // advance this snapshot on rejection or optimistic UI updates.
        confirmed = result?.analysis?.pageStyle || style;
        return result;
      });
      // Register at enqueue time: a route reopening while the first request is
      // in flight must also wait for any later choice already in this chain.
      registerAnalysisEditSave(projectId, pending);
      return pending;
    },
    wait() { return pending; },
  };
}

export function saveStoryboardPageStyle(projectId, style) {
  return persistAnalysisEdit(api, projectId, { pageStyle: style });
}
