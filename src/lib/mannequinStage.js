export function isMannequinStageEnabled(value = import.meta.env?.VITE_MANNEQUIN_STAGE) {
  return !['off', '0', 'false'].includes(value);
}
