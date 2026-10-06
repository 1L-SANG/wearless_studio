import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { draftSlot } from './draftSlot.js';
import { createDraftEntryActions } from './draftEntry.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { useAppStore } from '@/store/useAppStore.js';

export function useDraftEntryActions(options) {
  const { session } = useAuth();
  const { key: entryKey } = useLocation();
  const generation = useAppStore((state) => state.projectGeneration);
  const projectId = useAppStore((state) => state.projectId);
  const latest = useRef(null);
  latest.current = { ...options, entryKey, userId: session?.user?.id || null };
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [actions] = useState(() => createDraftEntryActions({
    slotClient: draftSlot,
    getUserId: () => latest.current.userId,
    getEntryKey: () => latest.current.entryKey,
    getProjectState: () => {
      const { projectGeneration, projectId } = useAppStore.getState();
      return { projectGeneration, projectId };
    },
    mobile: () => Boolean(latest.current.mobile),
    beginProject: (options) => latest.current.beginProject(options),
    onOpened: (...args) => latest.current.onOpened(...args),
    onKept: (meta) => latest.current.onKept?.(meta),
    onStarted: () => latest.current.onStarted(),
    onError: (...args) => latest.current.onError(...args),
    onChange: setDialog,
    onBusy: setBusy,
  }));
  useEffect(() => () => actions.cancel(), [actions]);
  useEffect(() => { actions.cancelIfStale(); }, [actions, generation, projectId, entryKey, session?.user?.id]);
  return { actions, dialog, busy };
}
