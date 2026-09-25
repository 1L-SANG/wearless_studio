# FaceMarket 협찬 동의 VC — 설계

2026-09-25. 브랜치 `feat/fm-sponsorship-vc`.

## 목적

모델이 협찬을 켜면 **협찬 동의 VC**를 발급하고, 끄면 폐기한다. 셀러는 유효한 협찬 VC가 있는
모델에게만 협찬 요청을 보낼 수 있고, 공개 검증 페이지(QR)에 협찬 동의 상태가 보인다.

결정 사항(사용자 확정):
- 라이선스 VC(`facelicense-v2`)는 **그대로** 둔다. 협찬은 **별도 VC**(`fmsponsorship-v1`).
- 클레임은 **동의 사실만**. 인스타 계정·팔로워·사이즈는 VC에 넣지 않는다(가변 개인정보).
- 소비자: ② 셀러 협찬 요청 게이트, ③ 공개 검증 페이지 노출.

## 데이터

새 테이블 `fm_sponsorship_credentials`:

| 컬럼 | 뜻 |
|---|---|
| `id uuid pk` | 증서 id. 클레임 `credentialId`, holder 멱등키 `fm-sponsorship:{id}` |
| `model_id uuid` | fk fm_models, on delete cascade |
| `consent_event_key text` | 이 증서를 만든 `fm_sponsorship_consent_events.idempotency_key` |
| `status text` | `pending` → `active` → `revoked` (pending → revoked 도 가능) |
| `vc_id text` | 발급된 VC id (active 이후) |
| `consent_doc_version text` | 참여 동의서 버전 |
| `participation_doc_sha256 text` | 참여 고지 해시(이벤트 행 값 복사) |
| `profile_doc_sha256 text` | 프로필 수집 고지 해시 |
| `consented_at timestamptz` | 동의 시각 |
| `issued_at`, `revoked_at timestamptz` | |
| `attempts int`, `next_attempt_at timestamptz`, `last_error_code text` | 발급 재시도 |

부분 유니크: 모델당 `status in ('pending','active')` 행은 하나.

`fm_vc_revocation_jobs`: `kind text not null default 'license'` 추가, `license_id` NOT NULL 해제,
`check (kind <> 'license' or license_id is not null)`. 폐기는 이미 vc_id 기준이라 워커 로직은 공유.

## 흐름

1. **켜기**(`PATCH /models/{id}/sponsorship`, false→true, `FM_SPONSORSHIP_VC=on`): 동의 이벤트와
   같은 트랜잭션에 `pending` 행 삽입.
2. **발급 워커**(30초): `pending` 이고 `next_attempt_at <= now()` 인 행 claim →
   모델 DID 없으면(라이선스 VC 전) 대기 → holder `/wallet`·`/register-did`·`/issue-vc`
   (`plan: "fmsponsorship-v1"`) → 조건부 update(`status='pending'` 일 때만 active).
   이미 revoked 로 바뀌었으면 받은 vc_id 를 즉시 폐기 큐에. 실패 백오프 `min(300, 2^n)` 초.
3. **끄기**(true→false): 열린 행을 `revoked` 로. vc_id 가 있으면 폐기 큐(`kind='sponsorship'`).
4. **재동의만**(켜진 상태에서 profile 재동의): VC 변화 없음. VC 는 on/off 전환에만 생긴다.

## 클레임 (`fmsponsorship-v1`, 6개)

`modelDid`, `credentialId`, `consentDocVersion`, `participationDocSha256`, `profileDocSha256`, `consentedAt`.

## 소비자

- **셀러 게이트**: `sponsorship-interest` GET/POST 와 카탈로그의 협찬 노출은 `on` 일 때
  `active` 증서가 있어야 한다. 없으면 404 `not_found`(기존 문구 유지 — 존재 누설 없음).
- **공개 검증**(`GET /verify/{license_id}`): `sponsorship: {active, vcId, consentedAt, consentDocVersion} | null`
  추가. 개인정보 없음. 화이트리스트 계약 변경을 주석에 기록.
- **모델 화면**: 협찬 설정 옆 상태(발급 중 / 등록 끝나면 발급 / 발급됨), 끄기 전 안내.
- **관리자 모델 상세**: 증서 상태·vc id.

## 파기·오류

- 생체 파기(withdrawal·account_delete): 열린 증서를 revoked + 폐기 큐. `withdrawn` 동의 이벤트는
  남기지 않는다 — 이벤트 테이블 reason 이 `model_toggle` 만 허용하고, account_delete 는 auth.users
  삭제로 이벤트가 cascade 되므로 증서 행(revoked_at)이 철회 증빙이다.
- holder 장애: 토글은 항상 즉시 성공, 발급·폐기는 워커 재시도. revoked 로 바뀐 순간부터
  게이트·검증은 무효로 본다(폐기 완료 여부와 무관).
- 발급 실패가 반복되면(`attempts >= 10`) 경고 로그 + 관리자 모델 상세에 `lastErrorCode`. (Slack 로그 알림은 api 5xx 만 거르므로 이 경고는 자동 알림 대상이 아니다.)

## 스위치·배포

`FM_SPONSORSHIP_VC=off|on`(기본 off). off 면 현재와 동일(행 생성·게이트 없음).

1. API 배포(off) → 2. Issuer 에 `fmsponsorship-v1` 프로비저닝 → 3. holder 수동 배포 →
4. `on` + 노*운 모델로 켜기·끄기 실측. 2~4 는 운영 반영 전 사용자 확인.
운영에 협찬 켜진 모델 0명(2026-09-24 실측) → 백필 없음.

## 테스트

서버: 켜기→발급→끄기→폐기, DID 없을 때 대기, 발급 중 끄기 경합, 파기, 게이트, 검증 응답 화이트리스트.
holder: 협찬 클레임 검증, 라이선스 발급 회귀. 로컬 QA(ws-fmqa 8002)로 실동작.
