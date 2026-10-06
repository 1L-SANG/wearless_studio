import { useRef, useState } from 'react';
import { draftSlot } from './draftSlot.js';
import { createDraftEntryActions } from './draftEntry.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';

export function useDraftEntryActions(options) {
  const { session } = useAuth();
  const latest = useRef(null);
  latest.current = { ...options, userId: session?.user?.id || null };
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [actions] = useState(() => createDraftEntryActions({
    slotClient: draftSlot,
    getUserId: () => latest.current.userId,
    mobile: () => Boolean(latest.current.mobile),
    beginProject: (options) => latest.current.beginProject(options),
    onOpened: (...args) => latest.current.onOpened(...args),
    onKept: (meta) => latest.current.onKept?.(meta),
    onStarted: () => latest.current.onStarted(),
    onError: (...args) => latest.current.onError(...args),
    onChange: setDialog,
    onBusy: setBusy,
  }));
  return { actions, dialog, busy };
}
