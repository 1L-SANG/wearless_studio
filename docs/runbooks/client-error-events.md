# 화면 오류 기록 운영

정본은 `docs/superpowers/specs/2026-10-08-client-error-events-design.md` 6절이며, 후속 리뷰의 DB 일일 salt, 로그인 사용자 즉시 알림, 공유 발송 예산, 요약 크기와 DB CI 계약을 적용한다. 서버를 먼저 배포하고 이후 브라우저에서 `X-Client-Request-Id`를 보낸다.

## 배포 전

1. migration `20261008090000_client_error_events.sql`과 새 전진 migration `20261008100000_client_alert_limits.sql`을 검토하고 운영 승인 후 적용한다. 기존 migration은 수정하지 않는다. 이벤트, 알림 원장, 공유 카운터, 일일 salt, 알림 커서와 발송 시도 모두 RLS가 켜지고 공개 역할 권한과 정책이 없다. API는 기존 관리자 DB 연결을 사용한다.
2. 대상 환경의 기존 SSM `SLACK_ALERT_WEBHOOK_URL`에 `copilot-application`, `copilot-environment` 태그를 붙인다. 값은 기존 운영 알림 webhook이다. 명세의 10월 8일 실측에서는 태그가 없었다. API manifest는 `/copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/SLACK_ALERT_WEBHOOK_URL`을 읽는다.
3. API만 배포한다. detail-worker에는 이 워커가 시작되지 않는다. DB 설정이 있으면 R2 설정 없이도 10초 간격으로 시작한다. Slack 값이 비어 있으면 알림만 건너뛰고 수신과 보관 정리는 계속한다.

## 수신 계약

모든 응답에 `X-Request-Id`가 있다. 입력 `X-Client-Request-Id`는 36자 UUID만 허용하고 잘못되거나 없으면 새 번호를 만든다. CORS는 입력 헤더를 허용하고 응답 헤더를 expose한다. job metadata의 `requestId`는 생성 요청 번호다. 기존 job 합류 시 번호를 바꾸지 않는다.

`POST /v1/client-events`는 아래 JSON 객체 한 건을 받는다. 유효한 Bearer JWT의 UUID sub만 user_id로 저장하고, 무효 토큰이나 토큰 없음은 익명이다. 본문 user_id와 signature는 무시한다. 정상, 중복, 한도 초과는 모두 `202 {"accepted":true}`다. 이 응답은 DB 저장 보장이 아니다. 형식 오류는 422, 실제 HTTP 본문 32768바이트 초과는 413, DB 설정 누락이나 연결 불가는 503이다. 거절 본문을 로그나 응답에 복사하지 않는다.

```json
{
  "event_id": "2df3af7f-7b3f-4a6d-bf29-147a3c59a567",
  "schema_version": 1,
  "occurred_at": "2026-10-08T09:00:00+09:00",
  "kind": "api_error",
  "action": "save",
  "route": "/v1/projects/:id/editor",
  "status": 500,
  "error_code": "save_failed",
  "request_id": "cdcc757c-c732-4102-9114-bcfb8511163c",
  "blocking": true,
  "device": "desktop",
  "breadcrumbs": []
}
```

필수 필드는 event_id, schema_version=1, 시간대가 있는 occurred_at, kind다. 허용 kind는 `api_error`, `ui_error`, `runtime_error`, `unhandled_rejection`, `error_boundary`, `network_error`, `toast_error`, `save_error`, `upload_error`, `generation_error`, `analysis_error`다. action은 `save`, `upload`, `generate`, `analyze`, `restore`, `navigate`, `load`, `poll`, `download`, `export`, `login`, `unknown`, `mannequinGenerate`, `mannequinAdjust`, `detailPageGenerate`, `editorImage`다. 미등록 action과 device는 unknown으로 저장한다. device는 desktop, mobile, tablet을 허용한다.

