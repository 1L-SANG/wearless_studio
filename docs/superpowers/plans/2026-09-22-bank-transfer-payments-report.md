# 계좌이체(무통장입금) 결제 경로 구현 보고 (2026-09-22)

지시서 `2026-09-22-bank-transfer-payments.md`(§10 결정 우선), Codex 검토 `…-review.md`. Codex 사용량이 소진돼(9/26 18:30 복구) **구현은 Claude가 직접** 했다. Codex 리뷰는 복구 뒤 별도로 돌린다.

## 바뀐 파일과 이유

**DB**
- `supabase/migrations/20260922120000_bank_transfer_requests.sql` (새 파일): `bank_transfer_requests`(신청·스냅샷·세금계산서 정보·지급 참조 3개), `manual_plan_grants`(구독 1개월 수동 이용권). 종류당 열린 신청 1건 부분 유니크, 사용자당 활성 이용권 1건 부분 유니크, RLS(service_role만), updated_at 트리거.

**서버**
- `server/app/bank_transfer_service.py` (새): 신청 생성·열린 신청·취소·이용권 조회·관리자 목록·**확인(지급)**·거절·만료의 DB 로직. 커밋은 호출자가. 지급은 신청 스냅샷으로, 신청 ID 로 멱등, 잠금 순서 신청 → 이용권 → 계정.
- `server/app/bank_transfer.py` (새): 사용자 라우트 `/v1/bank-transfer/{info, requests, requests/open, requests/{id}/cancel, entitlement}`. 입력 검증(입금자명 2~30자, 연락처 형식, 세금계산서 4항목).
- `server/app/bank_transfer_admin.py` (새): 관리자 라우트 `/v1/facemarket/admin/bank-transfers`(목록, `/{id}/confirm`, `/{id}/reject`). `require_admin`을 어떤 잠금보다 먼저, `write_audit`은 지급과 같은 트랜잭션.
- `server/app/bank_transfer_notify.py` (새): 신청 시 관리자 메일, 확인 시 사용자 메일. Resend 키 없으면 조용히 건너뜀. 커밋 뒤 호출.
- `server/app/workers/bank_transfer_expirer.py` (새): 300초 주기, 별도 advisory lock. 기한 지난 신청 `expired`, 종료된 이용권 `ended` + 구독 버킷 소멸 + (토스 구독 없으면) `profiles.plan='free'`. 사용자마다 커밋, 실패 시 건너뛰고 다음 주기.
- `server/app/config.py`: `bank_transfer_enabled`(기본 true), `bank_transfer_bank/account/holder`, `bank_transfer_notify_email`(기본 contact@wearless.kr). env `BANK_TRANSFER_*`.
- `server/app/main.py`: 사용자 라우터는 항상 등록(payments 옆), 관리자 라우터는 콘솔 라우터 옆(facemarket 플래그 아래), 만료 워커는 토스 설정과 무관하게 기동·정지.
- `server/app/repo.py`: `grant_subscription(allow_inactive=)` 인자 추가 — 확인 시점에 상품이 비활성이어도 신청 스냅샷대로 지급(지시서 §10-4).
- `server/app/subscriptions.py`: 구독 시작에 "활성 수동 이용권이면 409 `manual_plan_active`" 한 곳(지시서 §10-2 예외).
- `server/tests/test_bank_transfer.py` (새): 38개. 안내·신청(검증 7종, 중복, 재신청, 토스 차단, 플랜 교체 차단)·취소·이용권·관리자(권한, 목록, 충전 지급+멱등, 구독 지급, 같은 플랜 연장, 토스 차단, 교체 거절, 기한 지남 사유, 닫힌 신청, 거절)·토스 시작 가드·만료(등급 복귀, 토스 유지, 실패 건너뛰기, 워커 tick).

