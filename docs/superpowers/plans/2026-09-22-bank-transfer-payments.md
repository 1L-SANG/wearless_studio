# 작업지시서: 계좌이체(무통장입금) 결제 경로 (B안)

작성 2026-09-22. 오너 결정: PG(토스) 심사가 끝나기 전까지 사용자가 사업자 통장으로 입금하고, 관리자가 입금을 확인하면 크레딧과 요금제를 지급한다. 충전 팩뿐 아니라 **구독 플랜(Starter·Seller·Pro)도 1개월 단위로 수동 부여**한다. 세금계산서는 셀러가 원할 때만 오너가 홈택스에서 직접 발행한다(시스템은 필요 여부와 사업자 정보만 받는다).

## 0. 이 문서의 위치와 규칙
- 정본: 요금과 크레딧은 `documents/research/2026-09-07-credit-pricing-plans.md` §5-3(Seller 69,900/1,600, Pro 119,000/2,800, Starter 29,900/600, 충전 5종). 크레딧 원장 규칙은 `documents/credit_system_design.md`.
- 기존 관리자 지급 라우트 `POST /admin/users/{id}/credits/grants`(`server/app/facemarket_admin.py:1182`)는 충전 팩만 `repo.purchase_topup(provider='bank_transfer')`로 지급한다. 이 작업은 그 위에 "사용자 신청 → 관리자 확인" 흐름과 "구독 플랜 수동 부여"를 얹는다. 기존 라우트는 지우지 않는다.
- `CLAUDE.md` 규칙 전부 적용: Vite+React JSX, CSS Modules·토큰, 서비스 레이어 경유, 마이그레이션 append-only, 커밋 메시지 규칙.

## 1. 사용자 흐름 (ai.wearless.kr)

### 1.1 켜고 끄는 스위치
- 프론트 `src/lib/tossKeys.js`에 `BANK_TRANSFER_ENABLED`(기본 `true`)를 둔다. 토스가 열리면 `false`로 바꾸고 코드는 남긴다. 기존 `TOPUP_ENABLED`, `SUBSCRIPTION_TRANSFER_ENABLED`와 같은 자리, 같은 주석 결.
- 서버 `config.py`에 `bank_transfer_enabled: bool = True`와 계좌 정보 3개(`bank_transfer_bank`, `bank_transfer_account`, `bank_transfer_holder`)를 env로 받는다. 값이 비어 있으면 신청 라우트는 503(`bank_transfer_unavailable`, "계좌이체 신청을 잠시 받지 않아요.").
- `GET /v1/bank-transfer/info`(로그인 필요): `{enabled, bank, account, holder, expiresInDays: 3}`. 프론트는 이 값을 화면에 그린다. 계좌번호를 프론트에 하드코딩하지 않는다.

### 1.2 요금제 화면 `src/features/pricing/Pricing.jsx`
- `BANK_TRANSFER_ENABLED`가 true면 구독 카드 CTA를 **"계좌이체로 시작하기"**, 충전 카드 CTA를 **"계좌이체로 충전하기"**로 바꾸고, 토스 결제창(`subscribe`, `buyTopup`) 대신 계좌이체 신청 창을 연다. 비로그인은 지금처럼 `requireLogin`.
- 충전 탭은 `TOPUP_ENABLED`가 false라 숨겨져 있다. **계좌이체가 켜져 있으면 충전 탭도 보인다**(`activeTab` 계산에 `|| BANK_TRANSFER_ENABLED`). 토스 일반결제 계약 전이라도 계좌이체로는 충전을 팔 수 있기 때문이다.
- 신청 창 `src/features/pricing/BankTransferModal.jsx`(새 파일, CSS Module 새 파일):
  1. 상단에 상품명, 금액(원), 지급 크레딧, 구독이면 "1개월 이용권(자동 갱신 없음)".
  2. 계좌 정보(은행, 계좌번호, 예금주) + "복사" 버튼(`navigator.clipboard`).
  3. 입력: **입금자명**(실명, 필수, 2~30자), **연락처**(선택, 숫자·하이픈), **세금계산서 필요** 체크박스. 체크하면 사업자등록번호(필수, 10자리 숫자), 상호(필수), 계산서 받을 이메일(기본값 계정 이메일) 칸이 열린다. 메모(선택, 200자).
  4. 안내 문구(정확히): "입금이 확인되면 크레딧이 지급돼요. 확인은 영업일 기준 하루 안에 해드려요. 3일 안에 입금이 없으면 신청이 자동으로 닫혀요." 세금계산서 체크 시 한 줄 추가: "세금계산서는 입금 확인 뒤 입력하신 이메일로 보내드려요."
  5. 제출 → `api.createBankTransferRequest({...})` → 성공하면 창이 "신청이 접수됐어요" 상태로 바뀌고 계좌 정보와 금액을 다시 보여준다.
