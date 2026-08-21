# sam2 온디맨드 기동/종료 — 설계 (2026-08-21)

> 정본. 오너 확정: 2026-08-21 (유휴 30분 · 모든 사진 업로드 트리거 · 이메일 알림 `dlftkd3269@gmail.com`).
> Codex 설계 검토 1회 반영(2026-08-21). 미해결 결정은 §0에 모아 뒀다.

## 0. 오너 확인이 필요한 것

| # | 항목 | 내 권고 | 왜 |
|---|---|---|---|
| 1 | **Phase 0을 먼저 한다** (§3) | **필수** | 이걸 빼면 온디맨드가 첫날부터 깨진다. 원래 "범위 밖"으로 뒀던 판단이 틀렸다 |
| 2 | 하루 상한 강제 종료 | **넣지 않음**(알림만) | 강제로 꺼도 다음 reconcile이 되켜서 스래싱. 최악 노출 $5.5/일 |
| 3 | 마이그레이션 2건(인덱스) | 필요 | 매분 전역 스캔을 막는다. 새 테이블은 없다 |

---

## 1. 왜 하는가 — 실측

Cost Explorer 실측(크레딧 상계 전 UnblendedCost, 계정 439328746001 / ap-northeast-2):

| 시점 | 일 비용 | ECS 부분 |
|---|---|---|
| ~2026-08-11 | $1.75 | $0.81 |
| 2026-08-12 (sam2 배포) | $5.89 | $4.84 |
| 2026-08-13~ | **$7.36** | **$6.29** |

`sam2` 한 서비스가 청구서의 **76%**(월 ~$169). 그런데 `jobs` 30일 실측으로 SAM 계열 잡은
**62건 / 2.8시간** — 월 730시간 중 **0.4%**만 일한다. 세그멘테이션 1건당 실효 $2.7.

크레딧 잔액이 2026-08-21 기준 약 $100이라 현 속도로 13일. **2026-08-21 오너 승인으로 desired-count 0**
으로 내려 일 $7.36 → ~$1.71(크레딧 ~58일)을 확보했다. 이 문서는 그 임시 상태를 **기능을 살린 채로**
대체한다.

### 채택하지 않은 대안

- **Fargate Spot** (월 ~$118 절감): Spot 용량이 없으면 ECS가 온디맨드로 대체하지 않아 **공백에 상한이 없다.**
- **sam2 영구 종료**: 색감 조정·매칭 누끼를 포기하는 것이라 대안이 아니다.
- **4 vCPU → 2 vCPU**: `copilot/sam2/manifest.yml:45` 실측 주석이 "지연 2배(~50초/뷰)로 preprocessing 이
  baseline QC decision 을 지난다"고 경고한다. 재측정 없이 건드리지 않는다.

---

## 2. 핵심 원리

`sam2` 를 평소 0대로 두고, 필요해질 때 1대를 띄우고 유휴 30분 뒤 0대로 내린다.

**두 가지를 분리한다:**

| | 무엇 | 실패하면 |
|---|---|---|
| **Reconciler** | 진실의 원천. 60초마다 "지금 몇 대여야 하는가"를 계산해 실제와 맞춘다(양방향) | 다음 주기에 자동 복구 |
| **Prewarm 훅** | 지연 최적화. "60초 기다리지 말고 지금 켜라" | Reconciler가 60초 안에 덮는다 |

Reconciler가 양방향이라 훅 실패·AWS 오류·수동 변경이 전부 60초 안에 수렴한다.

---

## 3. Phase 0 — 전제조건 (이게 없으면 나머지는 무의미)

> **원래 이 항목을 "범위 밖"으로 뒀다. 틀린 판단이었고 Codex 검토로 뒤집혔다.**

### 3.1 문제

sam2가 꺼져 있다가 켜지는 데 **101초 + 모델 로드**가 걸린다. 그동안 들어온 SAM 잡은 실패한다.
그런데 세 SAM 잡 중 **둘은 자동 재시도가 없다.**

