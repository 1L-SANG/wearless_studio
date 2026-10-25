# 토스 심사용 임시 이메일 로그인

셀러 앱의 로그인창 하단의 **'이메일로 로그인하기'** 를 누르면 이메일과 비밀번호로 로그인할 수 있다. 진입은 `https://ai.wearless.kr/pricing`에서 `로그인하고 시작하기`를 누른다. 기존 Supabase 인증과 세션을 사용하며 이메일 회원가입이나 비밀번호 재설정 기능은 추가하지 않는다.

표시 설정은 `src/lib/tossKeys.js`의 `PG_REVIEW_LOGIN_ENABLED` 한 곳에 있다. 심사 전용 `toss.wearless.kr`과 `pay.wearless.kr`에서만 켜고 운영 `ai.wearless.kr`에는 노출하지 않는다. FaceMarket과 관리자 로그인에는 심사용 항목이 노출되지 않는다. 로컬 Supabase를 사용하는 QA 로그인은 별도로 유지한다.

심사 전에 일반 셀러 권한의 이메일·비밀번호 계정을 Supabase에 준비하고, 이메일 확인이 완료된 계정으로 실제 로그인과 요금제 진입을 검증한다. 계정 생성과 운영 배포는 이 코드 변경에 포함되지 않는다. 계정 정보는 소스나 공개 환경변수에 넣지 않는다.

## 심사 전용 호스트 toss.wearless.kr (2026-09-29)

운영(ai.wearless.kr)은 PG 심사 전이라 계좌이체로 받는다. 토스는 별도 주소에서 결제창까지 심사할 수 있다고 해서 `toss.wearless.kr`을 연다. 셀러 번들·API·계정·세션은 ai와 같고, 다른 건 결제 경로 하나다. `src/lib/host.js`의 `IS_PG_REVIEW_HOST`가 참이면 `tossKeys.js`의 `BANK_TRANSFER_ENABLED`가 거짓이 되어 구독 카드가 토스 정기결제 창(`구매하기`)을 연다. 충전은 일반결제 계약 전이라(`TOPUP_ENABLED=false`) 이 호스트에서도 보이지 않는다.

심사 진입은 `https://toss.wearless.kr/pricing` → `로그인하고 시작하기` → 이메일 로그인이다. 구글·카카오 로그인은 이 호스트의 복귀 주소가 등록돼 있지 않아 쓰지 않는다(카카오는 `server/app/kakao_oidc.py`의 목록과 카카오 콘솔 둘 다 필요).

⚠️ 서버의 자동결제 키는 라이브(`live_sk_`)다. 심사자가 카드 등록을 끝까지 하면 첫 달 요금이 **실제로 청구**된다. 심사 결제는 토스 상점관리자에서 취소하고, 생긴 구독은 해지한다.

연결 순서:
1. 이 PR 머지 → 프론트는 Vercel이, api(CORS에 `https://toss.wearless.kr` 추가)는 `deploy-server` CI가 배포한다. api 배포가 끝나야 심사 호스트에서 API가 열린다.
2. Vercel 스튜디오 프로젝트에 도메인 `toss.wearless.kr` 추가.
3. Cloudflare DNS에 `toss` CNAME → Vercel이 알려주는 대상, **DNS only**(회색 구름). ai와 같은 방식.
4. `https://toss.wearless.kr/pricing`에서 구독 카드 3개가 `구매하기`인지, 응답 헤더에 `X-Robots-Tag: noindex`가 있는지, 이메일 로그인 후 카드 등록창이 열리는지 확인한다.

## 심사 종료 후

0. 전 카드사 승인 뒤 상점관리자에서 `bill_wearlau5j`의 홈페이지를 `https://ai.wearless.kr/pricing`으로 변경 신청한다.
1. URL 변경 승인을 확인한 뒤 Vercel 도메인과 Cloudflare 레코드를 지우고, `copilot/api/manifest.yml`의 `CORS_ORIGINS`에서 `https://toss.wearless.kr`을 뺀다.
2. 일반결제 심사 호스트 `pay.wearless.kr`이 남아 있으면 `PG_REVIEW_LOGIN_ENABLED`와 심사용 계정을 유지한다. 운영 ai에는 호스트 분기로 이메일 로그인이 보이지 않는다.
3. 일반결제 심사와 URL 변경까지 모두 끝난 뒤 `PG_REVIEW_LOGIN_ENABLED`를 완전히 끄고, 심사용 계정을 비활성화하며 세션을 회수한다.
4. 코드까지 정리할 때는 심사용 표시 설정과 조건을 제거하고 `EMAIL_LOGIN_ENABLED`를 `IS_LOCAL_SUPABASE`로 되돌린다. 로컬 QA 폼과 기존 소셜 로그인은 유지한다.

## 운영 구독 전환 중 심사 호스트 유지 (2026-10-25)

`ai.wearless.kr`의 신규 판매 경로를 토스 카드로 바꾸더라도 `toss.wearless.kr`은 카드사 심사가 끝날 때까지 그대로 둔다. 같은 프런트 번들을 쓰므로 운영 플래그 변경 뒤에도 다음 조건을 각각 확인한다.

- `ai.wearless.kr/pricing`: 월간 구독 3종과 카드 `구매하기`, 수동 계좌이체와 추가구매 크레딧 없음
- `toss.wearless.kr/pricing`: 기존 월간 정기결제 심사 화면 유지
- `pay.wearless.kr/pricing?tab=topup`: 일반결제 크레딧 2종과 테스트 결제창 유지

정기결제 심사가 끝나도 일반결제 심사가 진행 중이면 테스트계정 비밀번호를 바꾸거나 계정을 비활성화하지 않는다.

화면 기준: 'Wearless가 처음이라면? 회원가입' 과 같은 링크 문구 한 줄로 두고, 누르면 그 자리에 폼이 펼쳐진다. `Login.module.css`의 글자, 여백, 버튼 토큰을 재사용하고 입력 라벨, 자동완성, 키보드 포커스와 오류 안내를 제공한다. 랜딩(wearless.kr)의 로그인 모달도 같은 화면이다.
