import { api } from '../../lib/api/index.js';
import { persistAnalysisEdit, registerAnalysisEditSave } from '../product-input/saveRouting.js';

export function createStoryboardStyleSaveQueue(save, { projectId = null } = {}) {
  let pending = Promise.resolve();
  return {
    save(style) {
      pending = pending.catch(() => {}).then(() => save(style));
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