**프론트**
- `src/lib/tossKeys.js`: `BANK_TRANSFER_ENABLED = true`. 토스 열리면 false.
- `src/lib/api/httpAdapter.js`: 사용자 함수 5개. `src/lib/api/facemarket.js`: 관리자 함수 3개. `src/mock/api.js`: 목 5개(메모리).
- `src/store/useAppStore.js`: `loadAccount({ force })` — 지급 뒤 열어 둔 화면이 새 등급을 보게.
- `src/features/pricing/Pricing.jsx` + `.module.css`: 계좌이체 안내 띠, 열린 신청 띠(취소 버튼), 구독 CTA "계좌이체로 시작하기"(현재 플랜이면 "1개월 연장 신청"), 충전 CTA "계좌이체로 충전하기", 충전 탭 노출(`TOPUP_ENABLED || bankTransfer`), 계좌 미설정·열린 신청이면 버튼 잠금과 사유, 구독 설명문을 이용권 기준으로. 토스 키는 보지 않는다.
- `src/features/pricing/BankTransferModal.jsx` + `.module.css` (새): 상품·금액·크레딧, 계좌 복사, 입금자명·연락처·세금계산서(사업자번호·상호·대표자·이메일)·메모, 접수 완료 화면.
- `src/features/subscription/Subscription.jsx`: 토스 구독이 없거나 라우트가 없을 때 수동 이용권 뷰(요금제·종료일·자동 갱신 없음·구독 크레딧·연장 링크).
- `src/features/admin/AdminBankTransfers.jsx` (새) + `AdminShell.jsx` 메뉴 + `src/apps/admin/App.jsx` 라우트: 상태 필터, 목록, 입금 확인(입금일·메모, 기한 지남이면 사유 필수), 거절(사유 필수), 세금계산서 정보 펼침.
- `tests/frontend/bank-transfer.test.mjs` (새, 11개), `pricing.test.mjs`·`subscription.test.mjs` 기대값 갱신.

## 검증 수치
- `cd server && .venv/bin/pytest -q`: **6,072 passed, 79 skipped** (새 38 포함).
- `npx vite build`: 통과.
- `npm run test:frontend`: **1,824 / 1,824 통과**.
- 목 모드 헤드리스 스크린샷: 신청 창(구독·충전), 요금제 화면(안내 띠·충전 탭). 페이지 오류 0.

## 지시서와 다르게 한 곳
- 결제 동의 문구는 "결제하면 …" 그대로 뒀다(법무 링크 테스트 `legal-pages.test.mjs`가 그 문구를 고정한다). 계좌이체도 결제다.
- 사용자 `requests/open` 응답의 `recent`는 최근 14일 안에 닫힌 신청 1건이다(지시서 "최근 완료 1건"의 구체화).
- `GET /entitlement`는 `null` 대신 `{"active": false}`를 준다(프론트 분기가 단순해진다).
- 첫 방문 완료 띠(§1.2 마지막)는 §10-8④대로 뺐다.

## 오너가 넣어야 할 env (API 서비스, copilot `variables`)
- `BANK_TRANSFER_BANK` 은행명, `BANK_TRANSFER_ACCOUNT` 계좌번호, `BANK_TRANSFER_HOLDER` 예금주. 셋 중 하나라도 비면 화면의 버튼이 "계좌이체 신청을 잠시 받지 않아요"로 잠긴다(사고 없음).
- 선택: `BANK_TRANSFER_NOTIFY_EMAIL`(기본 contact@wearless.kr), `BANK_TRANSFER_ENABLED`(기본 true).
- 메일 알림은 `RESEND_API_KEY`가 있을 때만 나간다.

## 미구현·별도 트랙
- 다른 플랜으로 중도 교체(1차 거절, §10-1). 카드 문구 "충전할 때마다 5%·10% 보너스"는 서버 미구현(토스 경로도 같음).
- 자동 은행 대사, 자동 세금계산서 발행, 수동 구독 자동 환불(운영 절차로).
- `documents/credit_system_design.md`의 월 리셋 서술 정리(문서 후속).
- Codex 리뷰(9/26 이후).
