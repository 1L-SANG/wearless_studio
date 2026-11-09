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
    && ['template', 'serif', 'boldGothic'].includes(value.fontStyle)
    && ['mono', 'point'].includes(value.colorMode)
    && value.catalogVersion === pageTemplatesCatalog.catalogVersion
    && (!Object.hasOwn(value, 'updatedAt') || typeof value.updatedAt === 'string'
      && Number.isFinite(Date.parse(value.updatedAt)) && /(?:Z|[+-]\d\d:\d\d)$/.test(value.updatedAt));
}

export function analysisPageStyle(value) {
  return validPageStyle(value) ? value : defaultPageStyle();
}

export const templateSelectionStyle = value => ({ ...analysisPageStyle(value), fontStyle: 'template' });

export function selectTemplateStyle(templateId, now = new Date().toISOString(), currentStyle = null) {
  if (!getTemplate(templateId)) throw new Error('invalid_page_style');
  return { ...templateSelectionStyle(currentStyle), templateId, updatedAt: now };
}

export function selectColorModeStyle(currentStyle, colorMode, now = new Date().toISOString()) {
  if (!['mono', 'point'].includes(colorMode)) throw new Error('invalid_page_style');
  return { ...templateSelectionStyle(currentStyle), colorMode, updatedAt: now };
}
