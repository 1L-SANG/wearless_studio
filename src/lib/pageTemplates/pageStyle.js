import { getTemplate, pageTemplatesCatalog } from './catalog.js';

export const defaultPageStyle = () => ({
  version: 1, ...pageTemplatesCatalog.defaults,
  catalogVersion: pageTemplatesCatalog.catalogVersion,
});

export function validPageStyle(value) {
  const allowed = ['version', 'templateId', 'fontStyle', 'colorMode', 'catalogVersion', 'updatedAt'];
  return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).every(key => allowed.includes(key))
    && value.version === 1 && !!getTemplate(value.templateId)
    && typeof value.fontStyle === 'string' && Object.hasOwn(pageTemplatesCatalog.fontStyles, value.fontStyle)
    && ['mono', 'point'].includes(value.colorMode)
    && value.catalogVersion === pageTemplatesCatalog.catalogVersion
    && (!Object.hasOwn(value, 'updatedAt') || typeof value.updatedAt === 'string'
      && Number.isFinite(Date.parse(value.updatedAt)) && /(?:Z|[+-]\d\d:\d\d)$/.test(value.updatedAt));
}

export function analysisPageStyle(value, storyboard = []) {
  // Read-only fallback for pre-style projects. Existing v4 runs determine their
  // template; a fresh/legacy board uses the catalog default, never another project.
  if (value == null && storyboard?.length && storyboard.every(block => block.taxonomyVersion === 4)) {
    const templateId = storyboard[0].pageSectionKey?.split('.')[0];
    if (getTemplate(templateId)) return { ...defaultPageStyle(), templateId };
  }
  if (validPageStyle(value)) return value;
  // A catalog option can retire without changing this project's template/tone.
  // Keep the usual validation for every other field and do not mutate storage.
  if (value && typeof value.fontStyle === 'string' && !Object.hasOwn(pageTemplatesCatalog.fontStyles, value.fontStyle)) {
    const fallback = { ...value, fontStyle: pageTemplatesCatalog.defaults.fontStyle };
    if (validPageStyle(fallback)) return fallback;
  }
  return defaultPageStyle();
}

export const templateSelectionStyle = value => ({ ...analysisPageStyle(value), fontStyle: pageTemplatesCatalog.defaults.fontStyle });

export function selectTemplateStyle(templateId, now = new Date().toISOString(), currentStyle = null) {
  if (!getTemplate(templateId)) throw new Error('invalid_page_style');
  return { ...templateSelectionStyle(currentStyle), templateId, updatedAt: now };
}

export function selectColorModeStyle(currentStyle, colorMode, now = new Date().toISOString()) {
  if (!['mono', 'point'].includes(colorMode)) throw new Error('invalid_page_style');
  return { ...analysisPageStyle(currentStyle), colorMode, updatedAt: now };
}

export function selectFontStyle(currentStyle, fontStyle, now = new Date().toISOString()) {
  if (typeof fontStyle !== 'string' || !Object.hasOwn(pageTemplatesCatalog.fontStyles, fontStyle)) throw new Error('invalid_page_style');
  return { ...analysisPageStyle(currentStyle), fontStyle, updatedAt: now };
}