| 잡 | SAM 장애 시 | 자동 재시도 |
|---|---|---|
| `editor_garment_mask` | `done/unavailable` | **있음** — 4회 / 285초 |
| `sam_preprocess` | `error/unavailable` (`sam_preprocess_job.py:63`) | **없음** |
| `matching_cutout` | `error/unavailable` (`matching_cutout_job.py:91-119`) | **없음** |

멱등키가 실패한 잡을 그대로 돌려주므로(`repo.py:1173-1177`) 다시 시도되지도 않는다.

### 3.2 이게 왜 치명적인가

도달 경로가 **일상 경로**다:

```
사진 업로드  ──▶ prewarm 훅이 sam2 기동 시작 (101초+)
     │
     │  사용자가 상품 정보 입력…
     ▼
상품 저장  ──▶ sam_preprocess enqueue (routes.py:846)
     │
     ▼
디스패처가 즉시 claim ──▶ SAM 아직 안 뜸 ──▶ error/unavailable ──▶ 영영 안 돎
```

업로드부터 상품 저장까지가 101초보다 짧으면 **매번** 이렇게 된다. 사용자가 사진 몇 장 올리고 바로
저장하면 그 시간은 쉽게 101초 미만이다. 즉 **온디맨드로 바꾸는 순간 `sam_preprocess` 는 사실상 항상
실패한다.** 캐노니컬 컷아웃이 늘 없는 상태가 되고, 마네킹 생성 품질이 조용히 나빠진다.

`matching_cutout` 도 같다 — 매칭 의류를 등록하면 곧바로 enqueue되므로 첫 콜드 기동과 겹친다.

### 3.3 해법

**`sam_preprocess` 와 `matching_cutout` 에 bounded retry 를 붙인다.**
톤 마스크가 이미 쓰는 세대 키 방식(`mask_job_key(..., retry=N)` → `:rN`)과 같은 패턴이다:

- 재시도 대상은 **인프라 장애만** — `SamUnavailable` 과 `TONE_MASK_RETRYABLE_CODES` 에 해당하는 것.
  `no_garment_candidate` 같은 **판정 실패는 재시도하지 않는다**(같은 답이 나온다).