- 열린 신청이 있으면 요금제 화면 상단(탭 아래)에 **상태 띠**를 보여준다: "입금 확인 중 · Seller 1개월 · 69,900원 · 입금자 홍길동 · 9/25까지 입금" + "신청 취소" 버튼. 열린 신청이 있는 동안 같은 종류(kind)의 새 신청 버튼은 비활성(툴팁 "확인 중인 신청이 있어요"). 구독 신청 1건 + 충전 신청 1건은 동시에 허용한다.
- 지급이 끝난 뒤 처음 요금제 화면에 오면 한 번만 "Seller 이용권이 시작됐어요" 안내 띠(세션 스토리지로 1회). 과하면 빼도 된다.

### 1.3 구독 관리 화면 `src/features/subscription/Subscription.jsx`
- 토스 구독이 없고 **수동 이용권**이 있으면 "계좌이체 이용권 · Seller · 10/22까지 · 자동 갱신 없음"과 "연장하려면 요금제에서 다시 신청" 링크를 보여준다. 토스 구독 화면 요소(결제수단 교체, 해지)는 숨긴다. 지금 코드가 구독 없음 상태를 어떻게 그리는지 먼저 읽고 최소로 끼운다.
- 상단바 `plan-badge`(`shell.jsx`)는 `profiles.plan`을 읽으므로 자동으로 바뀐다. 확인만.

## 2. 데이터

### 2.1 새 테이블 `bank_transfer_requests` (마이그레이션 `20260923090000_bank_transfer_requests.sql`, append-only)
```
id uuid pk, user_id uuid fk auth.users on delete cascade,
plan_code text not null, kind text not null check (kind in ('subscription','topup')),
amount integer not null check (amount > 0), credits integer not null check (credits > 0),   -- 신청 시점 pricing_plans 스냅샷
payer_name text not null, phone text,
tax_invoice boolean not null default false, business_no text, business_name text, invoice_email text,
note text,
status text not null default 'requested' check (status in ('requested','paid','rejected','canceled','expired')),
expires_at timestamptz not null,           -- 신청 + 3일
paid_at timestamptz, confirmed_by uuid, confirmed_at timestamptz, admin_note text,
grant_ref text,                            -- 지급 결과 참조(credit_source_id 또는 manual_plan_grant id)
created_at, updated_at (set_updated_at 트리거)
```
- 부분 유니크: `(user_id, kind) where status = 'requested'` → 종류당 열린 신청 1건.
- 인덱스: `(status, created_at)`.
- RLS는 다른 결제 테이블(`toss_payment_orders`)과 같은 방식으로.

### 2.2 구독 플랜 수동 부여 `manual_plan_grants` (같은 마이그레이션)
```
id uuid pk, user_id uuid fk, plan_code text not null, request_id uuid fk bank_transfer_requests,
starts_at timestamptz not null default now(), ends_at timestamptz not null,
status text not null default 'active' check (status in ('active','ended')),
ended_reason text, created_at, updated_at
```
- 인덱스 `(ends_at) where status = 'active'`.
- **`subscriptions` 테이블은 쓰지 않는다.** `billing_key_enc not null`이고 빌러·구독 화면이 그 행을 카드 구독으로 취급한다. 수동 이용권은 별개 개념으로 둔다. (Codex 검토 항목 §6-1: 이 판단이 맞는지, `subscriptions`를 재사용하는 게 나은지 근거를 들어 답할 것.)

