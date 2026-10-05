import { useRef, useState } from 'react';
import { draftSlot } from './draftSlot.js';
import { createDraftEntryActions } from './draftEntry.js';

export function useDraftEntryActions(options) {
  const latest = useRef(options);
  latest.current = options;
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [actions] = useState(() => createDraftEntryActions({
    slotClient: draftSlot,
    beginProject: () => latest.current.beginProject(),
    onOpened: (...args) => latest.current.onOpened(...args),
    onKept: (meta) => latest.current.onKept?.(meta),
    onStarted: () => latest.current.onStarted(),
    onError: (...args) => latest.current.onError(...args),
    onChange: setDialog,
    onBusy: setBusy,
  }));
  return { actions, dialog, busy };
}