- 예산은 톤 마스크와 같은 값을 쓴다: **4회 / 285초(~5분)**. 콜드스타트 2~2.5분의 2배 여유.
- 잡을 `error` 가 아니라 `done` + `state: unavailable` 로 끝낸다 — 톤 마스크가 이미 그렇게 한다
  (`editor_garment_mask_job.py:128-133` 의 주석이 이유를 설명한다: *"error 잡은 디스패처가 재시도하지
  않고 멱등키를 문 채 종착"*).
- 재시도를 **누가 미는가**가 톤 마스크와 다르다. 톤 에디터는 사용자가 상태를 폴링해서 밀지만,
  `sam_preprocess`·`matching_cutout` 은 폴링하는 화면이 없다. **Reconciler와 같은 백그라운드 태스크가
  민다** — 재시도 대상 잡을 찾아 백오프가 지났으면 다음 세대를 enqueue한다.

### 3.4 이건 온디맨드와 무관하게도 이득이다

지금도 sam2를 **재배포할 때마다** 같은 공백이 생기고, 그때 들어온 잡은 이미 영구 누락되고 있다.
Phase 0은 그 기존 결함도 함께 닫는다.

---

## 4. 켜는 조건 (want = 1)

셋 중 **하나라도** 참이면 1대여야 한다.

| # | 조건 | 출처 |
|---|---|---|
| 1 | SAM 계열 잡이 `pending` 또는 `running` | `jobs` |
| 2 | 마지막 SAM 계열 잡 `finished_at` < 30분 전 | `jobs` |
| 3 | 마지막 업로드 asset `created_at` < 30분 전 | `assets` |

SAM 계열 = `sam_preprocess`, `matching_cutout`, `editor_garment_mask`.

### 4.1 조건 2가 없으면 안 되는 이유

"어제 만든 프로젝트를 오늘 열어 색감 조정" 경로에는 새 업로드가 없다. 이때 톤 에디터 진입이
`editor_garment_mask` 를 enqueue하고(`routes.py:2211`), 디스패처가 즉시 claim해 **1초 만에**
`done/unavailable` 로 끝낸다. 60초 뒤 reconciler 가 볼 때는 `pending`도 `running`도 아니다.

조건 2가 이 경우를 잡는다. 실패한 잡도 "방금 SAM이 필요했다"는 증거다.

### 4.2 조건 3의 범위 — 알고 받아들이는 비용

`assets.created_at` 은 `/assets/{id}/complete` 에서 기록된다(`routes.py:1831`). 그런데 그 업로드 경로는
상품 사진 전용이 아니다:

| 무엇 | 파일 | SAM 수요 |
|---|---|---|
| 상품 사진 | `AnalysisForm.jsx` | 있음 |
| 커스텀 매칭 의류 (`purpose=custom_match_source`) | `AnalysisForm.jsx:461` | 있음 |
| draft-slot 백업 | `httpAdapter.js:391` | 나중에 있음 |
| 에디터 임의 이미지 삽입 | `Editor.jsx:2039` | **없음** |
| 톤 조정 결과 PNG 저장 | `ToneEditor.jsx:193` | 이미 켜져 있는 세션 |

에디터 이미지 삽입은 **불필요하게 30분을 켠다.** 오너가 *"의류를 하나라도 올리는 순간부터 넉넉히"*
로 명시 결정했으므로 그대로 두되, §11 계측에서 헛기동 비율을 본다. 비율이 높으면
`metadata.purpose` allowlist 로 좁힌다.

---

## 5. 끄는 조건 (want = 0)

§4의 세 조건이 **전부 거짓**일 때만. Reconciler에서만 수행한다 — 라우트는 절대 내리지 않는다.

### 5.1 경쟁 상태 — 왜 별도 상태 테이블을 두지 않는가

Codex가 지적한 경쟁이 실재한다: reconciler가 "잡 0건"을 확인한 뒤, 0을 내리기 직전에 업로드가 들어오면
켜기 요청이 0에 덮일 수 있다.

**그럼에도 상태 행을 두지 않는 이유:**

- 잘못된 상태가 **최대 60초** 산다. 다음 reconcile 이 `assets.created_at`(수 초 뒤 기록됨)을 보고
  want=1 로 되돌린다.
- Phase 0의 재시도 예산이 **285초**다. 60초 공백은 그 안에 흡수된다.
- 즉 **Phase 0이 이 경쟁을 무해하게 만든다.** 상태 행은 Phase 0 없이 갔을 때만 필요했다.

대신 **다중 api 태스크 대비**로 reconciler 는 PostgreSQL advisory lock 을 잡고 돈다(§6.2). 지금 `api` 는
`count: 1`이지만(`copilot/api/manifest.yml:45`), 2대가 되는 날 두 프로세스가 반대 방향으로 밀지 않게
지금 값싸게 막아 둔다.

---

## 6. 어디에 붙이는가

### 6.1 Prewarm 훅

| 자리 | 파일 |
|---|---|
| 업로드 서명 발급 | `routes.py:1729` `create_upload_url` |
| SAM 호출 실패 | `sam_client` 의 `SamUnavailable` 발생 지점 |

둘 다 **fail-open**: AWS 호출이 실패해도 삼키고 원래 응답을 낸다.
프로세스 내 **60초 캐시**로 사진 6장 연속 업로드를 AWS 호출 1회로 줄인다.
`UpdateService` 는 같은 값을 다시 넣으면 no-op이라(태스크 재시작 없음) 캐시는 순전히 호출 절약용이다.

### 6.2 Reconciler — 디스패처에 얹지 않는다

> **초안에서 바뀐 부분.** 기존 60초 스윕에 얹으려 했으나 Codex 지적으로 분리한다.

기존 스윕에 얹으면 안 되는 이유:

- 스윕은 잡 claim **앞에서 동기적으로** 기다린다(`dispatcher.py:84`). ECS/SNS 호출이 느리면 그 반복의
  잡 claim이 통째로 늦어진다.
- 디스패처는 워커를 `await` 한 뒤 다음 반복으로 간다(`dispatcher.py:106`). **긴 비-SAM 잡이 돌면 스윕도
  그만큼 멈춘다** — 엄밀한 60초 주기가 아니다. 마네킹 잡 평균이 130초, `detail_page` 는 563초다.

따라서 `main.py` lifespan 에서 **별도 background task** 로 띄운다:

- 60초 주기, 자체 `try/except` (실패해도 디스패처와 무관)
- boto3 는 동기 SDK이므로 `asyncio.to_thread` 로 격리한다 — `r2.py:8` 의 기존 관례와 같다
- 짧은 AWS 타임아웃(연결 3초 / 읽기 5초). reconciler 가 느려도 잡 처리에 영향이 없다
- advisory lock 을 못 잡으면 그 주기를 건너뛴다

같은 태스크가 Phase 0의 재시도 밀기(§3.3)도 담당한다.

---

## 7. sam2 서비스 찾기 — 이름을 박지 않는다

Copilot 서비스명에 랜덤 접미사가 붙는다(`wearless-prod-sam2-Service-6uWul9L25eM7`). 박아 두면 스택
재생성 시 조용히 깨진다.

**실측 확인(2026-08-21): ECS 서비스에 Copilot 태그가 실제로 붙어 있다.**

```
copilot-application = wearless
copilot-environment = prod
copilot-service     = sam2
```

`ListServices` → `DescribeServices(include=["TAGS"])` 로 **세 태그를 모두** 매칭해 ARN을 찾고 프로세스
수명 동안 캐시한다. 클러스터에 서비스가 2개뿐이라 호출 2회면 끝난다.

- **0개 또는 2개 이상 매칭되면 임의 선택하지 않는다** — 기능 비활성 + 알림 1회.
- `ServiceNotFound` 를 받으면 **한 번** 재탐색한다 (스택 재생성 대응).
- 못 찾아도 요청 경로는 절대 막지 않는다.

---

## 8. 안전장치 — 알림만, 강제 종료 없음

> 최초 제안(하루 6시간 상한 → 강제 종료)에서 바뀌었다. **오너 재확인 필요(§0-2).**

강제 종료를 넣지 않는 이유:

- 강제로 0을 넣어도 다음 reconcile 이 `want=1` 을 다시 계산해 **1로 되돌린다.** 진짜로 막으려면 쿨다운
  latch 를 durable 하게 들고 다녀야 하고, 그러면 정상 사용까지 막는다.
- 실행 중인 SAM 잡을 가진 태스크를 죽이면 그 워커는 transport 실패로 끝난다. **기능 차단**이지
  비용 최적화가 아니다.
- 최악 노출액이 작다. 침묵한 채 24시간 켜져 있어도 **$5.5/일** — 오늘까지의 상태로 돌아갈 뿐이다.

**대신**: reconciler 가 실행 중 태스크의 `startedAt`(`DescribeTasks` 가 그냥 준다)을 읽어
**연속 가동 3시간 초과 + 그 시점에도 `want=1`** 이면 이메일 1회. 누적 시간 테이블이 필요 없다.

> Codex 대안: 연속 3시간·당일 누적 4시간 경고 → 6시간에 **신규 start만 차단하고 진행 중 잡은 drain**,
> KST 자정 해제 + 수동 해제 경로. 더 안전하지만 durable latch 가 필요하다. **1차 범위에서 제외**하고,
> §11 계측에서 헛기동이 실제로 관측되면 그때 넣는다.

### 8.1 이메일

| 사건 | 발송 |
|---|---|
| 연속 가동 3시간 초과 | 가동 1회당 1번 |
| 켜기/끄기 실패(AWS 오류) | 디바운스 10분 |
| sam2 를 태그로 못 찾음 | 1회 |

수신 `dlftkd3269@gmail.com`. **계정에 SNS 토픽이 하나도 없음을 확인했다(2026-08-21).**

**토픽·구독은 Copilot addon(CloudFormation)으로 만든다.** 수동 생성을 하지 않는 이유는 이 저장소가
이미 그것으로 사고를 겪었기 때문이다 — `copilot/api/manifest.yml:222` 의 경고: *"raw
`aws ssm put-parameter` 로 만들면 copilot 태그가 없어 태스크가 못 읽는다(2026-07-17 실경험)"*.

`AWS::SNS::Topic` 과 **별도** `AWS::SNS::Subscription` 리소스로 만든다 — Topic 에 임베드하면 Topic 삭제
시 구독이 함께 지워지지 않을 수 있다.

> 구독 확인 메일은 **배포 시점에** 발송된다. 오너가 확인 링크를 1회 클릭해야 알림이 살아난다.
> 배포 후 테스트 알림 1건을 발송해 도달을 확인한다.

---

## 9. 권한 — 좁게

`copilot/api/addons/sam-autoscale.yml` (CloudFormation) 로 `api` task role 에 붙인다.

| 액션 | 리소스 | 조건 |
|---|---|---|
| `ecs:ListServices` | `*` (ARN 리소스가 없는 액션) | `ecs:cluster` = 이 클러스터 |
| `ecs:DescribeServices`, `ecs:UpdateService` | 이 클러스터의 service ARN 패턴 | `aws:ResourceTag/copilot-service` = `sam2` |
| `ecs:ListTasks`, `ecs:DescribeTasks` | 이 클러스터 | — |
| `sns:Publish` | `!Ref` 로 얻은 토픽 ARN 하나 | — |

`DescribeServices(include=["TAGS"])` 로 태그를 함께 받으므로 `ecs:ListTagsForResource` 는 필요 없다.
`api` 는 다른 AWS 자원에 아무 권한이 없다.

---

## 10. 설정·매니페스트

### 10.1 스위치

`server/app/config.py` 의 기존 `_flag(env, default, allowed)` 관례를 따른다(`config.py:345`):

```python
sam_autoscale: str = "off"          # dataclass 기본값 필수 — 없으면 테스트 fixture 다수가 깨진다
_flag("SAM_AUTOSCALE", "off", {"off", "on"})
```

**off 일 때는 ECS/SNS 클라이언트 생성도, 서비스 탐색도 하지 않는다.** 로컬·테스트는 AWS 호출 0.

프로덕션: `copilot/api/manifest.yml` 의 `variables` 에 `SAM_AUTOSCALE: "on"`.

### 10.2 sam2 매니페스트 — 빠뜨리면 전부 무효

`copilot/sam2/manifest.yml:53` 의 `count: 1` → `count: 0`.

안 바꾸면 **deploy-sam2 워크플로가 돌 때마다 desiredCount 가 1로 복원되어** 상시 가동으로 돌아간다.
지금 손으로 내려둔 0도 같은 이유로 다음 sam2 배포까지만 유효하다.

**Copilot v1.34.1 소스 기준 `Count` 타입은 "0 is a valid value" 로 명시돼 있다** — 검증 거부 가능성은
낮다. 다만:

- 배포 워크플로가 Copilot 을 **버전 고정 없이 `latest`** 로 받는다(`deploy-sam2.yml:107`). **버전을
  고정한다.**
- 배포 전 `copilot svc package` 로 `DesiredCount: 0` 을 확인하는 회귀 게이트를 둔다.
- desired 0 서비스는 새 이미지 digest 를 즉시 확정하지 못하고 1대로 올릴 때 처음 pull 한다. 현재
  워크플로가 **커밋 SHA 태그**를 넘기므로(`deploy-sam2.yml:115`) mutable tag 위험은 없다.
- `deploymentConfiguration` 은 바꾸지 않는다. desired 0 이면 필요한 healthy task 수가
  `ceil(0 × 100%) = 0` 이고, 시작한 task 가 없으니 circuit breaker 의 실패 카운트도 0이다.

**최초 1회는 스택 완료 → 0대 → 1대 기동 → 새 SHA 이미지 실행까지 눈으로 확인한다.**

---

## 11. 계측 — 추정을 실측으로 바꾼다

첫 2주간 다음을 남긴다. **상태 변경과 실패에만 로그를 남기고, 매 주기 성공 로그는 만들지 않는다**
(60초 × 30일 = 43,200행이 된다).

- scale transition (up/down, 사유)
- task running seconds (세션별)
- cold start 실측 (요청 → 첫 SAM 성공)
- 세션 수 / 헛기동 비율 (§4.2 — 에디터 이미지 삽입 등 SAM 수요 없는 업로드가 만든 기동)

목표: **sam2 월 $15 이하.** 2주 뒤 실측으로 재계산한다.

---

## 12. 마이그레이션 — 인덱스 2건 (새 테이블 없음)

매분 전역 `max()` 를 읽는데 현재 인덱스가 맞지 않는다:

- `assets` 는 `user_id`, `project_id` 만 (`init.sql:113`)
- `jobs` 는 `(project_id, kind, status)` 와 pending 전용만 (`init.sql:197,203`)

forward migration 으로 partial index 2건을 추가한다:

1. SAM 계열 잡의 active/finished 조회용
2. `source='upload'` asset 의 `created_at` 조회용

> 마이그레이션 파일은 만들되 **적용은 오너가 직접 실행한다** (이 저장소 관례).

---

## 13. 실패 모드

| 실패 | 결과 | 사용자 영향 |
|---|---|---|
| prewarm 훅 AWS 호출 | reconciler 가 60초 안에 올림 | 최대 1분 추가 대기 |
| reconcile 1회 실패 | 다음 60초 재시도 | 없음 |
| reconciler 태스크 사망 | 자동 기동·종료 정지 | SAM 기능 정지 — 잡 시스템은 살아 있음(분리했으므로) |
| 콜드스타트가 285초 초과 | 재시도 예산 소진 | 그 잡 실패 |
| 태그로 서비스 못 찾음 | 기능 비활성 + 알림 | 없음 |
| advisory lock 경합 | 그 주기 건너뜀 | 없음 |

**어느 경우에도 업로드·분석·마네킹 생성은 멈추지 않는다** — SAM 은 그 경로에서 이미 fail-open이다
(`routes.py:1082-1108`, `mannequin_job.py:159-182`).

---

## 14. 지연 예산

| 구간 | 값 |
|---|---|
| 켜기 명령 → 이미지 pull 시작 | 16초 (실측) |
| pull 완료 | 32초 (실측) |
| uvicorn 기동 | 54초 (실측) |
| **RUNNING + HEALTHY** | **101초 (실측)** |
| 모델 lazy load + 첫 추론 | 미측정 (추정 +30~60초) |
| **합계** | **약 2~2.5분** |

재시도 예산 **285초(~5분)** — 여유 2배. 톤 마스크는 이미 적용됨(`editor_garment_mask.py`, 2026-08-21),
나머지 둘은 Phase 0에서 같은 값을 쓴다.

> 상한 5분은 오너 결정이다 — *"그 이상 걸리면 사용자는 어차피 이탈한다."* 실측이 5분을 넘기면 이 상수를
> 늘리지 말고 **기동 트리거를 더 앞당긴다.**

**중요: `desiredCount=1` 은 "요청을 받을 준비가 됐다"가 아니다.** healthcheck 는 `/health` 만 보고 모델은
lazy load 라(`sam_service/model.py:22-49`), HEALTHY 판정 후에도 첫 요청은 느리다. 이 간극은 §3의
재시도가 흡수한다.

---

## 15. 테스트

`SAM_AUTOSCALE` off 인 기본 상태에서 **기존 2,519 테스트가 그대로 통과해야 한다**(AWS 호출 0).

**Phase 0:**
- `sam_preprocess`·`matching_cutout` 이 `SamUnavailable` 에서 `done`+`unavailable` 로 끝난다(`error` 아님)
- 백오프 경과 후 다음 세대가 enqueue되고, 4회로 멈춘다
- 판정 실패(`no_garment_candidate`)는 재시도하지 **않는다**
- 재시도가 성공하면 원래대로 asset 스왑이 일어난다

**Phase 1:**
- 훅: 이미 1대면 호출 안 함 / 0대면 호출함 / 60초 캐시가 6연속 업로드를 1회로
- 훅: AWS 예외에도 업로드 응답 200
- want=1: 잡 pending / running / finished 29분 전 / asset 29분 전 — 각각
- want=0: 넷 다 거짓일 때만
- 경계: 잡 finished 31분 + asset 31분 + 잡 0건 → 내림
- 양방향: 실제 0인데 want=1 이면 올림
- 태그 매칭 0개·2개 이상 → 비활성 + 알림, 요청 경로 정상
- 연속 3시간 초과 → 알림 1회, 다음 주기 재발송 없음
- advisory lock 을 못 잡으면 AWS 호출 0
- `SAM_AUTOSCALE` 오타·공백 → off 로 떨어짐, 프로덕션 매니페스트 값이 loader 통과

---

## 16. 예상 결과

| | 월 |
|---|---|
| 현재(sam2 상시) | ~$224 |
| sam2 0대(지금 임시) | ~$55 — 기능 없음 |
| **이 설계 적용 후** | **~$69** — 기능 살아 있음 |

sam2 부분만 보면 월 ~$169 → **$10~15**(62잡이 전부 분리돼 각각 30분 tail + 101초 콜드스타트를 만든다는
상한 계산으로 월 35~40시간). 새 비용은 무시 가능하다:

- ECS control-plane 호출: 별도 요금 없음
- SNS 이메일: 월 1,000건 무료 구간 안
- CloudWatch: 상태 변경·실패만 남기면 미미
- **ECS/SNS 용 VPC endpoint 를 새로 만들지 않는다** — 이 호출량에 고정 시간 요금은 손해다

> 이 추정은 "테스터 시기, 하루 1~2세션"을 가정한다. 정식 오픈 후 트리거가 하루 종일 걸리면 사실상 상시
> 가동이 되어 절감이 사라진다. **오픈 전에 §11 계측으로 재판단한다.**

---

## 17. 작업 순서

1. **Phase 0** — `sam_preprocess`·`matching_cutout` bounded retry (§3)
2. 마이그레이션 2건 작성 (§12) — 적용은 오너
3. Copilot addon: IAM + SNS 토픽·구독 (§8.1, §9)
4. `SAM_AUTOSCALE` 설정 + reconciler 백그라운드 태스크 (§6.2, §10.1)
5. prewarm 훅 2곳 (§6.1)
6. `copilot/sam2/manifest.yml` `count: 0` + Copilot 버전 고정 + `svc package` 게이트 (§10.2)
7. 배포 → 구독 확인 메일 클릭 → 테스트 알림 → 0대→1대 기동 눈으로 확인
8. 2주 계측 후 재판단 (§11)

**1~2번만 먼저 해도 독립적으로 이득이다**(§3.4) — sam2 재배포 공백으로 지금도 나고 있는 손실을 닫는다.
