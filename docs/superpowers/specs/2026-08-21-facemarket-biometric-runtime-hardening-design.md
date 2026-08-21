# FaceMarket 생체 인증·런타임 통제 설계

작성일: 2026-08-21  
상태: 구현 전 승인안  
범위: 실제 송금·지급을 제외한 FaceMarket 보안 결함과 3대 운영 구성

## 1. 목표와 완료 조건

실물 모델의 얼굴을 사용할 때 아래 조건을 모두 서버가 증명하고, 하나라도 불확실하면 생성 전에 실패시킨다.

1. OmniOne CX가 확인한 성인 본인이다.
2. CX 거래가 제공한 정부 신분증 사진과 AWS Face Liveness의 live selfie가 같은 사람이다.
3. 모델이 허용한 사용 분류에 해당하고 금지 분류에는 해당하지 않는다.
4. 현재 라이선스와 OpenDID VC가 모두 유효하다.
5. 현재 enrollment에 결속된 private 얼굴 자산만 사용한다.
6. 철회·재검증·삭제 중인 모델은 thumbnail, 다운로드, 생성에서 사용할 수 없다.

완료는 로컬/스테이지 E2E와 운영 Server 3 cutover가 모두 통과한 시점이다. 실제 송금·지급은 비범위이며 기존 온체인 정산은 감사 기록으로만 표시한다.

## 2. 확정한 선택

- 신원: 기존 OmniOne CX를 유지한다.
- 정부 사진: OACX `/trans`가 제공하는 신분증 사진만 인정한다. 사진이 없으면 등록을 차단하며 사용자 신분증 업로드 fallback은 만들지 않는다.
- Liveness: AWS Rekognition Face Liveness, `us-east-1`을 사용한다.
- 1:1 match: 이미 설치된 OpenCV YuNet 검출/정렬과 SFace 비교를 재사용한다. SAM은 얼굴 신원 비교기가 아니므로 사용하지 않는다.
- VC: OpenDID FaceLicense VC를 필수 조건으로 바꾸며 Holder 누락·장애·미발급은 실물 모델 사용을 차단한다.
- 기존 모델: 신규 등록 E2E가 준비된 뒤 즉시 재검증 대상으로 freeze한다. 유예 기간은 두지 않는다.
- 라이선스 용도: 기존 UI의 12개 분류를 닫힌 값으로 사용한다. 자유 입력이나 새 정책 엔진은 만들지 않는다.
- 썸네일: 원본 `face_front`를 카탈로그 썸네일로 제공하지 않는다. 비생체 cover가 별도로 검증되기 전까지 기본 placeholder만 표시한다.
- 삭제: 기존 personalization purge를 확장한 단일 idempotent purge 흐름을 계정 삭제에도 재사용한다.
- 운영: API, SAM, FaceMarket/OpenDID의 3대 서버를 유지한다. Orchestrator는 일회성 bootstrap/recovery에만 사용한다.

## 3. 생체 등록 흐름

```text
동의
  -> OACX 인증 및 /trans 검증
  -> 성인/CI/정부 사진 확인
  -> Face Liveness session 생성
  -> Amplify FaceLivenessDetector 촬영
  -> liveness 결과/reference image 조회
  -> YuNet 정렬 + SFace 1:1 비교
  -> 마켓용 3장 QC/자산 빌드
  -> FaceLicense VC 발급
  -> model/license 활성화
```

### 3.1 API 경계

- `POST /v1/facemarket/enrollments`: 별도 생체 동의와 OACX token을 받아 enrollment를 `identity_pending`으로 만든다.
- `POST /v1/facemarket/enrollments/{id}/liveness-session`: 사용자·enrollment·1회용 nonce를 확인한 뒤 AWS session을 만든다.
- `POST /v1/facemarket/enrollments/{id}/complete`: AWS 결과를 서버에서 직접 조회하고 match, asset build, VC issue를 진행한다.
- 클라이언트는 AWS score, SFace score, 신분증 사진, reference image를 받지 않는다. `passed`, 재시도 가능 여부, 일반화된 사유 코드만 받는다.

기존 `POST /licenses`의 직접 얼굴 업로드는 실물 모델에 대해 제거한다. 라이선스는 승인된 enrollment와 그 자산만 참조한다.

### 3.2 판정과 보존