선택 필드는 build_sha, status, error_code, request_id, project_id, job_id, device, blocking, context, breadcrumbs와 아래 진단 필드다. 새 진단 필드는 정제된 payload JSONB에만 저장하며 별도 컬럼이나 migration은 추가하지 않는다. 프로젝트와 작업 UUID는 브라우저 주장으로 취급한다. UUID 필드는 36자다. error_code는 `[a-z][a-z_]{0,31}`인 32자 이하의 소문자와 밑줄만 허용하고, 없거나 형식이 다르면 자르지 않고 other로 저장한다. 발자취의 error_code도 같다. build_sha는 최대 64자이며 영문, 숫자, 밑줄, 점, 콜론, 하이픈만 남긴다. route는 최대 입력 512자, 최대 16개 segment이며 query, hash, origin은 제거한다. 알려진 정적 segment만 남기고 UUID와 숫자는 :id, 그 밖의 값은 :segment로 바꾼다.

context는 online, local_backup boolean, visibility(visible 또는 hidden), draft_slot_status(owned, lost, none, unknown), photo_pending_count(0~1000)만 남긴다. breadcrumbs는 마지막 30개, at, type(api, action, navigation), method, route, action, request_id, status(0~599), error_code, count(1~10000), duration_ms(0~3600000)만 남긴다. 사용자에게 표시한 문구 원문(message), 입력값, DOM, store와 허용하지 않은 필드는 저장하지 않는다. 기술 정보는 아래 tech_message와 stack 계약으로만 받는다.

### 진단 필드 계약

| 필드 | 허용 값과 상한 |
|---|---|
| step | input, storyboard, mannequin, generating, editor, library, other. 없거나 잘못되면 other |
| message_code | error_code와 같은 `[a-z][a-z_]{0,31}`. 없거나 잘못되면 other |
| browser.family, os.family | `[A-Za-z][A-Za-z0-9 _-]{0,31}`, 최대 32자. 전체 User-Agent는 받지 않음 |
| browser.major, os.major | `[A-Za-z0-9][A-Za-z0-9._-]{0,7}`, 최대 8자의 문자열. 예: "132", "18", "unknown" |
| viewport.width, viewport.height | 정수 0~16384 |
| network.effective_type | slow-2g, 2g, 3g, 4g, unknown. 잘못된 값은 unknown |
| network.rtt_ms | 유한한 숫자 0~60000, 소수 허용 |
| network.downlink_mbps | 유한한 숫자 0~10000, 소수 허용 |
| network.save_data, network.online | boolean |
| page_age_ms | 정수 0~604800000, 최대 7일 |
| visibility | visible 또는 hidden |
| tech_message | 모든 kind에서 정제 후 최대 500자 |
| stack | ui_error, error_boundary, runtime_error, unhandled_rejection에서만 정제 후 최대 2000자. 다른 kind는 blocking=true여도 제외 |

객체 내부도 허용 필드만 남긴다. 정수 필드의 bool, 숫자 문자열, 소수는 제외하며 범위 밖 값과 NaN, Infinity도 제외한다. 선택 진단 값이 잘못되어도 나머지 이벤트는 받는다. browser와 os는 family, major 키를 쓰며 viewport와 network도 위 표의 키만 사용한다.

tech_message와 stack은 서버에서도 URL query와 hash, URL 인증 정보, 이메일 모양의 토큰, 6자리 이상 연속 숫자를 제거한다. NULL 문자, 기타 제어 문자와 단독 surrogate도 제거한다. 먼저 전체 문자열을 정제한 뒤 길이를 자르므로 상한 경계에서 개인정보가 잘려 정제를 우회하지 않는다. Firefox의 함수명@URL 프레임은 보존한다. 전체 HTTP 본문은 정제 전에도 기존 32KiB 상한이 적용된다. 이 텍스트와 새 환경 정보는 Slack payload에 추가하지 않는다.

서명은 정제한 `[kind, action, route, status, error_code]` 배열을 공백 없는 ASCII JSON으로 만든 뒤 SHA-256으로 계산한다. 수신 번호 헤더는 기록 업로드 요청 번호다. 본문의 request_id는 오류가 발생한 원래 API 요청 번호이므로 서로 다를 수 있다.

