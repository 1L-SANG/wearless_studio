# Toss Point Top-up Compliance Design

## Intent

Bring Wearless one-time credit purchases under Toss Payments' point-charging review rules without changing the separately approved subscription-credit rollover policy.

## Product rules

- Subscription credits keep their current behavior: they roll over while the subscription is maintained and expire at the end of the final paid period.
- A top-up can be purchased only while a paid subscription period is available.
- A new top-up expires at the earlier of one year after purchase or the end of the final paid subscription period.
- Pre-policy top-ups with no expiration remain grandfathered.
- Top-ups are non-transferable and cannot be exchanged for cash.
- One purchase may never exceed KRW 100,000. Current SKUs remain KRW 5,500 and KRW 26,000.

## Refund rules

- Standard self-service eligibility remains: the purchase is unused and the request is made within seven days.
- Approval must cancel the Toss payment using the original `paymentKey`; marking an internal ledger refunded without a successful Toss cancellation is forbidden.
- Toss cancellation uses the refund request ID as the idempotency key. A gateway failure leaves the request pending and the credits unavailable so an administrator can retry safely.
- Statutory or company-fault exceptions remain available through customer support. The published policy states that any refund request period is capped at one year from payment.

## Customer-facing evidence

- The top-up page states the use cases, purchase path, usage path, one-year maximum, subscription-end expiration, non-transferability, and original-method refund.
- The terms, refund policy, and FAQ use the same rules and include Toss' required one-year sentence.
- The point-charging PPT is rebuilt after deployment and shows purchase, payment, balance, usage, and refund paths with browser URL and PC time.

## Operations

- Use general-payment MID `wearlen6s9`; do not merge the product into billing MID `bill_wearlau5j`.
- Bank transfer, quick transfer, and virtual accounts stay disabled for top-ups.
- Recommend an initial monthly settlement limit and guarantee bond of KRW 2,000,000.
- The review account must have a paid review entitlement so the reviewer can reach the top-up checkout.

## Non-goals

- No change to subscription-credit rollover.
- No cash-like transfer between users.
- No launch before the point-charging review and guarantee bond are complete.

## Approaches considered

1. Merge top-ups into the billing MID. Rejected because Toss has provided a valid general-payment path and the billing contract covers a different product.
2. Expire every credit monthly. Rejected because Toss now permits point charging with a one-year cap and this would discard the selected product behavior.
3. Keep general payment with subscription-bound, one-year-capped top-ups. Selected because it preserves the intended experience and meets the new review conditions.
