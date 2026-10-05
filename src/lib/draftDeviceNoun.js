export function getDraftDeviceNoun(label) {
  if (typeof label !== 'string') return null;
  if (label.startsWith('iPad') || label.startsWith('Android 태블릿')) return '태블릿';
  if (label.startsWith('iPhone') || /^Android(?: (?:Chrome|Safari|Firefox|Edge))?$/.test(label)) return '스마트폰';
  return null;
}

export function isMobileDraftWriter(meta) {
  return Boolean(getDraftDeviceNoun(meta?.deviceLabel));
}
