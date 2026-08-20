export function createLatestEditorSaveGuard() {
  let latestRequestId = 0;

  return {
    begin(source) {
      latestRequestId += 1;
      return { id: latestRequestId, source };
    },
    invalidate() {
      latestRequestId += 1;
    },
    isNewest(ticket) {
      return ticket?.id === latestRequestId;
    },
    isCurrent(ticket, currentSource) {
      return ticket?.id === latestRequestId && ticket.source === currentSource;
    },
  };
}

export function bindEditorExitBackup({
  backupLatest,
  windowTarget = globalThis.window,
  documentTarget = globalThis.document,
}) {
  const backup = () => backupLatest();
  const backupWhenHidden = () => {
    if (documentTarget?.hidden === true) backup();
  };

  windowTarget?.addEventListener?.('pagehide', backup);
  documentTarget?.addEventListener?.('visibilitychange', backupWhenHidden);
  return () => {
    windowTarget?.removeEventListener?.('pagehide', backup);
    documentTarget?.removeEventListener?.('visibilitychange', backupWhenHidden);
  };
}
