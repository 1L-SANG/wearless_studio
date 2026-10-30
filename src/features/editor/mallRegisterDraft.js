import { defaultInfoFor, rebuildInfoBlock } from './presets/infoPresets.js';

export const MALL_LABELS = { cafe24: '카페24', zigzag: '지그재그', ably: '에이블리' };
const clean = (text) => String(text ?? '').replace(/\s+/g, ' ').trim();
const integer = (value, minimum = 0) => typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= 2147483647;
const numeric = (value) => typeof value === 'string' && /^\d+$/.test(value.trim()) ? Number(value) : value;
export const registrationActive = (view) => ['pending', 'running', 'queued'].includes(view?.status);

export function buildMallRegisterDraft({ projectId, product = {}, blocks = [], assets = [], defaults = {}, selectedMall }) {
  const sizes = blocks.find((block) => block.kind === 'size' || block.infoType === 'size_table')?.info?.rows?.map((row) => clean(row.label)).filter(Boolean) || ['FREE'];
  const images = [...new Set(assets.map((asset) => asset.assetId).filter(Boolean))];
  return { projectId, publicationId: null, editorRevision: null, selectedMalls: selectedMall ? [selectedMall] : [],
    productName: clean(product.name || product.productName), basePrice: '', defaultQuantity: 0,
    priceOverrides: {}, sizes: sizes.length ? sizes : ['FREE'], variantOverrides: [], mainAssetId: images[0] || null,
    additionalAssetIds: images.slice(1, 5), categories: {}, confirmedSourcesByMall: {}, salesOverrides: {} };
}

export function normalizeMallRegisterDraft(draft) {
  return { ...draft, productName: clean(draft.productName), basePrice: numeric(draft.basePrice),
    defaultQuantity: draft.defaultQuantity === '' ? 0 : numeric(draft.defaultQuantity), sizes: draft.sizes.map(clean),
    priceOverrides: Object.fromEntries(Object.entries(draft.priceOverrides || {}).map(([mall, value]) => [mall, { ...value, amount: numeric(value.amount) }])),
    variantOverrides: (draft.variantOverrides || []).map((value) => ({ ...value, quantity: value.quantity === '' ? 0 : numeric(value.quantity), additionalAmount: value.additionalAmount === '' ? 0 : numeric(value.additionalAmount) })),
    additionalAssetIds: [...new Set(draft.additionalAssetIds || [])].filter((id) => id !== draft.mainAssetId).slice(0, 4) };
}

export function validateMallRegisterDraft(input) {
  const draft = normalizeMallRegisterDraft(input), issues = [];
  const add = (path, message, action) => issues.push({ path, message, action });
  if (!draft.productName || draft.productName.length > 100 || /[\u0000-\u001f\u007f]/.test(input.productName)) add('productName', '상품명을 1자에서 100자로 확인해 주세요');
  if (!integer(draft.basePrice, 1)) add('basePrice', '판매가를 1원 이상 정수로 입력해 주세요', 'edit_price');
  if (!integer(draft.defaultQuantity)) add('defaultQuantity', '재고를 0 이상 정수로 입력해 주세요', 'edit_options');
  if (!draft.mainAssetId) add('mainAssetId', '대표 이미지를 골라 주세요', 'choose_image');
  if (!draft.selectedMalls.length) add('selectedMalls', '등록할 몰을 골라 주세요');
  if (!draft.sizes.length || draft.sizes.some((size) => !size) || new Set(draft.sizes).size !== draft.sizes.length) add('sizes', '중복이나 빈칸 없이 사이즈를 입력해 주세요', 'edit_options');
  for (const [mall, price] of Object.entries(draft.priceOverrides)) {
    if (!integer(price.amount, 1) || !['selling_price', 'tax_excluded'].includes(price.basis)) add(`priceOverrides.${mall}`, '몰별 판매가와 가격 기준을 확인해 주세요', 'edit_price');
  }
  for (const [index, variant] of draft.variantOverrides.entries()) {
    if (!integer(variant.quantity) || !integer(variant.additionalAmount) || !integer(draft.basePrice + variant.additionalAmount)
      || Object.values(draft.priceOverrides).some((price) => !integer(price.amount + variant.additionalAmount))) add(`variantOverrides.${index}`, '옵션 재고와 추가금액을 확인해 주세요', 'edit_options');
  }
  return issues;
}

export function registrationSelection(selectedMalls, preflight) {
  const selected = selectedMalls.filter((mall) => preflight?.items?.find((item) => item.mall === mall)?.status !== 'preparing');
  const readyMalls = selected.filter((mall) => preflight?.items?.some((item) => item.mall === mall && item.status === 'ready'));
  return { selectedMalls: selected, readyMalls, label: readyMalls.length > 1 ? `${readyMalls.length}곳에 등록`
    : readyMalls.length === 1 ? `${MALL_LABELS[readyMalls[0]]}에만 등록` : '확인 후 등록' };
}

