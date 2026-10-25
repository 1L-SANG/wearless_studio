# Toss Payment Cutover Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영 앱의 월간 구독을 토스 카드 자동결제로 열면서 일반결제 심사와 기존 수동 계좌이체 이용권을 안전하게 분리한다.

**Architecture:** 신규 판매 경로와 기존 권리 조회 경로를 분리한다. 프런트가 먼저 토스 구독으로 전환된 뒤 서버가 새 계좌이체 신청을 막으며, 두 심사 호스트는 MID별 카드사 심사가 끝날 때까지 고정한다.

**Tech Stack:** Vite, React, FastAPI, Supabase Postgres, AWS Copilot, Vercel, Cloudflare, Toss Payments SDK v2

**Spec:** `docs/superpowers/specs/2026-10-25-toss-payment-cutover-design.md`

## Global Constraints

- 자동결제 MID `bill_wearlau5j`와 일반결제 MID `wearlen6s9`의 키를 섞지 않는다.
- 카드사 심사 중 `toss.wearless.kr`과 `pay.wearless.kr`의 URL, 상품, 가격, 푸터와 테스트계정을 변경하지 않는다.
- 추가구매 크레딧 실판매는 2026년 11월 23일 전에 열지 않는다.
- 이미 지급한 수동 이용권과 기존 계좌이체 신청은 삭제하거나 무효화하지 않는다.
- 결제키, 빌링키, KEK와 웹훅 경로 시크릿을 로그, 계획서와 PR 본문에 기록하지 않는다.
- 실제 카드 승인과 청구를 실행하기 직전에 사용자 승인을 받는다.
- 프로드 DB 마이그레이션은 추가 파일만 허용한다. 이번 전환에는 스키마 변경이 필요하지 않다.

## Review Focus

- 자동결제 클라이언트 키와 서버 시크릿 키가 다른 MID 세트면 카드 등록 뒤 승인이 실패한다. Task 1과 Task 4에서 키 종류와 오류 코드를 확인한다.
- 프런트와 서버를 동시에 배포하면 수동 계좌이체 버튼이 남은 채 서버만 닫히는 시간이 생길 수 있다. Task 3과 Task 4에서 프런트 선배포를 강제한다.
- 기존 수동 이용권과 열린 계좌이체 신청이 새 판매 스위치와 함께 숨겨질 수 있다. Task 2와 Task 3에서 조회·취소·관리자 확인을 별도 테스트한다.
- 카드사 심사 중 등록 URL을 바꾸거나 심사 호스트를 내리면 심사가 다시 시작될 수 있다. Task 5에서 승인 확인 전 제거를 금지한다.
- 일반결제 판매를 정책 시행일보다 먼저 열면 최대 제공기간 답변과 실제 동작이 달라진다. Task 6에서 2026년 11월 23일 게이트를 검증한다.

---

### Task 1: 운영 전환 사전 점검

**Files:**
- Modify: `docs/runbooks/subscription-billing.md`
- Modify: `docs/runbooks/pg-review-login.md`

**Interfaces:**
- Consumes: 정기결제 MID `bill_wearlau5j`, 일반결제 MID `wearlen6s9`, 운영 API 상태
- Produces: 배포 전 체크 결과와 중단 조건

- [ ] **Step 1: 토스 상점관리자 상태 기록**

`bill_wearlau5j`의 계약 완료 여부와 카드사별 승인 상태를 기록한다. 등록 홈페이지는 `https://toss.wearless.kr`인지 확인한다. `wearlen6s9`는 일반결제 심사 진행 전 상태와 등록 홈페이지 `https://pay.wearless.kr`를 확인한다.

- [ ] **Step 2: 심사 중 별도 운영 URL 사용 가능 여부 확인**

토스 계약팀 또는 고객센터에 `toss.wearless.kr`을 심사 URL로 그대로 유지하면서 승인된 카드사부터 `ai.wearless.kr`에서 실결제를 받아도 되는지 확인한다. 허용되지 않으면 운영 오픈 주소를 심사 URL로 제한하거나 전 카드사 승인까지 Task 4를 보류한다.

- [ ] **Step 3: 키 세트 존재 여부 확인**

