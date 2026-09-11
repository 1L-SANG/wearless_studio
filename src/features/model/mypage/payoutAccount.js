export const PAYOUT_ACCOUNT_API_READY = true;

export const PAYOUT_BANKS = Object.freeze([
  { code: 'shinhan', name: '신한은행' },
  { code: 'kb', name: '국민은행' },
  { code: 'woori', name: '우리은행' },
  { code: 'hana', name: '하나은행' },
  { code: 'nh', name: 'NH농협은행' },
  { code: 'ibk', name: 'IBK기업은행' },
  { code: 'kakao', name: '카카오뱅크' },
  { code: 'toss', name: '토스뱅크' },
]);

export function normalizePayoutAccount({ bankCode = '', accountNumber = '', holderName = '' } = {}) {
  return {
    bankCode: String(bankCode).trim(),
    accountNumber: String(accountNumber).replace(/[\s-]/g, ''),
    holderName: String(holderName).trim(),
  };
}

export function validatePayoutAccount(input) {
  const account = normalizePayoutAccount(input);
  return PAYOUT_BANKS.some(bank => bank.code === account.bankCode)
    && /^\d{8,16}$/.test(account.accountNumber)
    && account.holderName.length > 0
    && account.holderName.length <= 40;
}

export function maskAccountNumber(raw) {
  const digits = String(raw ?? '').replace(/[\s-]/g, '');
  if (!/^\d{8,16}$/.test(digits)) return '';
  return `***-****-${digits.slice(-4)}`;
}

export function payoutAccountLabel(account, banks = PAYOUT_BANKS) {
  if (!account) return '';
  const bank = banks.find(row => row.code === String(account.bankCode).trim());
  const bankName = String(account.bankName || bank?.name || '').trim();
  const masked = account.accountMasked || account.accountNumberMasked || maskAccountNumber(account.accountNumber);
  const holderName = String(account.holderName ?? '').trim();
  if (!bankName || !masked || !holderName) return '';
  return `${bankName} · ${masked} · ${holderName}`;
}

export function payoutBanksFromConfig(config) {
  const rows = config?.payoutBanks;
  if (!Array.isArray(rows) || rows.length === 0) return PAYOUT_BANKS;
  const valid = rows.filter(row => typeof row?.code === 'string' && row.code && typeof row?.name === 'string' && row.name);
  return valid.length ? valid : PAYOUT_BANKS;
}

export async function getPayoutAccount() {
  const { http } = await import('../../../lib/api/httpAdapter.js');
  return http('/v1/facemarket/payout-account');
}

export async function savePayoutAccount(input) {
  const { http } = await import('../../../lib/api/httpAdapter.js');
  return http('/v1/facemarket/payout-account', {
    method: 'PUT',
    body: normalizePayoutAccount(input),
  });
}
