# 관리자 콘솔 기기 게이트 — 배포·복구 런북

설계: `docs/superpowers/specs/2026-09-11-admin-device-gate-design.md`

## 무엇인가

admin.wearless.kr 은 관리자 계정 **+ 승인된 기기**에서만 열린다. 기기 = 브라우저 프로필의
localStorage 토큰(`wl.admin.device.v1`). 서버 플래그 `ADMIN_DEVICE_GATE`:

| 값 | 뜻 |
|---|---|
| `off` | 기기 검사 안 함 |
| `shadow` | 검사하고 실패해도 통과. 로그 `admin_device_gate shadow reject user=… code=…` 만 남김. **코드 기본값** |
| `enforce` | 실패 시 403 (`device_missing`/`device_unknown`/`device_pending`/`device_revoked`) |

## 첫 배포 순서

1. 마이그레이션 `20260911150000_admin_devices.sql` 이 **앱 DB**(ftjxwxuactfjopbokbni)에 붙었는지 확인:
   `select count(*) from admin_devices;` 가 0 을 돌려주면 됨. 안 붙었으면 CI 시크릿 `SUPABASE_DB_URL` 이
   옛 DB 를 가리키는 것 — 2026-08-29 사고 런북대로 앱 DB 에 직접 적용. 이 확인이 끝나기 전에는
   머지하지 않는다(shadow 라도 조회 실패는 로그로만 남고 통과한다 — 그래서 조용히 지나갈 수 있다).
2. 머지 → CI 배포. env 는 손대지 않는다(shadow). 이 시점부터 `/openapi.json` 404.
3. 관리자 각자 admin.wearless.kr 접속 → "이 기기를 등록해요" 화면 → 이름 확인 → 승인 요청.
   shadow 라 바로 콘솔이 열린다. Slack 에 "관리자 기기 승인 요청" 이 온다.
4. `/staff` → "관리자 기기" → 서로의 기기를 **승인**. 자기 것도 다른 승인 기기가 있으면 승인 가능.
enforce 로 올리기 **전에 반드시**: `/staff` → 관리자 기기 목록의 approved 행이 전부 본인·동료가
아는 기기인지, 최근 기록의 `device.approve` 행위자가 맞는 사람인지 확인한다. shadow 기간에는
아직 승인 안 된 기기에서도 API 가 통과하므로, 그 창에 탈취된 세션이 기기를 등록하고 스스로
승인해 둘 수 있다 — Slack 알림과 원장에는 남지만 사람이 봐야 한다. 모르는 기기는 회수하고
그 계정 비밀번호를 바꾼다.

5. 배포 로그에 `shadow reject` 가 더 안 찍히면 `copilot/api/manifest.yml` 에
   `ADMIN_DEVICE_GATE: enforce` 를 넣어 PR → CI 배포. 이때부터 진짜 잠금.

## 새 관리자 / 새 기기

- 새 관리자: `/staff` 에서 승격 → 그 사람이 접속 → 등록 → 기존 관리자가 승인.
- 새 기기(폰 등): 접속 → 등록 → 대기 화면(10초 폴링) → 다른 관리자 또는 본인의 승인된 기기에서 승인.
- 요청한 적 없는 기기가 Slack 에 뜨면 **승인하지 말고** 그 계정의 비밀번호부터 바꾼다. `/staff` 에서 거절.

## 락아웃 복구 (아무 승인 기기도 없음)

1. manifest 의 `ADMIN_DEVICE_GATE` 를 `shadow` 로 → 배포.
2. 접속(등록 화면이 뜨면 등록) → `/staff` 에서 필요한 기기 승인.
3. 다시 `enforce` → 배포.
코드 변경·DB 직접 수정 없음. 정 급하면 DB 에서 `update admin_devices set status='approved' where id='…'` 도 되지만 감사 원장에 안 남는다.

## 로컬 개발

- 백엔드 `.env.local` 에 `ADMIN_DEVICE_GATE=off` 를 두면 등록 화면이 안 뜬다. 게이트를 보려면 `shadow`/`enforce`.
- `?admin=1` 오버라이드 오리진(localhost:5173)의 localStorage 는 prod 와 별개다.

## 관찰

- shadow 거절: CloudWatch 로그 `admin_device_gate shadow reject`
- 승인·회수: `admin_audit_log` (`device.approve`/`device.revoke`), `/staff` 최근 기록
- 등록 스팸: 1인당 pending 5개 상한(`ADMIN_DEVICE_MAX_PENDING_PER_USER`), 넘으면 429 `too_many_pending`