Vercel Production의 `VITE_TOSS_BILLING_CLIENT_KEY`가 라이브 자동결제 클라이언트 키인지 확인한다. AWS SSM의 `TOSS_BILLING_SECRET_KEY`, `TOSS_BILLING_KEK`, `TOSS_WEBHOOK_PATH_SECRET`가 존재하는지만 확인하고 값을 출력하지 않는다.

- [ ] **Step 4: 운영 라우트와 웹훅 확인**

인증된 계정으로 `GET /v1/subscriptions/me`가 200을 반환하는지 확인한다. 토스 개발자센터에서 `BILLING_DELETED` 웹훅이 운영 API의 비밀 경로로 등록됐는지 확인한다.

- [ ] **Step 5: 수동 결제 잔존 건수 확인**

개인정보를 출력하지 않고 `requested` 상태의 계좌이체 신청 수, 활성 `manual_plan_grants` 수와 활성 토스 구독 수를 집계한다. 열린 신청이 있으면 Task 2에서 계좌정보와 취소 경로를 유지하고, 신청이 모두 닫히기 전에는 Task 3의 서버 판매 스위치를 끄지 않는다.

- [ ] **Step 6: 런북에 전환 전제 기록**

`docs/runbooks/subscription-billing.md`와 `docs/runbooks/pg-review-login.md`에 프런트 선배포, 서버 후배포, URL 변경 시점과 심사 호스트 유지 조건을 추가한다.

- [ ] **Step 7: 문서 검토**

Run: `git diff --check -- docs/runbooks/subscription-billing.md docs/runbooks/pg-review-login.md`

Expected: 출력 없음.

### Task 2: 신규 결제수단과 기존 권리 조회 분리

**Files:**
- Modify: `src/lib/tossKeys.js`
- Modify: `src/features/pricing/Pricing.jsx`
- Modify: `src/features/subscription/Subscription.jsx`
- Modify: `tests/frontend/bank-transfer.test.mjs`
- Modify: `tests/frontend/subscription.test.mjs`
- Modify: `tests/frontend/pg-review-host.test.mjs`
- Modify: `tests/frontend/login-local-password.test.mjs`

**Interfaces:**
- Consumes: `VITE_BANK_TRANSFER_ENABLED`, 호스트 판정 함수, 기존 계좌이체 API
- Produces: `BANK_TRANSFER_ENABLED: boolean`은 신규 판매만 제어하고 기존 신청·이용권 조회는 항상 유지

- [ ] **Step 1: 실패하는 호스트별 결제 경로 테스트 작성**

`tests/frontend/pg-review-host.test.mjs`에 다음을 고정한다.

- `ai.wearless.kr`: 구독 3종과 `구매하기` 3개, 추가구매 크레딧 0개, 계좌이체 CTA 0개
- `toss.wearless.kr`: 기존 정기결제 심사 화면과 동일
- `pay.wearless.kr`: 일반결제 충전 2종만 노출

- [ ] **Step 2: 실패하는 기존 신청·수동 이용권 테스트 작성**

`tests/frontend/bank-transfer.test.mjs`와 `tests/frontend/subscription.test.mjs`에서 신규 계좌이체 판매가 꺼져 있어도 열린 신청의 상태·취소와 활성 수동 이용권의 종료일·크레딧이 표시되는지 검증한다.

- [ ] **Step 3: 테스트 실패 확인**

Run: `pnpm test:frontend:files tests/frontend/pg-review-host.test.mjs tests/frontend/bank-transfer.test.mjs tests/frontend/subscription.test.mjs`

Expected: `ai.wearless.kr`이 계좌이체를 표시하거나 기존 수동 권리 조회가 비활성화되어 실패.

- [ ] **Step 4: 신규 판매 스위치 구현**

`src/lib/tossKeys.js`에서 `BANK_TRANSFER_ENABLED`를 `VITE_BANK_TRANSFER_ENABLED === 'true'`일 때만 켜고 두 심사 호스트에서는 항상 끈다. 환경변수가 없으면 안전하게 `false`가 되게 한다. `TOPUP_ENABLED`는 계속 `pay.wearless.kr`에서만 참이어야 한다.

- [ ] **Step 5: 기존 신청 조회를 판매 스위치에서 분리**

