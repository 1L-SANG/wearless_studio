import { useEffect, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { draftSlot } from '@/lib/draftSlot.js';
import { isMobileDevice } from '@/lib/deviceClass.js';
import { baseProductPhoto, currentDraftVisuals, startVisualPolling } from '@/lib/draftVisuals.js';

export function useDraftVisuals({ projectId, product, customId }) {
  const [revision, setRevision] = useState(0);
  const [snapshot, setSnapshot] = useState(null);
  const userId = draftSlot.getUserId();
  const enabled = draftSlot.isVisualsEnabled();
  const photo = baseProductPhoto(product);
  const key = JSON.stringify([userId, enabled, photo?.id, photo?.src, customId, projectId]);
  useEffect(() => draftSlot.onVisuals(() => setRevision((value) => value + 1)), []);
  useEffect(() => {
    if (!enabled || projectId || !userId || isMobileDevice() || !draftSlot.getToken()) return undefined;
    const bindings = draftSlot.getVisualSources(product, customId);
    return startVisualPolling({ query: () => api.getDraftVisuals(draftSlot.getToken(), { expectedUserId: userId }),
      onResult: (result) => {
        draftSlot.recoverVisualHandoff(result, product, customId);
        setSnapshot({ key, result: currentDraftVisuals(result, bindings) });
      },
      onStop: () => setSnapshot(null) });
  }, [key, revision]);
  if (!enabled || projectId || !userId || isMobileDevice() || snapshot?.key !== key) return { product: null, custom: null };
  return snapshot.result;
}