- Liveness는 `Status=SUCCEEDED`, reference image 존재, 서버 정책 threshold 이상을 모두 요구한다.
- SFace threshold는 기존 3장 QC의 `0.363`을 재사용하지 않는다. 정부 ID 사진/라이브 셀피 검증 표본으로 별도 보정하고 `match_policy_version`과 함께 배포한다.
- AWS `AuditImagesLimit=0`, `OutputConfig` 미설정으로 별도 S3 사본을 만들지 않는다. reference image bytes는 응답 처리 메모리에서만 사용한다.
- OACX portrait, liveness reference image, OpenCV crop, embedding은 성공·실패·timeout 모든 경로에서 `finally`로 폐기한다.
- DB에는 provider, session/transaction digest, pass/fail, 정책 버전, 시간, 최소 사유 코드, raw 삭제 결과, 발급된 `vc_id`만 남긴다. 원본·embedding·상세 score는 남기지 않는다.
- AWS 문서상 Rekognition 입력은 별도 opt-out 없이는 서비스 개선에 사용될 수 있으므로, 운영 전 AWS Organizations AI services opt-out과 법무의 국외이전/위탁 승인을 필수 gate로 둔다.

### 3.3 남은 외부 계약 gate

현재 로컬 자료는 OACX `/trans` 응답의 신분증 사진 필드명, 인코딩, 최대 크기, TTL을 증명하지 못한다. 구현은 adapter와 mock contract까지 진행하되 운영 flag는 실제 샘플 응답 또는 제공사 명세로 이 네 항목을 확인하기 전까지 켜지 않는다. 사진이 없거나 해석할 수 없으면 `id_portrait_unavailable`로 fail closed한다.

## 4. 상태와 데이터

새 상태를 최소화한다.

- `fm_models.status`: 기존 값에 `reverification_required`만 추가한다. 등록 진행 중에는 기존 `pending`을 유지한다.
- `fm_licenses.status`: 기존 값에 `pending`, `reverification_required`를 추가한다. `pending`은 VC 발급 전 상태이고 `reverification_required`는 freeze의 authoritative lever다.
- 새 `fm_biometric_enrollments`: 사용자/model, OACX digest, AWS session digest, 상태, provider/policy version, decision, reason, raw deletion evidence, timestamps, `vc_id`만 저장한다.
- 새 `fm_cutover_batches`: dry-run 수량, 승인자/시간, affected model/license/job/asset count, 실행 상태를 저장한다.
- 새 `fm_vc_revocation_jobs`: `vc_id`별 durable, idempotent revoke/reconcile 상태를 저장한다.
- 기존 상태는 freeze 전에 `previous_status`와 batch id로 보존한다.

CI는 기존 digest 방식으로만 저장한다. 같은 CI의 다른 계정은 소유권을 자동 이전하지 않고 명시적 recovery 상태로 차단한다.

## 5. 용도 정책 강제

### 5.1 닫힌 분류

기존 UI 값 12개를 서버 상수로 이동하고 프런트가 이를 표시한다.

- 허용 후보: `일반 여성 의류`, `남성 의류`, `캐주얼·스트릿`, `스포츠·애슬레저`, `뷰티·화장품`, `액세서리·잡화`
- 금지 후보: `속옷·란제리`, `수영복·비키니`, `성인용품`, `주류·담배`, `의료·성형`, `정치·종교`

실물 모델을 선택하는 프로젝트/에디터 요청은 `brandUseCategory` 하나를 반드시 가진다. 서버는 닫힌 값만 저장하고 job payload에 snapshot한다.

판정은 단순하다.

1. category가 `forbidden_use`에 있으면 거절한다.
2. `allowed_use`가 비어 있거나 category가 포함되지 않으면 거절한다.
3. 둘 다 만족하면 허용한다.

금지가 항상 우선한다. 누락·알 수 없는 값·legacy 자유 문자열은 거절하고 모델이 새 분류로 라이선스를 다시 발급하게 한다. 제품의 `clothing_type/category`는 의복 종류일 뿐 사용 브랜드 분류가 아니므로 자동 추정하지 않는다.

## 6. 공통 실물 모델 게이트

기존 `resolve_model_license`와 `verify_license`를 확장해 API와 두 worker가 같은 판정을 사용한다. 별도 범용 policy framework는 만들지 않는다.

게이트 입력은 `model_id`, `license_id`, `brandUseCategory`, 현재 enrollment/asset 상태다. 다음 조건을 모두 검사한다.

