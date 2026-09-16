# 인물 LoRA 자동화 — 운영 스위치와 되돌리기

등록 사진 12장 → 학습 → 채점 → **LoRA 등록(꺼진 상태)** → **테스트컷 12장 자동 생성** →
**관리자가 보정 1종을 골라 4장 전송** → 등록자가 확대 1 + 전신 1 승인 → **LoRA 점등** → 착용컷.

한 건이 GPU 파드를 3~4시간, **약 $11** 쓴다(학습). 테스트컷 생성은 컷을 새로 만들지 않아
**생성 비용 0**, 얼굴 렌더 12회다. 이 문서는 그걸 켜고 끄는 법이다.

## 0. 선행 조건 — RunPod 운영 키 교체 (★ 아직 안 됨)

`FM_LORA_TRAINING` 을 켜기 전에 **반드시** 끝나야 한다.

- SSM `/copilot/wearless/use1/secrets/RUNPOD_API_KEY` 가 **옛 계정**이다.
- 맥의 `~/.runpod/api_key` 는 **401**.

잔액 조회가 fail-closed 라(못 읽으면 시작 안 함) 키가 살아 있지 않으면 큐를 켜도 **한 건도
안 돌고** CRITICAL 알림만 쌓인다.

교체 명령(대표가 직접 실행 — 이 문서는 만들어만 둔다):

```bash
# 1) RunPod 콘솔에서 새 API 키 발급(운영 계정)
# 2) SSM 에 넣는다 (us-east-1)
aws ssm put-parameter --region us-east-1 \
  --name /copilot/wearless/use1/secrets/RUNPOD_API_KEY \
  --value '<새 키>' --type SecureString --overwrite
# 3) 맥 쪽도 같은 키로
printf '%s' '<새 키>' > ~/.runpod/api_key && chmod 600 ~/.runpod/api_key
# 4) api 재배포(태스크가 SSM 을 기동 시점에 읽는다)
```

교체 뒤 **실측 확인이 남아 있다**: 잔액 조회는 REST v1 에 엔드포인트가 없어
구 GraphQL `{ myself { clientBalance } }` 를 쓰는데, 살아 있는 키로 확인한 적이 없다.

```bash
curl -sS "https://api.runpod.io/graphql?api_key=$(cat ~/.runpod/api_key)" \
  -H 'content-type: application/json' \
  -d '{"query":"{ myself { clientBalance } }"}'
```
숫자가 나오면 그대로 간다. 안 나오면 `FM_LORA_MIN_BALANCE_USD: "0"` 으로 검사를 끄고
(비상구) 잔액은 콘솔에서 사람이 본다.

## 1. 켜기

`copilot/api/manifest.yml` 한 줄:

```yaml
FM_LORA_TRAINING: "off"   →   "on"
```

같이 보는 값:

| 변수 | 기본 | 뜻 |
|---|---|---|
| `FM_LORA_TRAINING` | `off` | 큐를 돌릴지. off 면 RunPod 를 아예 호출 안 한다 |
| `FM_LORA_MIN_BALANCE_USD` | `20` | 파드 만들기 전 최소 잔액. **0 이하면 검사 끔** |
| `FM_LORA_MAX_SECONDS` | `18000` | 한 런 상한(5시간). 넘으면 실패 + 파드 삭제 |
| `FM_TEST_CUT_BUILD` | `off` | 테스트컷 12장 자동 생성 큐를 돌릴지 |
| `FM_TEST_CUT_SOURCE_CLOSEUP` | (빈값) | 기준 원본 확대샷 **2장**의 R2 키(쉼표) |
| `FM_TEST_CUT_SOURCE_FULLBODY` | (빈값) | 기준 원본 전신샷 **2장**의 R2 키(쉼표) |
| `FM_TEST_CUT_POD_WAIT_SECONDS` | `1200` | 얼굴 파드를 기다리는 상한(20분). 넘으면 실패 |

학습 카드 우선순위는 코드에 있다(`services/lora_train_pod.GPU_PRIORITY`) — 싼 것부터
A100 80GB $1.59 → RTX PRO 6000 $2.09 → H100 80GB $3.49.

## 1-2. 테스트컷 12장 자동 생성