## 3. 서버

### 3.1 사용자 라우트 `server/app/bank_transfer.py` (새 모듈, `main.py`에 `include_router`, prefix `/v1/bank-transfer`)
- `GET /info` (§1.1).
- `POST /requests` body `{planCode, payerName, phone?, taxInvoice, businessNo?, businessName?, invoiceEmail?, note?}`:
  - `pricing_plans`에서 `code`, `is_active`로 조회해 `kind`, `price`, `credits`를 스냅샷. 없으면 404 `unknown_plan`.
  - 같은 user_id·kind에 `requested`가 있으면 409 `request_already_open`.
  - `tax_invoice`면 사업자번호(숫자 10자리)·상호 필수, 아니면 400.
  - `expires_at = now() + interval '3 days'`.
  - 응답: 신청 행 + 계좌 정보.
  - 관리자 알림 메일: `facemarket_notify._send_email` 패턴으로 `settings.contact_email`(없으면 새 설정 `bank_transfer_notify_email`, 기본 contact@wearless.kr)에 "계좌이체 신청 · Seller · 69,900원 · 홍길동". Resend 키가 없으면 로그만 남기고 실패로 만들지 않는다.
- `GET /requests/open` → 내 열린 신청(kind별 최대 2건) + 최근 완료 1건.
- `POST /requests/{id}/cancel` → 본인·`requested`만 `canceled`.

### 3.2 관리자 라우트 `server/app/bank_transfer_admin.py` (새 모듈, 기존 admin 라우터들과 같은 `admin_guard.require_admin` 사용)
- `GET /admin/bank-transfers?status=requested|all&limit=` → 목록(사용자 이메일 join, 상품명).
- `POST /admin/bank-transfers/{id}/confirm` body `{paidAt (ISO, 기본 오늘), adminNote?}`, `Idempotency-Key` 헤더 지원:
  - 트랜잭션 안에서 `for update`로 신청 행 잠금. `requested`가 아니면 409.
  - **topup**: `repo.purchase_topup(conn, user_id, plan_code, idempotency_key=f"bank-transfer:{id}", provider="bank_transfer", provider_ref=f"{payer_name}/{paid_at}", metadata={request_id, granted_by, amount_krw, paid_at})`. 결과 `credit_source_id`를 `grant_ref`에.
  - **subscription**: ① `repo.grant_subscription(conn, user_id, plan_code, metadata={provider:'bank_transfer', request_id, granted_by}, period_end_sql="now() + interval '1 month'")`로 구독 버킷 지급 ② `manual_plan_grants` 행 생성(`ends_at = now() + 1 month`) ③ `profiles.plan = plan_code` ④ `payment_history`에 provider `bank_transfer` 행(기존 지급 경로가 남기는 것과 같은 모양) — `grant_subscription`이 이미 남기면 중복으로 넣지 않는다.
    - 이미 **활성 토스 구독**(`subscriptions.status in ('active','past_due')`)이 있으면 409 `toss_subscription_active`(관리자 화면에 사유 표시). 이미 활성 수동 이용권이 있으면: 같은 플랜이면 `ends_at`를 1개월 연장하고 버킷을 하나 더 얹는다(`grant_subscription`의 이월 규칙 그대로). 다른 플랜이면 기존 grant를 `ended(reason='replaced')`로 닫고 새로 만든다. 남은 크레딧은 만료일까지 그대로 둔다.
  - 신청 행 `status='paid', paid_at, confirmed_by, confirmed_at, admin_note, grant_ref`.
  - 사용자 알림 메일(있으면): "크레딧이 지급됐어요 · Seller 1개월 · 10/22까지".
  - 감사 로그: 기존 관리자 감사(`/admin/audit`) 기록 방식이 있으면 같은 방식으로 `bank_transfer.confirm` 기록.
