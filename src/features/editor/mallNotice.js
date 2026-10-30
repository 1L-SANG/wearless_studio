import { NOTICE_FIELDS, NEEDS_INPUT, defaultInfoFor, rebuildInfoBlock } from './presets/infoPresets.js';
import { MEASUREMENT_LABELS } from '../../lib/measurementSchema.js';

export const noticeValue = (value) => {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  return text === NEEDS_INPUT ? '' : text;
};

export function resolveMallNotice({ blocks = [], product = {}, defaults = {} }) {
  const notices = blocks.filter((block) => block.infoType === 'required_notice');
  const disclosure = defaults.disclosure || {};
  const context = { ...product.analysis, ...product, productName: product.name || product.productName,
    colorLabels: (product.colors || []).map((color) => color.name || color.label) };
  const suggested = defaultInfoFor('required_notice', context).fields;
  const common = { maker: disclosure.manufacturer, origin: disclosure.country,
    contact: disclosure.asManager && disclosure.phone ? `${disclosure.asManager} ${disclosure.phone}` : '',
    warranty: disclosure.warrantyText };
  const measurements = blocks.filter((block) => block.kind === 'size').flatMap((block) => (block.info?.rows || []).flatMap((row) => {
    const values = Object.entries(row.values || {}).filter(([, value]) => value != null && noticeValue(value)).map(([key, value]) => `${MEASUREMENT_LABELS[key] || key} ${value}`).join(', ');
    return values ? [`${row.label || ''}: ${values} ${block.info.unit || 'cm'}`] : [];
  })).join('; ');
  const care = blocks.find((block) => block.kind === 'care' && noticeValue(block.info?.text))?.info.text;
  const conflicts = [];
  const fields = NOTICE_FIELDS.map((field) => {
    const entries = notices.map((block) => ({ blockId: block.id, value: noticeValue(block.info?.fields?.find((f) => f.key === field.key)?.value) }));
    const values = [...new Set(entries.map((entry) => entry.value).filter(Boolean))];
    const conflict = values.length > 1;
    if (conflict) conflicts.push({ key: field.key, entries });
    const stored = entries.find((entry) => entry.value)?.value;
    const existing = field.key === 'sizes' && stored?.toUpperCase() === 'FREE' ? '' : stored;
    const fallback = noticeValue(common[field.key] || (field.key === 'sizes' ? measurements : field.key === 'care' ? care : '') || suggested.find((f) => f.key === field.key)?.value);
    return { ...field, value: conflict ? '' : existing || fallback, source: existing ? '에디터' : noticeValue(common[field.key]) ? '기본 정보' : fallback ? field.key === 'materials' ? '분석' : field.key === 'care' ? '소재 규칙' : '상품 정보' : '빈칸', conflict };
  });
  return { fields, conflicts, blocks: notices.map((block) => ({ id: block.id, name: block.name, fields: block.info?.fields || [] })),
    missingCount: fields.filter((field) => !field.value).length,
    nameMismatch: Boolean(fields.find((field) => field.key === 'name')?.value && context.productName
      && fields.find((field) => field.key === 'name').value !== context.productName) };
}

export function patchNoticeBlocks(blocks, { fields }, context = {}) {
  const info = { fields: NOTICE_FIELDS.map((field) => ({ ...field, value: noticeValue(fields.find((f) => f.key === field.key)?.value) })) };
  let found = false;
  const next = blocks.map((block) => {
    if (block.infoType !== 'required_notice') return block;
    found = true;
    return rebuildInfoBlock(block, 'required_notice', info, context);
  });
  return found ? next : [...next, rebuildInfoBlock(null, 'required_notice', info, context)];
}