**보정 3종** — 효과가 사람마다 갈려서 하나를 정하지 않고 셋을 다 만들어 보고 고른다
(2026-09-16 실측: v7 은 네 컷 모두 피부 결 +11~19%, v6 는 sc_1 0.52→0.50 · sc_2 0.55→0.44
하락, hz_d7 만 0.56→0.66 상승).

| 코드 | 화면 이름 | 업스케일러 | 네거티브 |
|---|---|---|---|
| `prod` | A 매끈하게(지금 기본) | 100% | 끔 |
| `texture` | B 결 살리기 | 100% | 켬 |
| `soft50` | C 중간 | 50% | 끔 |

`prod` 는 **지금 운영과 바이트 동일**하다(넘길 인자가 없다). `soft50` 이 게이트에 떨어지면
`prod` 로 한 번만 다시 그린다 — `texture` 로는 안 올린다(네거티브가 켜지면 다른 상품이다).

### 기준 원본 컷 — 고정 자산이다

12장 = **고정 원본 4장**(확대 2 + 전신 2) × 보정 3종. 사람이 바뀌어도 원본은 그대로여야
사람 간 비교가 된다. **컷 생성(gpt-image)은 하지 않는다** — 같은 원본에 얼굴만 세 번 다시
그린다.

원본을 갈아 끼우는 법:

```bash
# 1) 얼굴 전용 비공개 R2 에 새 원본을 올린다(키는 마음대로, 아래 env 와 맞추기만 하면 된다)
#    권장 자리: private/facemarket/test-cut-sources/<이름>.png
# 2) copilot/api/manifest.yml 의 두 줄을 새 키로 바꾸고 배포
FM_TEST_CUT_SOURCE_CLOSEUP: "private/facemarket/test-cut-sources/close_a.png,.../close_b.png"
FM_TEST_CUT_SOURCE_FULLBODY: "private/facemarket/test-cut-sources/full_a.png,.../full_b.png"
```

종류마다 **정확히 2장**이어야 한다. 아니면 런이 시작조차 안 한다(반쪽 묶음을 만들면 그
보정은 영영 못 보낸다).

### 언제 도는가

- **자동**: `fm_model_loras` 에 `status='ready'` 행이 붙는 순간 큐에 한 건 들어간다
  (`services/model_lora.register`). 손으로 seed 하든 학습이 끝나든 자리는 거기 하나다.
- **수동·다시 생성**: 관리자 콘솔 모델 상세 → 테스트컷 → **자동 생성 / 다시 생성**
  (`POST /v1/facemarket/admin/models/{id}/test-cuts/build`).
  다시 생성은 **승인 전 컷만** 갈아 끼운다 — 승인된 원본은 건드리지 않는다.

파드가 없는 건 실패가 아니라 대기다. 이 런 자체가 수요라 `face_autoscale` 이 보고 켠다.

### 결과 읽기

`fm_test_cut_builds` 한 행 = 한 런. 관리자 화면 배지가 그 값이다.

| status | 뜻 | 다음 |
|---|---|---|
| `queued` / `running` | 대기 / 생성 중 | 몇 분 기다린다 |
| `done` | 12장 다 됐다 | 셋을 비교해 하나를 보낸다 |
| `partial` | 일부만 됐다(`produced/requested`) | 된 것만 저장됐다. **다시 생성**을 누른다 |
| `failed` | 0장 — 두 번 해 보고도 안 됐다 | `error` 를 본다(파드 미기동·기준 원본·LoRA 부재) |

`partial` 은 자동 재시도하지 않는다 — 같은 게이트에 또 떨어질 값이면 돈만 나간다.

## 2. FACE_AUTOSCALE 전환 계획

