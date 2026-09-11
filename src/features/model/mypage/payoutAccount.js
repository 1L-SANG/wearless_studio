export const PAYOUT_ACCOUNT_API_READY = false;

export const PAYOUT_BANKS = Object.freeze([
  { code: '088', name: '신한' },
  { code: '004', name: '국민' },
  { code: '020', name: '우리' },
  { code: '081', name: '하나' },
  { code: '011', name: 'NH농협' },
  { code: '003', name: 'IBK기업' },
  { code: '090', name: '카카오뱅크' },
  { code: '092', name: '토스뱅크' },
]);

export function normalizePayoutAccount({ bankCode = '', accountNumber = '', holderName = '' } = {}) {
  return {
    bankCode: String(bankCode).trim(),
    accountNumber: String(accountNumber).trim().replaceAll('-', ''),
    holderName: String(holderName).trim(),
  };
}

export function validatePayoutAccount(input) {
  const account = normalizePayoutAccount(input);
  return PAYOUT_BANKS.some(bank => bank.code === account.bankCode)
    && /^\d{8,16}$/.test(account.accountNumber)
    && account.holderName.length > 0;
}

export function maskAccountNumber(raw) {
  const digits = String(raw ?? '').trim().replaceAll('-', '');
  if (!/^\d{8,16}$/.test(digits)) return '';
  return `${digits.slice(0, 3)}-***-${digits.slice(-4)}`;
}

export function payoutAccountLabel(account) {
  if (!account) return '';
  const bank = PAYOUT_BANKS.find(row => row.code === String(account.bankCode).trim());
  const masked = account.accountNumberMasked || maskAccountNumber(account.accountNumber);
  const holderName = String(account.holderName ?? '').trim();
  if (!bank || !masked || !holderName) return '';
  return `${bank.name} · ${masked} · ${holderName}`;
}

function notReady() {
  return new Error('입금 계좌 API가 아직 준비되지 않았어요.');
}

export async function getPayoutAccount() {
  if (!PAYOUT_ACCOUNT_API_READY) throw notReady();
  const { http } = await import('../../../lib/api/httpAdapter.js');
  return http('/v1/facemarket/payout-account');
}

export async function savePayoutAccount(input) {
  if (!PAYOUT_ACCOUNT_API_READY) throw notReady();
  const { http } = await import('../../../lib/api/httpAdapter.js');
  return http('/v1/facemarket/payout-account', {
    method: 'PUT',
    body: normalizePayoutAccount(input),
  });
}
