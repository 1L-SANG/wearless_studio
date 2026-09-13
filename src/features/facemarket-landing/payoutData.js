import { SETTLEMENT_DAY } from './facemarketTerms.js';

const SEOUL_TIME_ZONE = 'Asia/Seoul';
const DATE_PARTS = new Intl.DateTimeFormat('en-CA', {
  timeZone: SEOUL_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

const OPTIONAL_FIELDS = {
  shop: ['sellerName', 'shopName', 'mallName', 'storeName', 'merchantName'],
  item: ['itemName', 'productName', 'productTitle', 'item'],
  thumbnail: ['thumbnailUrl', 'productThumbnailUrl', 'itemThumbnailUrl'],
};

function seoulParts(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = Object.fromEntries(
    DATE_PARTS.formatToParts(date).filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]),
  );
  return { year: Number(parts.year), month: Number(parts.month), day: Number(parts.day) };
}

function compactDate(value) {
  const parts = seoulParts(value);
  if (!parts) return '확인 중';
  return `${parts.year}.${String(parts.month).padStart(2, '0')}.${String(parts.day).padStart(2, '0')}`;
}

export function nextSettlement(now = new Date()) {
  const parts = seoulParts(now) || seoulParts(new Date());
  let year = parts.year;
  let month = parts.month;
  if (parts.day > SETTLEMENT_DAY) {
    month += 1;
    if (month === 13) { year += 1; month = 1; }
  }
  return { year, month, day: SETTLEMENT_DAY };
}

function hasOwnField(rows, fields) {
  return rows.some((row) => fields.some((field) => Object.prototype.hasOwnProperty.call(row || {}, field)));
}

export function settlementColumns(rows = []) {
  const hasShop = hasOwnField(rows, OPTIONAL_FIELDS.shop);
  const hasItem = hasOwnField(rows, OPTIONAL_FIELDS.item);
  const hasThumbnail = hasOwnField(rows, OPTIONAL_FIELDS.thumbnail);
  return [
    { key: 'date', label: '날짜' },
    ...(hasShop ? [{ key: 'shop', label: '쇼핑몰' }] : []),
    ...(hasItem ? [{ key: 'item', label: '품목' }] : []),
    ...(hasThumbnail ? [{ key: 'thumbnail', label: '썸네일' }] : []),
    { key: 'share', label: '내 몫' },
    { key: 'status', label: '상태' },
  ];
}

function firstValue(row, fields) {
  for (const field of fields) {
    if (row?.[field] != null && row[field] !== '') return row[field];
  }
  return null;
}

function settlementStatus(row, license, now) {
  const raw = row?.licenseStatus || row?.status || license?.status;
  const validUntil = row?.licenseValidUntil || license?.licenseValidUntil;
  if (['expired', 'revoked', 'inactive', 'reverification_required', 'suspended'].includes(raw)) return '만료';
  if (validUntil) {
    const expiry = new Date(validUntil);
    if (!Number.isNaN(expiry.getTime()) && expiry <= now) return '만료';
  }
  if (raw === 'active') return '활성';
  // /licenses는 revoked 행을 의도적으로 제외한다. 목록 조회가 성공했는데 정산의 licenseId가
  // 보이지 않으면 과거 라이선스가 끝난 경우이므로, 알 수 없음이 아니라 만료로 표시한다.
  if (row?.licenseId && !license) return '만료';
  return '확인 중';
}

export function normalizeSettlementRow(row, licenses = [], now = new Date()) {
  const license = licenses.find((candidate) => candidate.id === row?.licenseId) || null;
  const billingType = row?.billingType || 'per_use';
  const periodStart = row?.periodStart;
  const periodEnd = row?.periodEnd;
  const billingLabel = billingType === 'monthly'
    ? `월정액${periodStart && periodEnd ? ` · ${compactDate(periodStart)}~${compactDate(periodEnd)}` : ''}`
    : '건당';

  return {
    id: row?.id || row?.paymentId,
    date: compactDate(row?.createdAt),
    shop: firstValue(row, OPTIONAL_FIELDS.shop),
    item: firstValue(row, OPTIONAL_FIELDS.item),
    thumbnail: firstValue(row, OPTIONAL_FIELDS.thumbnail),
    share: Number.isFinite(Number(row?.modelAmount)) ? Number(row.modelAmount) : 0,
    status: settlementStatus(row, license, now),
    billingLabel,
  };
}
