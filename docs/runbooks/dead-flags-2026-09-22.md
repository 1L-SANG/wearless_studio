# 꺼져 있는 스위치 전수조사 (2026-09-22)

`off`·`false` 로 박혀 있는 스위치가 여럿이라 "이건 안 쓰는 건가, 켤 건가"가 매번 헷갈렸다. 여섯 개를 전수로 뒤져 **죽은 것 / 켤 것 / 켤 계획 없지만 코드는 남은 것**으로 갈랐다. 매니페스트의 각 줄에도 같은 내용이 주석으로 붙어 있다.

## 결론

| 스위치 | 판정 | 지금 상태 |
|---|---|---|
| `MANNEQUIN_QC_ENABLED` | **죽었다 — 제거함** | 2026-09-22 삭제. 켜도 코드가 안 봤다 |
| `FM_LIVENESS_ENABLED` | 되살릴 계획 없음 | 코드는 남음. 제거는 발표 뒤 |
| `FM_FACE_MATCH_ENABLED` | 되살릴 계획 없음 | 코드는 남음. 제거는 발표 뒤 |
| `GARMENT_QC_MODE` | 되살릴 계획 없음 | 코드는 남음. 제거는 발표 뒤 |
| `FM_LORA_TRAINING` | **켤 것** | off = 돈 새는 걸 막는 안전장치 |
| `FM_TEST_CUT_BUILD` | **켤 것** | 위와 같은 성격 |

## 제거한 것 — `MANNEQUIN_QC_ENABLED`

`mannequin_job.gate_decision` 은 2026-07-12 사고(공유 DB 를 폴링하던 `QC=true` 프로세스가 사용자 잡을 가로채 전멸) 뒤로 `pillow_reject` 를 상수 `False` 로 못 박아 뒀다. **스위치를 `true` 로 바꿔도 아무 일도 일어나지 않는 상태**로 두 달을 있었다. 매니페스트·config·테스트·문서에서 걷어냈다.

Pillow 판정 자체(로그·관측)는 그대로다. 되살리려면 `scripts/qc_calibrate.py` 로 임계를 재캘리브한 뒤 `gate_decision` 의 `pillow_reject` 를 되살린다 — 스위치를 다시 만들 일이 아니다.

## 남긴 것과 이유 — 셋 다 "플래그"가 아니라 "기능"이었다

세 개는 지우려면 플래그 한 줄이 아니라 그 기능이 흐르는 경로를 통째로 들어내야 한다. 9/30 발표 전에 QA 할 바로 그 경로라 미뤘다.

### `FM_LIVENESS_ENABLED` (AWS Rekognition Face Liveness)
관리자 사진 심사(`FM_ENROLLMENT_REVIEW`)가 이 역할을 대신하기로 확정(2026-09-22). 제거하면 같이 빠지는 것:

- `server/app/facemarket_enrollment.py` 96곳 — 세션 생성·결과 조회·브라우저 자격증명 assume, `/enrollments/{id}/liveness-session` 라우트
- 테스트 218곳(`test_facemarket_biometric_enrollment.py` 132, `test_facemarket_photo_recovery.py` 34 …)
- 프론트 23곳 + `FaceLivenessStep.jsx` + npm `@aws-amplify/ui-react`·`ui-react-liveness`·`aws-amplify`(2.3MB, lazy chunk라 런타임 비용은 0)
- env 5개(`FM_LIVENESS_REGION`·`FM_LIVENESS_CONFIDENCE_THRESHOLD` …) + IAM 역할 시크릿 `FM_LIVENESS_BROWSER_ROLE_ARN`
- DB: `fm_biometric_enrollments.liveness_session_digest` 컬럼·유니크 인덱스, `liveness_pending` 상태값(CHECK 제약)

prod 에 `liveness_pending` 행은 **0건**(2026-09-22 확인) — 상태값을 CHECK 에서 빼는 마이그레이션은 안전하다.

### `FM_FACE_MATCH_ENABLED` (신분증 초상 ↔ 등록 사진 기계 대조)
사람 심사로 대체 확정(2026-09-22). 게이트 4곳 + 매칭 루프 + 임계값 3개(`FM_ID_LIVE_THRESHOLD`·`FM_SIDE_LIVE_THRESHOLD`·`FM_RETOUCHED_LIVE_THRESHOLD`) + `FM_MATCH_POLICY_VERSION` + 실패 사유 `face_match_failed`(프론트 문구 포함)가 딸려 있다. 끈 상태에서는 심사 카드에 `faceMatch: "disabled"` 로 남는다.

### `GARMENT_QC_MODE` (구 best-of)
`IMAGE_QC`·`MANNEQUIN_PANTS_QC`·`MANNEQUIN_AXIS_QC` 하드게이트가 같은 일을 하게 되면서 2026-08-17 부터 off. 컷마다 후보 2장을 더 생성하던 중복 비용이라 되살릴 이유가 없다.

**함정**: 매니페스트에서 이 줄만 지우면 코드 기본값 `"bestof"` 로 떨어져 **되살아난다**(= 컷마다 생성 3배). 지우려면 `image_qc.best_of` 와 호출부(상세페이지·에디터 워커)까지 같이 가야 하는데, **테스트 90개(13파일)가 그 함수를 주입 지점으로 쓴다** — 지우면 상세페이지·에디터 테스트를 다시 설계해야 한다. 2026-09-22 에 실제로 해보고 되돌렸다.

## 남긴 것 — 켤 예정이라 지우면 안 되는 것

`FM_LORA_TRAINING` · `FM_TEST_CUT_BUILD` 는 2026-09-16 에 들어온 신규 기능이다(PR #322~#327, #330). `off` 는 "안 쓰는 기능"이 아니라 **RunPod 파드가 켜진 채 잊히면 하루 $38~84 가 나가는 걸 막는 안전장치**다. 켜는 절차는 `docs/runbooks/facemarket-lora-automation.md`.

## 발표 뒤 할 일

1. 라이브니스 완전 제거 — 서버·프론트·npm 3개·IAM 역할·env 5개 + `liveness_pending`·`liveness_session_digest` 마이그레이션
2. 얼굴 매칭 제거 — 게이트 4곳·임계 3개·정책버전·`face_match_failed`
3. best-of 제거 — `image_qc.best_of` + 호출부 + 테스트 90개 주입 지점 재설계