- `POST /admin/bank-transfers/{id}/reject` body `{reason}` → `rejected`, 사유는 `admin_note`.

### 3.3 만료
- `server/app/workers/subscription_biller.py`의 만료 루프(expire) 옆에 **수동 이용권 만료** 단계를 추가한다: `manual_plan_grants.status='active' and ends_at <= now()` → `status='ended', ended_reason='expired'`, 그리고 그 사용자에게 **활성 토스 구독도 다른 활성 수동 이용권도 없으면** `profiles.plan='free'`. 크레딧 버킷은 `credit_sources.expires_at`으로 이미 만료되는지 확인하고, 안 되면 `repo.expire_subscription_buckets`와 같은 방식으로 만료 처리한다(확인 결과를 보고서에 적을 것).
- 신청 만료: `requested and expires_at <= now()` → `expired`. 같은 루프에서.
- 기존 빌러 로직은 손대지 않는다. 새 단계는 별도 함수로 분리하고 기존 advisory lock 규칙을 따른다.

## 4. 관리자 화면 (admin.wearless.kr)
- `src/features/admin/AdminBankTransfers.jsx`(새 파일) + `AdminShell.jsx` 메뉴 한 줄 추가(`{ to: '/bank-transfers', label: '계좌이체 확인', icon: Landmark }`, 라우트 등록은 기존 admin 라우트 파일에 한 줄).
- 목록: 기본 "확인 대기" 필터. 열: 신청일, 사용자 이메일, 상품(Seller 1개월 / 마무리 충전), 금액, 입금자명, 연락처, 세금계산서(필요 시 사업자번호·상호·이메일 펼침), 메모, 기한. 행 액션: **입금 확인**(입금일 기본 오늘, 관리자 메모) / **거절**(사유 필수). 확인 뒤 행이 "지급 완료"로 바뀌고 지급 크레딧·만료일이 보인다.
- 기존 `AdminUsers.jsx`의 수동 지급 폼은 그대로 둔다(신청 없이 직접 줄 때 쓴다).
- 스타일은 기존 admin 화면(Tailwind 유틸 클래스 사용 중)과 같은 결.

## 5. 서비스 레이어와 목
- `src/lib/api/httpAdapter.js`: `getBankTransferInfo`, `createBankTransferRequest`, `getOpenBankTransferRequests`, `cancelBankTransferRequest`, 관리자용 `adminListBankTransfers`, `adminConfirmBankTransfer`, `adminRejectBankTransfer`.
- `src/mock/api.js`: 같은 이름으로 목 구현(메모리 배열). `pnpm dev:mock`에서 신청→관리자 확인까지 눌러볼 수 있게.
- `src/lib/api/index.js` 계약 목록에 추가.

## 6. Codex가 먼저 답할 기획 검토 항목 (구현 전, 보고서로)
1. §2.2 `subscriptions` 미사용 판단. 재사용이 낫다면 `billing_key_enc not null`과 빌러·구독 화면 영향을 어떻게 피하는지 구체적으로.
2. 구독 버킷 만료가 `credit_sources.expires_at`만으로 되는지, 별도 만료 처리가 필요한지(현재 `expire_subscription_buckets`가 하는 일 기준).
3. `grant_subscription`이 `payment_history`를 남기는지. 남기지 않으면 어디서 남길지.
4. 열린 신청 규칙(종류당 1건)과 3일 만료가 운영에 맞는지. 더 단순한 규칙이 있으면 제안.
5. 관리자 확인 시 "활성 토스 구독이 있으면 409"가 맞는지, 아니면 수동 이용권을 얹어야 하는지.
6. 세금계산서 정보 수집 항목(사업자번호·상호·이메일)이 충분한지. 홈택스 발행에 필요한 최소 항목 기준.
7. 다른 세션과의 충돌 위험: `git worktree list`와 `gh pr list --state open`으로 확인하고 겹치는 파일이 있으면 지적. 특히 `facemarket_admin.py`, `AdminShell.jsx`, `Pricing.jsx`, `subscription_biller.py`, `main.py`, `config.py`.
8. 이 지시서에서 빠진 것, 과한 것.

