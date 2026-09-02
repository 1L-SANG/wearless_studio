# 로컬 QA — FaceMarket 모델 지원·검토 리뉴얼

2026-09-02. 브랜치 `feat/facemarket-application-renewal`. 지원서 제출 → 관리자 검토 →
승인/거절 → ModelHub 상태 흐름을 로컬에서 확인한다.

## 현재 가동 상태 (이 세션에서 띄워 둠)
- 로컬 Postgres (Supabase) `127.0.0.1:54322` — 전 마이그레이션 재적용 완료(`supabase db reset`).
- 백엔드 `:8001` (uvicorn, `server/.env.local` 로드) — 프론트 `VITE_API_BASE_URL=http://localhost:8001` 과 정렬.
- 프론트 dev `:5173` (vite).
- 로그: `/tmp/wl-qa/backend.log`, `/tmp/wl-qa/frontend.log`.

재기동이 필요하면:
```bash
# 백엔드
cd server && set -a && . ./.env.local && set +a && \
  .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
# 프론트 (레포 루트)
node_modules/.bin/vite --port 5173
```

## 접속 URL (로컬은 쿼리로 도메인 강제)
- 모델(지원자): `http://localhost:5173/?facemarket=1` → 이후 `/model` 허브, `/model/apply` 지원서.
- 관리자: `http://localhost:5173/?admin=1` → 지원 검토 콘솔.
- 셀러(기존): `http://localhost:5173/` (쿼리 없음).

한 번 `?facemarket=1` / `?admin=1` 을 보면 그 탭이 기억한다(sessionStorage). 빠져나오려면
`?facemarket=0`.

## QA 활성 플래그 (`server/.env.local` 에 추가됨)
```
FM_APPLICATION_REQUIRED=true       # 신규 진입을 지원서로 게이트
FM_BIOMETRIC_ENROLLMENT_ENABLED=false  # 생체 스택(OpenDID·face QC weights) 무거워서 이번 QA 는 제외
FM_APPLICATION_AUTO_APPROVE=false  # true 면 제출 즉시 승인(관리자 없이 혼자 테스트)
# RESEND_API_KEY / FM_SLACK_WEBHOOK_URL 미설정 → 메일·Slack 은 로그만(스킵)
```

## ⚠️ 로컬 auth 특성 (반드시 먼저 이해)
이 환경은 **인증 = 프로드 Supabase(JWKS), DB = 로컬**(하이브리드)이다. 그래서 로그인한
사용자의 `sub` 이 로컬 `auth.users` 에 없고, `fm_model_applications.user_id` 등 FK 가
막힌다 — **지원서 제출·사진 스테이징이 500 으로 실패한다.**

해소: 로그인한 뒤 **본인 sub 을 로컬 auth.users 에 시딩**한다(+ 관리자 권한도 같이).

1. `?facemarket=1` 에서 로그인.
2. 브라우저 콘솔에서 sub 확인:
   ```js
   JSON.parse(atob((await window.__supabase?.auth?.getSession?.())?.data?.session?.access_token?.split('.')[1] || '')).sub
   ```
   (전역 핸들이 없으면 개발자도구 Application → Local Storage 의 supabase 세션 토큰을
   jwt.io 로 디코드해 `sub` 확인.)
3. 시딩 + 관리자 지정(한 번에):
   ```bash
   scripts/qa_grant_admin.sh <sub>
   ```
   이제 그 계정으로 지원서 제출이 되고, `?admin=1` 콘솔도 열린다.

> 이미 지원서를 낸 계정이 있다면(다른 방법으로 auth.users 가 채워진 경우)
> `scripts/qa_grant_admin.sh --applicants` 로 지원자 전원을 관리자로 올릴 수 있다.

## QA 시나리오 (핵심 데모)
1. **지원**: `?facemarket=1` → `/model` 허브 → "모델 지원 시작하기" → `/model/apply`.
   - 프로필 사진 업로드(스테이징) → 이름·생년월일·지역·성별·키·에이전시·카테고리·포트폴리오·
     SNS·자기소개 입력 → 개인정보 동의 체크 → 제출.
   - 제출 후 허브가 **"지원 · 검토 중"** 으로 바뀌는지.
2. **관리자 검토**: `?admin=1` → 카드에 지원 정보·프로필 사진 노출 → 승인 또는 거절(사유).
   - 두 관리자 동시 처리 방지(409)는 같은 카드를 두 번 승인해 확인.
3. **결과 반영**: 지원자 허브로 돌아가:
   - 승인 → "지원 · 승인됨" + "모델 등록 계속하기".
   - 거절 → "지원 · 거절됨" + 사유 + "다시 지원하기"(프리필 확인).
4. **재지원**: 거절 후 다시 지원 시 이전 값·사진(30일 내) 프리필.
5. **메일 미발송 뱃지**: RESEND 키 없으면 승인/거절 후 관리자 카드에 "메일 미발송" + 재발송 버튼.
6. **혼자 빠르게**: `FM_APPLICATION_AUTO_APPROVE=true` 로 바꿔 재기동하면 제출 즉시 승인 상태.

## 이번 QA 범위 밖 (별도 셋업 필요)
- **신분증 대조·이후 등록 여정**(승인 → 신분증 인증 → 사진 → VC): 생체 스택
  (`FM_BIOMETRIC_ENROLLMENT_ENABLED=true` + OpenDID holder + face QC weights + 임계값)이
  필요하다. 기존 생체등록 로컬 QA 런북을 따른다. 이 경로가 켜지면 `compare_identity_claim`
  (지원서 이름·생년월일 ↔ 신분증 대조, 3회 불일치 자동거절)까지 확인 가능.
- **admin.wearless.kr 실도메인**: 프로덕션은 DNS + Vercel 도메인 추가 필요(ops). 로컬은 `?admin=1`.

## 메일·Slack 실발송까지 QA
`server/.env.local` 에 `RESEND_API_KEY`(+ 인증된 `FM_APPLICATION_FROM_EMAIL` 도메인),
`FM_SLACK_WEBHOOK_URL` 채우고 재기동. 메일 본문에는 신원정보가 없고 거절 사유·딥링크만 담긴다.