- model `verified`
- license `active`, 미만료
- `vc_id` 존재
- Holder 연결 성공 및 VC `valid`
- allowed/forbidden 통과
- `assets_status=ready`
- 필수 private asset 존재
- asset source/evidence version이 현재 enrollment와 일치
- purge/revoke/reverification 진행 중 아님

실패 시 API는 credit 예약/job 생성 전에 409를 반환한다. Holder timeout/장애는 503을 반환한다. worker는 큐 진입 뒤 상태가 바뀐 경우 job을 실패시키고 credit을 환불하며 성공 결과·정산 기록을 만들지 않는다. 선택된 실물 모델을 가상 모델이나 얼굴 없는 결과로 대체하지 않는다. 가상 모델 ID는 기존 경로를 그대로 사용한다.

## 7. 얼굴 접근 통제

- `/models/{id}/thumbnail`은 `face_front`를 읽지 않는다. 이번 범위에서는 placeholder만 반환하고, 추후 별도 검증된 non-biometric cover가 생기면 그것만 허용한다.
- `/licenses/{id}/face`는 모델 소유자 본인의 관리 화면에만 유지하되, verified/active/current-consent/current-enrollment를 모두 요구한다.
- seller에게 원본 얼굴 다운로드 권한을 주지 않는다. 생성 worker만 private R2에서 서버 내부로 읽는다.
- freeze/revoke/purge/계정 삭제 상태는 모두 404로 통일해 객체 존재를 노출하지 않는다.
- 모든 얼굴 응답은 `Cache-Control: no-store, private`이며 signed public URL을 만들지 않는다.

## 8. Freeze, purge, 계정 삭제

파괴 작업은 신규 enrollment와 mandatory VC E2E가 준비된 후 실행한다.

1. fail-closed API/worker를 먼저 배포한다.
2. 신규 real-model enqueue를 닫는다.
3. pending/running `detail_page`, 관련 `editor_image`, `fm_model_asset_build`를 취소하거나 drain한다.
4. 대상 active license를 `reverification_required`로 전환한다.
5. model을 `reverification_required`로 맞춘다.
6. VC revoke job을 durable queue에 기록한다.
7. personalization originals, license face, `face_front`, `grid_sedcard`와 모든 파생 객체를 삭제한다.
8. DB key/asset row를 정리하고 manifest와 R2를 reconcile한다.

dry-run은 대상 수와 key digest만 출력하고 원본 key/CI/VC 전체값을 로그에 남기지 않는다. 실행에는 cutover batch와 명시적 admin 승인이 필요하다. 각 단계는 재실행 가능해야 하며 이미 삭제된 객체는 성공으로 취급한다.

계정 삭제도 같은 엔진을 호출한다. 라이선스 정지, VC revoke queue, job 취소, 원본/파생 얼굴 삭제 후 사용자 식별자와 CI 연결을 익명화한다. 법적으로 필요한 record-only 정산 감사는 사용자 재식별이 불가능한 범위로만 보존한다.

## 9. VC와 Server 3

Server 3에는 PostgreSQL, Besu, TAS `8090`, Issuer `8091`, CAS `8094`, fm-holder `8100`을 private Compose로 실행한다. Orchestrator `9001`은 bootstrap이 끝나면 종료한다.

배포 순서는 다음과 같다.

1. Holder 누락 DTO를 공식 OpenDID V2.0.0 artifact로 고정하고 `clean test`를 통과시킨다.
2. 기존 export/restore 도구로 DB, Besu, provider wallet/DID/key/config, Holder data를 같은 snapshot으로 이전한다.
3. Orchestrator로 entity와 FaceLicense namespace/schema/profile/plan을 한 번 bootstrap한다.
4. Orchestrator를 종료하고 자동 기동에서 제외한다.
5. Server 1만 private `8100`에 접근하도록 방화벽을 제한한다. `5432/8545/8090/8091/8094/9001`은 외부와 Server 1에서 직접 접근시키지 않는다.
6. Holder 요청에는 공유 HMAC secret, timestamp, nonce를 사용해 재전송을 차단한다. 네트워크 격리만으로 무인증 mutation API를 신뢰하지 않는다.
7. Server 1의 `OPENDID_HOLDER_URL`을 설정하고 설정 누락 시 production startup을 실패시킨다.
8. `issue -> valid -> revoke -> revoked -> 전체 재시작 -> revoked 유지` smoke를 통과한 뒤 mandatory VC flag를 켠다.