IP는 KST 날짜별 DB salt로 HMAC-SHA256을 계산하며 이벤트의 ip_hmac과 IP 한도 키에 같은 값을 사용한다. `client_event_salts(day, salt, created_at)`의 salt는 서버가 `secrets.token_bytes(32)`로 생성한다. INSERT ON CONFLICT DO NOTHING 후 SELECT하고, 커밋한 값만 인스턴스별 메모리에 하루 단위로 캐시한다. 여러 인스턴스는 같은 날짜의 DB salt를 공유하고 KST 자정에 새 salt로 바뀐다. IP 원문은 저장하지 않고 날짜가 달라지면 같은 IP의 해시도 달라진다. 별도의 SSM 비밀값 생성이나 기존 pepper 재사용은 필요 없다.

초기 공유 한도는 Postgres 기준 고정 1분 창에 IP 120건, 로그인 사용자 60건, 전체 3000건이다. IP와 전체 한도를 JWT 확인과 본문 파싱 전에 소비한다. 유효하지 않은 본문도 이 한도를 소비한다. 사용자 한도는 검증된 JWT에만 적용한다. event_id 재전송도 한도에 포함한다.

## 알림과 보관

즉시 알림은 로그인 사용자 이벤트만 대상으로 한다. 익명 이벤트는 하루 요약에만 포함하며 로그인 사용자의 burst 건수에도 더하지 않는다. 차단 오류는 첫 사례부터, 그 밖의 오류는 해당 사례의 received_at까지 10분 안에 같은 서명 5건 이상일 때 알린다. 같은 서명은 1시간 간격이다.

모든 인스턴스를 합쳐 최근 1시간에 발송 시도는 최대 10회다. 개별 즉시 알림은 최대 9회, 나머지 한 자리는 "알림이 많아 일부를 묶었어요" 요약에 사용한다. 실패와 결과 불명확한 시도도 예산을 소비한다. 초과 알림은 DB에 누적하고 요약은 최근 1시간에 한 번만 보낸다. 요약 뒤 들어온 초과분은 다음 요약까지 누적한다. 같은 서명의 초과분도 1시간 간격으로 집계하며 대표 사례는 3개만 남긴다.

client_alert_progress의 received_at과 event_id 커서로 로그인 이벤트를 한 tick에 최대 100개 처리한다. 미처리 이벤트를 최근 10분이라는 벽시계 창으로 버리지 않는다. 조회 전에 진행 중인 수신 커밋을 기다리는 짧은 잠금을 잡고, Slack 전송 전에 해제한다. 새 수신은 전송 중에도 계속되고 다음 tick이나 재시작 후 처리한다. 하루 요약의 catch-up은 tick당 미발송 날짜 하나씩 진행한다.

하루 요약은 오전 9시 KST 이후에 전날 KST 00:00 이상, 다음 날 00:00 미만을 집계한다. retained 이벤트가 시작된 날짜부터 미발송 날짜를 재시작 후 따라잡는다. 로그인 사용자만 영향 사용자 수에 넣는다. 건수가 많은 상위 30개 서명의 건수, 영향 사용자 수, 대표 3개 event_id와 request_id를 보낸다. 나머지는 "그 외 N종 M건" 한 줄로 합친다. 메시지는 30,000자 미만으로 제한한다. Slack에서는 유형을 번호로 표시하고 사용자 ID, 화면, 동작, 원문은 보내지 않는다. 대표 event_id로 DB에서 서명과 상세 정보를 조사한다.

워커는 session advisory lock을 잡고 발송 전 `sending` 원장을 커밋한다. HTTP 성공은 sent, 명확한 비성공 응답은 failed로 남겨 다음 실행에서 재시도한다. 타임아웃, 연결 오류, 발송 중 프로세스 종료는 실제 도착 여부를 알 수 없어 sending을 유지한다. digest의 sending은 자동 재발송하지 않는다. burst는 1시간이 지나고 새 사례가 있을 때만 다시 보낼 수 있다. 외부 webhook에는 멱등 기능이 없어 발송 성공 후 DB 기록 전 중단에서 정확히 한 번을 보장할 수 없다. 여기서는 중복 방지를 우선한다.

