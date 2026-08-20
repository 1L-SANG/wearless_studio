# FaceMarket 배포·기능 보안 하드닝 실행 계획

> 운영 데이터 삭제, 실서버 배포, 기존 모델 freeze는 이 계획의 로컬 구현 범위가 아니다. 외부 OACX 사진 필드·liveness·1:1 match 계약과 법무 동의가 확인되고 재등록 경로가 준비된 뒤 별도 컷오버로 실행한다.

**목표:** 현재 기능과 3대 서버 구조를 유지하면서 즉시 분리 가능한 결함을 먼저 막고, 이후 생체 재검증 컷오버가 얼굴 없는 성공·오정산 없이 실행되도록 순서를 고정한다.

**배포 기준:** API 서버와 SAM 서버는 현행 유지. 세 번째 FaceMarket/OpenDID 서버에 PostgreSQL, Besu, TAS, Issuer, CAS, `fm-holder`를 함께 둔다. Orchestrator는 설치·초기화 제어면으로만 사용하고 정상 VC 발급 런타임에서는 제외한다.

**첫 hardening 배포:** 신규 enqueue 차단 → 실행 job 드레인 → `rolling: recreate` 배포 → API/정산 smoke → 트래픽 재개 순서로 짧은 maintenance window를 잡는다. 구버전 task는 신규 signer intent/fence를 모르므로 이 첫 배포만 overlap을 금지한다. 안정화 후 `rolling: default`를 복원한다.

## Task 1: 근거와 배포·기능 보고서 확정

**Files:**

- Modify: `docs/research/2026-08-20-facemarket-security-remediation-technical-report.md`
- Modify: `docs/research/facemarket-opendid-vc-deployment-audit.md`

- [x] 현재 `origin/main` 커밋과 모든 `path:line` 인용을 다시 검증한다.
- [x] 3대 서버 배치, 실제 VC 발급 필수 프로세스, Orchestrator 제외 조건, 이관할 DB·Besu·키·wallet 상태를 명시한다.
- [x] 기능 유지 범위를 ship-now와 생체 컷오버로 분리한다.
- [x] authoritative freeze를 `fm_licenses.status` 비활성화로 고정하고 모델 상태는 UI 표시용으로 한정한다.
- [x] `previous_status` 감사 보존, detail/editor 잡 차단·드레인, fail-closed 배포 후 purge 순서를 고정한다.

## Task 2: record-only 정산 문구

**Files:**

- Modify: `src/features/editor/Editor.jsx`
- Test: `tests/frontend/*facemarket*settlement*.test.mjs`

- [x] 기존 문구를 실패하는 테스트로 고정한다.
- [x] 실제 지급으로 오해되는 `정산 완료`를 온체인 사용기록/감사 영수증 문구로 최소 변경한다.
- [x] 대상 테스트를 통과시킨다.

## Task 3: 정산 시뮬레이션 admin·멱등 게이트

**Files:**

- Modify: `server/app/facemarket.py`
- Modify: `server/app/facemarket_chain.py`
- Modify: `copilot/api/manifest.yml`
- Test: `server/tests/test_facemarket_settlement.py`
- Test: `server/tests/test_facemarket_chain.py`
- Create: `supabase/migrations/20260820010000_facemarket_settlement_simulation_rate_limit.sql`
- Create: `supabase/migrations/20260820020000_facemarket_settlement_signer_intents.sql`