export function registrationVariants(colors, sizes) {
  return colors.flatMap((color) => sizes.map((size) => ({ variantKey: JSON.stringify([color.id, clean(size)]), color: color.name || color.label, size: clean(size) })));
}

export function patchRegistrationSizes(blocks, sizes, context = {}) {
  const labels = sizes.map(clean);
  if (!labels.length || labels.some((size) => !size) || new Set(labels).size !== labels.length) throw new Error('중복이나 빈칸 없이 사이즈를 입력해 주세요');
  let found = false;
  const patch = (block) => {
    const info = block?.info || defaultInfoFor('size_table', context);
    const rows = info.rows || [];
    const nextRows = labels.map((label) => {
      const existing = rows.find((row) => clean(row.label) === label);
      return { ...existing, label, values: { ...existing?.values } };
    });
    return rebuildInfoBlock(block, 'size_table', { ...info, rows: nextRows }, context);
  };
  const next = blocks.map((block) => {
    if (block.kind !== 'size' && block.infoType !== 'size_table') return block;
    found = true; return patch(block);
  });
  return found ? next : [...next, patch(null)];
}

export async function prepareMallRegisterDraft(draft, { waitForSave, onPublish, preflight, assertCurrent }) {
  assertCurrent();
  const revision = await waitForSave(); assertCurrent();
  if (!Number.isInteger(revision)) throw new Error('저장한 편집본을 확인하지 못했어요. 다시 열어 주세요');
  const publication = await onPublish(assertCurrent, revision); assertCurrent();
  if (publication.editorRevision !== revision) throw new Error('편집본이 바뀌었어요. 다시 저장해 주세요');
  const next = { ...normalizeMallRegisterDraft(draft), editorRevision: revision, publicationId: publication.publicationId, confirmedSourcesByMall: {} };
  const checked = await preflight(next); assertCurrent();
  for (const item of checked.items) if (item.status === 'ready') next.confirmedSourcesByMall[item.mall] = item.sourceVersions;
  return { draft: next, preflight: checked, publication };
}

export function trapRegistrationFocus(event) {
  if (event.key !== 'Tab') return;
  const root = event.currentTarget;
  const elements = [...root.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')]
    .filter((node) => !node.closest?.('details:not([open])') || node.tagName === 'SUMMARY');
  const first = elements[0], last = elements.at(-1), active = root.ownerDocument.activeElement;
  if (!first) { event.preventDefault(); root.focus?.(); return; }
  if (event.shiftKey && (active === first || !elements.includes(active))) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && (active === last || !elements.includes(active))) { event.preventDefault(); first.focus(); }
}

export function openRegistrationIssue(root, issue) {
  const path = issue.path || '';
  const candidates = [...(root?.querySelectorAll?.('[data-field]') || [])];
  const field = candidates.find((node) => node.dataset?.field === path) || candidates.find((node) => node.dataset?.field?.startsWith(`${path}.`))
    || root?.querySelector?.(`[data-field="${path.replace(/[^a-zA-Z0-9_.]/g, '')}"]`);
  if (!field) return;
  for (let node = field.parentElement; node; node = node.parentElement) if (node.tagName === 'DETAILS') node.open = true;
  field.focus(); field.scrollIntoView?.({ block: 'nearest' });
}

// A new preflight version is not consent to settings the seller has not seen.
export function preflightForDisplayedSources(result, displayed) {
  const items = result.items.map((item) => {
    if (item.status !== 'ready') return item;
    const versions = item.sourceVersions || {};
    const expected = {
      connectionVersion: displayed.malls?.items?.find((connection) => connection.mall === item.mall)?.connectionVersion,
      mallDefaultsRevision: displayed.mallDefaults?.[item.mall]?.revision,
      catalogVersion: displayed.catalogs?.[item.mall]?.catalogVersion,
    };
    const changed = expected.mallDefaultsRevision == null ? 'mallDefaultsRevision'
      : Object.keys(expected).find((key) => expected[key] != null && expected[key] !== versions[key]);
    if (!changed) return item;
    const code = changed === 'connectionVersion' ? 'connection_changed' : changed === 'catalogVersion' ? 'catalog_changed' : 'defaults_changed';
    return { ...item, status: 'blocked', sourceVersions: undefined, issues: [{ code, path: `confirmedSourcesByMall.${item.mall}`,
      message: '확인한 정보가 바뀌었어요. 기본 정보를 다시 확인해 주세요', action: 'open_defaults' }] };
  });
  return { ...result, items, readyMalls: items.filter((item) => item.status === 'ready').map((item) => item.mall),
    blockedMalls: items.filter((item) => item.status !== 'ready').map((item) => item.mall) };
}
