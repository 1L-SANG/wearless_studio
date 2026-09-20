# 옆·뒷모습 컷의 머리 교체 (각도 교체)

2026-09-20 실측으로 확정. 정면·3/4 컷은 그대로 Qwen LoRA 얼굴 패스 + Sunburst 목선 보정(PR #336)을 쓰고,
**옆·뒷모습 컷만** 등록자 각도 실사진으로 머리를 다시 그린다.

## 왜 따로인가

- v7 LoRA 는 정면·3/4 만 배웠다. 얼굴 패스는 yaw > 0.65 인 그림을 건너뛰므로 옆모습 컷은 곧 빈 컷이 된다.
- 뒷모습은 얼굴이 아예 안 잡혀 얼굴 패스가 시작도 못 한다. 참고 없이 그리면 남의 뒤통수가 나온다.
- 정면 사진에서 옆·뒤를 지어내는 방법(각도 LoRA·영상 턴어라운드·3D)은 전부 추측이라 신원이 안 맞았다
  (2026-09-19 조사). **그 각도의 실사진이 답이다.**

## 실측 (2026-09-20, GPT 베이스컷 8장 + 등록자 실사진)

- 옆모습 4/4: 머리 모양·귀·볼 피부가 본인. 옷 변화 0~51px. 이음새 없음.
- 뒷모습 4/4: 얼굴을 그려 넣지 않음. 두상·귀·뒷목선이 본인 쪽. 옷 변화 156~228px(칼라 가장자리).
- BFS 강도 스윕: **1.0 을 쓴다**(육안 판정). 1.2 는 닮음 점수만 올랐고(0.333→0.41) 머리가 부풀고 앞머리가
  두꺼워졌다. 1.4 는 더 멀어졌다(0.25). 합격 판정을 받은 옆 4컷·뒤 4컷이 전부 1.0 이다.
- 왼쪽·오른쪽 옆모습 모두 확인. 참고 사진은 컷이 보는 쪽과 같은 칸을 쓰고 반전하지 않는다.
- 옆·뒤의 동일인 점수(SFace)는 0.1~0.4 로 의미가 없다 — **눈으로 확인한다**(머리 모양·귀·목덜미·코·턱선).

## 흐름

1. `cut_generator.generate()` 가 provider 이미지를 받은 뒤, 컷 방향이 side·back 이면 얼굴 패스 대신
   `face_angle_swap.swap()` 으로 간다(`_angle_swap_direction`). 이 경로의 프롬프트는 옆모습을 3/4 로
   바꾸지 않는다(`DIR:side_identity` 미사용) — 진짜 옆모습을 주문한다.
2. 머리 자리 찾기 (SAM 없이)
   - 옆: YuNet 얼굴 박스를 키운 창 안의 전경, 얼굴과 이어진 덩어리만.
   - 뒤: 위쪽에서 가장 큰 어두운 덩어리(머리카락). 어두운 옷과 붙으면 목에서 자른다.
   - 배경 문턱 50 — 벽 그림자(차이 12)는 떨어지고 피부(82)·옷(130)·머리(206)는 남는다.
3. 머리카락 여유(옆 0.25 · 뒤 0.30 × 머리 너비)를 준 마스크, 머리 크기 1.9 배 정사각 크롭 → 1024.
4. ComfyUI: Qwen-Image-Edit-2511 + BFS Head V5 + LanPaint(20 step, cfg 2.5, 강도 1.0), 그림1 = 크롭,
   그림2 = 등록자 각도 사진.
5. 마스크 안만 원본 해상도로 합성하고 옷은 원본 픽셀로 되돌린다(경계 2px 만 섞음).

## 등록 사진 칸

| 컷 | 칸 | 비고 |
|---|---|---|
| 옆모습(코가 화면 왼쪽) | `sh_side` | |
| 옆모습(코가 화면 오른쪽) | `sh_side_right` | 컷의 얼굴 박스 위치로 자동 선택 |
| 뒷모습 | `sh_back` | |

`identity_source.resolve_angle_photos()` 가 `fm_biometric_enrollment_photos` 에서 R2 키를 읽고,
워커가 바이트를 읽어 `generate(angle_swap=...)` 로 넘긴다. **선택 자산이다** — 조회·읽기가 실패해도 잡은
계속 돌고, 사진이 없는 각도의 컷만 실패한다.

## 실패 계약

얼굴 패스와 같다. 머리를 못 찾거나(`no_head`·`no_face`·`no_person`), 각도 사진이 없거나(`no_angle_photo`),
파드가 실패하면(`backend_error`·`graph_error`) **원본을 내보내지 않고** `AngleSwapUnavailable` 을 올린다.
원본은 provider 가 그린 남의 머리라서, 나가면 셀러가 사지 않은 사람이 상품 페이지에 실린다.

## 설정

| 이름 | 기본 | 뜻 |
|---|---|---|
| `FACE_ANGLE_SWAP_ENABLED` | false | 이 경로를 켠다 |
| `FACE_ANGLE_BACKEND_URL` | 없음 | ComfyUI 파드 주소 **폴백**. 살아 있는 파드가 있으면 그쪽이 이긴다 |
| `FACE_ANGLE_BACKEND_TOKEN` | 없음 | 파드 Bearer 토큰 |
| `FACE_ANGLE_SEED` | 42 | 렌더 시드 |
| `ANGLE_AUTOSCALE` | off | 파드 온디맨드 기동/종료 |
| `ANGLE_AUTOSCALE_IDLE_MINUTES` | 20 | 이만큼 일이 없으면 끈다 |
| `ANGLE_AUTOSCALE_START_GRACE_MINUTES` | 20 | 켠 뒤 이 안에 안 뜨면 내린다(요금 방지) |
| `ANGLE_RUNPOD_POD_ID` | 없음 | 파드 id 초기값. 정본은 `fm_angle_render_pod` |

## 파드 운영 (services/angle_autoscale.py)

얼굴 패스 파드(2509 diffusers 렌더 서비스)와 **다른 파드**다. 둘 다 8000 포트를 쓰기 때문에
한 파드에 못 얹는다 — 얼굴은 `POST /render`, 각도는 ComfyUI 의 `POST /prompt` 다.

기동·종료·재고 폴백·알림은 얼굴 파드와 **같은 어댑터**(`face_autoscale.RunpodAutoscaleAdapter`)가
한다. 다른 것만 `PodProfile` 에 담아 끼운다:

| | 얼굴 렌더 | 각도 교체 |
|---|---|---|
| 코드 묶음 | `face_render/<sha>.tgz` | `comfy_angle/<sha>.tgz` |
| 주소 | `https://<pod>-8000.proxy.runpod.net/render` | `https://<pod>-8000.proxy.runpod.net` |
| 준비 판정 | `/healthz` 의 `base_loaded` | `/healthz` 의 `comfy` |
| 파드 표 | `fm_face_render_pod` | `fm_angle_render_pod` |
| 수요 | 켜진 LoRA 등록자의 착장 컷 잡 | 그중 **옆·뒤를 만드는 잡만** |

파드 이미지·부팅 스크립트·토큰 주입(RunPod Secret 참조)은 같다. 묶음은
`server/deploy/comfy_angle/` 이고 CI 가 커밋 sha 로 R2 에 올린다(deploy-server.yml).

수요에서 방향을 가리는 이유: 정면 컷만 만드는 잡이 시간당 $2 짜리 GPU 를 켜면 그 파드는
아무것도 하지 않는다. `detail_page` 는 컷 묶음이라 옆·뒤가 섞였다고 보고 세고, `editor_image` 는
페이로드의 `direction` 이 side·back 일 때만 센다.

콜드스타트는 얼굴 파드보다 길다 — ComfyUI 설치 + 2511/2509 가중치 내려받기로 2026-09-20 실측
약 10분이다. 워밍 핑은 두지 않았다: 셀러가 모델을 고르는 순간에는 옆·뒤를 만들지 알 수 없다.

## 남은 일

- `ANGLE_AUTOSCALE=on` 으로 켜기 전에 **파드 1대로 실측 1회** — 자동 생성된 파드가 묶음을 받아
  ComfyUI 까지 뜨는지, `/healthz` 의 `comfy` 가 true 로 바뀌는지. 손으로 만든 파드로는 통과했지만
  묶음을 R2 에서 받는 경로는 아직 안 돌려 봤다.
- 왼쪽 옆모습은 참고 사진의 앞머리가 눈을 덮어 결과도 그렇게 나온다 — 촬영 안내에 반영할지 결정.
- 턱선은 어느 설정에서도 본인보다 날렵하다(베이스 몸이 가상모델). 개선하려면 베이스 컷 단계가 필요하다.
