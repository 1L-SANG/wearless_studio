# 로컬 QA — FaceMarket 모델 지원·검토 리뉴얼

2026-09-02. 브랜치 `feat/facemarket-application-renewal`. **full-local 셋업**(auth·DB·API 전부 로컬)
이라 로그인만 하면 바로 지원→검토→상태 흐름을 볼 수 있다. API 레벨 E2E(로그인→사진→제출→
admin 목록→승인→상태) 통과 확인됨.

## 계정 (로컬 Supabase, 이메일/비번 로그인 폼)
| 용도 | 이메일 | 비번 |
|---|---|---|
| 관리자(검토 콘솔) | `qa@local.test` | `qa-local-1234` |
| 지원자(비관리자) | `model@local.test` | `qa-local-1234` |

한 계정으로 지원+검토 둘 다 해도 된다(관리자도 지원 가능).

## URL
- 지원자: `http://localhost:5173/?facemarket=1` → 로그인 → `/model` 허브 → "모델 지원 시작하기"
- 관리자: `http://localhost:5173/?admin=1` → 로그인 → 지원 검토 콘솔
- 셀러(기존): `http://localhost:5173/` (쿼리 없음)

로컬은 쿼리로 도메인을 강제한다. `?facemarket=1` 은 탭이 기억(sessionStorage)하고, 쿼리 없이
새로고침해도 이제 자동으로 `?facemarket=1` 로 되돌린다(무한 Navigate 루프 픽스). `?admin=1` 은
기억하지 않으니 관리자 탭은 쿼리를 유지.

## 가동 상태 (이 세션에서 띄워 둠)
- Postgres `127.0.0.1:54322` — `supabase db reset` 으로 전 마이그레이션 적용.
- Supabase auth `127.0.0.1:54321` — ES256 JWKS, 서버가 이걸로 토큰 검증.
- 백엔드 `:8001` (`server/.env.local`, 프론트 `VITE_API_BASE_URL` 과 정렬).
- 프론트 dev `:5173`.
- 로그: `/tmp/wl-qa/backend.log`, `/tmp/wl-qa/frontend.log`.

재기동:
```bash
cd server && set -a && . ./.env.local && set +a && \
  .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001   # 백엔드
node_modules/.bin/vite --port 5173                                       # 프론트(루트)
```

## env 변경점 (전부 gitignore, 되돌리기 쉬움)
- 루트 `.env.local`: `VITE_SUPABASE_URL=http://127.0.0.1:54321` + 로컬 `VITE_SUPABASE_ANON_KEY`
  → 이메일/비번 로그인 폼 노출. 프로드 auth 로 돌아가려면 두 줄 삭제.
- `server/.env.local`: `SUPABASE_URL=http://127.0.0.1:54321` (원래 prod 값은 바로 위 주석),
  `FM_APPLICATION_REQUIRED=true`, `FM_BIOMETRIC_ENROLLMENT_ENABLED=false`,
  `FM_APPLICATION_AUTO_APPROVE=false`.

## QA 시나리오
1. **지원**: `?facemarket=1` → `model@local.test` 로그인 → 허브 "모델 지원 시작하기" → 사진 업로드 →
   필드 입력 → 동의 → 제출 → 허브 **"지원 · 검토 중"**.
2. **검토**: 새 탭 `?admin=1` → `qa@local.test` 로그인 → 카드(사진·정보) → 승인 또는 거절(사유).
3. **반영**: 지원자 탭 새로고침 → 승인 "지원 · 승인됨"+"모델 등록 계속하기" / 거절 사유+"다시 지원하기".
4. **재지원**: 거절 후 다시 지원 → 이전 값 프리필.
5. **동시 처리 방지**: 같은 카드 두 번 승인 → 409 안내.
6. **메일 미발송 뱃지**: RESEND 키 없으면 승인/거절 후 카드에 "메일 미발송"+재발송 버튼.
7. 혼자 빨리: `FM_APPLICATION_AUTO_APPROVE=true` 로 백엔드 재기동 → 제출 즉시 승인.

## 범위 밖 (별도 셋업)
- 신분증 대조·등록 연속(승인→신분증→사진→VC): `FM_BIOMETRIC_ENROLLMENT_ENABLED=true` + OpenDID
  holder + face QC weights + 임계값 필요(기존 생체등록 QA 런북). 대조 로직 자체는 유닛 12건 green.
- admin.wearless.kr 실도메인: prod DNS + Vercel 도메인(ops).

## 새 QA 계정 추가
```bash
scripts/qa_grant_admin.sh <user_id>   # 기존 로컬 유저를 관리자로
```
로컬 Supabase 유저 생성은 GoTrue admin API(service_role)로 — 런북 상단 계정 두 개는 이미 생성됨.