`src/features/pricing/Pricing.jsx`에서 열린 계좌이체 신청 조회와 취소는 로그인 사용자에게 계속 제공한다. 열린 신청이 남아 있으면 입금 계좌도 계속 조회한다. 신규 신청 모달과 새 CTA만 `BANK_TRANSFER_ENABLED`로 제어한다.

- [ ] **Step 6: 수동 이용권 조회를 판매 스위치에서 분리**

`src/features/subscription/Subscription.jsx`에서 `api.getManualEntitlement()`를 로그인 상태라면 계속 조회한다. 판매 스위치가 꺼졌다는 이유로 기존 이용권을 없는 것으로 표시하지 않는다.

- [ ] **Step 7: 심사용 이메일 로그인 범위 축소**

`PG_REVIEW_LOGIN_ENABLED`를 `toss.wearless.kr`과 `pay.wearless.kr`에서만 참으로 만든다. 로컬 Supabase QA 이메일 로그인은 유지하고 `ai.wearless.kr`에서는 이메일 폼을 숨긴다.

- [ ] **Step 8: 관련 테스트 통과**

Run: `pnpm test:frontend:files tests/frontend/pg-review-host.test.mjs tests/frontend/bank-transfer.test.mjs tests/frontend/subscription.test.mjs tests/frontend/login-local-password.test.mjs`

Expected: 모두 PASS.

- [ ] **Step 9: 프런트 전체 테스트와 빌드**

Run: `pnpm test:frontend && pnpm build`

Expected: 실패 0건, Vite production build 성공.

### Task 3: 수동 계좌이체 신규 신청 서버 차단

**Files:**
- Modify: `copilot/api/manifest.yml`
- Modify: `server/tests/test_bank_transfer.py`

**Interfaces:**
- Consumes: `BANK_TRANSFER_ENABLED=false`
- Produces: 신규 신청과 계좌 노출은 중단하지만 기존 신청 조회·취소·관리자 확인·수동 이용권 조회는 유지

- [ ] **Step 1: 실패하는 서버 계약 테스트 작성**

`server/tests/test_bank_transfer.py`에 설정이 꺼졌을 때 다음을 검증한다.

- `GET /v1/bank-transfer/info`는 `{"enabled": false}`
- `POST /v1/bank-transfer/requests`는 `503 bank_transfer_unavailable`
- `GET /v1/bank-transfer/requests/open`, 취소, 관리자 확인과 `GET /v1/bank-transfer/entitlement`는 기존 데이터에 계속 접근

