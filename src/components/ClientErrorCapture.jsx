import { useEffect, useRef } from 'react';
import { reportUserVisibleError } from '../lib/clientEvents.js';

export const ERROR_CAPTURE_POINTS = {
  errorState: { kind: 'ui_error', action: 'load', messageCode: 'screen_load_failed', blocking: true },
  storyboardSave: { kind: 'save_error', action: 'save', messageCode: 'storyboard_save_failed', blocking: true },
  editorSave: { kind: 'save_error', action: 'save', messageCode: 'editor_save_failed', blocking: true },
  editorGeneration: { kind: 'generation_error', action: 'detailPageGenerate', messageCode: 'editor_generation_failed', blocking: true },
  photoRestore: { kind: 'upload_error', action: 'restore', messageCode: 'photo_restore_failed', blocking: true },
  customMatchUpload: { kind: 'upload_error', action: 'upload', messageCode: 'custom_match_upload_failed', blocking: true },
  mobileSave: { kind: 'save_error', action: 'save', messageCode: 'mobile_save_failed', blocking: true },
  handoff: { kind: 'ui_error', action: 'restore', messageCode: 'handoff_load_failed', blocking: true },
};

export function useClientErrorCapture({ point = 'errorState', active = true, error, kind, action, messageCode,
  blocking, projectId, jobId, localBackup } = {}) {
  const reported = useRef(null);
  useEffect(() => {
    if (!active) { reported.current = null; return; }
    const defaults = ERROR_CAPTURE_POINTS[point];
    const key = error || `${point}:${messageCode || defaults?.messageCode}:${jobId || ''}`;
    if (reported.current === key) return;
    reported.current = key;
    reportUserVisibleError({ ...defaults, error, ...(kind ? { kind } : {}), ...(action ? { action } : {}),
      ...(messageCode ? { messageCode } : {}), ...(blocking !== undefined ? { blocking } : {}),
      context: { projectId, jobId, ...(localBackup !== undefined ? { local_backup: localBackup } : {}) } });
  }, [point, active, error, kind, action, messageCode, blocking, projectId, jobId, localBackup]);
}

export function ClientErrorCapture(props) {
  useClientErrorCapture(props);
  return null;
}