검토 보고서는 `docs/superpowers/plans/2026-09-22-bank-transfer-payments-review.md`에 쓴다. 차단 / 권고 / 이상 없음으로 나누고 근거에 파일:줄을 붙인다. **검토 단계에서는 소스 파일을 수정하지 않는다.**

## 7. 구현 단계 준수사항
- 워크트리 `/Users/daily/Documents/wearless_studio/.worktrees/bank-transfer`(브랜치 `feat/bank-transfer-payments`, base origin/main 8c802365) **안에서만** 작업. 다른 워크트리·본 트리 접근 금지. `git checkout`, `stash`, `reset` 금지.
- 새 파일 우선. 공유 파일(`Pricing.jsx`, `AdminShell.jsx`, `main.py`, `config.py`, `subscription_biller.py`, `httpAdapter.js`, `mock/api.js`, `tossKeys.js`)은 최소 줄만 만진다. 인접 코드 정리 금지.
- 기존 마이그레이션 파일 수정 금지. 새 파일 1건.
- 토스 코드(`payments.py`, `subscriptions.py`, `toss_billing.py`) 수정 금지.
- 커밋은 워크트리 안에서, 규칙대로(Conventional 접두사 + 쉬운 말). 푸시·PR 금지.
- 계좌 정보 실제 값은 넣지 않는다(env 자리만).

## 8. 통과 기준
- `cd server && .venv/bin/pytest -q` 전부 통과(기존 + 새 테스트). 새 테스트 최소: 신청 생성(정상·중복 409·세금계산서 필수값 400·비활성 상품 404), 취소, 관리자 확인(topup 지급·subscription 지급과 plan 변경·중복 확인 멱등·토스 구독 활성 409·같은 플랜 연장·다른 플랜 교체), 거절, 만료 루프(신청 만료·이용권 만료와 plan free 복귀·토스 구독 있으면 plan 유지).
- `npx vite build` 통과, `npm run test:frontend` 실패 0. 새 프론트 테스트: 요금제 화면이 `BANK_TRANSFER_ENABLED`일 때 CTA 문구와 충전 탭 노출, 신청 창 필수값, 열린 신청 띠, 관리자 목록 렌더(기존 `pricing.test.mjs` 패턴).
- 스크린샷은 Claude가 찍는다.

## 9. 보고 형식
- 커밋 해시·제목, 바뀐 파일과 한 줄 이유, 테스트 수치, 지시서와 다르게 한 곳과 이유, 오너가 넣어야 할 env 값 목록.

---

## 10. 검토 반영 결정 (2026-09-22, Codex 검토 보고서 `…-review.md` 기준. 이 절이 위 본문과 어긋나면 이 절이 이긴다)