지금 `FACE_AUTOSCALE: "off"` 다. 그 주석의 이유(자동 생성 파드가 스스로 부팅 못 함)는
**해소됐다** — 2026-09-15 `POD_BOOT_SCRIPT` 가 R2 묶음을 직접 받아 깔고, 그 묶음에
`face_mask_lock.py` 가 들어간 뒤(PR #314) 실제로 부팅한다.

전환 순서:

1. 키 교체(위 0번)가 먼저다. 어댑터는 키가 없으면 클라이언트도 안 만든다.
2. 수동으로 파드를 하나 만들어 `/healthz` 가 뜨는지 본다(자동 생성 경로와 같은 부팅 스크립트).
3. `FACE_AUTOSCALE: "on"` 으로 바꾸고 배포.
4. 30분 관찰: 파드가 만들어지는지, 유휴 10분에 꺼지는지, `fm_face_render_pod` 행이 한 줄인지.
5. 안 되면 `"off"` 로 되돌린다 — 그 사이 켜진 파드는 RunPod 콘솔에서 직접 지운다.

## 3. 전체 1회 점검

아래를 순서대로 확인한다. 각 줄이 **다음 줄의 전제**다.

- [ ] 등록자가 사진 18칸을 올린다
- [ ] 관리자 콘솔 "사진 확인" 탭에 뜬다 → 전체 사진을 보고 **확인 완료**
- [ ] `fm_lora_training_runs` 에 `queued` 한 건을 넣는다(지금은 손으로 — 자동 큐잉은 다음 회차)
- [ ] 60초 안에 `preparing` → `training` 으로 바뀌고 `pod_id`·`gpu_type` 이 채워진다
- [ ] **두 번째 런을 큐에 넣어도 안 집힌다**(동시 1건 인덱스)
- [ ] 3~4시간 뒤 `scoring`, R2 에 체크포인트 6개 + `result.json`
- [ ] 채점이 합격선(개별 중앙 0.70) 을 넘는다 → `fm_model_loras` 행 생성, **`enabled=false`**
- [ ] **파드가 사라졌다**(RunPod 콘솔 0대)
- [ ] `fm_test_cut_builds` 에 한 건이 저절로 들어간다(ready 행이 붙는 순간)
- [ ] 60초 안에 `running` → 몇 분 뒤 `done`, `fm_model_test_cuts` 12행
- [ ] 12행이 보정 3종 × (확대 2 + 전신 2)로 갈린다(`skin_finish_code`)
- [ ] 관리자 콘솔에 보정별 3묶음으로 뜬다 → 셋을 비교해 **하나를 보낸다**
- [ ] `fm_models.skin_finish_code` 가 보낸 값으로 바뀐다(**보내기와 같은 트랜잭션**)
- [ ] 등록자 화면에 **그 4장만** 보인다(보정 이름표는 없다) → 확대 1 + 전신 1 승인
- [ ] 승인과 **같은 트랜잭션에서 LoRA 가 켜진다**
- [ ] 셀러 컷에 그 보정으로 얼굴이 적용된다(`face_recipe` 의 `skin_finish` 가 컷 원장에 남는다)
- [ ] 얼굴 렌더 파드도 유휴 뒤 정리된다

## 4. 되돌리기

| 증상 | 되돌리기 |
|---|---|
| 학습이 돈을 먹는다 / 파드가 안 지워진다 | `FM_LORA_TRAINING: "off"` + RunPod 콘솔에서 파드 직접 삭제 |
| 잔액 조회가 계속 실패 | `FM_LORA_MIN_BALANCE_USD: "0"`(검사 끔) — 잔액은 사람이 본다 |
| 얼굴 합성 경계가 이상하다 | `FACE_MASK_LOCK: "false"` (옛 합성 방식으로) |
| 보정 때문에 컷이 비어 나간다 | 해당 모델 `fm_models.skin_finish_code = 'prod'` (지금 운영으로) |
| 테스트컷 생성이 파드를 붙든다 | `FM_TEST_CUT_BUILD: "off"` — 학습은 그대로 돈다 |
| 기준 원본이 잘못됐다 | `FM_TEST_CUT_SOURCE_*` 를 바꾸고 배포 → **다시 생성** |
| 파드가 계속 만들어진다 | `FACE_AUTOSCALE: "off"` |

전부 **env 한 줄 + 재배포**다. 마이그레이션을 되돌릴 일은 없다 — 추가만 했고 기본값이 지금 동작이다.
보정은 컬럼을 **새로 더했다**(`skin_finish_code`) — 옛 `skin_finish` 는 그대로 있고, 그 컬럼만
아는 옛 코드는 기본값 100(=지금 운영)을 계속 읽는다.
