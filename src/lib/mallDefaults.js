const SECTIONS = ['shipping', 'returns', 'addresses', 'disclosure'];
const FEES = {
  shipping: ['baseFee', 'jejuFee', 'isolatedFee', 'conditionalAmount'],
  returns: ['partialFee', 'totalFee', 'exchangeFee'],
};
const present = (value) => value != null && String(value).trim() !== '';
const amount = (value) => (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0)
  || (typeof value === 'string' && /^\d+$/.test(value) && Number.isSafeInteger(Number(value)));

export function validateDefaults(section, values = {}) {
  const errors = {};
  const require = (keys) => keys.forEach((key) => { if (!present(values[key])) errors[key] = '필수 항목을 채워 주세요'; });
  if (section === 'shipping') {
    if (!['FREE', 'CHARGED', 'CONDITIONAL_FREE'].includes(values.feeType)) errors.feeType = '배송비 정책을 골라 주세요';
    require(['jejuFee', 'isolatedFee', ...(values.feeType === 'FREE' ? [] : ['baseFee']),
      ...(values.feeType === 'CONDITIONAL_FREE' ? ['conditionalAmount'] : [])]);
  } else if (section === 'returns') require(FEES.returns);
  else if (section === 'addresses') require(['shippingAddressId', 'returnAddressId']);
  else if (section === 'disclosure') {
    require(['country', 'manufacturer', 'asManager', 'phone']);
    if (present(values.phone) && !/^[0-9-]+$/.test(values.phone)) errors.phone = '전화번호는 숫자와 하이픈만 넣어 주세요';
    if (values.taxType != null && !['TAX', 'FREE'].includes(values.taxType)) errors.taxType = '과세 여부를 골라 주세요';
    if (values.productOrigin != null && !['domestic', 'imported'].includes(values.productOrigin)) errors.productOrigin = '상품 출처를 골라 주세요';
  } else errors.section = '설정 항목을 다시 골라 주세요';
  const activeFees = (FEES[section] || []).filter((key) => section !== 'shipping'
    || (key !== 'baseFee' || values.feeType !== 'FREE')
      && (key !== 'conditionalAmount' || values.feeType === 'CONDITIONAL_FREE'));
  for (const key of activeFees) {
    if (present(values[key]) && !amount(values[key])) errors[key] = '0 이상 정수로 넣어 주세요';
  }
  return { ok: Object.keys(errors).length === 0, errors };
}

export function completedItems(defaults = {}) {
  return SECTIONS.filter((section) => validateDefaults(section, { ...defaults[section],
    ...(section === 'disclosure' ? { taxType: defaults.taxType, productOrigin: defaults.productOrigin } : {}) }).ok);
}
