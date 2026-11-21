import patterns from '../../../server/app/data/page_copy_seller_cleanup.json' with { type: 'json' };
const promotion = new RegExp(patterns.promotion, 'i');
const direction = new RegExp(patterns.design_direction);
export const hasPromotion = text => promotion.test(String(text || ''));
// Keep in parity with page_copy_checks.clean_seller_text, including Python's
// splitlines separators. The editable analysis source never changes in place.
export function cleanSellerText(text) {
  return String(text || '').split(/\r\n|[\n\r\v\f\x1c-\x1e\u0085\u2028\u2029]/).map(line => line.split(/(?<=[.!?。！？])(?=\s|[가-힣])\s*|…+/)
    .filter(sentence => sentence.trim() && !promotion.test(sentence) && !direction.test(sentence))
    .map(sentence => sentence.trim()).join(' ')).filter(Boolean).join('\n');
}
