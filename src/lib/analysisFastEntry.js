export function isAnalysisFastEntryEnabled(value = import.meta.env?.VITE_ANALYSIS_FAST_ENTRY) {
  return value === '1' || value === 'true';
}