sending이 오래 남으면 운영자가 Slack의 event_id와 request_id를 대조한다. 도착했으면 관리자 DB에서 sent와 sent_at을 확정한다. 미도착을 확인한 경우에만 failed로 바꾼다. 후속 digest가 같은 날짜를 재시도하며 burst는 실패한 사례의 커서를 진행시키지 않아 다음 tick에 예산 안에서 재시도한다. 예산을 소진하면 묶음 요약으로 전환한다. 묶음 요약의 명확한 전송 실패는 실패 상태와 대기분 복원을 같은 트랜잭션으로 저장하며 다음 시간 예산에서 재시도한다.

KST 날짜별 첫 워커 실행에 30일보다 오래된 이벤트와 원장을 지우고 1일보다 오래된 카운터와 발송 시도 원장을 지운다. 영속 커서는 보관 정리 때 초기화하지 않는다. 일일 salt는 `day < 오늘 KST 날짜 - 2일`인 행을 지우므로 오늘과 2일 이내의 날짜만 남긴다. `DELETE /v1/me/account`의 기존 파기 작업 생성과 같은 트랜잭션에서 해당 사용자 이벤트와 사용자 카운터를 지운다. 동시 수신은 사용자 잠금으로 직렬화하고 탈퇴 중 계정의 신규 기록은 버린다. 최종 auth.users 삭제에도 FK cascade가 적용된다.

## 관리자 조사

관리자 DB 역할(superuser 또는 BYPASSRLS)이 필요하다. 스크립트는 읽기 전용 트랜잭션이며 .env를 자동 로드하지 않는다. DATABASE_URL은 안전한 관리자 세션에 명시한다. 결과에는 사용자 기록이 있으므로 공개 채널에 붙여 넣지 않는다.

```bash
cd server
.venv/bin/python -m scripts.client_events_timeline --user-id '<사용자 UUID>'
.venv/bin/python -m scripts.client_events_timeline --request-id '<요청 UUID>'
```

이벤트와 발자취는 발생 시간순으로 출력하고, 관련 jobs는 생성 시간순으로 함께 출력한다. 이벤트에 build_sha, step, message_code, browser, os, viewport, network, page_age_ms, visibility, 정제된 tech_message와 허용된 stack, context를 함께 표시한다. request_id는 이벤트 및 발자취에서 검색한다. 관련 작업은 사용자, 원래 생성 requestId, 브라우저가 주장한 job_id로 찾는다. `client_claimed_job_ids`는 검증된 소유 관계가 아니다.

## 검증

```bash
cd server
.venv/bin/pytest -q --ignore=tests/test_personalization.py
.venv/bin/pytest -q tests/test_client_event*.py tests/test_client_alerts.py tests/test_client_alert_limits_db.py tests/test_client_request_id.py -rs
.venv/bin/pytest -q tests/test_client_events_db.py tests/test_client_event_salts.py tests/test_client_alert_limits_db.py -rs
```

DB 테스트는 `CLIENT_EVENTS_TEST_DATABASE_URL` 또는 기본 `127.0.0.1:54322`의 로컬 테스트 DB만 허용한다. 새 migration을 해당 DB에 적용한 뒤 RLS, 권한, 인덱스, dedupe, 제한, 삭제, 일일 salt의 동시 생성과 보관, 잠금과 원장의 물리 동작을 확인한다. 로컬 DB가 없으면 해당 검사는 skip으로 보고한다.

GitHub Actions의 test-db는 위 세 파일을 명시적으로 실행하고 CLIENT_EVENTS_REQUIRE_TEST_DB=1을 설정한다. 이 잡에서 DB 연결 실패는 skip이 아니라 실패다. 일반 test 잡은 세 파일을 제외한다.