- [ ] **Step 2: 실패 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_bank_transfer.py`

Expected: 운영 매니페스트에 판매 중단 값이 없거나 기존 경로 계약이 부족해 실패.

- [ ] **Step 3: 운영 매니페스트에서 신규 판매 차단**

`copilot/api/manifest.yml`의 평문 변수에 `BANK_TRANSFER_ENABLED: "false"`를 추가한다. 계좌번호 변수와 기존 라우트는 삭제하지 않는다.

- [ ] **Step 4: 서버 테스트 통과**

Run: `cd server && .venv/bin/pytest -q tests/test_bank_transfer.py`

Expected: PASS.

- [ ] **Step 5: 전체 서버 테스트**

Run: `cd server && .venv/bin/pytest -q`

Expected: 실패 0건.

### Task 4: 두 단계 운영 배포

**Files:**
- No source changes. Task 2와 Task 3의 커밋과 배포만 수행.

**Interfaces:**
- Consumes: Task 2의 프런트 변경, Task 3의 서버 판매 차단
- Produces: 운영 카드 구독 활성화와 수동 계좌이체 신규 신청 종료

- [ ] **Step 1: 프런트 전용 PR 배포**

Task 2 변경만 먼저 머지한다. Vercel Production 배포가 Ready가 될 때까지 기다린다. 서버 판매 스위치는 아직 건드리지 않는다.

- [ ] **Step 2: 프런트 운영 확인**

다음을 브라우저에서 확인한다.

- `ai.wearless.kr/pricing`: 구독 3종과 카드 `구매하기`, 추가구매와 계좌이체 없음
- `toss.wearless.kr/pricing`: 심사 화면 변화 없음
- `pay.wearless.kr/pricing?tab=topup`: 충전 2종과 일반결제 테스트창 유지
- `ai.wearless.kr/subscription`: 구독 없음 또는 기존 수동 이용권 상태를 정확히 표시

- [ ] **Step 3: 카드 등록창 스모크 테스트**

운영 계정에서 `구매하기`를 눌러 토스 카드 등록창이 열리는 것까지만 확인하고 닫는다. 결제 완료와 실제 청구는 별도의 소액 승인 테스트에서 수행한다.

- [ ] **Step 4: 서버 전용 PR 배포**

`requested` 계좌이체 신청이 0건임을 다시 확인한 뒤 Task 3 변경을 머지한다. `deploy-server`가 DB 확인, ECS 롤링, `/readyz`와 CORS 게이트까지 통과하는지 확인한다.

- [ ] **Step 5: 서버 판매 차단 확인**

인증된 계정으로 계좌이체 정보가 `enabled=false`인지 확인한다. 기존 열린 신청 조회·취소와 관리자 확인이 계속 동작하는지 확인한다.

- [ ] **Step 6: 소액 실결제 승인**

사용자의 실행 직전 승인을 받은 뒤, 승인된 카드사 카드로 Starter 월간 구독 1건을 실제 결제한다. 구독 `active`, 첫 인보이스 `paid`, 600크레딧 지급, 다음 결제일과 카드 끝자리 표시를 확인한 뒤 필요하면 토스 관리자에서 즉시 취소하고 구독을 해지한다.

- [ ] **Step 7: 24시간 관측**

`wearless.toss_billing`, `wearless.subscription_biller` 로그와 `subscription_invoices.fail_code`를 확인한다. `UNAUTHORIZED_KEY`, `NOT_SUPPORTED_METHOD`, 중복 인보이스 또는 신규 계좌이체 신청이 없어야 한다.

### Task 5: 정기결제 심사 완료 후 URL 교체

**Files:**
- Modify: `src/lib/host.js`
- Modify: `vercel.json`
- Modify: `copilot/api/manifest.yml`
- Modify: `tests/frontend/cors-origins-cover-hosts.test.mjs`
- Modify: `tests/frontend/pg-review-host.test.mjs`
- Modify: `docs/runbooks/pg-review-login.md`

**Interfaces:**
- Consumes: 전 카드사 승인과 토스의 홈페이지 URL 변경 승인
- Produces: 정기결제 운영 URL `https://ai.wearless.kr/pricing`, 제거된 `toss.wearless.kr`

- [ ] **Step 1: 전 카드사 승인 확인**

상점관리자 `이용정보 > 결제·부가서비스`에서 `bill_wearlau5j`의 전 카드사 승인을 확인한다. 하나라도 심사 중이면 이 Task를 시작하지 않는다.

- [ ] **Step 2: 홈페이지 URL 변경 신청**

상점관리자 `회사정보 > 상점아이디 bill_wearlau5j > 홈페이지 정보`에서 `https://toss.wearless.kr`를 `https://ai.wearless.kr/pricing`으로 변경 신청한다.

- [ ] **Step 3: 변경 승인 확인**

상점관리자 또는 토스 계약팀 회신으로 변경 승인을 확인한다. 신청 접수만으로 심사 호스트를 내리지 않는다.

- [ ] **Step 4: 심사 호스트 제거 테스트 작성**

프로덕션 문서 호스트와 CORS 기대 목록에서 `toss.wearless.kr`만 빠지고 `pay.wearless.kr`은 남는지 테스트한다.

- [ ] **Step 5: 코드와 인프라 참조 제거**

`src/lib/host.js`, `vercel.json`, `copilot/api/manifest.yml`에서 `toss.wearless.kr` 전용 분기, noindex 헤더와 CORS 오리진을 제거한다. 일반결제 심사 호스트와 테스트계정은 유지한다.

- [ ] **Step 6: Vercel과 Cloudflare 정리**

Vercel 프로젝트에서 `toss.wearless.kr` 도메인을 제거하고 Cloudflare DNS의 `toss` 레코드를 제거한다. URL 변경 승인을 확인한 뒤에만 수행한다.

- [ ] **Step 7: 회귀 검증**

Run: `pnpm test:frontend && pnpm build`

