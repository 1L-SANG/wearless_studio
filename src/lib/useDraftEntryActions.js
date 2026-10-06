import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { draftSlot } from './draftSlot.js';
import { createDraftEntryActions } from './draftEntry.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import { useToast } from '@/components/ui.jsx';
import { notifyFlowKept } from './flowAbandon.js';

export function useDraftEntryActions(options) {
  const { session } = useAuth();
  const navigate = useNavigate();
  const { push } = useToast();
  const { key: entryKey } = useLocation();
  const generation = useAppStore((state) => state.projectGeneration);
  const projectId = useAppStore((state) => state.projectId);
  const latest = useRef(null);
  latest.current = { ...options, entryKey, navigate, push, userId: session?.user?.id || null };
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
    beginProject: async (options) => {
      const previous = { ...useAppStore.getState() };
      const started = await latest.current.beginProject(options);
      if (started) notifyFlowKept(previous, latest.current.push, latest.current.navigate);
      return started;
    },
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