1. **이월과 플랜 교체.** 같은 플랜 연장은 허용: 새 `ends_at = 기존 ends_at + 1개월`, 새 버킷 `period_end`도 그 값, 기존 활성 구독 버킷의 `period_end`도 같은 값으로 갱신(표시 정합). **다른 플랜으로의 중도 교체는 1차에서 거절**: 신청 단계에서 409 `plan_change_not_supported`("지금 이용권이 끝난 뒤 다른 요금제를 신청할 수 있어요. 종료일 M/D"), 관리자 확인 단계에서도 같은 코드. 그동안 충전 팩은 살 수 있다.
2. **토스와 수동 이용권 상호배제.** 구독 종류에 한해 `subscriptions.status in ('active','past_due','canceled')`면 신청·확인 모두 409 `toss_subscription_active`. 역방향 가드로 `server/app/subscriptions.py`의 구독 시작 경로에 **활성 수동 이용권이 있으면 409 `manual_plan_active`** 검사 한 곳을 추가하는 것을 §7의 토스 수정 금지 예외로 허용한다(그 파일에서 그 검사 외에는 손대지 않는다). 사용자 단위 직렬화는 `credit_accounts` 행 `for update`(기존 지급 함수가 이미 잠그는 행)를 확인·연장·만료 트랜잭션의 첫 잠금으로 쓴다.
3. **기간과 늦은 입금.** 신규 시작 = 관리자 확인 시각, 연장 = 기존 `ends_at` 기준, `paid_at`은 관리자가 입력한 실제 입금 일시(별도 저장). 신청 기한은 생성 + 3일의 정확한 일시(`expires_at`)로 저장하고 화면에도 일시를 보여준다. 신청 생성 시 같은 사용자·종류의 기한 지난 `requested`를 먼저 `expired`로 바꾼 뒤 삽입(부분 유니크 충돌은 409). **`expired` 신청도 관리자는 확인 가능**(`paidAt` 필수, `adminNote` 필수). 안내 문구는 이렇게 바꾼다: "입금이 확인되면 크레딧이 지급돼요. 확인은 영업일 기준 하루 안에 해드려요. 3일 뒤에는 신청이 자동으로 닫히지만, 기한 안에 입금하셨다면 확인 후 지급해 드려요."
4. **지급 금액과 크레딧.** 지급은 언제나 신청 행의 스냅샷(`credits`, `amount`)을 쓴다. 충전은 `purchase_topup(..., snapshot={"credits": req.credits, "price": req.amount})`, 구독은 `grant_subscription(..., credits=req.credits, period_end_sql="%s", period_end_params=(ends_at,))`. 확인 시점에 상품이 비활성이어도 지급한다. 화면의 "충전할 때마다 5%·10% 보너스"는 이번 범위 밖(토스 경로도 아직 안 준다). 보고서에 미구현으로 명시.
5. **멱등과 결과 참조.** `Idempotency-Key` 헤더는 받지 않는다. 신청 ID가 유일 식별자. 신청 행을 `for update`로 잠근 뒤 이미 `paid`면 저장된 결과(`grant_ref`, 지급 크레딧, 만료일)를 200으로 돌려주고 아무것도 다시 하지 않는다. `rejected`·`canceled`는 409. 신청 행에 `payment_id`, `credit_source_id`, `manual_plan_grant_id` 컬럼을 두어 명시적으로 연결한다(`grant_ref` 하나 대신).
6. **payment_history.** 구독 확인은 같은 트랜잭션에서 `payment_history` 한 건을 직접 만든다(`kind='subscription'`, `provider='bank_transfer'`, `status='paid'`, `amount=req.amount`, `plan_id`, `provider_ref=req.id`). 충전은 `purchase_topup`이 만드는 기록을 쓴다. 결제 기록·이용권·버킷·원장·`profiles.plan`·신청 전이·감사 기록은 한 트랜잭션, 하나라도 실패하면 전부 롤백. 메일은 커밋 뒤에 보내고 실패해도 결과를 뒤집지 않는다.
7. **만료는 독립 모듈.** `server/app/workers/bank_transfer_expirer.py`(새 파일)에 두고 `main.py` 수명주기에서 `bank_transfer_enabled`와 무관하게 기동한다(판매를 꺼도 기존 이용권은 정리해야 한다). 300초 주기, advisory lock 별도 키. 하는 일: ① `requested and expires_at <= now()` → `expired` ② `manual_plan_grants.status='active' and ends_at <= now()` → `ended(reason='expired')` + 그 사용자의 구독 버킷 만료(`repo.expire_subscription_buckets`, 상호배제 덕분에 그 사용자에게는 수동 버킷뿐이다) + 다른 활성 수동 이용권과 활성 토스 구독이 없으면 `profiles.plan='free'`. `reserved <= balance` 제약으로 실패하면 그 사용자만 건너뛰고 다음 주기에 재시도(기존 워커와 같은 방식). 기존 `subscription_biller.py`는 수정하지 않는다.
8. **누락 보완.** ① `GET /v1/bank-transfer/entitlement` → `{planCode, startsAt, endsAt, credits: 남은 구독 크레딧, autoRenew: false}` 또는 `null`. `Subscription.jsx`는 `/subscriptions/me`가 none이거나 토스 라우트가 없을 때 이 값을 함께 봐서 수동 이용권 화면을 그린다. ② 계정 캐시: 관리자 확인 뒤 사용자 화면이 새 plan을 보게 `useAppStore`의 계정 로드에 강제 재조회 옵션을 두고(최소 수정), 요금제 화면과 구독 화면 진입 시 그 옵션으로 한 번 부른다. ③ CTA: 계좌이체 분기의 활성 조건은 "서버 info.enabled && 같은 종류 열린 신청 없음"뿐이다. 현재 플랜과 같으면 문구를 "1개월 연장 신청"으로. 토스 키 유무는 보지 않는다. 충전 탭 노출 조건 두 곳(`Pricing.jsx:129`, `:142`) 모두 수정. ④ 첫 방문 완료 띠(§1.2 마지막 항목)는 뺀다.
9. **세금계산서.** 수집 항목은 사업자등록번호(숫자 10자리), 상호, **대표자 성명**, 수신 이메일(체크 시 필수, 서버에서 형식 검증). 입금자명으로 대표자 성명을 대신하지 않는다. 관리자 화면은 총액(부가세 포함)과 실제 입금일, 확인일을 보여주고 발행은 운영자가 홈택스에서 한다. `admin_note`에 발행일·승인번호를 남기는 운영.
10. **권한·감사·URL.** 목록·확인·거절 모두 `admin_guard.require_admin(conn, actor, request)`를 어떤 잠금·변경보다 먼저 호출. 감사는 기존 `write_audit`(있는 이름 그대로)를 지급과 같은 트랜잭션에서. 관리자 API 전체 경로는 기존 관리자 라우터의 prefix를 따른다(구현 전에 `admin_console_router` 등의 실제 prefix를 확인해 `/v1/admin/bank-transfers` 형태로 확정하고 서비스 함수와 맞춘다). 사용자용 응답에는 `admin_note`, `confirmed_by`를 내보내지 않는다. 알림 수신 설정은 새 `bank_transfer_notify_email`(기본 contact@wearless.kr), `config.py` 필드 선언과 `load_settings` 양쪽에 추가.
11. **환불·문서.** 수동 구독은 자동 환불 대상이 아니다(현행 자동 환불은 topup만). 요금제 화면의 환불 안내 문구를 수동 구독에 확대하지 않는다. `documents/credit_system_design.md`의 월 리셋 서술 정리는 Claude 후속 문서 작업.
12. **동시 작업.** Claude가 열린 PR 4건(#364, #331, #315, #247)을 확인했고 이 작업의 공유 파일과 겹치지 않는다. `main.py`, `config.py`, `repo.py`, `AdminShell.jsx`는 Codex가 최소 줄만 수정하고, PR 전에 Claude가 origin/main 위에 다시 맞춘다.
13. **테스트 보강.** §8 목록에 다음을 더한다: 응답 유실 뒤 재확인이 결제·원장·기간 연장을 정확히 한 번만 남기는지, 확인과 취소·거절·만료가 경쟁할 때 한 결과만 남는지, 신청 뒤 카탈로그 가격·크레딧 변경과 비활성화에도 스냅샷대로 지급되는지, 토스 해지 예약(`canceled`) 상태에서 구독 신청·확인이 409인지, 수동 이용권 활성 중 토스 시작이 409인지, 토스 설정 off·키 없음에서 신청·지급·조회·만료가 동작하는지, 같은 플랜 반복 연장이 `ends_at`과 버킷 `period_end`를 같이 미는지, 다른 플랜 신청이 409인지, `reserved <= balance` 실패 시 부분 만료 없이 다음 주기로 넘어가는지, 비관리자·타인 신청 접근 거절, 메일 실패가 지급을 되돌리지 않는지.