Expected: 실패 0건. `ai.wearless.kr/pricing`의 자동결제와 `pay.wearless.kr`의 일반결제 심사 화면이 정상.

### Task 6: 일반결제 메일 제출과 추후 운영 전환

**Files:**
- Modify: `docs/runbooks/pg-review-login.md`
- Modify later: `src/lib/tossKeys.js`
- Modify later: `tests/frontend/pg-review-host.test.mjs`

**Interfaces:**
- Consumes: 일반결제 계약 완료, 정책 시행일 2026-11-23, 일반결제 라이브 키 세트
- Produces: `ai.wearless.kr`의 추가구매 크레딧 실결제와 제거된 `pay.wearless.kr`

- [ ] **Step 1: 제출 전 일반결제 정합성 확인**

메일과 PPT가 다음과 일치하는지 확인한다.

- 100 크레딧 5,500원, 500 크레딧 26,000원
- Seller는 5%, Pro는 10%의 조건부 보너스 크레딧
- 결제 완료 즉시 지급
- 2026년 11월 23일 이후 결제일부터 12개월
- 미사용 결제는 결제일부터 7일 이내 전액 환불
- 비회원 구매 불가와 유효한 이메일 테스트계정
- 일반결제 계좌이체는 사용하지 않고 카드와 간편결제만 제공

- [ ] **Step 2: 일반결제 자료 제출**

MID `wearlen6s9` 회신 메일에 검증된 결제경로 PPT를 첨부한다. 신청서에 포함된 계좌이체는 사용하지 않으므로 제외 요청하고, 휴대폰결제도 신청하지 않는다. `pay.wearless.kr`의 상품, 가격, 푸터, URL과 테스트계정을 심사 완료까지 고정한다.

- [ ] **Step 3: 계약과 카드사 심사 완료 대기**

일반결제 라이브 시크릿 키가 발급되기 전에는 서버 `TOSS_SECRET_KEY`를 바꾸지 않는다. 실제 일반결제 판매는 2026년 11월 23일보다 먼저 열지 않는다.

- [ ] **Step 4: 라이브 키 쌍 등록**

일반결제 계약 완료 후 Vercel Production의 `VITE_TOSS_CLIENT_KEY`와 AWS SSM의 `TOSS_SECRET_KEY`를 `wearlen6s9`의 같은 라이브 키 세트로 등록한다. 값은 출력하거나 커밋하지 않는다.

- [ ] **Step 5: 운영 충전 판매 스위치 구현**

`src/lib/tossKeys.js`에 명시적 `VITE_GENERAL_PAYMENT_LIVE === 'true'` 게이트를 추가한다. `ai.wearless.kr`에서는 이 값이 참일 때만 충전 2종을 노출하고, `pay.wearless.kr`은 심사 완료 전까지 기존 테스트 경로를 유지한다.

- [ ] **Step 6: 일반결제 회귀 테스트**

`tests/frontend/pg-review-host.test.mjs`에서 운영 플래그가 거짓이면 ai에 충전이 없고, 참이면 승인된 두 SKU만 표시되는지 검증한다. pay 호스트의 상품과 테스트 결제창도 그대로 검증한다.

- [ ] **Step 7: 소액 실결제와 취소 검증**

100 크레딧 5,500원을 결제해 주문 `paid`, 크레딧 100 지급과 만료일 12개월을 확인한다. 같은 결제를 토스 관리자에서 취소하고 크레딧 회수 및 원 결제수단 환불 절차를 확인한다.

- [ ] **Step 8: 일반결제 MID URL 변경과 pay 호스트 종료**

전 카드사 심사 완료 후 `wearlen6s9`의 홈페이지를 `https://ai.wearless.kr/pricing`으로 변경 신청한다. 승인 뒤 `pay.wearless.kr`의 Vercel 도메인, Cloudflare DNS, CORS, noindex와 호스트 분기를 제거한다.

- [ ] **Step 9: 심사용 로그인과 계정 종료**

두 심사 호스트가 모두 제거된 뒤 `PG_REVIEW_LOGIN_ENABLED`를 완전히 끄고 QA 테스트계정을 비활성화하며 세션을 회수한다. 구글·카카오 로그인과 로컬 QA 이메일 로그인은 유지한다.
