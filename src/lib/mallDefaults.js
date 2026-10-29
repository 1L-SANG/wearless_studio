const FEES = {
  shipping: ['baseFee', 'jejuFee', 'isolatedFee', 'conditionalAmount'],
  returns: ['totalFee', 'partialFee', 'exchangeFee'],
};
const present = (value) => value != null && String(value).trim() !== '';
const amount = (value, min = 0) => ((typeof value === 'number' && Number.isInteger(value))
  || (typeof value === 'string' && /^\d+$/.test(value))) && Number(value) >= min && Number(value) <= 2147483647;

export function validateDefaults(section, values = {}, { partial = false } = {}) {
  const errors = {};
  const require = (keys) => { if (!partial) keys.forEach((key) => { if (!present(values[key])) errors[key] = '필수 항목을 채워 주세요'; }); };
  if (section === 'shipping') {
    if (present(values.feeType) && !['FREE', 'CHARGED', 'CONDITIONAL_FREE'].includes(values.feeType)) errors.feeType = '배송비 정책을 골라 주세요';
    require(['feeType', 'jejuFee', 'isolatedFee', ...(values.feeType === 'FREE' ? [] : ['baseFee']),
      ...(values.feeType === 'CONDITIONAL_FREE' ? ['conditionalAmount'] : [])]);
  } else if (section === 'returns') require(FEES.returns);
  else if (section === 'addresses') require(['returnAddressId']);
  else if (section === 'disclosure') {
    require(['country', 'manufacturer', 'asManager', 'phone', 'warrantyText']);
    if (present(values.phone) && !/^[0-9-]+$/.test(values.phone)) errors.phone = '전화번호는 숫자와 하이픈만 넣어 주세요';
  } else errors.section = '설정 항목을 다시 골라 주세요';
  for (const key of FEES[section] || []) {
    if (section === 'shipping' && (key === 'baseFee' && values.feeType === 'FREE' || key === 'conditionalAmount' && values.feeType !== 'CONDITIONAL_FREE')) continue;
    if (present(values[key]) && !amount(values[key], key === 'conditionalAmount' ? 1 : 0)) errors[key] = key === 'conditionalAmount' ? '1 이상 2,147,483,647 이하 정수로 넣어 주세요' : '0 이상 2,147,483,647 이하 정수로 넣어 주세요';
  }
  return { ok: Object.keys(errors).length === 0, errors };
}

export function mallDefaultsValues(mall, defaults = {}) {
  const values = { disclosure: { country: defaults.disclosure?.country ?? '' },
    taxType: defaults.taxType ?? 'TAX', listingMode: defaults.listingMode ?? 'HIDDEN' };
  if (mall === 'cafe24') return values;
  return { ...values,
    shipping: { feeType: 'CHARGED', ...defaults.shipping }, returns: { ...defaults.returns },
    disclosure: Object.fromEntries(['country', 'manufacturer', 'asManager', 'phone', 'warrantyText'].map((key) => [key, defaults.disclosure?.[key] ?? ''])),
    addresses: { returnAddressId: defaults.addresses?.returnAddressId || '' },
    productOrigin: defaults.productOrigin ?? 'domestic', shippingDays: defaults.shippingDays ?? 2,
  };
}

export function validateMallDefaults(mall, values, saved = {}) {
  const errors = {};
  for (const section of mall === 'zigzag' ? ['shipping', 'returns', 'disclosure'] : ['disclosure']) {
    for (const [key, error] of Object.entries(validateDefaults(section, mall === 'cafe24' ? { country: values.disclosure.country } : values[section], { partial: true }).errors)) errors[`${section}.${key}`] = error;
    for (const key of FEES[section] || []) {
      if (section === 'shipping' && (key === 'baseFee' && values.shipping.feeType === 'FREE' || key === 'conditionalAmount' && values.shipping.feeType !== 'CONDITIONAL_FREE')) continue;
      if (present(saved[section]?.[key]) && !present(values[section][key])) errors[`${section}.${key}`] = '금액을 입력해 주세요';
    }
  }
  if (mall === 'zigzag' && (!amount(values.shippingDays, 1) || Number(values.shippingDays) > 30)) errors.shippingDays = '1일부터 30일 사이로 넣어 주세요';
  for (const [key, allowed] of [['taxType', ['TAX', 'FREE']], ['productOrigin', ['domestic', 'imported']], ['listingMode', ['HIDDEN', 'ON_SALE']]]) {
    if (key === 'productOrigin' && mall !== 'zigzag') continue;
    if (!allowed.includes(values[key])) errors[key] = '항목을 골라 주세요';
  }
  return { ok: Object.keys(errors).length === 0, errors };
}

export function mallDefaultsPatch(mall, values) {
  if (mall === 'cafe24') return { disclosure: { country: values.disclosure.country }, taxType: values.taxType, listingMode: values.listingMode };
  const shipping = { feeType: values.shipping.feeType };
  for (const key of FEES.shipping) {
    if (key === 'baseFee' && shipping.feeType === 'FREE' || key === 'conditionalAmount' && shipping.feeType !== 'CONDITIONAL_FREE') continue;
    if (present(values.shipping[key])) shipping[key] = Number(values.shipping[key]);
  }
  if (shipping.feeType === 'FREE') shipping.baseFee = 0;
  const returns = Object.fromEntries(FEES.returns.filter((key) => present(values.returns[key])).map((key) => [key, Number(values.returns[key])]));
  return { shipping, returns, addresses: { ...values.addresses }, disclosure: { ...values.disclosure }, taxType: values.taxType,
    productOrigin: values.productOrigin, shippingDays: Number(values.shippingDays), listingMode: values.listingMode };
}
