# 인물 LoRA 자동화 — 운영 스위치와 되돌리기

등록 사진 12장 → 학습 → 채점 → LoRA 등록 → 테스트컷 → 등록자 승인 → 착용컷.
한 건이 GPU 파드를 3~4시간, **약 $11** 쓴다. 이 문서는 그걸 켜고 끄는 법이다.

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

학습 카드 우선순위는 코드에 있다(`services/lora_train_pod.GPU_PRIORITY`) — 싼 것부터
A100 80GB $1.59 → RTX PRO 6000 $2.09 → H100 80GB $3.49.

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
- [ ] 테스트컷 4장(확대 100/50/0 + 전신 1) 준비 → 관리자 전송
- [ ] 등록자 화면에 "보정 100/50/0" 이름표가 보인다 → 하나 골라 승인
- [ ] `fm_models.skin_finish` 가 고른 값으로 바뀌고 **같은 트랜잭션에서 LoRA 가 켜진다**
- [ ] 셀러 컷에 그 단계로 얼굴이 적용된다(`face_recipe` 의 `skin_finish` 가 컷 원장에 남는다)
- [ ] 얼굴 렌더 파드도 유휴 뒤 정리된다

## 4. 되돌리기

| 증상 | 되돌리기 |
|---|---|
| 학습이 돈을 먹는다 / 파드가 안 지워진다 | `FM_LORA_TRAINING: "off"` + RunPod 콘솔에서 파드 직접 삭제 |
| 잔액 조회가 계속 실패 | `FM_LORA_MIN_BALANCE_USD: "0"`(검사 끔) — 잔액은 사람이 본다 |
| 얼굴 합성 경계가 이상하다 | `FACE_MASK_LOCK: "false"` (옛 합성 방식으로) |
| 보정 단계 때문에 컷이 비어 나간다 | 해당 모델 `fm_models.skin_finish = 100` (기본으로) |
| 파드가 계속 만들어진다 | `FACE_AUTOSCALE: "off"` |

전부 **env 한 줄 + 재배포**다. 마이그레이션을 되돌릴 일은 없다 — 추가만 했고 기본값이 지금 동작이다.