Holder 발급은 더 이상 background best-effort가 아니다. license는 `pending`으로 생성하고 issue 성공 뒤에만 `active`가 된다. revoke는 durable queue로 재시도하며, revoke 요청 즉시 local license는 non-active가 되어 생성은 먼저 차단된다.

## 10. 오류와 UX

- 사용자는 상세 점수 대신 `신분증 사진 확인 불가`, `라이브 인증 재시도`, `얼굴 일치 확인 실패`, `VC 발급 지연`, `사용 조건 불일치`처럼 조치 가능한 일반 사유만 본다.
- AWS 권고대로 동일 device/account에서 3분 내 5회 실패하면 30~60분 cooldown한다. 반복 패턴은 더 길게 차단한다.
- liveness가 불편하거나 광과민 위험이 있는 사용자를 위한 대체 수동 심사는 이번 해커톤 범위에서 만들지 않는다. 해당 사용자는 모델 등록을 완료할 수 없다는 제한을 동의 화면에 명시한다.
- virtual model, 일반 Editor, SAM, personalization의 비-FaceMarket 기능은 계속 동작한다.

## 11. 테스트와 출시 gate

### 자동 테스트

- OACX portrait 누락/형식 오류/expired token/replay -> model 미검증
- AWS session ownership/nonce/replay/timeout/liveness fail -> model 미검증
- raw portrait/reference/embedding이 성공·실패·예외에서 보존되지 않음
- SFace match 정책 version과 threshold 경계
- forbidden 우선, allowed 누락, unknown category 거절
- Holder unset/unreachable/invalid/revoked -> job·credit 예약 전 차단
- 요청 후 revoke/purge race -> worker 실패, 환불, 결과/정산 없음
- thumbnail이 `face_front`를 읽지 않음
- owner face endpoint가 freeze/revoke/purge 후 404
- purge 재실행, 부분 실패 resume, R2/DB reconcile
- account deletion 뒤 생체 API 200 없음

### E2E/운영 gate

- 실제 OACX portrait contract fixture
- 실제 N. Virginia Face Liveness 모바일 브라우저 촬영
- 실제 YuNet/SFace match와 mismatch 표본 보정
- 실제 OpenDID issue/valid/revoke/restart persistence
- Server 3 외부 port scan과 Server 1 -> Holder만 허용 확인
- 기존 real model dry-run count와 product/security/privacy/operations 승인
- freeze 이후 가상 모델/일반 생성 회귀 QA

## 12. 성능·정확도 보고 원칙

사전에 `% 빨라짐` 또는 `% 정확도 향상`을 주장하지 않는다. 등록에는 OACX, liveness, match, VC가 추가되어 기존보다 느려진다. 대신 아래를 스테이지에서 측정해 기술 보고서에 이전/이후로 기록한다.

- 등록 성공률, p50/p95 완료 시간
- liveness 재시도율과 호출당 비용
- match false accept/false reject 표본 결과
- VC issue/verify p50/p95와 장애 차단률
- purge 대상/삭제/reconcile 불일치 수
- real-model 생성 gate latency와 기존 virtual-model 생성 회귀

Runtime face asset은 사전 빌드된 private 자산을 계속 쓰므로 생성 자체의 모델 추론 시간은 크게 바꾸지 않는 것이 목표다.

## 13. 비범위

- 실제 원화/토큰 송금과 모델 지급
- 별도 국내 liveness/face-match 벤더 계약
- 사용자 업로드 신분증 fallback
- SAM 기반 얼굴 신원 비교
- Orchestrator 상시 운영
- 범용 ABAC/policy engine

## 14. 외부 근거

- AWS Face Liveness 흐름과 결과: <https://docs.aws.amazon.com/rekognition/latest/dg/face-liveness-programming-api.html>
- GetFaceLivenessSessionResults: <https://docs.aws.amazon.com/rekognition/latest/APIReference/API_GetFaceLivenessSessionResults.html>
- AWS 사용 권고와 rate-limit: <https://docs.aws.amazon.com/rekognition/latest/dg/recommendations-liveness.html>
- AWS 데이터 암호화/AI opt-out 주의: <https://docs.aws.amazon.com/rekognition/latest/dg/security-data-encryption.html>
- 모바일 신분증 제공정보 안내: <https://www.mobileid.go.kr/mip/hps/svcIntrcn/svcIntrcnIdnty.do>