- [x] 저장소의 기존 관리자 인증 패턴과 요청 모델을 재사용한다.
- [x] 비관리자 요청이 실 TX를 만들지 못하는 실패 테스트를 먼저 추가한다.
- [x] 동일 idempotency key 재요청이 동일 `payment_id`를 사용하고 DB/체인 기록을 중복 생성하지 않는 실패 테스트를 추가한다.
- [x] 동시 pending/duplicate 제출이 winning TX 확정을 기다리고 같은 영수증을 반환하는 실제 concurrency 테스트를 추가한다.
- [x] 공유 PostgreSQL admin/IP 분당 한도를 적용하고 429 요청이 체인을 호출하지 않는 테스트를 추가한다.
- [x] 단일 signer의 `latest` nonce lock을 첫 TX 온체인 확정까지 유지하고 서로 다른 두 payment 동시 제출 테스트를 추가한다.
- [x] PostgreSQL session advisory try-lock으로 여러 API task의 signer 구간을 공유 직렬화하고, 미획득자는 DB 연결을 즉시 반납한다.
- [x] RPC 전 durable `broadcasting` intent를 commit하고 재시작 후 기존 payment를 먼저 reconcile해 pending nonce 재사용을 막는다.
- [x] 장기 pending이 불명확하면 같은 payment만 재전송하고 `broadcasting`을 유지해 새 nonce 사용을 fail-closed한다.
- [x] pool size 3에서 owner와 여러 waiter가 있어도 무관 DB 연결이 가능하고, 중단된 intent가 신규 submit 전에 복구되는 회귀 테스트를 추가한다.
- [x] 구버전 task가 advisory lock을 모르는 첫 배포만 `rolling: recreate`로 겹침을 막고, 안정화 후 `rolling: default`로 되돌리는 운영 지시를 남긴다.
- [x] 새로운 의존성 없이 공통 `record_license_settlement`의 기존 UNIQUE 의미론을 재사용해 최소 구현한다.
- [x] 대상 테스트를 통과시킨다.

## Task 4: FaceMarket CX 원본 토큰 제거

**Files:**

- Modify: `server/app/facemarket.py`
- Modify: `server/tests/test_facemarket_identity.py`
- Create: `supabase/migrations/20260820000000_facemarket_cx_token_digest.sql`

- [x] DB 매개변수에 원본 토큰이 들어가지 않고 SHA-256 digest만 들어가는 실패 테스트를 먼저 추가한다.
- [x] 동일 토큰의 digest가 같아 기존 UNIQUE 리플레이 차단이 유지되는 테스트를 추가한다.
- [x] 표준 라이브러리 `hashlib`를 재사용하고 기존 `cx_tx_id UNIQUE`에는 digest만 저장한다.
- [x] 기존 행을 in-place digest로 바꾸고 rolling deploy 중 구버전 raw insert도 같은 digest로 정규화하는 DB trigger를 둔다.
- [x] 대상 테스트를 통과시킨다.

## Task 5: 생체 컷오버 설계 게이트

- [ ] OACX 정부ID 사진 필드 또는 별도 ID 캡처, liveness, 1:1 match 계약과 테스트 환경을 서면 확인한다.
- [ ] 민감정보 별도 동의, 최소 수집, 원본 즉시 파기, 위탁·국외이전 검토를 완료한다.
- [ ] 외부 호출 abuse/rate limit과 threshold probing 방어를 enrollment 앞단에 둔다.
- [ ] `reverification_required`를 읽는 모든 SQL/API/UI와 DB CHECK/enum 변경을 전수 검증한다.
- [ ] 기존 모델의 `previous_status`와 변경 사유·시각을 PII 없는 감사 기록으로 보존한다.
- [ ] 신규 등록·생성을 막은 뒤 영향 라이선스를 transaction으로 비활성화한다.
- [ ] 대기·실행 중 `detail_page`와 `editor_image` 실모델 잡을 취소하거나 드레인한다.
- [ ] 선택된 real 모델의 얼굴·자산·동의·라이선스가 없으면 잡 전체가 실패하고 정산되지 않는 워커를 먼저 배포한다.
- [ ] dry-run 수량과 비즈니스 승인을 확인한 뒤에만 얼굴 자산과 라이선스 사본을 purge한다.

## Task 6: 통합 검증

- [x] 변경된 Python 테스트를 먼저 실행한다.
- [x] FaceMarket 관련 Python 회귀 테스트 전체를 실행한다.
- [x] 프런트엔드 테스트 전체를 실행한다.
- [x] `git diff --check`와 보고서 인용 검사를 실행한다.
- [x] 독립 보안 리뷰를 받고 치명·높음 지적을 해소한다.
