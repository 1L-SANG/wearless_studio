# 정기결제(빌링) 운영 런북

계획서: `docs/plans/2026-09-09-toss-billing-subscription.md`
코드: `server/app/subscriptions.py` · `server/app/toss_billing.py` · `server/app/workers/subscription_biller.py`

플래그 `SUBSCRIPTION_BILLING_ENABLED` 가 `false` 인 동안 `/v1/subscriptions/*` 라우트는 **존재하지 않고** 청구·만료 워커도 뜨지 않는다. 아래 순서대로 채운 뒤 켠다.

---

## 1. 켜기 전 확인 — 토스 계약 MID

자동결제는 일반결제와 **별도 계약**이고, 상점아이디(MID)가 갈리면 시크릿 키도 갈린다.

**우리 자동결제 MID: `bill_wearlau5j`** (2026-09-09 확인 — 일반결제 MID 와 별개다)

### 어디서 받나

[개발자센터 > API 키](https://developers.tosspayments.com/my/api-keys) 에 로그인 → 상단에서 **상점(MID) `bill_wearlau5j` 선택** → 그 화면에 **클라이언트 키와 시크릿 키가 한 세트로** 표시된다.

키 읽는 법:

| 구분 | 생김새 | 우리가 쓰는 것 |
|---|---|---|
| 테스트 / 라이브 | `test_` / `live_` 로 시작 | 검증은 `test_`, 출시 후 `live_` |
| **API 개별 연동 키** | 중간에 **`ck`** / **`sk`** | ✅ 이것 |
| 결제(위젯) 키 | 중간에 `gck` / `gsk` | ❌ 아님 |

> 토스 문서 원문: *"클라이언트 키와 시크릿 키는 항상 '세트'로 묶여 있고, 한 세트로 써야 한다. 세트가 아닌 키를 사용하거나 테스트 또는 라이브 키를 섞어 사용하면 `INVALID_API_KEY` 오류가 발생한다."*

키를 다른 사람과 공유해야 하면 그 화면 좌하단 **'사용자 추가하기'** 로 권한을 준다(키 문자열을 메신저로 보내지 말 것).

### 어디에 넣나 — **두 군데다**

| 키 | 넣는 곳 | 없으면 |
|---|---|---|
| 시크릿 키 (`sk`) | SSM `TOSS_BILLING_SECRET_KEY` (서버) | 일반결제 키로 떨어져 승인이 `NOT_SUPPORTED_METHOD` |
| 클라이언트 키 (`ck`) | 빌드 env `VITE_TOSS_BILLING_CLIENT_KEY` (프런트) | 카드 등록창이 `NOT_SUPPORTED_METHOD` / `INVALID_API_KEY` |

**둘 다 채워야 한다.** 시크릿만 바꾸고 클라이언트 키를 일반결제 것으로 두면 카드 등록 단계에서 막히고, 반대면 승인 단계에서 막힌다. MID 가 하나뿐인 상점이면 둘 다 비워 둔다 — 코드가 일반결제 키로 떨어진다(`src/lib/tossKeys.js`, `toss_billing.billing_secret`).

테스트 단계에서는 비워도 된다. 테스트 키는 MID 구분 없이 빌링이 열려 있다 —
2026-09-09 실측: `test_sk_…` 로 `POST /v1/billing/authorizations/issue` 호출 시
`NOT_FOUND_BILLING`(= 인증 통과, authKey 만 무효)이 돌아왔다.

키가 어긋났을 때 나오는 코드로 원인을 가른다. 셋 다 4xx(확정 거절)이라 재시도해도 같다.

| 응답 코드 | 뜻 |
|---|---|
| `NOT_FOUND_BILLING` | ✅ 키·계약 정상. authKey 가 만료·오타일 뿐 |
| `NOT_SUPPORTED_METHOD` | 자동결제 계약이 없는 키다 → `bill_wearlau5j` 의 키로 바꾼다 |
| `UNAUTHORIZED_KEY` | 키 자체가 틀렸다(또는 Basic 인코딩에서 `:` 누락) |

## 2. 시크릿 3종 등록

```bash
copilot-aws secret init --name TOSS_BILLING_SECRET_KEY   # MID 가 하나면 생략 가능
copilot-aws secret init --name TOSS_BILLING_KEK
copilot-aws secret init --name TOSS_WEBHOOK_PATH_SECRET
```

- `TOSS_BILLING_KEK` — 빌링키 컬럼 암호화 키. 충분히 긴 난수(예: `openssl rand -base64 48`)
- `TOSS_WEBHOOK_PATH_SECRET` — 웹훅 URL 경로에 들어갈 난수(예: `openssl rand -hex 16`)

> ⚠️ **KEK 분실 = 전 구독자 카드 재등록.** 토스는 빌링키 조회 API 를 제공하지 않는다. 저장한 암호문을 못 푸는 순간 그 빌링키는 영원히 사라진 것과 같다. 로테이션이 필요하면 새 KEK 로 **재암호화하는 일회성 스크립트를 먼저 돌리고** 값을 바꾼다. 값만 바꾸면 모든 구독의 다음 청구가 한꺼번에 실패한다.

## 3. 웹훅 등록

개발자센터 웹훅 설정에 아래를 등록한다.

```
https://api.wearless.kr/v1/webhooks/toss/<TOSS_WEBHOOK_PATH_SECRET 값>
```

- 이벤트: `BILLING_DELETED`
- 토스 일반 웹훅에는 서명 헤더가 없다 → **경로 시크릿이 인증 대용**이다. URL 을 문서·이슈·스크린샷에 붙여넣지 말 것
- 수신해도 구독을 끊지 않는다. `subscriptions.billing_key_invalid = true` 만 세워 화면에 '카드 재등록' 배너를 띄운다(위조 요청 하나로 남의 구독이 종료되면 안 된다)

## 4. 마이그레이션 적용

`supabase/migrations/20260909100000_subscriptions.sql` — `pgcrypto` 확장과 테이블 2개를 만든다.

> ⚠️ CI 의 `SUPABASE_DB_URL` 이 앱 DB 를 가리키는지 먼저 확인한다. 2026-08-29 에 이 값이 옛 DB 를 가리켜 마이그레이션이 prod 에 안 붙은 전례가 있다. 앱 DB 는 `ftjxwxuactfjopbokbni`.

## 5. 테스트 키로 e2e

1. `.env` 에 테스트 클라이언트/시크릿 키 + 위 3종을 넣고 `SUBSCRIPTION_BILLING_ENABLED=true`
2. `/pricing` → 구독 카드 '구독하기' → 결제창에서 테스트 카드 입력
   - 본인인증 문자는 발송되지 않는다. 인증번호 **`000000`**
   - 테스트 환경은 카드번호 앞 6자리(BIN)만 유효하면 등록된다
3. `/subscription/success` 로 복귀 → 크레딧 충전 확인
4. DB 확인:
   ```sql
   select status, plan_code, current_period_end, next_billing_at from subscriptions;
   select kind, status, amount, credits, attempt from subscription_invoices order by created_at desc;
   ```
   `subscriptions.status='active'` · `subscription_invoices.status='paid'` 여야 한다
5. `/subscription` 에서 해지 → 확인창에 **소멸 예정 크레딧 수량과 날짜**가 뜨는지 본다(이 문구가 정책 고지다)

## 6. 켜기

`SUBSCRIPTION_BILLING_ENABLED=true` 로 배포 후:

```bash
curl -s -H "Authorization: Bearer <token>" https://api.wearless.kr/v1/subscriptions/me
# 구독이 없으면 {"status":"none"} — 200 이어야 한다(404 면 라우트 미등록)
```

## 7. 관측

청구·만료는 `job_events` 를 쓰지 않는다. 로그 로거로 본다.

| 무엇 | 어디 |
|---|---|
| 청구 실패·지연 | 로그 `wearless.subscription_biller` |
| 토스 거절 코드 | 로그 `wearless.toss_billing` (시크릿·빌링키는 안 찍힌다) |
| 실패 누적 | `select fail_code, count(*) from subscription_invoices where status='failed' group by 1` |
| 유예 중인 구독 | `select count(*) from subscriptions where status='past_due'` |

## 8. 자주 나오는 상황

**결제 실패가 갑자기 늘었다**
`fail_code` 를 먼저 본다. `REJECT_CARD_COMPANY`·잔액부족은 사용자 사정(유예 3일 안에 카드 변경 유도). `UNAUTHORIZED_KEY`·`NOT_SUPPORTED_METHOD` 가 섞이면 **우리 키 문제**다 — §1 로 돌아간다.

**`payment_gateway_unreachable` 만 쌓인다**
승인 여부 미상 상태다. `fail_count` 는 오르지 않으므로 해지되지 않는다. 토스 장애가 걷히면 같은 `Idempotency-Key`(=`order_id`)로 재시도돼 이중 청구는 없다. 멱등키는 첫 요청일로부터 **15일** 유효하므로 그 안에 복구되면 안전하다.

**구독을 껐다 켰다 하고 싶다**
플래그를 `false` 로 되돌리면 라우트와 워커가 사라진다. 이미 열린 구독은 **청구되지 않을 뿐** 상태는 그대로 남는다 — 오래 꺼두면 `next_billing_at` 이 지난 구독이 쌓이고, 다시 켜는 순간 한꺼번에 청구된다. 장기간 끌 거라면 구독을 먼저 정리한다.

**크레딧이 안 사라진다**
소멸은 `SubscriptionExpirer` 만 한다. 워커가 떠 있는지(로그 `subscription-expirer`), `grace_until`/`current_period_end` 가 지났는지 확인한다. `active` 구독은 **설계상 절대 소멸하지 않는다**(이월 정책).
