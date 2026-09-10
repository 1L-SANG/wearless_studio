# 인물 LoRA(ohwx man) 인수인계 — 2026-09-07 기준 총정리

다른 에이전트가 이 문서만 보고 이어서 운용할 수 있게 쓴다. 이전 경위는 `identity-lora-progress-2026-09-07.md`.

## 1. 목표와 판정 기준

- 목표: FaceMarket 등록 인물이 착용컷에서 **실제 본인과 같게** 나오게 한다. 현재 접근 = **얼굴 inpaint 전용 LoRA**(Qwen-Image-Edit-2509, rank 16).
- 지표: SFace(YuNet 정렬) 코사인, **홀드아웃 실사 대비 중앙값**. 창: yaw ≤ 0.25, 눈간격 40~200px. 측면·후면은 채점 불가(육안).
- 눈금: 실사끼리 0.853 · 타인 0.384(최대 0.71) · prod(Gemini) 인물컷 0.763 · prod 경로로 만든 이 사람 카페컷 6장 0.73.
- **채점 세트 두 벌, 모든 표에 두 열**: (a) 기존 홀드아웃 6장 `scratchpad/person_holdout/` (b) 기존 6 + 신규 홀드아웃 8 = 14장 `~/Downloads/lora_runs/person_holdout_14/`.
- **v5 판정**(14장 기준, prod 카페 6장 중앙값): ≥0.55 진전 / ≥0.64 Gemini 수준. 최종은 블라인드 육안(prod 6 × {Gemini 얼굴, v5 얼굴} 좌우 무작위 + 정답 키 별도, `blind_sheet.py`).
- 규칙: 클라우드 API(Gemini·GPT-image) 호출 0건. 파드에 API 키 두지 않음(R2 는 presigned URL 로만).

## 2. 현재 상태 (2026-09-07 15:00 UTC) — v5 완주, 파드 전부 정지

- **v5 2000 step 완주.** 파드1(`hn8h5jtyb435s0`, $5 계정)이 10:44 UTC step ≈1000 에서 잔액 소진으로 사망 → 파드2 `lora-v5-resume`(`pnzc3bp2rl0iia`, $15 계정, A100 SXM community $1.39/h)에서 R2 의 900 체크포인트+optimizer 로 재개(`Found step 900 in metadata, starting from there`, optimizer 복원됨, cosine 이어짐), 14:05 UTC 종료, 15:00 UTC stop(EXITED). 지출 파드1 $5.0 · 파드2 ≈$6.5 → $15 계정 잔액 ≈$8.5.
- 산출물 전부 맥에 있고 R2 와 md5 대조 완료(15/15 일치, 재수신 0): `~/Downloads/lora_runs/v5_ckpt/` = 체크포인트 150~1950 + 최종 `ohwx_man_v5.safetensors`(295,144,488B) + optimizer.pt + 표본 75장(500/1500/2000 각 16, 1000 은 27 = 파드1 부분 11 포함) + train_v4.log + push.log + yaml.
- 주의: optimizer.pt 는 **step 900 시점**(크기가 안 변해 push·pull 루프가 다시 안 올림). 2000 이후 연장은 가중치만 이어받아 새 런으로.
- 결과·판정 §8, 다음 §9.

## 3. v5 설정 (확정안)

- 데이터 **236쌍** = 기존 크롭 78 + 그 조명증강 78 + 신규 40 + 그 증강 40. 전부 1024² 얼굴폭×3 크롭.
  - target = 크롭, control = 얼굴+머리 타원(YuNet 박스 위 0.6h·옆 0.3w·아래 1.15h)을 σ=0.5×얼굴폭 가우시안 블러, mask = 타원(페더).
  - 증강 = **R/B(전체 mean R/mean B) 목표 역산**(기존: U[1.19,1.43] → 결과 중앙 1.276; 신규: 목표 1.27, 배율 [0.85,1.35] 클립) + 밝기 ×0.8~1.2 + 방향광 0.08~0.20. target·control 동일 파라미터, mask 불변, 파일별 시드 고정. Kelvin 추측 금지(처음 시도는 R/B 1.58 로 과함).
  - 캡션: `ohwx man, {표정}, head and shoulders portrait, {각도/포즈}, {배경}, {조명}, photograph`. 구도는 **전부 head and shoulders portrait**(크롭이므로 full/upper body 는 거짓 라벨 — 38장 교체). 표정 neutral 54/slight smile 19/smiling 5(FaceMesh 기하+재임계), 프로필·미검출 11장은 표정 없음. 조명: 기존 원본 `soft even indoor lighting`, 기존 증강 `warm indoor light`, 신규 = 폴더(해가왼쪽 `direct sunlight from the left`/해가오른쪽 `soft daylight`/그늘 `open shade`/해등지고 `backlit`) + `outdoors`, 신규 증강은 원본과 동일 문구(비대칭, 기록).
- 신규 48 HEIC(`~/Downloads/새로운데이터셋/`, 폴더=조명, 파일명=포즈): sips 변환은 회전 태그를 무시 → 4방향 YuNet 탐색으로 전부 90° 보정. 홀드아웃 8 = 해가왼쪽/정면_무표정·3:4_왼쪽, 해가오른쪽/정면_무표정·3:4_오른쪽, 그늘/정면_무표정_2·3:4_왼쪽, 해등지고/정면_무표정·3:4_왼쪽 (학습 제외, 누출 0 확인).
- yaml `armA_v5.yaml`: name ohwx_man_v5 · `pretrained_lora_path` = v4c-1500 **가중치만**(optimizer 복원 안 함 — 복원하면 step 1500 재개·T_max 3500 이 돼 스펙 위반) · 2000 step · cosine T_max 2000 · lr 1e-4 · save 150 · **sample_every 500** · mask_min 0 · inverted_mask_prior 0.5 · rank 16 · resolution 1024 · quantize false · 표본 16 = 홀드아웃 6 + prod 카페 6 + 표정 지정 4(smiling ×2 / neutral ×2, 측면 없음). 표본 control = `samples_ctrl/`(크롭+블러).
- 학습 중 예상: LR warm restart 라 300 표본이 v4c-1500(0.67)보다 낮은 게 정상, 900~1000 에서 회복, 1500 이후 초과해야 함.

## 4. 파일·버킷 위치

```
~/Downloads/lora_train_v5/{target,control,mask}/   236쌍 + 캡션(txt)      meta_v5.json(증강 파라미터)
~/Downloads/lora_new48/{png,manifest.tsv,meta_new48.json,captions_new40.txt}
~/Downloads/lora_runs/person_holdout_14/            채점 세트 (b)
~/Downloads/lora_runs/v4c_ckpt/                      v4c 체크포인트 10·표본·samples_ctrl(12)
~/Downloads/lora_runs/v5_ckpt/                       v5 전체 산출물(체크포인트 14·optimizer(900)·표본 75·로그) — R2 md5 대조 완료
~/Downloads/lora_runs/v5_launch_kit/                 기동·재개 키트(§5)
R2 wearless-face/lora/ohwx_man_v4c_1500.safetensors  v4c 가중치(281MB)
R2 wearless-face/lora/v5_dataset.tgz                 236쌍+samples_ctrl(421MB)
R2 wearless-face/lora/v5/ohwx_man_v5_*.safetensors   v5 체크포인트 150~1950 + 최종 ohwx_man_v5.safetensors + optimizer.pt(900)
scratchpad(/private/tmp/claude-501/…/80ee5a12…/scratchpad/)  채점·시트 스크립트: score_v5.py, score_v4c.py, score_inpaint.py(feat/cos), blind_sheet.py, blind_final.py, build_v5.py, build_new48.py, expr_classify.py; 결과 v5_score_step*.json, final_scores.json, measure_scores*.json, blind_final.png(+key)
```
R2 자격은 `server/.env`(R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_FACE_BUCKET) — 프로세스 환경에만 싣고 출력·저장 금지. 맥→R2 4~50MB/s, 맥→RunPod 0.15~0.5MB/s(항상 R2 경유).

## 5. 운용 명령 (맥, `~/Downloads/lora_runs/v5_launch_kit/`)

- 새 파드(A100 80GB secure, 컨테이너 디스크 200GB, 볼륨 없음, 이미지 `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`, SSH 키 `~/.ssh/id_ed25519.pub`) 생성 후:
  - 처음부터: `launch_v5.sh <host> <port> 2000` — setup_ci.sh(hf download, venv --system-site-packages, ai-toolkit requirements, ~2~5분) → presign.py 로 데이터·v4c GET URL → v5_go.sh(수신·검증 236/236/236/12·run.py) + push_ckpt.sh(presign_put.py 의 PUT URL 로 체크포인트 R2 push) + 맥 pull_v5.sh.
  - **재개**: `resume_v5.sh <host> <port>` — 같은 세팅 + `presign_get_v5.py`(R2 최신 v5 체크포인트·optimizer GET URL) → `v5_resume.sh` 가 `output/ohwx_man_v5/` 에 놓고 run.py → ai-toolkit 이 파일명 step 부터 자동 재개(cosine 도 step 기준). 손실 ≤150 step.
- 채점: `posevenv/bin/python score_v5.py <step> <samples_dir> <ctrl_dir> <hold6> <hold14>` → 표(두 열)+시트. 표본 파일 `ohwx_man_v5/samples/*_<step 9자리>_<idx>.jpg`, 순서 = yaml 순서.
- 종료 후: 회수 검증(바이트 대조) → stop-pod → 블라인드 시트 → 판정.
- 파드 상태 확인: `chain.log`(단계), `train_v4.log`(진행바), `push.log`(R2 push), `V4_STATUS`/`v4_exit`.

## 6. 이전 런 요약 (수치는 기존 홀드아웃 6 기준)

| 런 | 데이터/control | 최고 | 핵심 |
|--|--|--|--|
| v2 | 57 / 옷 누끼 | 1200: 0.516 | 옷↔얼굴 상충, 없던 옷 전이 실패 |
| v3 | 79 / 옷 누끼 + 얼굴 마스크 | 1500: 0.673 | 마스크가 옷 결합을 끊음. 전체생성 상한 ≈0.67 |
| v4(09-06) | 인물 재렌더 control | 보류 | 프레이밍 불일치(정렬 35/79), zero-shot inpaint 0.47~0.51 |
| v4c | 78 크롭 / 얼굴 블러 | 1500: 0.671 · prod 0.62 · holdout 0.746 | **inpaint 파이프라인 확정**, holdout 상승 중 |
| v5 | 236 / 얼굴 블러 + 조명·신규 | 2000: 0.850 · prod 0.729(14장 0.723) · C 경로 0.703 | **Gemini 수준 도달**(§8), 제품 경로 = Plus 전체생성+합성 |

## 7. 재발 금지 함정

`/workspace` FUSE(파이썬 멈춤, /root 사용) · `huggingface-cli`→`hf download` · tar 추출이 `/root` 소유자를 바꿔 sshd 잠김(`--no-same-owner`, 웹터미널 chown 으로 복구) · `pgrep -f` 자기매칭(패턴 `[x]` 사용, 자기 셸 kill 주의) · zsh 배열/변수로 ssh 옵션 전달 불가(명시) · 48GB GPU 는 bf16 로드 OOM · 체크포인트는 학습 직후 R2 로(맥 경유 금지) · 검증 상수는 파일에서 읽기(v3 크기 하드코딩 사고) · 표본 프롬프트는 크롭 구도에 맞게(전신 장면 유도 버그) · 같은 폴더를 만지는 검증 태스크 2개 동시 실행 금지(크기 불일치·개수 오락가락으로 헛수고) · 회수 검증은 파드 말고 **R2 md5(ETag) 대조**로 · push/pull 루프는 크기 변화로 판단하므로 optimizer.pt 같은 고정 크기 파일은 첫 판만 남음 · 파드 stop 전에 파드 전용 산출물(재시도 PNG 등)부터 회수(볼륨 없는 파드는 stop 즉시 컨테이너 디스크 소멸).

## 8. v5 결과 (2026-09-07)

학습기 표본 16장(Plus 경로), SFace 중앙값. 열 = 기존 홀드아웃 6 / 14장.

| step | holdout 6 | prod 카페 6 (n=5) | 표정 4 | 전체 15 |
|--|--|--|--|--|
| 500 | 0.806 / 0.770 | 0.656 / 0.641 | 0.704 / 0.679 | 0.725 / 0.699 |
| 1000 | 0.837 / 0.796 | 0.654 / 0.647 | 0.722 / 0.712 | 0.725 / 0.707 |
| 1500 | 0.856 / 0.812 | 0.721 / 0.711 | 0.743 / 0.726 | 0.760 / 0.749 |
| **2000** | 0.850 / 0.807 | **0.729 / 0.723** | 0.757 / 0.742 | 0.767 / 0.750 |

- **판정**: 14장 기준 prod 6 중앙 0.723 ≥ 0.64 → **Gemini 수준**(같은 5장의 Gemini 원본 0.744). 홀드아웃은 실사끼리 눈금(0.853)에 도달. v4c-1500 대비 prod +0.10. 탈락 1 = weak_34(생성 얼굴 yaw 0.28~0.38, 매 step 동일 → 표본 프롬프트가 3/4 회전을 유도).
- **제품 경로 C**(Plus 전체생성 + 타원 페더 합성 + 되붙임, `final_c_v5.py`, seed 42+idx) prod 6장: good_front 0.749/0.741 · good_cafe 0.703/0.696 · good_close 0.699/0.687 → 중앙 0.703/0.696(같은 3장 Gemini 원본 0.714/0.773/0.710). weak_sit·weak_full no_face, weak_34 yaw 0.44. Inpaint 파이프라인은 폐기(이전 실측 4/6 탈락, `lora-product-path-plus-not-inpaint`).
- **시드 재시도**(`retry_weak.py`, 약점 3 × seed 101~103, 게이트 = 얼굴 검출 & yaw ≤0.25 & 눈간격 40~200): weak_sit 은 seed 102·103 통과(yaw 0.18/0.13), weak_34 는 얼굴은 나오나 yaw 0.45~0.66(프롬프트 유도), weak_full 은 얼굴이 너무 작음(프롬프트 결함). → 제품은 **게이트 + seed 재시도**로 앉은 컷 구제 가능. 프롬프트는 크롭 구도용 문구로 고정(전신·3/4 문구 금지). 재시도 PNG·점수 json 은 파드 stop 전 회수 못 함(위 요약만 남음).
- **블라인드 시트**: `scratchpad/blind_final.png`(prod 6 × {Gemini 얼굴, v5-C 얼굴} 좌우 무작위), 정답 `blind_final_key.json` — 육안 판정 뒤에 열 것. 표본 시트 `v5_samples_step0500…2000.png`.

## 9. 서비스 배선 (2026-09-08, 브랜치 `feat/face-identity-pass`, 미커밋)

- `server/app/agents/face_identity.py`: CPU 모듈. 기하 = build_v4c.py 복제(`plan_from_box`, 정수 절단 포함) — **build_control 이 v4c 학습 표본 control 6장과 픽셀 단위 동일**(테스트로 고정). `plan_face_pass`(YuNet 최대 얼굴, `upscale`·`low_detail`(>3.0, prod 는 weak_full 4.20 만)) · `build_prompt`(짧은 표본 형식 `"ohwx man, [smiling|neutral expression, ]<각도구>, photograph"`, 각도구 = yaw_proxy <0.16 facing / <0.45 three-quarters / else in profile) · `composite`(링 8px 색보정 → 페더 합성 → 조건부 grain(고주파 σ < 0.85×원본일 때만) → 알파 되붙임, **마스크 밖 픽셀 100% 동일** 테스트) · `check_gate`(결과 얼굴폭 ±30%, 중심 이탈 ≤ 얼굴높이 15% — 기준점은 타원 기하 중심이 아니라 **얼굴 박스 중심**: 타원은 위로 0.225·h 치우쳐 있어 문자 그대로 쓰면 정상 결과도 탈락) · `run_face_pass`(seed 42,43,44,102,103,104 순차, 예외 없음, 폴백+메타).
- 백엔드: `FaceBackend.render(control, prompt, seed)` — `QwenLocalBackend`(`face_identity_qwen.py`, no-torch 가드 예외; EditPlus 25 step · guidance 4 · neg "" · 1024², `cpu_offload` 옵션 = 48GB 카드용) · `HttpFaceBackend`(FACE_IDENTITY_BACKEND_URL, JSON b64) · `NullBackend`.
- 호출 지점: `cut_generator.generate()` 가 Gemini 결과를 받은 직후(`_face_identity_spec`) — provider 불변. 조건 = `FACE_IDENTITY_ENABLED=true` **and** 착용컷 **and** `_face_fits`(얼굴이 실제로 담기는 컷) **and** `virtual_models.json` 항목에 `faceIdentity{loraPath, token}`. 필드는 `_meta` 에 문서화했고 **현재 어떤 모델에도 안 붙어 있음**(누구에게 붙일지 = 사용자 결정; 실존 인물은 JSON 레지스트리가 아니라 DB 라 그 경로는 별도).
- config: `face_identity_enabled`(기본 False) · `face_identity_backend_url` · `face_identity_lora_path`(디렉터리 또는 단일 .safetensors). 기본값에서 기존 동작 불변(테스트).
- 메타(로그 `wearless.face_identity`): seed·attempts·fallback·upscale·low_detail·face_width_in/out·yaw_proxy·elapsed_ms·시도별 게이트 사유·색보정·hf σ·grain.
- 테스트 `server/tests/test_face_identity.py`(34): prod 6장 픽스처 `~/Downloads/lora_runs/prod_inputs/`(레포 밖, 없으면 skip).

## 10. 3단계 — 파드 검증 결과 (2026-09-08 01:36~01:57 UTC, 파드 `ww7rf05ue8r7np` stop)

- GPU: A40 **secure $0.49/h**(community 재고 0, secure 150GB 디스크는 "리소스 부족" → 100GB 로 생성). 지출 ≈ $0.18(20.4분). $15 계정 잔액 ≈ $8.4.
- 메모리: cpu offload 는 컨테이너 cgroup RAM 50GB(모델 CPU 사본 57GB) 에 걸려 불가 → **분할 경로**(`qwen_split_backend.py`): TE 만 GPU 에 올려 sid 별 prompt/negative 임베딩(`_get_qwen_prompt_embeds`, 조건 이미지 384² 리사이즈 동일) 선계산 → 해제 → transformer+VAE 만 GPU 상주(39.2GiB/44GiB) → `pipe(prompt_embeds=…)`. bf16 그대로, 디노이즈·시드·CFG 는 표준 경로와 같은 함수. precompute 47.6s(TE 6.5 + encode 3.3 + transformer 20.4), **렌더 101.9~102.5초/장**(≤150 기준 → A40 완주), 6장 총 675초.
- 6/6 이 seed 42 첫 시도에 게이트 통과(얼굴폭 in→out 편차 ≤ 5%, 중심 이탈 ≤ 0.07·h).

| 입력 | Gemini 원본 | 얼굴패스 f0.12 | f0.25 | v5 표본 S6 | Δ f0.12−Gemini | grain(f0.12) | 색보정 RGB |
|--|--|--|--|--|--|--|--|
| weak_sit | 0.761 | 0.758 | 0.750 | 0.731 | −0.003 | 적용(2.69→1.90) | −0.3/−0.5/−0.6 |
| weak_34 | 0.758 | 탈락(yaw0.43) | 탈락(yaw0.70) | 탈락(yaw0.29) | — | 없음 | +1.6/+0.6/−0.3 |
| weak_full★ | 탈락(eye38) | 0.677 | 0.667 | 0.679 | — | 없음 | +0.8/+0.4/−0.8 |
| good_front | 0.714 | **0.779** | 0.755 | 0.729 | **+0.065** | 없음 | −1.6/−2.1/−2.6 |
| good_cafe | 0.773 | 0.766 | 0.751 | 0.755 | −0.007 | 적용 | −0.8/−1.3/−1.5 |
| good_close | 0.710 | 0.707 | 0.706 | 0.685 | −0.003 | 적용 | −0.6/−1.1/−1.3 |
| **중앙** | 0.758 (n5) | 0.758 (n5) | 0.750 (n5) | 0.729 (n5) | −0.003 (n4) | | |

- 판정(옛 홀드아웃 6 기준, 동일 입력 우열): **Gemini 와 동률**. 우세 1(good_front +0.065) / 열세 3(−0.003~−0.007, 잡음 수준) / 비교불가 2. 학습기 표본(S6 중앙 0.729)보다는 f0.12 가 위(+0.03, 합성 효과 — C 경로 실측과 일치).
- 페더: **0.12 채택**. 0.25 는 생성 헤어 실루엣이 원본과 다른 곳에서 반투명 이중 머리카락 고스트(good_cafe·good_close·weak_sit 우측 경계), 점수도 5/5 로 0.12 ≥ 0.25.
- 발견 2건: ① weak_34 입력 yaw 0.162 가 경계(0.16) 바로 위라 "three-quarters" 프롬프트 → 결과가 원본보다 더 돌아감(yaw 0.43). 경계 재조정(예: 채점 창과 같은 0.25) 또는 히스테리시스 필요. ② 프롬프트에 표정이 없어 Gemini 의 미소(good_front 등)가 **무표정으로 바뀜** — 입력 표정 추정 → `expression` 전달이 다음 개선.
- 산출물: `/Users/nojeong-un/Downloads/lora_runs/face_pass_results/20260908T015637Z/`(control·raw·f12·f25·result.json·scores.json + LABELED_qa_*.png 는 판정 후 볼 것). 블라인드: `~/Downloads/lora_runs/blind_facepass_20260908.png`(6행 × {Gemini, 얼굴패스 f0.12} 좌우 무작위), 키 `blind_facepass_20260908_key.json` 별도. 기존 blind_final.png 불변.

## 11. 회차 2 — 각도 경계·표정 보존 (2026-09-08 02:27~03:36 UTC, 파드 `24j50jvtfe0nj3` stop)

- 배경: 회차 1 블라인드(사용자, 6행) **Gemini 5 : 얼굴패스 1** → 패. 마지막 시도로 두 가지만 고침(머리 길이는 재학습 사안, 미변경).
- 수정 1: 프롬프트 각도 경계 0.16→**0.25**(프롬프트 전용), 게이트에 결과 yaw > max(0.20, 입력×2) → `yaw_drift` 재시도.
- 수정 2: 표정 추정(YuNet 5점 + 입술 색 마스크, 외부 모델 없음). `mc`=(입술마스크 중심−입꼬리)/입폭, `ratio`=입폭/눈간격. 캘리브레이션 = v5 학습 원본 118장(캡션 라벨): 규칙 mc≤0 & ratio≥0.86→smiling / mc≥0.045 & ratio≤0.87→neutral / 그 외 생략 → 무표정→smiling 0/47, smiling→neutral 0/11, smiling 재현 7/11, 무표정 재현 28/47. 치아 비율은 메타만(렌더에서 잡음).
- prod 6장 추정: good_front smiling · good_cafe neutral · weak_full smiling · 나머지 생략(애매).
- 실행: A40 secure, 분할 적재 동일, 102초/장, 6/6 seed 42 통과. **비용 ≈$0.56(예산 $0.5 초과)** — 02:42 완료 후 알림 지연으로 53분 유휴. 잔액 ≈$7.9.

| 입력 | Gemini | 회차2 f0.12 | 회차1 f0.12 | 표정 | 비고 |
|--|--|--|--|--|--|
| weak_sit | 0.761 | 0.758 | 0.758 | — | 동일 |
| weak_34 | 0.758 | 탈락(yaw0.28) | 탈락(yaw0.43) | — | 정면화 개선(0.43→0.28), 창 0.25 에 아깝게 미달 |
| weak_full★ | 탈락 | 0.642 | 0.677 | smiling | **과잉**: 원본 입 다문 옅은 미소 → 큰 웃음 |
| good_front | 0.714 | 0.690 | 0.779 | smiling | 표정은 Gemini(이빨 미소)와 일치, 지표는 하락 |
| good_cafe | 0.773 | 0.761 | 0.766 | neutral | |
| good_close | 0.710 | 0.707 | 0.707 | — | 동일 |
| 중앙 | 0.758 | **0.707** | 0.758 | | Δ 중앙 −0.007, 우세 0/열세 4 |
| 컷 간 일관성(중앙/최저) | 0.764/0.699 | **0.720/0.575** | 0.814/0.733 | | smiling 2장이 끌어내림 |

- 지표상 회차 2 는 Gemini·회차 1 보다 나쁨(웃는 얼굴이 SFace 유사도·일관성을 깎음). 최종은 블라인드: `~/Downloads/lora_runs/blind_facepass_20260908_r2.png`(3:3 균형, 키 `_r2_key.json` 별도).
- 남은 손잡이(사용자 결정): smiling 문턱 ratio 0.86→0.88(옅은 미소는 생략) 또는 학습 캡션에 있던 `slight smile`(36장) 허용. 파드 유휴 방지 = 완료 즉시 stop 하는 파드 쪽 장치 필요(API 키는 파드에 못 두므로 방법 미정).
- 산출물 `/Users/nojeong-un/Downloads/lora_runs/face_pass_results/20260908T033556Z/` (LABELED_qa_gemini_r1_r2.png 는 판정 후).

## 12. v6 준비 (2026-09-08, 사용자 결정 B = 현재 머리로 재촬영·재학습) — 키트 `~/Downloads/lora_runs/v6_kit/`

- 이유: 회차 1·2 실패의 남은 원인 = 학습 사진이 머리 자르기 전 → 결과에 긴 머리 → 사람 눈이 "다른 사람". 튜닝으로 못 고침.
- 촬영 55 = A 기준용 6(**학습 제외, 새 자**) / B 스튜디오 25 / C 창가 18 / D 표정 6. 도착 순서: `v6_intake.py <src> <intake>`(HEIC→PNG, 4방향 YuNet 회전 보정, 필터: 미검출·다중검출(최대 면적 25%↑ 2개)·눈간격<폭 8%, `manifest_draft.tsv` + 그룹별 시트) → 사용자가 `expression`(촬영 라벨: smiling/neutral/slight smile/빈칸)·`pose`·배경·조명 확정 → `v6_refset_check.py <intake>/refset`(A 끼리 pairwise 중앙 **≥0.80** 아니면 재촬영, 학습 금지; 옛 6장 0.871 재현 확인) → `build_v6.py <intake> <dataset> --tgz`(build_v4c 기하 = face_identity 모듈, 2000px↑ 원본은 ×4 축소 검출(new48 과 동일 — 풀해상도는 큰 얼굴을 놓침), v5 캡션 형식, 증강 기본 on(R/B 1.27·B/D 증강 조명 "warm indoor light"), samples_ctrl = prod 6).
- 학습 `armA_v6.yaml`: init = v5-2000 가중치만, 옵티마이저·cosine 새로, 1500 step, save 250, sample 250(prod 6, v5 짧은 프롬프트), rank16·lr1e-4·1024·bf16·quantize false(v5 동일). 80GB 급 필요(A40 불가).
- 운용: `presign_v6.py upload/get/put/done/pull/clear`, `v6_launch.sh <host> <port> <pod_id>`(setup → 파드 체인 → `v6_go.sh`: 수신·검증·run.py → 표본/로그 tgz PUT → **DONE PUT**), `push_ckpt_v6.sh`(250 마다 R2, mtime 기준), `pull_v6.sh`(표본·로그 scp), **`v6_watchdog.sh <pod_id>`**(60초 R2 DONE 폴링 → `runpod_stop.py` REST v1→GraphQL, 6h 상한; API 키 `~/.runpod/api_key` 맥 전용, 없으면 기동 거부).
- 채점: 기준셋 = A 6(`score_v6_samples.py <step> <samples> <refset>` 개별 + 컷 간 일관성 중앙/최저/최고/std, 같은 자로 잰 Gemini 열), `score_face_pass.py --holdout <refset>`. v5 수치와 비교 금지. 채택 = 블라인드(3:3 균형) 4승↑, 3승 재시험, ≤2승 종료.
- 예산: 잔액 ≈$7.9 vs v6 ≈4.3~4.6h×$1.39 ≈ $6.0~6.4 → 잔액의 76~81% > 70% 규칙 → 충전 또는 step 축소 필요(1200 step ≈ $5.0 = 63%).
- **EXIF 회전 정규화(필수, 2026-09-08 사용자 지시)**: iPhone HEIC/JPG 는 Orientation=6, sips 변환본(JPEG·PNG)도 태그 유지 → PIL 4032×3024 vs cv2 3024×4032 불일치 실측. `v6_intake.normalize_to_png` 가 진입점에서 `ImageOps.exif_transpose` → EXIF 없는 PNG 저장 → 파일마다 PIL==cv2 assert(실패 시 exit 1). 옛 48장 48/48 통과, 드라이런 18/18. v5 때 쓴 `lora_new48/png` 는 이미 태그 없음·48/48 일치(v5 데이터는 어긋나지 않았음).
- 기준셋 8장(옛 야외) 0.885 재현 **실패**: 정규화 풀이미지 + 검출 배율(풀/×2/×4)·정렬 해상도·1024 크롭 7가지 방법 전부 중앙 0.57~0.63(정면 4장끼리 0.665). 크롭 시트 육안·크롭 기반 점수(0.631)와 풀이미지 점수(0.619~0.634) 일치 → 좌표계 불일치 아님. 0.885 의 측정법은 미확인.

## 13. v6 실행 결과 (2026-09-08 05:54~09:38 UTC)

- 데이터: `v6_dataset` train 40(야외, 현재 머리) + 색온도 증강 40 = 80쌍(refset 8 강제 제외, build_v4c 픽셀 동일 40/40, R/B 증강 중앙 1.273). 시작 게이트: refset 8 pairwise 중앙 0.885 · 최저 0.768 · 28쌍 재현 ✓. 잔액 $12.76 확인.
- 학습: init v5-2000 가중치만, 2000 step, save 250, sample 500(8장). **H100 community 재고 0 → secure $3.49/h**(사용자 기준 $2.69 아님), 4.81 s/it, 2:40:41 완주. 50-step 판정은 사용자 중단 중 흘러가 못 함. 파드1 ≈$10.5 소진. R2 push 8 ckpt + 최종 + optimizer. DONE→워치독 stop 은 `runpod_stop.py` UA 누락(Cloudflare 1010)으로 **실패** → 수동 stop(유휴 1.5분). UA 수정 완료(REST v1 은 Bearer + UA 필수; MCP 는 옛 계정 OAuth).
- 표본 채점(새 자, yaw>0.16 미채점, 학습기 샘플러): Gemini 개별 0.674(n4)·일관성 0.757/0.699 vs v6 @500 0.710/0.812·@1000 0.715/0.791·@1500 0.709/0.811·@2000 0.684/0.799 → 표본상 전 step Gemini 위. 후보 = 1500·1000·2000(500 은 weak_full 미채점 부풀림, 2000 은 1500 보다 하락 = 24 epoch 과적합 의심).
- A40 secure 검증(≈$0.37, 어댑터 교체식 분할 적재, 시드 42, 페더 0.12, 장당 119초): **C 경로에서는 Gemini 우위 사라짐**.

| C 경로(새 자) | Gemini | v6@1000 | v6@1500 | v6@2000 |
|--|--|--|--|--|
| 6장 전수 개별 중앙 | 0.669 | 0.641 | **0.675** | 0.666 |
| 6장 전수 일관성 중앙/최저 | **0.762/0.662** | 0.699/0.573 | 0.711/0.568 | 0.697/0.625 |
| 통과분 개별 중앙 | 0.674 (n4) | 0.627 (n5) | 0.675 (n4) | 0.666 (n4) |
| 통과분 일관성 중앙/최저 | 0.757/0.699 | 0.701/0.573 | 0.783/0.651 | 0.783/0.644 |
| 게이트 실패 | — | weak_34 yaw_drift | weak_34 yaw_drift | weak_34 yaw_drift |

- 1위 **1500**(통과분 일관성 중앙 0.783 → 개별 0.675; weak_full 제외해도 동일). 그러나 전수 일관성은 Gemini(0.762)보다 낮고 최저 0.568(weak_full·weak_sit 조합) — 표본 점수의 우위가 제품 경로에서 사라짐(v5 때와 같은 패턴).
- weak_34: 3 후보 전부 `yaw_drift`(결과 yaw 0.39 > 상한 0.324; 입력 0.162 라 "facing the camera" 프롬프트인데도 돌아감) → 블라인드 5행(2:3). weak_full(upscale 4.2): 1500 결과 yaw 0.19 로 통과분 미채점, 전수 0.618 vs Gemini 0.593.
- 블라인드: `~/Downloads/lora_runs/blind_v6_20260908.png`(5행, 얼굴 중심 2배 확대, 영문 라벨, 키 `_key.json` 별도). 판정 규칙 4승↑ 채택 / 3승 재시험 / ≤2승 종료 — 5행 기준 해석은 사용자.
- 비용: 파드1(H100 secure) ≈$10.5 + 파드2(A40) ≈$0.4 → 잔액 **$1.79**. 검증 후보 8→3 축소(규칙: 종료 시 잔액 <$4).
- 산출물: `~/Downloads/lora_runs/v6_ckpt/`(체크포인트 8·표본 32·로그), `~/Downloads/lora_runs/v6_verify/`(a40_verify: 후보 3 × prod 6 렌더·control·result.json, scores.json, LABELED_qa_*.png 는 판정 후).
- 함정 추가: ssh 를 `while read` 안에서 쓰면 stdin 을 먹어 첫 줄만 처리(`ssh -n`) · ai-toolkit 최종 step 은 접미사 없는 파일명 · 워치독 stop 경로는 실제 API 호출로 사전 검증할 것(이번엔 status 호출만 검증하고 stop 은 안 해봄) · REST v1 create 는 `supportPublicIp` 때문에 community 재고를 못 잡을 수 있음.

## 14. 얼굴패스 확정 설정 (2026-09-08, GPU 0) — `server/app/agents/face_identity.py`

세 회차(룩북 4컷 · 표정 8+2장 · coor 4컷 ×2)의 결론을 모듈 기본값으로 못박았다. 플래그는 여전히 False, 레지스트리 미부착.

- **타원**: 기본 = 확장 `ELLIPSE (-0.55, -1.10, 1.55, 1.25)`. 학습용은 `ELLIPSE_TRAIN (-0.30, -0.60, 1.30, 1.15)` 로 남겨 `plan_from_box(..., ellipse=ELLIPSE_TRAIN)` 로 재현(학습 표본 control 픽셀 동일 테스트가 이 경로를 쓴다). 근거: lb1 0.673→0.730, lb2 0.685→0.737, 이마 경계 띠 소멸. 페더 0.12 유지(0.25 는 머리카락 고스트).
- **표정어 생략 금지**: `build_prompt` 는 어떤 경로로도 표정어를 뺀 프롬프트를 만들지 않는다(`EXPR_FALLBACK = "neutral expression"`). 어휘 3개(`neutral expression` / `slight smile` / `smiling`). 추정이 smiling 이고 치아 비율 <0.05 면 호출자가 `slight` 로 낮춘다(`EXPR_TEETH_MIN`). 근거: 표정어를 빼면 학습 평균으로 수렴해 입꼬리가 처진다(lb1·lb2 실측), coor 4컷을 `slight smile` 로 고정했더니 원본 무표정과 어긋나 세트로 못 씀.
- **각도 규칙**(전부 확대본 기준 yaw): ≤0.25 적용 · 0.25~0.65 적용 + `pose_risk=True` · >0.65 건너뜀(`skipped_reason="yaw"`). 0.45 는 Lanczos 입력으로 정한 값이었고, ESRGAN 회차에서 0.453~0.489 입력이 0.469~0.701 로 **각도를 유지**해 0.65 로 올렸다(정면화의 원인은 각도가 아니라 입력 해상도). ★ ESRGAN 전처리 전제 — Lanczos 폴백 경로에는 정면화 위험이 남는다(coor2 0.741→0.437). 표본 4컷·시드 1개 잠정값. 게이트에 `yaw_flatten` 추가 — 결과 yaw < 입력 × 0.6 이면 실패·시드 재시도(기존 `yaw_drift` 는 "더 돌아간 것"만 잡아 Lanczos coor2 0.741→0.437 을 통과시켰다).
- **자동 확대**: `prepare_image()` 가 얼굴폭 <120px 이면 `auto_upscale`(k = ceil(150/얼굴폭), 상한 6)로 이미지 전체를 키운 뒤 계획을 다시 세운다. 확대기는 `set_upscaler(fn)` 훅(ESRGAN) → 없거나 실패하면 Lanczos. 결과는 **확대 해상도 그대로 반환**(되돌리면 생성 얼굴이 버려진다). 메타: `upscale_applied/method/k`, `face_w_before/after`, `skipped_reason`(no_face·too_small·yaw), `pose_risk`. `run_face_pass` 가 이 경로를 쓴다.
- **실측 확인**: lb1 적용(145px, yaw 0.076) · coor1_src(440px·얼굴 39.5px) k=4 → 160.7px, yaw 0.49 → **적용+pose_risk** · coor3 0.506·coor4 0.431·lb3 0.595 모두 적용+pose_risk · coor2 0.741 만 skip=yaw · weak_full 자동확대 81.6→168.7px 후 적용.
- 테스트 45개(신규 6): 확장 타원 좌표식·학습 타원 병존, 표정어 빈칸 불가(3 어휘 × 7 입력 × 3 추정), yaw 0.24/0.26/0.44/0.46/0.64/0.66 분기, `prepare_image` 의 skip/pose_risk, `yaw_flatten` 게이트, 자동 확대(k=5·상한 6·ESRGAN 훅/폴백), 마스크 밖 픽셀 동일 assert 유지. 전체 스위트 3848 통과.

## 15. 통합 검증 20컷 (2026-09-09 01:00~02:12 UTC) — **부분 완주, 결함 3건 발견**

커밋된 `run_face_pass` 를 그대로 호출(파라미터 수동 조정 없음), ESRGAN 을 `set_upscaler` 로 물림. 입력 20컷 원본 그대로.

**드라이런(맥 CPU)은 예측과 100% 일치**: 적용 18(정면 12 / pose_risk 6) · 건너뜀 2(c10_3 yaw 1.196, coor2 0.741). 즉 모듈의 분기 로직 자체는 예측대로 동작한다.

**파드 실행은 두 번 실패 후 부분 완주**(내 하네스 버그, 모듈 아님):
1. TE·ESRGAN 을 transformer 상주(39.2GiB) 상태에서 GPU 에 올려 **18컷 전부 CUDA OOM**(렌더 0). → 3단 적재(ESRGAN·계획 → TE 임베딩 → transformer)로 수정.
2. 확대 캐시 키를 `(크기, k)` 로 잡아 **같은 해상도 컷이 첫 컷의 확대본을 재사용**(440×660 4컷·360×480 3컷이 섞임). 로그의 동일 얼굴폭(160.6/146.0)과 뒤바뀐 판정으로 발각. → 픽셀 해시 키로 수정.
3. 세 번째 판이 9컷 처리(7 적용)한 뒤 렌더 예산 상한과 잔액 바닥이 겹쳐 중단. c9_1 이 6시드 전부 `center_off` 로 630초를 먹은 것이 결정적. scp 중 연결이 끊겨 c9_2 결과 PNG 가 잘렸다(1 유실).

**얻은 결과(6컷, 교체된 얼굴 기준 채점 · 기준자 refset 8)**

| 컷 | 확대 | yaw in→out | 시드/시도 | 게이트 | 원본→결과 유사도 |
|--|--|--|--|--|--|
| c10_1 (pose_risk) | 없음 | 0.426→0.334 | 44/3 | center_off ×2 → ok | 0.041→0.634 |
| c10_2 | **ESRGAN ×3** | 0.004→0.051 | 42/1 | ok | 0.008→0.698 |
| c10_4 | 없음 | 0.181→0.028 | 43/2 | center_off → ok | 0.032→0.755 |
| c10_5 | 없음 | 0.131→0.022 | 42/1 | ok | 0.089→0.792 |
| c5_1 | 없음 | 0.194→0.082 | 42/1 | ok | 0.093→0.718 |
| c5_2 | 없음 | 0.104→0.143 | 43/2 | face_width → ok | 0.012→0.640 |
| c10_3 | ESRGAN ×3 | 0.988 | — | — | skip=yaw (규칙대로) |
| c9_1 | 없음 | 0.039 | 6회 전부 실패 | center_off ×6 | 폴백(원본 유지) |

- **교체된 얼굴끼리 일관성: 중앙 0.746 · 최저 0.638 · std 0.059 (6컷 15쌍)**. 지금까지 최대 표본(이전엔 4~6컷). 출처별: c10 4컷 0.774/0.642/0.07 · c5 2컷 단일쌍.
- 원본 인물 유사도는 0.008~0.093(= 타인) → **정체성이 우리 인물로 확실히 교체됨**(Δ +0.59~+0.72).

**모듈 결함 2건(코드 수정 필요)**
1. **큰 이미지에서 엉뚱한 얼굴을 고른다.** `detect_face` 가 원본 해상도로 YuNet 을 돌린다. c5_1(5884×7005)에서 폭 617px 의 주 얼굴을 놓치고 폭 214px 의 다른 얼굴을 바꿨다. v6 데이터 빌더는 2000px 초과 시 ×4 축소 검출을 쓰는데(그래서 큰 얼굴을 잡는다) 모듈에는 그 규칙이 없다. → `detect_face` 에 축소 검출 도입 필요.
2. **다인 컷에서 한 얼굴만 바뀐다.** c10_1(2인)·c10_4(3인)은 최대 얼굴만 교체되고 나머지는 원본 인물로 남는다. 상세페이지 컷에 모델이 2명 이상이면 그대로 노출된다. → 제품 규칙 필요(다인 검출 시 건너뛰기 또는 전원 교체).
3. `center_off` 게이트가 과하게 엄격할 소지: c9_1 은 정면(yaw 0.039)인데 6시드 전부 중심 이탈로 실패했고, c10_1·c10_4·c5_2 도 재시도가 필요했다. 6시드 × 105초 = 컷당 최악 10분 → 제품에선 시드 수를 줄이거나 게이트 여유를 재검토해야 한다.

**육안**: 정면 4컷(c10_2·c10_4·c10_5·c5_2)은 경계·색이 자연스럽고 동일 인물로 보인다. c5_1·c5_2 는 원본이 **긴 머리 여성 모델**이라 얼굴만 남성으로 바뀌어 머리·목 경계가 명백히 이질적이다(구조적 한계 재확인). c10_2 는 원본 선글라스가 사라졌다. c10_1(pose_risk)은 각도는 유지되나 곱슬머리가 직모로 교체됐다.

**비용**: 파드 3세션 ≈$0.50(1차 OOM $0.05 + 2·3차 $0.45). 잔액 **$0.72**(바닥 $0.6 직전 정지). 산출물 `~/Downloads/lora_runs/verify20_out/{live,v20_face_zoom.png,v20_full_frames.png}`, 입력 `verify20_in/`(20컷 정규화본).

## 16. 결함 3건 수정 + 남자 컷 최종 집계 (2026-09-09)

**여자 모델 컷은 집계에서 제외**(c5_1·c5_2·c10_2·c10_4). 성별이 다른 원본에 남성 얼굴을 넣은 것은 테스트 설계 오류이고, 머리·목 경계 이질감이 방법의 한계가 아니라 입력 선택의 결과였다.

**수정 3건**(CPU, 테스트 54 → 전체 3857 통과)
1. `detect_face` 축소 검출: 긴 변 >2000px 이면 ×4 축소본에서 YuNet → 박스·랜드마크 ×4 복원(v6 빌더·기준자 채점과 같은 규칙). `downscale=False` 로 학습 재현 경로 분기. **m5_1(2005×6673)이 no_face → 얼굴폭 627 검출**로 살아났고, c5_1(5884×7005)에서 주 얼굴을 놓치던 문제의 원인이었다. 학습 표본 control 픽셀 동일 테스트는 그대로 통과(prod 입력 848×1264 < 2000 → 축소 미적용).
2. 시드 낭비 차단 `GATE_SAME_REASON_STOP = 3`: 같은 게이트 사유 3연속이면 남은 시드를 포기하고 폴백(`reason="gate_failed:<사유>x3"`, `stopped_early=True`). c9_1 이 center_off 로 6시드 630초를 태운 사례 방지.
3. `GATE_CENTER_FRAC` 0.15 → **0.25**. 근거를 먼저 측정: 채택 6컷 offset 0.065~0.145 · 거부 9회 0.164~0.235 로 갈렸고, 원인은 확장 타원의 수직 중심이 얼굴박스 중심보다 **0.425·h 위**(상단 −1.10h·하단 +1.25h → 중심 +0.075h vs 박스 +0.5h)여서 생성 얼굴이 체계적으로 위로 치우치는 것. 오붙임이 아니라 타원 편향이므로 얼굴높이 1/4 까지 허용. 폭·yaw 게이트는 유지. 효과: 남자 4컷의 offset 이 0.061~0.223 였고 **4/4 첫 시드 통과·재시도 0**(옛 임계면 3컷이 실패했을 값).

**남자 4컷 재실행**(A40 secure, ESRGAN 훅, ≈$0.08): m5_1 0.011→0.029 · m9_1 0.025→0.073 · m9_3(ESRGAN ×3) 0.028→0.011 · m9_5(ESRGAN ×2) 0.069→0.036. 전부 `ok`.

**최종 집계 — 남자 모델 촬영본 14컷 (기준자 = v6_dataset/refset 8)**

| 군 | 컷 | 개별 유사도 중앙(범위) | 컷 간 일관성 중앙/최저/std(쌍) |
|--|--|--|--|
| 정면 yaw≤0.25 | 7 (m5_1·m9_1·m9_3·m9_5·lb1·lb2·c10_5) | **0.717** (0.637~0.792) | **0.721 / 0.550 / 0.079** (21) |
| pose_risk 0.25~0.65 | 7 (c10_1·lb3·lb4·coor1~4) | 0.634 (0.545~0.647) | 0.639 / 0.413 / 0.121 (21) |
| 전체 | 14 | 0.643 (0.545~0.792) | 0.631 / 0.413 / 0.097 (91) |
| 건너뜀 | 2 (c10_3 yaw 0.988·m10_3 1.097) + coor2 는 ESRGAN 후 0.624 로 적용됨 | — | — |

원본 인물(타인) 유사도는 −0.067~0.172 → 정체성 교체는 전 컷에서 확실(Δ +0.46~+0.70). **정면군이 pose_risk 군보다 개별 +0.08, 일관성 +0.08, 산포 절반** — yaw 규칙의 pose_risk 구간 표시가 실제 품질 차이와 일치한다.

**육안(남자 4컷)**: 얼굴은 동일 인물로 일관. m5_1·m9_1 은 원본 선글라스가 사라졌다(마스크가 눈을 덮으므로 구조적). 곱슬 갈색 머리가 검정 직모로 교체되는 현상은 여전하며, 이것이 남은 최대 결함이다(원본 헤어 보존 미해결).

## 17. 타원 3단계 시험 — 머리 교체 (2026-09-09 02:54~03:09 UTC, ≈$0.12)

control 의 블러 타원 자체를 키워 **재생성**(저장된 raw 재합성은 무효 — raw 의 타원 밖은 모델이 원본 머리를 보고 재현한 픽셀이라 마스크만 키워도 원본 머리가 붙는다). 컷 2 × 타원 3 = 6장, 시드 42 고정, σ 0.5·페더 0.12·표정어·각도구 전부 동결.

| 컷 | 단계 | 타원 면적 | 게이트 | center_off | 얼굴폭 in→out(비) | yaw in→out | 유사도 | E1 대비 |
|--|--|--|--|--|--|--|--|--|
| m9_3 | E1 (-0.55,-1.10,1.55,1.25) | 45.6% | ok | 0.168 | 194→204 (×1.05) | 0.028→0.013 | 0.715 | — |
| m9_3 | E2 (-0.75,-1.45,1.75,1.30) | 56.5% | ok | 0.148 | 194→206 (×1.06) | 0.028→0.003 | **0.767** | +0.052 |
| m9_3 | E3 (-0.95,-1.80,1.95,1.35) | 67.2% | ok | 0.184 | 194→221 (×1.14) | 0.028→0.067 | 0.769 | +0.054 |
| m9_5 | E1 | 47.8% | ok | 0.063 | 160→177 (×1.10) | 0.069→0.039 | 0.743 | — |
| m9_5 | E2 | 58.8% | ok | 0.067 | 160→182 (×1.14) | 0.069→0.065 | 0.734 | −0.009 |
| m9_5 | E3 | 70.1% | ok | 0.060 | 160→183 (×1.14) | 0.069→0.083 | 0.733 | −0.010 |

**붕괴 징후 없음**: 6/6 게이트 통과, center_off 0.060~0.184(임계 0.25 이내), 얼굴폭 비 1.05~1.14(±30% 이내), yaw 이동 ≤0.05. 학습 분포를 벗어난 E3(면적 70%)에서도 얼굴 위치·크기가 흔들리지 않았다.

**머리 교체 판정(육안)**
- E1 → E2: 이마 위 잔털이 **줄었다**. m9_3 은 E1 에서 결과 머리 위에 원본 곱슬 잔털이 반투명하게 얹혀 있었는데 E2 에서 거의 사라지고 헤어라인이 이어진다. 측면은 E1·E2 가 비슷.
- E2 → E3: 머리 실루엣이 **더 커지고 볼륨이 붙는다**(m9_3 hairline 확대에서 명확). 다만 m9_5 는 E3 에서 원본 곱슬 잔털이 상단에 다시 보이고(타원이 커져 원본 머리 경계까지 닿음) 측면 헤어가 부풀어 원본 프레이밍과 어긋난다.
- 세 단계 모두 **원본 곱슬 갈색 머리는 이미 E1 에서 검정 직모로 교체돼 있었다.** 이번 시험이 개선한 것은 "머리 교체 여부"가 아니라 **경계에 남던 원본 잔털**이다.

**채택: E2 (-0.75, -1.45, 1.75, 1.30)** — 붕괴 없음, m9_3 +0.052, m9_5 −0.009(잡음 수준), 잔털 최소. E3 는 점수 이득이 E2 와 같고(+0.054 vs +0.052) m9_5 에서 잔털 재출현·헤어 부풀림이 있어 버린다. 표본 2컷·시드 1개의 잠정값.

**모듈 반영(사용자 승인, 2026-09-09)**: `ELLIPSE = (-0.75, -1.45, 1.75, 1.30)`. 이전 확장본은 `ELLIPSE_PREV (-0.55,-1.10,1.55,1.25)` 로 남겨 §14~16 회차를 재현할 수 있게 했고, `ELLIPSE_TRAIN (-0.30,-0.60,1.30,1.15)` 은 그대로 유지(학습 표본 픽셀 동일 테스트가 이 경로를 쓴다). 실측 확인: m9_3 면적 56.4% · m9_5 58.3%(시험값 56.5/58.8% 재현). center_off 주석의 타원 중심 산식도 E2 기준으로 정정(E2 중심 −0.075h).

산출물 `~/Downloads/lora_runs/ell3_out/`(`ell3_compare.png` 원본/E1/E2/E3, `ell3_hairline_zoom.png` 이마·측면 ×3, `ell3/` raw·control·full·result.json).

## 18. 확정과 남은 일 (2026-09-09) — Qwen 얼굴패스 채택, DB 배선이 다음 관문

**확정**: Qwen v6-1500 얼굴패스. 같은 자(refset 8) 남자 모델 실촬영 기준 정면 7컷 개별 0.717(0.637~0.792)·일관성 0.721 vs Krea 인물 LoRA 최고 0.639 + 전 체크포인트 가슴 글자(의상 통제 불합격). **Krea 는 보류(중단 아님, 우선순위만 내림) — 재학습하지 않는다. 캡션·데이터 준비가 선행 조건.**
E2 타원 반영 커밋 `e8fd1c3a`(§17). `face_identity_enabled` 기본 False 유지.

### 18.1 DB 배선 — 현행 구조 조사(스키마 변경 전, 제안만)

실존 인물은 `virtual_models.json` 이 아니라 DB(`public.fm_models`)에 있고, 자산은 `fm_model_assets` 에 view 별로 붙는다. 워커는 `agents/identity_source.resolve_real_model_assets()` 로 두 자산(face_front·grid_sedcard)만 꺼내 쓴다(`workers/editor_image_job.py:392`).

| 테이블 | 키 | 지금 있는 것 | LoRA 배선에 쓸 수 있나 |
|--|--|--|--|
| `fm_models` | `id uuid` | display_name · status(pending/awaiting_confirm/verified/suspended/reverification_required) · **gender(male/female)** · height_bucket · body_type · current_enrollment_id · assets_status(none/building/ready/failed) · assets_source_hash · cover_image_url | 인물 1행 = LoRA 1묶음의 주인. **gender 는 이미 있다.** hair 는 없다 |
| `fm_model_assets` | `(model_id, view)` | view ∈ {face_front, grid_sedcard} · r2_key · mime · bucket · source_enrollment_id · evidence_version | 자산 패턴의 선례. view 를 늘리면 LoRA 를 여기 담을 수 있으나 **1인 1행 제약**이라 버전 여러 개를 못 담는다 |
| `personalization_profiles` | `user_id` | **hair_length(short/medium/long)** · hair_color(8종) · gender(female/male/other) · body_type · 3사이즈 | hair_length 어휘가 이미 있다. 다만 **셀러 개인화 프로필**이라 FaceMarket 모델과 다른 축이다(user_id 기준, 모델 id 아님) |
| `fm_model_test_cuts` | `id` | model_id · r2_key · sort · approved | 모델별 다행(多行) 자산의 선례 — LoRA 버전 테이블 모양의 참고 |

**제안(선택지 A/B, 마이그레이션은 승인 후)**

| 안 | 모양 | 장점 | 단점 |
|--|--|--|--|
| **A. 새 테이블 `fm_model_loras`** (권장) | `id uuid pk · model_id uuid fk · version text · lora_r2_key text · trigger_token text · base_model text · status text(building/ready/failed/retired) · enabled boolean default false · hair_length text · trained_steps int · source_enrollment_id uuid · metrics jsonb · created_at` + `unique(model_id, version)` + 부분 유니크 `unique(model_id) where enabled` | 한 인물에 체크포인트 여러 개(v6-1500·v7…) 공존, 버전 승격/롤백이 한 행 토글, 지표(refset 유사도·일관성)를 jsonb 로 같이 보관, `fm_models` 를 안 건드림 | 테이블 1개 추가, 조회 조인 1회 |
| B. `fm_model_assets` 에 view 추가 | `view in (…, 'identity_lora')` + `fm_models.lora_trigger_token`·`lora_enabled` 컬럼 | 마이그레이션 최소 | **(model_id, view) PK 라 버전 다중 불가**, 지표·steps 둘 자리 없음, view 제약을 계속 늘려야 함 |

- 매칭 필터 필드: `gender` 는 `fm_models` 것을 그대로 쓴다(중복 저장 금지). `hair_length` 는 **LoRA 쪽에 둔다** — 인물의 현재 헤어가 아니라 *그 체크포인트가 학습한 헤어*가 매칭 기준이기 때문이다(v6 는 짧은 머리로 학습, v5 는 긴 머리였다). 어휘는 `personalization_profiles.hair_length`(short/medium/long) 재사용.
- 적용 여부는 두 단계: 전역 `FACE_IDENTITY_ENABLED`(env) **and** 행의 `enabled`(인물별). 지금 `cut_generator._face_identity_spec` 이 `virtual_models.json` 의 `faceIdentity` 를 보는 자리를 DB 조회로 바꾸면 된다(모듈 인터페이스 `FaceIdentitySpec(lora_path, token)` 은 그대로 쓸 수 있다).
- 매칭 규칙(이번 회차 실측 근거): 원본 컷의 성별이 다르면 얼굴만 교체돼 머리·목 경계가 명백히 이질적이었다(c5_1·c5_2 여자 모델). 헤어 길이도 다르면 티가 난다. → 컷의 기존 모델 성별·헤어를 알 수 없으면 **적용하지 않는 쪽이 안전**하다는 것이 현재 데이터의 결론.

### 18.2 GPU 백엔드 3안 (결정 보류, 선택지만)

실측 기준: 컷당 렌더 100~105초(A40 44GiB, bf16 3단 분할 적재) + ESRGAN 확대 5~10초. 모델 적재는 회당 86~138초(캐시 있는 파드). A40 단가 community $0.35/h · secure $0.49/h · **serverless $1.22/h**(RunPod 공시).

| 안 | 형태 | 비용(컷 100장/월 가정) | 지연 | 위험 |
|--|--|--|--|--|
| 1. RunPod serverless | 요청당 워커. `HttpFaceBackend` 가 그대로 물림 | 100컷 × 115초 = 3.2h × $1.22 ≈ **$3.9/월** + 콜드스타트분 | 콜드스타트가 문제 — 모델 적재 86~138초가 요청마다 붙으면 컷당 4분. flex worker 를 1대 warm 유지하면 $1.22/h × 24 × 30 = $878/월(불가) | 콜드스타트, 컨테이너 이미지에 20GB 모델 포함 필요 |
| 2. 상시 파드 | A40 1대 상시 + 내부 큐 | $0.35~0.49/h × 720 = **$252~353/월** | 적재 1회 후 컷당 115초 | 유휴 낭비가 절대적으로 큼 |
| 3. 온디맨드 파드 + 유휴 종료 (SAM 선례) | 수요 발생 시 파드 기동, N분 유휴 후 정지. `sam_autoscale`/`sam_autoscale_idle_minutes` 와 같은 패턴 | 100컷을 배치로 묶으면 3.2h + 적재 0.1h ≈ 3.3h × $0.49 ≈ **$1.6/월**(+ 기동 지연) | 첫 컷 지연 = 파드 기동 2~4분 + 적재 1.5~2.3분 ≈ **4~6분** | RunPod API 키 서버 보관, 기동 실패(재고 없음) 처리 |

- 레포에 이미 선례가 있다: SAM 세그멘테이션이 `SAM_AUTOSCALE` + `SAM_AUTOSCALE_IDLE_MINUTES`(기본 30분) 로 3안을 쓴다. 코드·운영 패턴을 그대로 복제할 수 있다.
- 3안의 지연은 상세페이지 잡이 이미 수 분 단위라 흡수 가능. 1안은 콜드스타트를 못 피해 사실상 2안(warm) 비용이 되고, 2안은 지금 트래픽에 과투자다. **정리하면 3안이 유력하나 결정은 사용자 몫.**

### 18.3 미해결 항목(제품 규칙 필요)

| 항목 | 현재 상태 | 성격 |
|--|--|--|
| 선글라스·안경 소실 | 마스크 타원이 눈을 덮으므로 생성이 안경을 지운다(m5_1·m9_1·c10_2 실측) | **구조적** — 마스크 방식으로는 못 고침. 안경 검출 후 건너뛰기 또는 안경 재합성이 필요 |
| 다인 컷 = 최대 얼굴만 교체 | c10_1(2인)·c10_4(3인)에서 나머지 인물은 원본 그대로. 메타에 다인 플래그 없음 | 규칙 필요: 다인 검출 시 건너뛰기 또는 전원 교체 |
| 체형은 원본 모델 것 | 얼굴 타원만 바꾸므로 키·어깨·체형은 원본 유지 | 트랙 B(전체 생성)로만 해결. 얼굴패스 범위 밖 |
| 성별·헤어 길이 매칭 | 필터 없음. 성별 다른 원본에 넣으면 경계가 명백히 이질적(c5_1·c5_2), 헤어 길이 달라도 티남 | 18.1 의 `gender`·`hair_length` 필터로 해결. 미상이면 미적용이 안전 |
| 헤어스타일 교체 | 곱슬·갈색 → 검정 직모로 바뀜(E2 로 경계 잔털은 해결, 스타일 자체는 그대로 교체됨) | 원본 헤어 보존은 미해결. 재학습(원본 헤어 다양화) 또는 헤어 영역 제외 마스크가 후보 |

## 19. 트랙 B 1단계 — v6 로 백지 전체 생성 (2026-09-09 11:10~11:36 UTC, ≈$0.25)

**질문**: v6 는 "1024² 크롭에서 블러 타원을 복원" 하나만 학습했다. 전체 생성은 뭉갠 영역도 주변 단서도 없는 다른 조건인데, 신원이 실리는가.

**판정: 통과(0.6 기준 크게 상회). 편집 경로보다 높다.**

| 기준 | 값 |
|--|--|
| 실물 천장(refset 8, 28쌍 중앙) | 0.885 — 파드에서 재확인 |
| 편집 경로 실측 정면 7컷 | 0.717 |
| **백지 생성 P1(정면) 8장** | **중앙 0.786 · 범위 0.459~0.825** |
| 백지 생성 P2(3/4·미소) 3장 | 중앙 0.682 · 범위 0.670~0.718 |

lora_scale 스윕(P1 × 시드 42·43): 0.8 → 0.760 · **1.0 → 0.776** · 1.2 → 0.627(0.459/0.794 편차 큼, 과증폭 의심). 최고 1.0 으로 phase B: 시드 44 **0.820**, 시드 45 **0.825**.

**우회 명시 — 순수 t2i 는 안 된다.** `QwenImageEditPlusPipeline` 에 `image=None` 을 주면 `AttributeError: 'NoneType' object has no attribute 'size'`(4-step probe 실측). 그래서 **회색 1024² 캔버스를 control 로 넣었다**(프롬프트 임베딩도 그 캔버스를 조건으로 계산). 즉 엄밀히는 "무조건 t2i"가 아니라 "정보 없는 조건 이미지 + 프롬프트" 생성이다.

**육안(11장 그리드)**: 정면 P1 은 전부 같은 인물로 읽히고 배경은 회색 스튜디오 무지, 상의는 모델이 임의로 입힌 무지 티/셔츠/데님. 0.459 한 장(scale 1.2, seed 42)은 얼굴이 부어 보이고 조명이 다르다 — 과증폭 실패 사례로 그대로 보고한다. P2(3/4 미소)는 신원이 조금 떨어지고 미소가 과하다.

**한계(1단계 범위에서 확인된 것)**
- 프레이밍이 얼굴·상반신에 갇힌다. P1·P2 는 전부 흉상. **P3(full body)는 예산 마감으로 렌더 못 함(0장)** — 전신에서 신원·체형이 어떻게 되는지는 **미측정**이다.
- 옷·장면 통제는 시험하지 않았다(2단계 과제).
- 표본이 프롬프트 2종·시드 4개·11장이다.

**비용**: 첫 판이 파이프라인 전체(TE+transformer ≈55GB)를 A40 44GiB 에 올려 로드 단계 OOM 으로 죽었다(≈$0.03 손실, 로그는 파드 재기동 불가로 유실). 3단 분할(TE 임베딩 선계산 → 해제 → transformer+VAE)로 재기동해 완주. 렌더 118~119초/장 고정. 잔액 $0.22.

산출물 `~/Downloads/lora_runs/gen1_out/`: `gen1_grid.png`(11장 + 점수), `gen1_vs_prod_contact.png`(Gemini prod 6장 vs 생성 상위 6장), `live/`(png·result.json·gen1.log).

**다음(2단계) 전 확인 필요**: 잔액 $0.22 로는 2단계(참조 3장 입력 × 프롬프트 × 비율 2종)를 못 돈다. 충전 또는 범위 축소 결정이 필요하다.

## 20. 트랙 B 2단계 — 조건을 하나씩 걸었을 때 신원이 버티는지 (2026-09-09 11:54~12:2x UTC, ≈$0.20)

목적: 1단계(백지 자유 생성)에서 확인한 신원 전달력이 **제품 조건**을 걸어도 남는지 측정.
**비교 기준은 1단계 자기 자신뿐** — P1 8장 중앙 `0.785`, refset 천장 `0.885`. 편집 경로 0.717 과는 비교하지 않는다(층위가 다름).

조건: `a` 장면 참조만(참조 1장) · `b` 장면+옷 앞/뒤(참조 3장) · `c` = b 를 848×1264(2:3)로. 시드 42·43, lora_scale 1.0, 25 step, true_cfg 4.0.
러너 `~/Downloads/lora_runs/v6_kit/gen2_pod.py`, LoRA `ohwx_man_v6_000001500.safetensors`, A40 secure 3단 분할 로딩(TE 임베딩 선계산 → 해제 → transformer+VAE 39.0GiB).

### 20.1 대체·우회한 것 (전부 명시)
| 항목 | 사실 |
|---|---|
| 장면 자산 인물 | 남성·상의 세트 후보 **4/4 전부 인물 포함**(YuNet 얼굴 검출 3/4, sc04 는 얼굴이 상단에서 잘려 검출 0·몸통 존재) |
| SAM 사용 | **안 씀**. 대신 `ss_set_07_..._small_shop_upnormal_7126_01.png` 의 **인물 없는 좌측 30% 크롭**(`scene_sc07_shelf.png`, 325×1448, 얼굴 0)을 장면판으로 사용 |
| 옷 참조 | 디네뎃 webp → PNG 변환(1200×1440). 앞면에 붉은 `thisisneverthat®` 가슴 프린트 |
| 순수 t2i | 1단계와 동일하게 **미지원** → 조건 이미지를 반드시 넣어야 함. 2단계는 조건 자체가 입력이라 회색 캔버스 불필요 |
| 파드 정지 | 계정 전환으로 워치독이 옛 계정 파드를 못 끔 → 잔액 소진 자동 정지에 맡김(사용자 승인) |

### 20.2 기록 항목 (판정 아님 — 관찰만)
| 컷 | 점수 | 옷 충실도 | 장면 준수 | 해부·프레이밍 |
|---|---|---|---|---|
| a s42 | 0.706 | 참조 없음 → 흰 티 임의 생성 | **부분** 크림 벽 + 나무 선반·검은 브래킷 | 이상 없음. "standing" 인데 얼굴 클로즈업(face_w 415) |
| a s43 | 0.777 | 참조 없음 → 흰 티 | **부분** 크림 벽·카운터·식물 흐릿 | 이상 없음. 흉상(face_w 342) |
| b s42 | 0.809 | 회색 조직·붉은 프린트·크루넥 재현. **글자 깨짐** `thisisneverthata`, ® 소실 | **미준수** 평평한 올리브베이지 스튜디오 배경 | 이상 없음(face_w 232) |
| b s43 | 0.706 | **글자까지 정확** `thisisneverthat®`, 회색 조직·핏 재현 | **준수** 크림 벽·나무 선반 2단·검은 브래킷·와이어 바구니 | 이상 없음(face_w 249) |
| c s42 | 0.758 | 글자 정확 `thisisneverthat®`(프린트가 다소 바램), 회색 조직 재현 | **미준수** 평평한 흰/회색 스튜디오 배경 | 이상 없음(face_w 218), 2:3 프레이밍 흉상 |

관찰: 장면 준수와 글자 정확도는 **조건이 아니라 시드**로 갈린다(같은 b 조건에서 s42 는 장면을 버리고 글자가 깨졌고, s43 은 둘 다 지켰다). 옷 참조를 넣으면 프레이밍이 넓어지고 얼굴이 작아진다(face_w 415/342 → 232/249).

### 20.3 채점기 동일성 (비교 성립 근거)
1단계 `gen1_pod.py` 와 2단계 `gen2_pod.py` 는 **같은 `build_scorer`** 를 쓴다 — YuNet 검출 → 최대 얼굴 → SFace 코사인 → refset 8장 중앙, 2000px 초과 시 ×4 다운스케일. 프로덕션 채점자(`score_v6_verify.py`)의 **yaw ≤0.16 · 눈간격 40~200 창은 적용되지 않는다**. 두 단계가 동일 조건이므로 Δ 비교는 성립하나, 이 숫자를 프로덕션 창 통과분과 직접 비교하면 안 된다.

검출된 얼굴 폭: a 415·342 → b 232·249. 옷 참조가 프레이밍을 넓혀 얼굴이 작아지지만 눈간격 추정치는 모두 창 안(≈95~165px)이다.

### 20.4 결과 — 5/6 완주 (c 시드43 미렌더, 잔액 소진)
| 조건 | 참조 | 해상도 | 시드 | 점수 | 중앙 | Δ1단계(0.785) |
|---|---|---|---|---|---|---|
| a 장면만 | 1 | 1024² | 42 / 43 | 0.706 / 0.777 | **0.742** | **−0.043** |
| b 장면+옷 | 3 | 1024² | 42 / 43 | 0.809 / 0.706 | **0.758** | **−0.027** |
| c 장면+옷 | 3 | 848×1264 | 42 / (43 미렌더) | 0.758 / — | **0.758** (n1) | **−0.027** |

2단계 전체 중앙 **0.758** (n5) · 최고 0.809 · 최저 0.706 · 천장 0.885.

**어느 조건이 깎았나 (한 문장)**: 깎은 것은 장면 참조 하나로 −0.043 이고, 옷 참조 2장과 2:3 해상도는 추가로 깎지 않았다(오히려 +0.016 회복).

문턱 판정: 세 조건 모두 사용자 기준 **≥0.70 = 신원이 버틴다**.

**미완**: c 시드43. 파드(옛 계정)가 12:19:57Z 에 잔액 소진으로 정지 — `ssh: connect to host ... Connection refused`. 렌더 시간이 예상보다 길었던 것이 원인(참조 3장 조건에서 282~285s/장, 참조 1장은 120s/장). 사용자 승인 아래 자동 정지에 맡긴 결과이며 이미 나온 5장은 SSH 증분 회수로 전부 확보했다.

컨택시트: `~/Downloads/lora_runs/gen2_out/gen2_contact.png` (2단계 5장 + 1단계 상위 3장 0.825/0.820/0.797).

**관찰된 시드 패턴**: 옷 참조가 붙으면 시드42 는 장면을 버리고(b s42·c s42 모두 스튜디오 배경), 시드43 은 지켰다(b s43). 장면 준수는 조건이 아니라 시드로 갈린다 → 제품에서는 시드 재시도로 흡수 가능한 종류의 실패다.

## 21. ZARA 룩북 5컷 얼굴패스 (2026-09-09 12:30~ UTC, A40 secure $0.49/h)

사용자 요청으로 외부 룩북(ZARA 남성 스트라이프 LS 세트, 2048×3072 5장)에 커밋된 `run_face_pass` 를 그대로 적용.
러너 `verify20_pod.py`(파라미터 수동 주입 없음), LoRA `ohwx_man_v6_000001500.safetensors`, 파드 `txsyf20ekw7hzm`, 워치독 = 신 계정 키로 정상 작동.

### 21.1 CPU 사전 예측 (파드 결과와 대조용)
| 컷 | 입력 | face_w | yaw | 크롭 확대율 | 예측 |
|---|---|---|---|---|---|
| 1 전신 | 2048×3072 | 218.5 | 0.046 | ×1.56 (655→1024) | 적용 |
| 2 정면 흉상 | 2048×3072 | 322.3 | 0.018 | ×1.06 (967→1024) | 적용 |
| 5 3/4 인물 | 2048×3072 | 488.1 | 0.231 | ×0.70 (1464→1024) | 적용, pose_risk 경계(0.25) 바로 아래 |
| 3 뒷모습 | 2048×3072 | — | — | — | **스킵 `no_face`** |
| 4 측면 | 2048×3072 | 178.7 | **1.745** | — | **스킵 `yaw`** (>0.65) |

표정은 3장 모두 `ambiguous` → 모듈 폴백 `neutral expression`. 자동 확대는 전부 미발동(face_w > 120).

원본 얼굴의 refset 대비 점수: 1 = 0.134 · 2 = 0.148 · 4 = 0.219 · 5 = 0.146 · 3 = 얼굴 미검출. 즉 **원본은 확실히 다른 사람**이고, 천장은 0.885.

주의: 원본 모델이 **금발**이고 E2 타원은 머리까지 덮으므로 결과는 `ohwx man` 의 검은 머리로 바뀐다(설계대로). 옷·포즈·배경은 픽셀 그대로 보존된다.

### 21.2 결과 (12:32~12:43 UTC, 실비 $0.113 · 파드 자동 정지 확인 EXITED)
셋업 109초(모델 54GB 포함) · 1단계 87.7s · 2단계 26.9s · 적재 총 135.3s · GPU 39.2GiB · ESRGAN 캐시 0(자동 확대 미발동).

| 컷 | 적용 | 사유 | 시드 | 시도 | yaw 입력→결과 | 원본점수 | 결과점수 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| 1 전신 | ✅ | ok | 42 | 1 | 0.046→0.013 | 0.134 | **0.671** | ok |
| 2 정면 | ✅ | ok | 42 | 1 | 0.018→0.120 | 0.148 | **0.721** | ok |
| 3 뒷모습 | — | `no_face` | — | 0 | — | 미검출 | — | — |
| 4 측면 | — | `yaw` | — | 0 | 1.745→— | 0.219 | — | — |
| 5 3/4 | ❌ | `gate_failed:yaw_flatten×3` | — | 3 | 0.231→0.127·0.107·0.097 | 0.146 | — | yaw_flatten ×3 |

적용분 결과 중앙 **0.696** (n2, 0.671~0.721), 천장 0.885. 원본 0.134~0.148 에서 **+0.53~0.57**.

**CPU 사전 예측과 파드 실측이 5컷 전부 일치** — 파라미터 수동 주입 없이 모듈이 스스로 분기했다. 픽셀 해시 업스케일 캐시도 동일 해상도 5장에서 섞임 없음(캐시 0건).

**컷 5 = 정면화 게이트가 제대로 막았다.** 3/4 포즈(yaw 0.231)를 모델이 3시드 모두 0.10~0.13 으로 펴버렸고 문턱 0.139(=0.231×0.6) 미달로 전량 거절 → 정면 얼굴을 3/4 몸통에 붙이는 사고를 방지. 반대로 말하면 **3/4 포즈는 이 경로로 아직 처리 불가**다.

### 21.3 새로 드러난 결함 — 합성 이음선이 눈에 보인다
| 컷 | 변경 픽셀 | 경계 띠(12px) 평균 \|Δ\| | 내부 평균 \|Δ\| | color_shift(RGB) | grain |
|---|---|---|---|---|---|
| 1 | 276,019 (4.39%) | 15.5 | 54.8 | [27.2, 16.4, 5.0] | 적용(hf 4.11→2.77→3.74) |
| 2 | 585,219 (9.30%) | 18.1 | 92.1 | [16.1, 13.1, 10.9] | 적용(hf 5.65→4.01→5.20) |

타원 밖 픽셀은 그대로다(변경이 bbox 내부에 한정). 문제는 **타원 안 생성분의 색이 원본과 크게 어긋나** 링 보정이 R +27 까지 밀어야 했고, 그 잔차가 **평평하고 균일한 벽 배경에서 사각 크롭 상단 테두리와 칼라 주변 창백한 링**으로 드러난다. 프로덕션 컷(어수선한 배경·작은 얼굴)에서는 안 보였던 것이 룩북 조건에서 노출됐다.

처방 후보(미검증): ① 링 보정을 경계 국소 그라디언트로 바꿈 ② feather 를 얼굴폭 비례로 키움 ③ 타원 하단을 칼라 위로 당김(단, E1 에서 이미 머리 경계 잔털 문제로 E2 를 택한 이력) ④ 생성 단계에서 조건에 원본 색 참조를 추가.

산출물: `~/Downloads/lora_runs/zara_out/zara/{1,2}_result.png` · `*_meta.json` · `result.json` · 컨택시트 `zara_out/zara_contact.png` · 러너 `v6_kit/zara_{kit.py,run.sh,launch.sh}` · 채점 `v6_kit/zara_report.py`.

## 22. Phase 0 — 이음선 수정 + 게이트 확장 + 인테이크 재작성 (2026-09-10)

### 22.F 에디터 착장 horizon 컷에 붙는 입력 이미지 (조사만, 코드 수정 없음)
정본 순서는 `server/app/workers/editor_image_job.py:465-478`(리스트 결합) → `:527`(무드/장면 삽입 지점) 이고,
역할 라벨은 `cut_generator.build_manifest(:1063)` 이 같은 순서로 낸다. 프롬프트는 `server/prompts/cut_generate_v1.txt`
(438줄, 섹션 `[[BASE]] [[OUTER_INNER]] [[DETAIL_COLOR_TRANSFER]] [[IDENTITY_REF]] [[BODY_REF]] [[FACE_REF]] [[SPACE]] [[SPACE_SET_PLATE]]`).

**REAL 모델 (FaceMarket 실존 인물)** — `identity_source.resolve_real_model_assets(:73)`
| # | 슬롯 라벨 | 출처 | 버킷 | 해상도·형식 |
|---|---|---|---|---|
| 1 | `MANNEQUIN` (조건부) | `cut_mannequin_asset.r2_key` — 기준색 사용 & 착장 컷일 때만 | public `r2` | 마네킹 베이스 원본 |
| 2 | `MODEL` (얼굴 연속성) | `fm_model_assets` view=`face_front` = 등록 `front` 사진 원본 바이트 | **비공개 `r2_face`** | 업로드 원본(리사이즈 없음), mime = 등록 사진 mime |
| 3 | `MODEL SHEET` | view=`grid_sedcard` — `face_grid.compose_sedcard` 가 등록 3장(front/angle45/side)을 셀 640px 로 합성 | **비공개 `r2_face`** | **1280×1280 PNG** 2×2(4번째 칸 중립색) |
| 4.. | `PRODUCT — front/back/detail/backdetail view` | 셀러 업로드 `assets[].r2_key`, 슬롯 라벨은 `_SLOT_LABEL` | public | 업로드 원본 |
| .. | `MATCHING` ≤2 | 사용자가 고른 매칭 의류(`custom_` 접두어면 2×2 컨택시트 라벨) | public | 원본 |
| .. | `MOOD` (조건부) | resolved all/bg 장면이 **없을 때만** `scene_suffix_start` 위치에 삽입 | public | 원본 |
| .. | `SPACE SET PLATE` / `EXAMPLE REFERENCE(all\|bg)` / `POSE CONTROL` | `exampleId` 가 `ss_` 로 시작하면 `space_set_assets.resolve_published_example_reference(:527)` | public | 원본 |

REAL 은 `face_front + grid_sedcard` **두 장 원자적**이고, 로드 실패 시 폴백 없이 잡 실패(`model_assets_unavailable`).
`fm_face_injected` = REAL ∧ horizon ∧ 모델 이미지 2장일 때만 체형 블록(`body_profile` = gender/heightBucket/bodyType)을 얹는다.

**VIRTUAL 모델** — `cut_generator.resolve_virtual_model_assets(:446, require_full_body=True)`
| # | 슬롯 라벨 | 출처 | 버킷 | 형식 |
|---|---|---|---|---|
| 1 | `MANNEQUIN` (조건부) | 위와 같음 | public | — |
| 2 | `MODEL FACE` | `virtual_models.json` views.`face_front` (예: `seed/models/mA/face_front.webp`) | public | webp |
| 3 | `MODEL FULL BODY` | views.`body_front` | public | png/webp |
| 4.. | PRODUCT / MATCHING / MOOD / EXAMPLE | REAL 과 동일 | public | — |

에디터는 `require_full_body=True` 로 부르므로 두 번째 장이 `grid_sedcard` 가 아니라 `body_front` 다
(`build_manifest` 는 `has_model_sheet` 와 `has_model_full_body` 동시 선언을 `conflicting_model_body_authority` 로 거부).
레지스트리 8모델 전부 `body_front` 를 갖고 있어 이 경로는 항상 성립한다.

프롬프트 치환 토큰(`${...}` → 최종 `{...}`): `allDirectionRule allFramingRule allPoseRule authorityPlanLine bodyRefLine
cutLabel cutSection detailColorTransferLine directionLine exampleLine exampleRepeatLine faceLine faceRefLine
identityRefLine imageManifest outerClosureLine outerwearInnerLine poseLine poseName referenceColor shotLine
spaceLine spaceVariation targetColor`. bg 컷은 첫 첨부를 `scene_plate` 로 삼아 장소일치 QC 루프(`:662-680`)를 돈다.

### 22.A 크롭 경계 알파 강제 0 — **성공**
`EDGE_FADE_PX = 48`(+ 맨 앞 `EDGE_FADE_HARD_PX = 3` 은 정확히 0). `feather_mask`/`binary_mask` 는 손대지 않고
새 `composite_alpha()` 에서만 곱한다 — 학습 control 은 정본이라 건드리지 않는다(바이트 동일성 테스트 통과 유지).

**실측 정정**: 표준 3× 크롭에서 E2 타원 상단은 **모든 컷에서** 크롭 밖이다 — prod 6컷 T=−330~−428, ZARA 3컷 T=−391~−569.
좌·우·하는 전 컷 안쪽(L 86~87 · R 939~942 · B 735~898). 즉 "프로덕션 컷은 영향 없음" 전제는 사실과 달랐고,
프로덕션 컷도 같은 이음선을 갖고 있었으나 어수선한 배경에 묻혀 있었다. 그래서 페이드를 4변 일괄로 걸지 않고
`crossing_sides()` 로 **벗어난 변에만** 건다(4변 일괄이면 좌·우에서 페더 가우시안 꼬리 σ≈40px 를 깎아
E2 가 없앤 측면 헤어 경계 문제가 되살아난다).

크롭 상단 4행 평균 |원본−결과| (= 이음선 직접 측정. **같은 raw 에 색 보정은 양쪽 동일한 전역 shift**, 페이드만 0 ↔ 48):
| 컷 | 옛 | 새 | 띠\|Δ\| 옛→새 | SFace 옛→새 |
|---|---|---|---|---|
| good_cafe | 1.71 (max 8) | **0.00** | 18.3 → 18.6 | 0.713 → 0.713 |
| good_close | 2.74 (max 9) | **0.00** | 25.6 → 26.5 | 0.659 → 0.659 |
| good_front | 1.00 (max 6) | **0.00** | 20.1 → 20.2 | 0.640 → 0.640 |
| weak_34 | 1.13 (max 5) | **0.00** | 21.4 → 21.7 | 0.720 → 0.720 |
| weak_full | 1.05 (max 6) | **0.00** | 19.3 → 19.5 | 0.615 → 0.616 |
| weak_sit | 5.55 (max 69) | **0.00** (max 1) | 23.2 → 23.9 | 0.711 → 0.711 |
| ZARA 1 | 15.57 (max 32) | **0.00** | 16.3 → 14.6 | 0.667 → 0.669 |
| ZARA 2 | 9.58 (max 25) | **0.00** | 20.9 → 20.5 | 0.713 → 0.722 |
| 중앙 | 2.23 | **0.00** | — | 0.689 → 0.690 (컷별 최대 변화 0.009) |

**지시된 지표는 목표에 도달하지 못했다** — "경계 띠 평균 |Δ| 15.5/18.1 → 목표 5 이하" 는 달성 못 했고 거의 그대로다.
그 지표는 이음선이 아니라 **변경영역 외곽 12px 에서 원본과 얼마나 다른지**를 재므로, 얼굴·머리를 실제로 교체하는 한
5 로 내려갈 수 없다(타원 경계에는 원본 머리 ↔ 생성 머리의 진짜 차이가 있다). 문턱을 옮기지 않고 지표를 바꾸는 대신,
이음선 위치를 직접 재는 두 지표를 추가해 판정했다. 점수는 전 컷 ±0.009 로 무해하다.

육안(`ab_out/seam_zoom.png`): ZARA1 머리 위의 누런 사각 테두리가 사라지고 원본 벽 톤으로 이어진다.

### 22.B 링 보정 → 저주파 차분 필드 — **되돌렸다**(지표는 통과, 육안은 회귀)
1안 normalized convolution(σ=0.5×얼굴폭, 링 밖은 신뢰도 가중으로 전역 평균 복귀)과
2안 `cv2.seamlessClone(MIXED_CLONE)` 을 **같은 raw 생성분**에 색 보정만 바꿔 넣고 비교했다(A 는 세 안 모두 동일 적용).

| 컷 | 안 | 링 잔차 | 타원 내 보정 최대 | 목 색이동 | SFace |
|---|---|---|---|---|---|
| ZARA 1 | 전역(옛) | [4.53, 0.78, 0.0] | 27.2 | **38.7** | **0.669** |
| ZARA 1 | 필드(1안) | [2.26, 0.98, 0.22] | 87.9 | 65.6 | 0.688 |
| ZARA 1 | seamless(2안) | [−4.04, −3.17, −1.07] | 247.0 | 43.2 | **0.151** |
| ZARA 2 | 전역(옛) | [0.0, −0.0, 0.0] | 16.1 | **27.5** | 0.722 |
| ZARA 2 | 필드 | [0.57, 0.39, 0.29] | 33.0 | 31.8 | 0.733 |
| ZARA 2 | seamless | [2.04, 0.8, 0.4] | 231.0 | 22.5 | **0.127** |

**1안**은 링 잔차를 27.2 → 2.26 으로 줄였고 얼굴 점수도 올랐지만(0.669→0.688) ZARA1 의 **목·턱이 노랗게 과보정**됐다
(목 색이동 38.7→65.6, field_max 87.9). 원인: 이 컷의 링(타원 테두리 안쪽 8px)은 위쪽에서 **밝은 크림색 벽**을,
아래쪽에서 **목·셔츠**를 함께 표본한다. σ=170px 필드가 그 배경 차이를 타원 내부까지 끌고 들어왔다.
**2안**은 더 나쁘다 — MIXED_CLONE 이 그라디언트를 섞어 얼굴을 오염시키고 신원이 붕괴했다(0.669→**0.151**).

육안 3자 비교(`b_variants/zara1_neck_3way.png`): 전역이 가장 자연스럽고, 필드는 목이 노랗고, seamless 는 얼굴이 어둡게 물든다.
→ **전역 링 shift 로 되돌렸다.** 지표(링 잔차)는 좋아지는데 그림이 나빠지는, 지표 단독판정의 전형이었다.
회귀 방지 테스트 `test_color_correction_stays_global_ring_shift` 로 "보정량은 타원 안에서 상수" 를 고정했다.

칼라 주변 창백한 링은 세 안 모두 남아 있다 — 그건 색 보정 문제가 아니라 **타원 하단이 칼라를 덮는 기하**(처방 ③) 문제다. 미해결.

### 22.C 게이트 광학 검사 2개 — 문턱은 8컷 분포에서
`_recognizer`(SFace)를 `_detector` 와 같은 락·캐시 규칙으로 추가(`face_embedding` / `cosine` / `identity_score`).
기준셋 8장 pairwise 중앙 **0.885** 를 기존 채점기와 동일하게 재현 → 배선 검증.

기존 게이트를 통과한 8컷(prod 6 + ZARA 2, LoRA v6-1500, 시드 42) 실측 분포:
| 컷 | SFace(기준셋 중앙) | field 링평균 \|RGB\|max |
|---|---|---|
| weak_34 | 0.676 | 1.47 |
| ZARA 1 | 0.678 | 27.26 |
| weak_full | 0.689 | 1.30 |
| good_front | 0.694 | 1.04 |
| weak_sit | 0.698 | 5.28 |
| good_cafe | 0.716 | 0.31 |
| good_close | 0.728 | 0.43 |
| ZARA 2 | 0.750 | 15.62 |
| — | 최저 0.676 · 평균 0.704 · σ 0.024 | 최대 27.26 |

- `GATE_IDENTITY_MIN = 0.60` — 최저 0.676 − 3σ(0.072) = 0.604 → 0.60. 통과 8/8 을 모두 남기고, 원본 타인(0.13~0.22)과 확실히 갈라진다. 옛 남자 14컷의 pose_risk 군(0.545~0.647)은 이 문턱에서 재시도 대상이 된다(의도된 동작).
- `GATE_COLOR_MAX = 35.0` — 허용한 최대 보정량 27.26(ZARA1, 결과 0.678 로 정상)의 1.3배 = 35.4 → 35.0. prod 컷은 전부 5.3 이하라 여유가 크다.

둘 다 `GateResult.reason` 에 들어가고 `GATE_SAME_REASON_STOP = 3` 에 걸린다. `references` / `color_ring_mean` 을
주지 않으면 기존 기하 게이트와 완전히 동일(하위 호환).

### 22.D 등록 합격선
판정을 **중앙 ≥0.80 AND 최소 페어 ≥0.70** 으로 바꾸고 leave-one-out jackknife SE 를 병기.
0.885 는 정상 등록자의 평균이므로 문턱으로 쓰면 절반이 탈락한다(채택). v6 기준셋 실행 결과:
중앙 **0.879 ± 0.017**(SE, n=8) · 최소 페어 0.806 · 최악 페어 `반신_팔짱,미소 × 시선_왼쪽` → **OK**.

### 22.E 인테이크 재작성 — 12/12 자동 선별
스펙: 조명 4방향(그늘·해가왼쪽·해가오른쪽·해등지고) × 3컷(정면무표정·정면미소·3/4) = 학습 12 + 기준셋 4(그늘·정면·무표정 반복).
`face_w < 342px` 반려 신설(근거: 크롭 변 3×얼굴폭을 1024 로 맞추면 크롭 안 얼굴폭 = 1024/3 = 341.3 → 342 이상이면 확대 배율 ≤1.0).
진입점 정규화(HEIC→sips PNG → exif_transpose → EXIF 없는 PNG → PIL==cv2 크기 assert)는 그대로 유지.

`~/Downloads/새로운데이터셋` 실행 결과: **학습 12/12 · 기준셋 4/4 · 반려 0** (16/16 정규화 통과).
얼굴폭 485~680px 로 전부 342 상회 — 클로즈업 스펙에 맞는다.

되풀이 금지 함정 둘을 잡았다:
1. **macOS NFD/NFC** — 파일시스템의 한글 디렉터리는 분해형이고 소스 상수는 조합형이라 첫 실행이 0장 선별로 나왔다. `_nfc()` 로 양쪽 정규화.
2. **3/4 는 파일명으로 고르면 안 된다** — 이 촬영의 `3:4_오른쪽` 은 전 조명에서 실측 yaw 0.617~0.867(거의 측면)이고 `3:4_왼쪽` 이 0.249~0.396(진짜 3/4)이다. 후보 전부를 재서 `YAW_34_TARGET=0.35` 에 가장 가까운 것을 고른다.
   또 `eye_ratio ≥0.08` 은 **정면 컷에만** 적용한다 — yaw 가 커지면 두 눈 간격이 투영으로 줄어들어(진짜 3/4 4장 0.066~0.094) "멀리 찍혔다" 를 잘못 판정한다. 3/4 의 거리 판정은 `FACE_W_MIN` 이 한다.

## 23. Phase 0 마무리 (2026-09-10, GPU 0 · 파드 켜지 않음 · API 0회)

### 23.1 타원 하단 스윕 — 합성 경계만 (작업 1)
E2 하단 1.30 은 `face_top + 1.30·h` = **턱 아래 0.30·h**(크롭좌표 139~142px)에 있어 칼라를 덮는다.
위·옆(−0.75, −1.45, 1.75)은 고정하고 하단만 1.30/1.20/1.12/1.05/1.00 로 스윕.
raw(`zraw_out/zara{1,2}_s42_raw.png`)를 재사용해 `plan_from_box(ellipse=(-0.75,-1.45,1.75,k))` + `composite_with_meta` 만 다시 돌렸다. grain 은 비교 가능성을 위해 off.

**★ 한계**: 생성은 이미 E2 control 로 이뤄졌다. 여기서 바꾼 것은 **합성 경계**뿐이므로 이 표는
"합성 경계를 어디에 두면 이음선이 최소인가" 의 답이고, "타원 하단을 줄여 학습·생성까지 바꾸면 어떻게 되나" 의 답이 아니다.
하단을 올리면 그 아래는 원본 픽셀로 남지만, 생성분의 목은 여전히 E2 기준으로 그려져 있다.

| 컷 | 하단 k | 타원하단 y | 칼라선 대비 | (a) 칼라띠 \|Δ\| | (a2) 파랑최상단 | (b) 턱선띠 \|Δ\| | (c) SFace | 경계 불연속(초과) |
|---|---|---|---|---|---|---|---|---|
| zara1 | **1.30** | 714 | −21 | 1.06 (max 21) | 12.86 (max 109) | 22.83 | 0.667 | +0.00 |
| zara1 | **1.20** | 685 | −50 | **0.00** (max 1) | **2.61** (max 41) | 14.85 | **0.693** | +0.00 |
| zara1 | 1.12 | 661 | −74 | 0.00 | 0.30 | 9.53 | 0.653 | +0.03 |
| zara1 | 1.05 | 640 | −95 | 0.00 | 0.01 | 4.33 | 0.650 | +0.08 |
| zara1 | 1.00 | 625 | −110 | 0.00 | 0.00 | 1.90 | 0.651 | +0.03 |
| zara2 | **1.30** | 953 | +8 | 4.73 (max 57) | 6.08 (max 64) | 17.50 | 0.701 | +0.01 |
| zara2 | **1.20** | 908 | −37 | **0.29** (max 8) | **0.63** (max 13) | 14.57 | **0.723** | +0.01 |
| zara2 | 1.12 | 872 | −73 | 0.00 | 0.01 | 9.19 | 0.728 | −0.04 |
| zara2 | 1.05 | 841 | −104 | 0.00 | 0.00 | 4.35 | 0.713 | +0.00 |
| zara2 | 1.00 | 818 | −127 | 0.00 | 0.00 | 1.79 | 0.717 | −0.01 |

칼라선은 사람이 원본을 보고 잡은 중앙-앞 라인(zara1 y=735 · zara2 y=945), (a2)는 측정 밴드 안 파랑 칼라 최상단 행(684 · 932). 띠 폭 ±12px.

**(b) 는 예상한 교차점을 보이지 않는다.** 지시된 정의(턱선 ±12px 띠의 원본 대비 |Δ|)는 "턱 영역이 얼마나 바뀌었나" 를
재므로 타원을 줄이면 단조 감소한다. **새 이음선(불연속)** 은 못 잡는다. 그래서 경계 행의 세로 미분이 주변 대비 몇 배인지를
원본과 결과에서 각각 재 그 **초과 배수**를 추가했다: 전 값에서 ≤ +0.08 = 잡음. 즉 하단을 올려도 턱에 새 이음선이 생기지 않는다
(페더 σ = 0.12×얼굴폭 ≈ 40px 이 경계를 덮는다).

**승자 = 하단 1.20.** 두 컷 모두 (a)·(b)·(c) 전부 1.30 보다 좋거나 같은 유일한 값이다.
1.12 이하는 칼라띠는 더 내려가지만 zara1 SFace 가 0.667 → 0.653/0.650/0.651 로 **1.30 보다 나빠진다**
(타원 밖으로 나간 턱·목이 원본 = 다른 사람 픽셀로 남아 신원이 내려간다).
육안(`ellipse_sweep/zara{1,2}_sweep.png`): 1.30 의 칼라 위 창백한 베일이 1.20 에서 사라지고, 더 줄여도 추가 이득이 없다.

**ELLIPSE 상수는 바꾸지 않았다** — 지시대로 결과만 보고한다. 세 변 모두 남은 어깨·배경의 흰 얼룩은
타원 **측하단**이라 이 스윕 범위가 아니다. prod 6컷은 이 실험용 raw 가 없어 못 잤다(아침 파드 항목).

### 23.2 등록 채점기 정의 통일 (작업 2)
0.879 ± 0.017 / 최소 0.806 의 출처: **`v6_refset_check.py` 가 자체 검출 경로(`s = 2 if max(w,h) > 2400`, ×2 축소)를
쓰고 있었다.** 파일 집합(v6_dataset/refset 8장)과 통계(28쌍 전체의 쌍 중앙값, 사진 1장 빼는 jackknife SE)는 동일하고,
축소 배율만 달라 박스·랜드마크 → `alignCrop` → 임베딩이 달라졌다. 실측 재현:

| 검출 경로 | 축소 | v6_dataset 8장 | intake_out 4장 |
|---|---|---|---|
| `v6_refset_check.py` 옛 경로 | ×2 (>2400px) | 중앙 **0.879 ± 0.017** · 최소 **0.806** | 0.881 ± 0.018 · 최소 0.854 |
| `face_identity.detect_face` | ×4 (>2000px) | 중앙 **0.885 ± 0.035** · 최소 **0.768** | 0.900 ± 0.010 · 최소 0.864 |
| 축소 없음 | ×1 | 28쌍 중 1쌍만 검출(전체 미검출) | — |

**정본 = `face_identity` 모듈 경로(×4).** 프로덕션 게이트·v6 데이터 빌더·이번 라운드의 모든 측정이 쓰는 경로이며,
스크립트가 자체 구현하지 않고 `fi.detect_face` → `fi.face_embedding` 을 그대로 호출하도록 바꿨다. 정의는 주석에 박았다:
검출·임베딩 = 모듈 그대로 · 통계 = 전체 쌍의 **쌍 중앙값**(사진별 중앙의 중앙 아님) · SE = 사진 단위 leave-one-out jackknife(편향보정 없음).
판정은 양쪽 다 통과: v6_dataset 0.885/0.768 OK · intake 0.900/0.864 OK.

### 23.3 hair 문구 배선 (작업 3)
`facemarket_physique.py` 에 physique 와 같은 패턴으로 추가:
`HAIR_LENGTHS(buzz/short/medium/long)` · `HAIR_COLORS(black/dark_brown/brown/blonde/gray/other)` ·
`HAIR_TEXTURES(straight/wavy/curly)` + 한글 라벨 + 영문구, `validate_hair`(코드 `invalid_hair`), `build_hair_block`.
어순은 길이 → 머릿결 → 색. `other` 색은 문구를 내지 않는다("other hair" 방지). 셋 다 없으면 블록 생략.

```
SUBJECT HAIR (generated; owned by the registrant's trained likeness): the model has short, straight,
black hair. Keep it consistent across cuts; it has no authority over the face.
```

`cut_generator` 의 `render_cut_prompt` / `build_prompt` / `generate` 에 `hair_profile` 을 `body_profile` 과 같은 방식으로 스레딩.
**착장 컷에서만** 블록을 낸다(product 컷은 사람이 없다). 값이 없으면 프롬프트가 바이트 단위로 기존과 동일(테스트로 고정).
`editor_image_job` 은 손대지 않았다 — DB 컬럼이 없어 넘길 값이 없다. Phase 3 에서 `fm_model_loras.hair_*` 를 읽어 넘긴다.

**왜 fm_models 가 아니라 LoRA 행인가**(상수 주석에도 적었다): 이 값은 등록자의 "현재" 머리가 아니라
**LoRA 가 학습한 머리**다(v6 = short·straight·black). 등록자가 나중에 머리를 기르거나 염색해도 그 LoRA 가 내는
얼굴의 머리는 바뀌지 않는다. 사람 하나가 여러 LoRA 버전을 가질 수 있고 버전마다 머리가 다르므로 값은 버전에 붙는다.

## 24. 턱 이중 윤곽 — 하단 방향 페더 σ 스윕 (2026-09-10, GPU 0 · 파드 켜지 않음)

`feather_mask(..., feather_bottom=)` 신설: 타원 전체 σ 는 그대로 두고 **턱선 아래만** 다른 σ 를 쓴다
(위·옆을 좁히면 E2 가 없앤 측면 잔털 경계가 되살아난다 — a59da0fe). 이음은 얼굴박스 하단에서 ±σ_상단 선형 보간.
`composite_alpha`/`paste_alpha`/`composite_with_meta`/`composite` 에 스레딩, 기본 None = 기존과 바이트 동일.
`binary_mask`·학습 control 경로는 손대지 않았다.

기하(크롭 1024²): zara1 턱 744 · 칼라 835 → 목 띠 **91px** / zara2 턱 749 · 칼라 869 → **120px**.
지금 σ=0.12×얼굴폭=41px → 블렌드 ±2σ ≈ 164px 로 띠에 안 들어간다.

### 24.1 스윕 (하단 k{1.15,1.20,1.25} × 하단 σ{41,25,15,10}, 두 컷 24판)
| 컷 | k | σ | 경계(크롭) | 블렌드 ±2σ | 턱아래 | 칼라위 | SFace | 칼라띠 \|Δ\| | 봉우리 |
|---|---|---|---|---|---|---|---|---|---|
| zara1 | 1.15 | 41 | 813 | 731~895 | −13 | −60 | 0.650 | 0.79 | 5 |
| zara1 | 1.15 | 25 | 813 | 763~863 | 19 | −28 | 0.664 | 0.37 | 5 |
| zara1 | 1.15 | 15 | 813 | 783~843 | 39 | −8 | 0.664 | 0.17 | 3 |
| zara1 | 1.15 | **10** | 813 | 793~833 | **49** | **2** | 0.696 | 0.08 | 3 |
| zara1 | 1.20 | 41 | 836 | 754~918 | 11 | −83 | 0.693 | 2.61 | 5 |
| zara1 | 1.20 | 25/15/10 | 836 | — | 43/63/73 | −51/−31/−21 | 0.690/0.692/0.699 | 2.25/2.26/2.48 | 4/5/5 |
| zara1 | 1.25 | 41/25/15/10 | 860 | — | 34~96 | −107~−45 | 0.702~0.706 | 6.13~8.72 | 4~6 |
| zara2 | 1.15 | 41 | 820 | 738~902 | −11 | −33 | 0.727 | 0.08 | 4 |
| zara2 | 1.15 | 25 | 820 | 770~870 | 21 | −1 | 0.720 | 0.00 | 5 |
| zara2 | 1.15 | 15 | 820 | 790~850 | 41 | 19 | 0.714 | 0.00 | 4 |
| zara2 | 1.15 | **10** | 820 | 800~840 | **51** | **29** | 0.712 | 0.00 | 4 |
| zara2 | 1.20 | 41/25/15/10 | 844 | — | 13~75 | −57~+5 | 0.723~0.729 | 0.66/0.22/0.05/0.01 | 4 |
| zara2 | 1.25 | 41/25/15/10 | 868 | — | 36~98 | −81~−19 | 0.725~0.731 | 2.37~1.61 | 4 |

**새 지표(가로 에지 봉우리 수)는 실패했다** — 원본이 이미 5~6개다(목걸이·턱 그림자·칼라). 이중 윤곽을 구분하지 못한다.
그래서 판정은 확대 시트 육안이 정본이다.

### 24.2 판정 — **승자 없음**
1. **σ 를 좁히면 더 나쁘다.** σ ≤ 15 에서 타원 하단 선을 따라 **목을 가로지르는 피부톤 계단**이 두 컷 모두 생긴다
   (`jaw_sweep/zara{1,2}_jaw_2x.png` 의 σ10 판, 빨간 선 = 타원 하단). σ=41 은 매끄럽다.
   기하상으로는 k1.15/σ10 만 블렌드가 턱 아래·칼라 위에 모두 들어가지만(zara1 49/2 · zara2 51/29), 육안으로 탈락이다.
2. **σ=41 에서 2배 확대로도 턱 이중 윤곽이 보이지 않았다.** 남아 있는 것은 **턱 실루엣 불일치** —
   생성 턱이 원본보다 좁고 위에 있어, 원본 턱 옆 음영이 타원 **밖**(원본 픽셀)에 남는다. 이건 블렌드 문제가 아니라
   기하 문제라 어떤 σ 로도 못 고친다. 고치려면 생성 쪽에서 턱 위치·폭을 맞추거나(Phase 1 프롬프트) 타원을 훨씬 크게 잡아야 한다.
3. 두 컷은 **방향이 일치한다**(둘 다 σ 좁힘 = 악화). 그래서 "두 컷에서 같은 조합이 이겼는가" 의 답은 "이긴 조합이 없다" 다.

**한계**: raw 는 E2 control 로 생성된 것이라 이건 합성 경계 실험이다. 목 피부 띠가 좁은 컷에서는 어떤 σ 로도
띠 안에 못 들어간다는 것이 zara1(91px)에서 확인됐다 — σ10 조차 겨우 2px 여유다. 남의 룩북(경로 B) 최악 조건이며,
gpt-image 경로에서는 턱선을 프롬프트로 맞출 수 있어 덜 나올 가능성이 있다(Phase 1 에서 확인).

**상수는 바꾸지 않았다**(FEATHER_FRAC·ELLIPSE 그대로, `feather_bottom` 기본 None). 부정 결과는 `feather_mask` docstring 에 박아 재시도를 막는다.

## 25. prod raw 확보 → 합성 상수 확정 + Phase 1 준비 (2026-09-10)

### 25.A prod 6컷 raw (파드 1회, 15분 20초, 실비 $0.128)
`praw_kit.py`/`praw_run.sh`/`praw_launch.sh` — ZARA 키트를 그대로 쓰되 입력만 `prod_inputs/*.jpg` 로.
**ZARA 사고(sed /g 누락으로 arcname 이 `ab_run.sh` 로 남아 45분 유휴 $0.37) 재발 방지**: 빌드 후 tar 를 내려받아
`tar tzf` 로 arcname 을 확인하고 나서 파드를 띄웠다(`praw_run.sh` + inputs 6장 확인). 파드가 도는 동안 로그를 봤다.

6컷 전부 첫 시드 통과: good_cafe 0.037→0.031 · good_close 0.079→0.032 · good_front 0.055→0.090 ·
weak_34 0.162→0.227 · weak_full(ESRGAN ×2) 0.100→0.090 · weak_sit 0.101→0.055, 각 ~105초.
산출 `praw_out/{id}_s42_raw.png` + `_control.png` + `_meta.json` + `_result.png` (6/6 회수).
15분 강제 정지 대기자가 scp 도중 파드를 끊었으나 `PRAW_DONE` 이 03:35:31Z 에 이미 올라가 있어 R2 로 전량 회수했다.

### 25.B 합성 상수 확정 — 8컷 × 4판, 판정 규칙 사전 고정
같은 raw 로 합성만 재실행. `feather_bottom` 은 None 고정(§24 부정 결과). 칼라 y 는 컷별로 사람이 잡았다
(prod: 515·720·500·470·345·655 / ZARA: 735·945).

| 컷 | SFace 1.30→1.20 | Δ | 칼라띠 1.30→1.20 | 상단행 fade 0→48 |
|---|---|---|---|---|
| good_cafe | 0.715 → 0.715 | +0.000 | 0.73 → 0.12 | 8.13 → 0.00 |
| good_close | 0.727 → 0.728 | +0.001 | 0.00 → 0.00 | 21.38 → 0.03 |
| good_front | 0.696 → 0.692 | −0.004 | 0.01 → 0.00 | 6.44 → 0.02 |
| weak_34 | 0.678 → 0.676 | −0.002 | 0.24 → 0.02 | 8.05 → 0.00 |
| weak_full | 0.675 → 0.668 | **−0.007** | 0.08 → 0.00 | 11.99 → 0.31 |
| weak_sit | 0.698 → 0.699 | +0.001 | 0.00 → 0.00 | 12.83 → 0.01 |
| ZARA 1 | 0.669 → 0.693 | **+0.024** | 1.06 → 0.00 | 15.57 → 0.00 |
| ZARA 2 | 0.722 → 0.746 | **+0.024** | 4.74 → 0.29 | 9.58 → 0.00 |

**판정(규칙 그대로)**: k=1.20 채택 — SFace 최악 −0.007 로 문턱 −0.02 안, 칼라띠가 나빠진 컷 0개.
EDGE_FADE_PX=48 채택 — 8컷 전부 상단행 |Δ| 감소.
→ `ELLIPSE = (-0.75, -1.45, 1.75, 1.20)` 으로 변경. 하단 1.30 원판은 `ELLIPSE_E2_BOTTOM130` 으로 보존,
`ELLIPSE_PREV`·`ELLIPSE_TRAIN` 과 학습 control 바이트 동일성 테스트는 무손상. 상수 옆 주석에 위 표 요약을 남겼다.
육안(`const_sweep/{id}_sheet.png`): 네 판 모두 목·칼라 깨끗, fade48 에서 상단 테두리 소멸.

### 25.C 얼굴형 블록 (커밋 e767324e)
`FACE_SHAPES{oval,round,square,heart,long}` · `JAW_LINES{soft,defined,angular}` + `validate_face_shape` +
`build_face_shape_block`. hair 와 같은 이유로 **LoRA 가 학습한 얼굴형**이라 LoRA 행에 붙는다(주석 명시).
v6 는 사람이 보고 {oval, defined} 로 잠정(측정값 아님). 착장 컷만, 값 없으면 프롬프트 바이트 동일.
프롬프트 블록 순서: BUILD → HAIR → FACE SHAPE.

### 25.D Phase 1 대기 스크립트 (실행 안 함 — OPENAI_API_KEY 없음)
`~/Downloads/lora_runs/phase1/phase1_gptimage.py`
- 준비 확인(지금, API 0회): `~/devs/wearless_studio/server/.venv/bin/python ~/Downloads/lora_runs/phase1/phase1_gptimage.py --check`
- 실행(키 들어온 뒤): `MODEL_ROUTING_IMAGE_HIGH=gpt-image-2 OPENAI_API_KEY=sk-... ~/devs/wearless_studio/server/.venv/bin/python ~/Downloads/lora_runs/phase1/phase1_gptimage.py --run`

`--check` 결과: 세 블록(BUILD·HAIR·FACE SHAPE)이 프롬프트에 실제로 들어감을 확인, 입력 3종·기준셋 8장 존재 확인,
`OPENAI_API_KEY` 없음·`MODEL_ROUTING_IMAGE_HIGH` 미설정 보고. `--run` 본문은 키 확보 후 채운다
(지금 채우면 검증 없이 죽은 코드가 된다).

## 26. Phase 1 — 경로 A(gpt-image) 착장 컷 생성 → 얼굴 패스 (2026-09-10)

### 26.0 에디터 전용 모델 노브 (커밋 b16fcc76)
에디터(`editor_image_job`)는 `resolve_model(settings,"image_high")` 를 직접 읽어, image_high 를 바꾸면
마네킹(`mannequin_tier`)·매칭 플랫레이(`matching_flatlay_tier`)·AG-07(`cut_variator`)까지 전부 딸려 간다.
`detail_page_job` 선례대로 워커 안에서만 불변 Settings 복사본의 image_high 를 치환한다.

- `config.py` `model_editor_cut = ""` · `MODEL_ROUTING_EDITOR_CUT`
- `model_routing.py` `resolve_editor_cut_model()` + 스냅샷 `"editor_cut"` 키
- `editor_image_job.py` 진입부 `editor_settings = replace(s, model_image_high=resolve_editor_cut_model(s))`,
  이미지 생성 호출 5곳(`cut_generator.generate` ×4 · `cut_variator.generate` ×1)만 사용. SAM·QC·업로드는 `s` 그대로.

확인 출력: `editor_cut = gpt-image-2.5-flare` · `image_high = gemini-3-pro-image`(유지) · `detail_cut = gemini-3-pro-image`(유지).
테스트 +5(총 4051 통과) — 빈 노브 폴백, 노브가 에디터만 바꿈, Settings 복사본이 image_high 한 필드만 바꿈,
`mannequin_tier`/`matching_flatlay_tier` 가 여전히 image_high 로 해석, 워커 소스에 `s` 직접 사용 없음.

### 26.1 모델 id — 하이픈 오타
`gpt-image-2.5flare` 로 첫 호출이 **400**:
```
"message": "The model 'gpt-image-2.5flare' does not exist.", "code": "invalid_value"
```
계정 모델 목록 조회(무료)로 실재 id 확인 — `gpt-image-1`, `gpt-image-1-mini`, `gpt-image-1.5`, `gpt-image-2`,
`gpt-image-2-2026-04-21`, **`gpt-image-2.5-flare`**, `gpt-image-2.5-flare-2026-09-08`, `gpt-image-2.5-sunburst`,
`gpt-image-2.5-sunburst-2026-09-08`, `chatgpt-image-latest`. 하이픈만 넣어 재실행 → 5/5 생성 성공.
400 회차는 이미지가 안 나왔으므로 과금 0.

### 26.2 생성 (5/5, 1024×1536)
입력 순서는 REAL 경로 그대로: MODEL(refset 정면무표정) → MODEL SHEET(refset 4장 2×2 1280²) →
PRODUCT 앞 → PRODUCT 뒤 → SPACE SET PLATE. 프로필 hair{short,straight,black} · face_shape{oval,defined} ·
physique{male, m_175_180, slim}. 컷 5종은 prod_inputs.json 스펙 재사용(정면·3/4·전신·앉은자세·클로즈업).

**채점 기준셋은 4장이다** — refset 8장 중 입력으로 쓴 4장(정면무표정·정면미소·시선왼쪽·시선오른쪽)을 제외했다.
이 4장 pairwise 중앙(천장) 0.888.

**패스 전 SFace**: front 0.842 · three_quarter 0.836 · full 0.803 · seated 0.856 · close 0.877.
→ **얼굴 패스 없이 이미 통과 조건 0.70 을 넘고 천장(0.888)에 붙는다.** 경로 A 에서는 참조 2장(MODEL+SHEET)만으로
gpt-image-2.5-flare 가 인물을 재현한다. 프린트 글자(`thisisneverthat®`)·장면판·머리/얼굴형 지정도 전 컷 일관.

### 26.3 얼굴 패스 (파드 1회 13분 21초, 실비 $0.109)
5/5 전부 **첫 시드 통과**(`gates=['ok']`, 재시도 0). 게이트 사유별: `ok` 5 · `identity_low` 0 · `lighting_off` 0 ·
`face_width` 0 · `center_off` 0 · `yaw_drift` 0 · `yaw_flatten` 0 · `no_face` 0.
`full` 만 얼굴이 작아(face_w 110) ESRGAN ×2 자동 확대가 걸렸고 결과가 2048×3072 로 반환됐다(모듈 계약).
픽셀 비교는 원본 해상도로 되돌려서 했다 — 그 컷의 지표 2·3 에는 리샘플 오차가 섞여 있다.

| 컷 | 게이트 | 1 SFace 전→후 | 2 상단행 \|Δ\| | 3 칼라띠 \|Δ\| | 4 턱(휘도) px | 5 어깨 좌/우 Δ |
|---|---|---|---|---|---|---|
| front | ok | 0.842 → **0.780** | 0.00 | 0.05 | 8.0 | 0 / 0 |
| three_quarter | ok | 0.836 → **0.796** | 0.00 | 0.02 | 6.0 | 2 / 2 |
| full | ok | 0.803 → **0.823** | 3.12※ | 3.83※ | 4.0 | 2 / 2 |
| seated | ok | 0.856 → **0.770** | 0.01 | 0.35 | 8.0 | 15 / 2 |
| close | ok | 0.877 → **0.834** | 0.00 | 0.01 | 4.0 | 1 / 4 |

※ ESRGAN ×2 후 다운스케일 비교라 리샘플 오차 포함.
지표 4 의 "피부/비피부 경계" 정의는 **작동하지 않는다** — 턱 아래는 여전히 피부(목)라 그 띠에 전이가 없다
(ZARA 기준선 두 컷 모두 None). 그래서 휘도 에지(열별 |d(luma)/dy| 최대 행) 기준으로 바꿔 재고 그 값을 쓴다.

### 26.4 핵심 질문 — (a)(b) 가 ZARA 대비 줄었는가: **줄었다, 크게**
| 지표 | ZARA(남의 룩북) | 경로 A(gpt-image) |
|---|---|---|
| (a) 턱 실루엣 이동 | 19.0 · 34.5 px | **4 ~ 8 px** (중앙 6) |
| (b) 어깨 좌 Δ | 111 · 106 | **0 ~ 15** (중앙 2) |
| (b) 어깨 우 Δ | 37 · 23 | 0 ~ 4 |

근거: ZARA 는 원본 인물과 LoRA 인물이 서로 다른 사람이라 타원 밖에 남의 턱·어깨가 남았다. 경로 A 는
얼굴형·머리·체형을 프롬프트로 지정해 생성기가 그린 몸이 LoRA 가 그리는 몸과 거의 같아졌고, 그래서
타원 경계 바깥에 남는 불일치가 사실상 사라졌다. 육안(`phase1/measure/*_zoom.png`)에서도 사각 테두리·칼라
베일·턱 이중 윤곽이 전부 보이지 않는다. → **타원 마스크로 간다. SAM 인물 마스크는 필요 없다.**

### 26.5 예상 못한 결론 — 경로 A 에서는 얼굴 패스가 손해다
패스 **전** 이미 0.803~0.877 로 천장(0.888)에 붙어 있었고, 패스 **후** 4/5 컷에서 신원이 내려갔다
(−0.062 · −0.040 · −0.086 · −0.043). 오른 컷은 `full` 하나뿐이고(+0.020) 그 컷만 얼굴이 작아(face_w 110)
ESRGAN 확대가 걸린 컷이다.

읽는 법: gpt-image-2.5-flare 가 MODEL + MODEL SHEET 참조 2장만으로 인물을 이미 재현하므로, 그 위에
LoRA 얼굴을 덮으면 **더 좋아지지 않고 미세하게 나빠진다**. 얼굴 패스의 값어치는 **얼굴이 작아 생성기가
인물을 흐리게 그리는 컷**(full 류)에 남는다.

주의: 채점 기준셋이 4장뿐이고 그 4장은 입력 4장과 같은 촬영·같은 조명이다. 절대값은 이 조건에서만 유효하다.
전/후 비교는 같은 기준셋으로 재서 방향은 신뢰할 수 있다.

**아침에 사람이 결정할 것**: 경로 A 에서 얼굴 패스를 (1) 끄고 gpt-image 단독으로 갈지, (2) `face_w` 임계
아래 컷에만 켤지(예: face_w < 150), (3) 그대로 전 컷에 켤지. 지금 수치는 (2)를 가리킨다.

## 27. 전신 슬롯(MODEL FULL BODY) 을 REAL 등록자에게 열기 (2026-09-10)

### 27.A 코드 (커밋 아래)
1. **마이그레이션** `20260910000000_fm_model_assets_body_front.sql` — `view` CHECK 에 `body_front` 추가
   (additive · PG16-safe · drop-if-exists → add). PK `(model_id, view)` 유지.
   `body_front` 는 `bucket='face'` 강제 CHECK 도 함께 건다.
   ※ 지시가 인용한 `biometric_purge.py` 는 이 저장소에 없다. 대신 확인한 사실: 모델 자산의 쓰기·복사·삭제는
   전부 `app.state.r2_face` 하나로만 하고(`fm_model_asset_job.py:282·421·448·591`), 정리 경로
   (`facemarket_enrollment.py:598`)는 `fm_model_assets` 에 **없는** 키만 그 버킷에서 지운다.
   즉 `bucket='public'` 행은 파일이 다른 버킷에 있는데 장부만 남아 정리 대상에서 빠진다.
2. **`identity_source.resolve_real_model_assets`** — 얼굴 2장은 필수(fail-closed 유지), `body_front` 는
   **선택**으로 세 번째. 핀(같은 `source_enrollment_id` · `evidence_version == policy_version` ·
   `bucket=='face'` · `assets_source_hash` 재계산)은 전신에도 **똑같이** 건다. 전신이 있는데 핀이 어긋나면
   그 자산만 빼지 않고 **전체를 거부**한다 — 반쪽 근거로 컷을 만들지 않는다.
   반환 순서 `face_front → grid_sedcard → body_front`.
3. **`editor_image_job`** — `len(model_images)==2` 하드코딩 3곳을 장수 분기로.
   `fm_face_injected` 조건도 `n>=2`. **크레딧·정산 영향**: 이 플래그가 `record_license_settlement` 와
   비공개 캐시를 켠다. 기존 REAL 2장은 동작 동일이고, 새로 생기는 3장 상태에서 `==2` 를 그대로 뒀다면
   **정산이 조용히 빠졌을** 것이다(라이선스는 소비되는데 과금 안 됨) — `>=2` 가 맞다.
4. **`build_manifest`** — `has_model_sheet ∧ has_model_full_body` 를 무조건 거부하던 규칙을
   `∧ not has_model_face` 로 좁혔다. 세 슬롯은 **서로 다른 자리**에 오므로 상충이 아니다. 옛 가드가 막으려던
   진짜 위험(얼굴 시트 한 장을 체형 근거로 위장)은 얼굴 슬롯 없이 sheet·full_body 만 선언하는 경우뿐이고,
   그 경우는 계속 `ValueError` 다. 라벨 순서를 **FACE → SHEET → FULL BODY** 로 맞췄다(자산 해석 순서와 동일).
   얼굴 라벨은 전신이 붙는 조합에서만 명시형(`MODEL FACE`)을 쓰고, 기존 REAL 2장은 `MODEL` 그대로 —
   그 경로 프롬프트는 바이트 단위로 안 바뀐다.

매니페스트 3조합:
```
REAL 2장(기존)    1 MODEL          2 MODEL SHEET   3·4 PRODUCT  5 SPACE SET PLATE
REAL 3장(신규)    1 MODEL FACE     2 MODEL SHEET   3 MODEL FULL BODY  4·5 PRODUCT  6 SPACE SET PLATE
VIRTUAL 2장(기존) 1 MODEL FACE     2 MODEL FULL BODY  3·4 PRODUCT  5 SPACE SET PLATE
```
테스트 +8(총 4059 통과).

### 27.B 실측 A/B — 6렌더 (gpt-image-2.5-flare, 파드 0)
전신 자산 등록 경로가 아직 없어 **대역**을 썼다: `v6_dataset/train/해가오른쪽__전신_정면.png`(48MP 원본,
1536 로 축소해 첨부). BODY 문구는 양쪽 다 넣었다 — 이번 비교는 "문구만 vs 문구+사진".

| 컷 | SFace 없음 | SFace 있음 | Δ |
|---|---|---|---|
| full | 0.828 | **0.476** | −0.352 |
| three_quarter | 0.841 | 0.743 | −0.098 |
| seated | 0.835 | **0.883** | +0.048 |

### 27.C 판정 — **전신 사진은 체형을 따라오게 하지만, 옷·배경 권한 0 이 지켜지지 않아 그대로 쓸 수 없다**
`_MODEL_FULL_BODY_LABEL` 은 "ZERO authority over facial identity, facial features, hair, pose, framing or
clothing" 인데 gpt-image-2.5-flare 는 그걸 지키지 않았다. 육안(`phase1/body_ab/body_ab_sheet.png`):

- **full·있음**: 배경이 SPACE SET PLATE 의 실내 상점 → 전신 사진의 **실외 골목**(주차된 차·건물)으로 바뀌었다.
  하의도 검정 데님 → 전신 사진의 **카키 팬츠**, 신발도 전신 사진의 검은 구두.
- **three_quarter·있음**: 배경 실외 골목, 하의 카키.
- **seated·있음**: 배경은 상점 유지, 하의만 카키로 바뀜.

수치 뒷받침(하의 영역 세로 55~80% 평균 RGB · 전신 대역 사진의 하의 = [137,129,122]):
`full` 없음 [68,69,72](검정) → 있음 [161,155,147], `seated` 없음 [182,164,158] → 있음 [162,143,135].
배경(좌우 가장자리): `full` 있음이 장면판과 |Δ|25.3, 전신 사진과 |Δ|**2.1** — 전신 사진 배경을 그대로 가져왔다.
(`three_quarter` 는 가장자리 평균이 우연히 장면판에 가깝게 나왔지만 육안으로는 실외다 — 육안이 정본.)

`full` 의 얼굴 급락(0.828→0.476)도 같은 원인이다. 인물이 전신 사진의 먼 거리 구도로 옮겨가 얼굴이 작아졌다.

**체형 효과 자체는 판정 불가다.** 옷·배경 오염이 같이 왔기 때문에 "체형이 실물에 가까워졌는가" 를 분리해서
볼 수 없다. `seated·있음` 의 어깨가 조금 넓어 보이지만 확신할 수준이 아니다. 결론을 억지로 내지 않는다.

**다음에 시도할 것(미검증 제안)**: ① 전신 참조를 인물만 남기고 배경 제거(SAM) 후 첨부 ② 전신 참조를
실루엣/포즈만 남긴 추상화본으로 대체 ③ 라벨을 더 강하게(부정 열거 대신 "use ONLY the body outline") 쓰기.
지금 상태로는 **REAL 전신 슬롯을 프로덕션에 켜면 안 된다** — 코드는 준비됐지만 자산을 붙이면 옷·배경이 오염된다.

## 28. 전신 참조 정화 재시도 + ZARA 컷 참조 생성 (2026-09-10)

### 28.A SAM 은 못 썼다 — GrabCut 으로 대체
`sam_client.py` 에는 `segment_garment`(상품 배경 제거)·`segment_worn_garment`(착장컷 의류 마스크) 둘뿐이고
**인물 분할 엔드포인트가 없다**. `sam_service_url=None` 이라 이 환경에 서비스도 안 붙어 있다.
새 모델을 받지 않고 OpenCV **GrabCut**(cv2 내장)으로 인물을 잘라냈다 — 씨앗 사각형은 YuNet 얼굴 박스에서
사람 몸 비율로 추정(`phase1/make_person_cutout.py`). 산출물 `phase1/body_ref/person_on_grey{,_1536}.png`,
마스크 `person_mask.png`. 인물 면적 8.7%, 발 아래 그림자 일부만 잔존.
**배경은 지워졌지만 옷(검은 티·카키 팬츠)은 그대로 남아 있다** — 배경 오염은 크롭이, 옷 오염은 라벨이 막아야 한다.

### 28.B 라벨 강화
`_MODEL_FULL_BODY_LABEL` 을 부정 열거에서 **긍정 지시 + 명령형 금지**로 바꿨다. 접두어 `"MODEL FULL BODY —"`
는 유지(`cut_output_qc.py:293` 이 이걸로 역할을 판정).

```
MODEL FULL BODY — use ONLY the body outline and proportions from this image: height, head-to-body
ratio, shoulder width and slope, torso length and build, waist, pelvis and hip width, and arm and leg
proportions. The subject is cut out on a flat grey field; that field is not a location. Do NOT copy its
background, location, garments, shoes, pose, framing or camera distance; PRODUCT and MATCHING own the
clothing and SPACE SET PLATE owns the location. ZERO authority over facial identity, facial features or hair
```

`test_cut_generator.py::test_build_manifest_places_exact_virtual_model_labels_after_mannequin` 의 기대 문자열을
갱신하고 이유를 주석으로 남겼다. **VIRTUAL 경로도 같은 라벨을 쓴다 — 이번엔 REAL 만 측정했으므로 가상모델
컷에 대한 영향은 미측정이다.**

### 28.C 전신 A/B 2회차 — **불채택**
같은 시드·같은 스펙, 전신만 정화본(`person_on_grey_1536.png`)으로. 6렌더.

| 컷 | 팔 | SFace | 배경 승자 | 하의 RGB |
|---|---|---|---|---|
| full | 없음 | 0.781 | 참조 | [57, 55, 53] |
| full | **있음(정화)** | **0.695** | 참조(18.8 vs 18.1) | [106, 100, 96] |
| full | 있음(원본, 1회차) | 0.476 | 참조 | [161, 155, 147] |
| three_quarter | 없음 | 0.822 | 장면판 | [181, 173, 173] |
| three_quarter | **있음(정화)** | **0.845** | 장면판 | [175, 167, 167] |
| three_quarter | 있음(원본) | 0.743 | 장면판 | [170, 163, 164] |
| seated | 없음 | 0.855 | 장면판 | [186, 168, 160] |
| seated | **있음(정화)** | **0.858** | 장면판 | [147, 133, 128] |
| seated | 있음(원본) | 0.883 | 장면판 | [162, 143, 135] |

`hist_L1` 은 이제 **회색 배경 참조** 대비라 1회차 값(1.476/1.207/0.375)과 비교 불가다 — 전부 1.96~1.99 로
회색이 지배한다. 지표로서 죽었다.

**진전**: 배경 오염은 사라졌다. 육안(`phase1/body_ab2/body_ab_4col.png`)에서 "있음-정화" 3컷 모두 상점 배경을
유지한다(1회차 "있음-원본" 은 실외 골목이었다).
**남은 실패**: 하의가 3컷 모두 카키/브라운으로 바뀐다. 정화본에도 참조의 카키 팬츠가 남아 있고 강화한 라벨로도
못 막았다.

판정 규칙(사전 고정) 대로:
- 배경 |Δ| 장면판 < 참조 → `full` 실패(18.8 vs 18.1). ※ 참조 배경이 평평한 회색이라 이 비교는 퇴화했다 —
  밝은 실내 벽이면 회색과 가까워진다. 육안으로는 `full` 도 상점 배경을 유지했다.
- 하의 RGB "없음" ±15 → `full` Δ49 · `seated` Δ39 실패(`three_quarter` Δ6 통과)
- SFace "없음" −0.05 이내 → `full` −0.086 실패
- 육안 오염 없음 → 하의 오염 있음, 실패
→ **불채택.**

**체형은 판정 불가다.** 배경은 걷혔지만 하의가 통짜 카키로 바뀌어 실루엣 판단이 오염된다. `full·있음(정화)` 이
"없음" 보다 다리가 조금 짧고 몸이 살짝 넓어 실물 방향으로 보이지만, 옷이 바뀐 상태라 체형 효과와 분리되지 않는다.

**다음 후보(미검증)**: ① 참조를 실루엣/뎁스맵으로 추상화해 옷 정보를 아예 제거 ② 참조를 상반신·하반신
비율 수치로만 전달(사진 없이) ③ 옷을 중립 회색 단색으로 리페인트한 참조.

### 28.D ZARA 컷을 EXAMPLE 로 넣고 새로 생성 (경로 A) — **불채택, 다만 실패 지점이 하나뿐**
입력 5장(MODEL FACE · MODEL SHEET · PRODUCT 앞/뒤 · EXAMPLE). SPACE SET PLATE 없음.
MODEL FULL BODY 도 없음(28.C 불채택). `bg` 는 EXAMPLE 을 **첫 첨부**로 넣고 매니페스트도 맨 앞 재번호
(`editor_image_job.py:612` 와 동일). 인물 제거 판은 GrabCut 마스크 + `cv2.inpaint(TELEA)`.

| 컷 | SFace | 파랑비율(ZARA 줄무늬) |
|---|---|---|
| zara2 원본(남) | 0.178 | 0.411 |
| zara2 경로B 얼굴패스 | 0.765 | 0.405 |
| zara2 **경로A all** | **0.837** | **0.000** |
| zara2 **경로A bg** | **0.872** | **0.000** |
| zara1 원본(남) | 0.149 | 0.389 |
| zara1 경로B 얼굴패스 | 0.714 | 0.382 |
| zara1 **경로A all** | **0.811** | **0.000** |
| zara1 **경로A bg** | **0.778** | **0.000** |

- **옷: 통과** — 4/4 에서 디네뎃 회색 티가 나왔고 ZARA 줄무늬는 0.0 이다. PRODUCT 가 EXAMPLE 을 이겼다.
- **얼굴: 통과** — 0.778~0.872, 전부 ≥0.70 이고 경로 B(0.714·0.765)보다 **+0.06~+0.11** 높다.
- **이음선: 통과** — 합성이 없으므로 구조적으로 0.
- **장면: 실패** — ZARA 는 실내 흰 벽 스튜디오인데 4/4 모두 실외(건물 외벽·나무·보도)를 만들었다.

→ 규칙대로 **불채택**. 다만 실패 원인 둘 다 EXAMPLE 슬롯의 결함이 아니다:
1. `refScope="all"` 은 **설계상 같은 장소를 재현하지 않는다.** 프롬프트 계약이 그렇게 쓰여 있다 —
   "Styling/all keeps scene type ... **use a coherent different location instance**". 남의 컷을 그대로 복제하지
   않으려는 제품 의도다. 그러니 all 로 "장면이 ZARA 를 따름" 을 요구한 건 애초에 성립하지 않는 조건이었다.
2. `refScope="bg"` 계약은 맞다 — "The FIRST attached image is the finished, real location of this photo...
   This exact plate owns the location". 실패한 건 **내가 만든 판이 퇴화**해서다. zara2 는 인물이 프레임의 65%라
   지우고 나니 거의 백지가 됐고, zara1 도 민벽+바닥만 남았다. 앵커가 없으니 모델이 장소를 지어냈다.

**경로 A 가 경로 B 를 대체할 수 있는가 — 한 문장**: 옷·얼굴·이음선 세 축에서는 경로 A 가 경로 B 를 이겼고
(줄무늬 0 · SFace +0.06~+0.11 · 이음선 구조적 0), 남은 것은 장소 재현 하나인데 그건 EXAMPLE 슬롯의 한계가
아니라 `all` 의 설계 의도와 내가 만든 bg 판의 품질 문제라서, **대체 가능성은 높지만 이번 측정으로 확증되지 않았다.**

**다음에 할 것(미검증)**: 배경이 넉넉한 컷으로 bg 판을 다시 만들어(인물이 프레임의 25% 이하) `refScope="bg"`
를 재측정. 그때 장소가 따라오면 경로 A 로 갈아탈 근거가 선다.

## 29. ZARA 참조 생성 재측정 — cutType styling → horizon (2026-09-10) — **채택**

§28.D 에서 경로 A 가 4/4 실외로 나온 원인은 EXAMPLE 슬롯이 아니라 **cutType** 이었다.
`styling` 이면 `cut_plan.uses_styling_all_location_recomposition()` 이 참이 되어
`"use a coherent different location instance in the same visual family"` 가 붙고
TASK 가 `"lifestyle styling cut"` 으로 렌더된다. ZARA 는 흰 배경 스튜디오라 `horizon` 이 맞다.

**문구 제거 확인**(렌더 전, API 0회):
```
styling  all  · 'different location instance' 남아 있음 ✗ · TASK: Create ONE lifestyle styling cut photo ...
styling  bg   · 'different location instance' 없음 ✓
horizon  all  · 'different location instance' 없음 ✓ · TASK: Create ONE clean studio horizon cut photo ...
horizon  bg   · 'different location instance' 없음 ✓
```
(그 문구는 `all` 에만 붙는다 — `styling·bg` 에는 원래 없었다.)

cutType 외 나머지(입력 5장·시드·refScope·프로필·bg 판)는 §28.D 와 동일. 4렌더.

| 컷 | SFace | 줄무늬 | 배경 RGB | 배경 채도 |
|---|---|---|---|---|
| zara2 원본(남) | 0.178 | 0.411 | [200,200,196] | 5.7 |
| zara2 경로B 얼굴패스 | 0.765 | 0.405 | [200,200,196] | 5.7 |
| zara2 A-styling-all(§28) | 0.837 | 0.000 | [162,160,155] | 10.6 |
| zara2 **A-horizon-all** | **0.843** | **0.000** | **[227,226,226]** | **0.6** |
| zara2 A-horizon-bg | 0.858 | 0.000 | [123,125,118] | 12.6 |
| zara1 원본(남) | 0.149 | 0.389 | [210,210,206] | 5.3 |
| zara1 경로B 얼굴패스 | 0.714 | 0.382 | [210,210,206] | 5.3 |
| zara1 A-styling-all(§28) | 0.811 | 0.000 | [145,141,133] | 13.6 |
| zara1 **A-horizon-all** | **0.785** | **0.000** | **[214,214,214]** | **0.8** |
| zara1 A-horizon-bg | 0.851 | 0.000 | [134,136,131] | 8.2 |

**판정 = 채택** (규칙: 두 장면 중 적어도 한 scope 에서 흰 스튜디오 · 회색 티 · SFace ≥0.70 · 육안 이음선/뜸 없음).
`horizon-all` 이 **두 장면 모두**에서 네 조건을 전부 만족한다.
- 장소: 흰 벽 스튜디오. 배경 채도 0.6·0.8 로 원본(5.7·5.3)보다 오히려 더 무채색이고, styling-all(10.6·13.6)과 확연히 갈린다.
- 옷: 줄무늬 0.000 — 디네뎃 회색 티. PRODUCT 가 EXAMPLE 을 이겼다.
- 얼굴: 0.843·0.785, 경로 B(0.765·0.714) 대비 +0.078·+0.071.
- 이음선: 합성이 없어 구조적으로 0.

`horizon-bg` 는 여전히 실외다 — §28.D 에서 진단한 대로 **bg 판이 퇴화**(zara2 는 인물이 프레임 65%라
지우니 백지, zara1 도 민벽+바닥)해서 앵커가 없다. cutType 과 무관한 별개 원인이 확인된 셈이다.

**경로 B 의 "뜨는" 세 가지 — 경로 A 에서는 셋 다 없다**(`zara_example_h/seam_compare.png`):
| 증상 | 경로 B(얼굴패스) | 경로 A(horizon-all) |
|---|---|---|
| 머리 위 벽 톤 사각 패치 | 두 컷 모두 뚜렷 | 없음 |
| 벽 그림자가 패치 경계에서 잘림 | 두 컷 모두 | 없음 |
| 목·어깨 양옆 흰 얼룩 | 두 컷 모두 | 없음 |

**브랜드 룩북은 편집(경로B) 대신 생성(경로A)으로 가도 되는가 — 한 문장**: 장소·옷·얼굴·이음선 네 축에서
`horizon-all` 이 경로 B 를 전부 이겼고(흰 스튜디오 재현 · 줄무늬 0 · SFace +0.07~+0.08 · 합성 이음선 구조적 0),
남의 룩북을 픽셀로 편집하지 않고 참조로만 쓰는 쪽이 법적으로도 안전하므로 **가도 된다** —
단 cutType 을 장면에 맞게 골라야 하고(스튜디오=horizon, 라이프스타일=styling), `bg` scope 는 배경이
넉넉한 컷(인물이 프레임 25% 이하)에서 판을 다시 만들어 재측정해야 한다.

## 30. ZARA 스튜디오 컷 "똑같이" 재현 — 편집 두 안 비교 (2026-09-10) — **안 B 채택**

> ★ 사진 픽셀 편집은 **브랜드가 소유한 사진에만** 적용한다 — ZARA 는 테스트 대역이다.

**§29 의 채택은 취소한다.** 목표가 "비슷한 흰 스튜디오"가 아니라 "원본과 벽 톤·벽 그림자 방향·조명·바닥 띠·
구도·포즈가 **같고** 사람만 우리 인물 + 상의만 디네뎃 회색 티" 였다. `horizon-all` 은 장면을 새로 그리는
모드라 설계상 이 목표에 맞지 않는다.

### 30.A 안 A — 깨끗한 빈 판 → bg_edit
**A-1(판 생성)은 성공했다.** gpt-image-2.5-flare 에 원본 1장 + "Remove the person completely, including their
cast shadow ... Keep the exact wall color and texture, floor strip, light direction and color temperature" 로
장면당 1회. 두 판 모두 **사람·그림자 잔상 없이** 벽 질감·바닥 띠·색온도가 원본대로 나왔다
(`phase1/studio/{zara2,zara1}_plate.png`, 배경 |Δ| vs 원본 20.5 · 7.0).

**A-2 는 실패했다.** 그 깨끗한 판을 `refScope="bg"` 첫 첨부로 넣고 `[[CUT:bg_edit]]` 계약
("The FIRST attached image is the finished, real location ... base canvas")을 태웠는데도 두 컷 모두
**실외**(나무·석재 벽)를 그렸다. 배경 |Δ| vs 원본 **131.7 · 113.3**.
→ §28.D 에서 "bg 판이 퇴화해서 실패했다" 고 진단했던 것은 **틀렸다.** 판을 깨끗하게 줘도 같은 결과다.
   `bg_edit` 계약 자체가 이 모델에서 안 먹는다.

### 30.B 안 B — ZARA 원본을 캔버스로 두고 사람·상의만 교체 — **채택**
스크립트 전용 프롬프트(코드에 섹션 추가 없음). 첨부 5장: BASE PHOTO(원본) · MODEL · MODEL SHEET · PRODUCT 앞/뒤.
```
Edit the BASE PHOTO in place. Keep its exact framing, camera, pose, body position, wall, floor, lighting
and cast shadow. Replace ONLY the person's face and hair with the MODEL references, and replace the top
with the PRODUCT garment. Keep the trousers and shoes as in the base photo. Do not keep the striped shirt
or the blonde hair.
```
hair · face_shape · physique 블록 동일 첨부.

| 컷 | SFace | 줄무늬 | 금발 | 배경 \|Δ\| vs 원본 |
|---|---|---|---|---|
| zara2 원본 | 0.178 | 0.411 | 0.357 | 0.0 |
| zara2 A-1 빈 판 | — | — | — | 20.5 |
| zara2 A-2 bg_edit | 0.855 | 0.000 | 0.062 | **131.7** |
| zara2 **B 캔버스 편집** | **0.861** | **0.000** | **0.058** | **10.2** |
| zara2 horizon-all(§29) | 0.843 | 0.000 | 0.072 | 33.6 |
| zara1 원본 | 0.149 | 0.389 | 0.342 | 0.0 |
| zara1 A-1 빈 판 | — | — | — | 7.0 |
| zara1 A-2 bg_edit | 0.804 | 0.000 | 0.062 | **113.3** |
| zara1 **B 캔버스 편집** | **0.757** | **0.000** | **0.069** | **7.2** |
| zara1 horizon-all(§29) | 0.785 | 0.000 | 0.074 | 26.3 |

판정 항목 1~6(사후 이동 없음):
1. **벽 톤·벽 그림자 방향·바닥 띠** — B 통과. 배경 |Δ| 10.2·7.2 로 A-2(131.7·113.3)·horizon-all(33.6·26.3)과
   자릿수가 다르다. 확대(`studio/B_zoom.png`)에서 왼쪽 벽 그림자의 방향·강도가 원본과 같다.
2. **포즈·구도** — B 통과. 서 있는 자세·팔 위치·프레이밍이 원본 그대로. A-2 는 장면 자체가 달라 실패.
3. **상의** — B 통과. 줄무늬 0.000(원본 0.411·0.389), 디네뎃 회색 티에 프린트도 정확.
4. **머리** — B 통과. 금발 지표 0.058·0.069 (원본 0.357·0.342). 육안으로 흑발, 금발 잔상 없음.
5. **얼굴 SFace** — B 통과. 0.861·0.757, 둘 다 ≥0.70.
6. **새 인물의 벽 그림자** — B 통과. 사람이 원본과 같은 자리에 있어 원본 그림자가 그대로 맞고, 두 겹이 없다.

→ **안 B 채택.** A·B 중 3·4 오염(줄무늬·금발)은 둘 다 같은 수준이지만 1·2 에서 A-2 가 완전히 실패한다.

부수 효과로 하의·신발이 **원본 그대로** 유지된다(녹색 팬츠·구두). 상의만 바꾸는 게 목표였으므로 의도한 동작이다.

### 30.C 새로 확정된 사실
- `[[CUT:bg_edit]]` 계약은 gpt-image-2.5-flare 에서 **작동하지 않는다.** 판이 깨끗해도 장소를 지어낸다.
  §28.D 의 "판 퇴화가 원인" 진단은 이번 실험으로 반증됐다.
- 같은 모델이 **BASE PHOTO 를 직접 편집하라**는 지시(안 B)는 정확히 따른다. 즉 이 모델에서 "장소 고정"은
  참조 슬롯이 아니라 **캔버스 편집** 으로 가야 한다.
- 다만 안 B 는 원본 픽셀을 편집하므로 **브랜드가 소유한 사진에만** 쓸 수 있다. 남의 룩북에는 못 쓴다 —
  그 경우는 §29 의 `horizon-all`(참조로만 사용, 장소는 새로 생성)이 남는 선택지다.

## 31. 사람만 교체 — 옷·배경·포즈 원본 유지 (2026-09-10) — **P1 채택, 트랙 종료**

> ★ 사진 픽셀 편집은 **브랜드가 소유한 사진에만** 적용한다 — ZARA 는 테스트 대역이다.

브랜드가 자기 룩북을 주고 "모델만 바꿔 달라" 는 시나리오. §30 안 B 와 달리 **옷을 바꾸지 않는다**.
입력 3장(BASE PHOTO · MODEL · MODEL SHEET) — PRODUCT 미첨부, physique 블록 미첨부(몸은 원본 것을 유지),
face_shape 블록은 첨부. 모델 id 확인 출력: `resolve_editor_cut_model = 'gpt-image-2.5-flare'` (정확히 일치),
`image_high = gemini-3-pro-image` 유지.

- **P1**(기본안, hair 블록 포함): "Replace ONLY the person's face and hair with the MODEL references."
- **P2**(대조군, hair 블록 없음): "Replace ONLY the person's face ... Keep the original hair exactly as it is."

| 컷 | SFace | 줄무늬(원본 대비) | 금발 | 배경 \|Δ\| | 해상도 |
|---|---|---|---|---|---|
| zara2 원본 | 0.178 | 0.4112 | 0.357 | 0.0 | 2048×3072 |
| zara2 **P1 얼굴+머리** | **0.840** | **0.4246** (+0.013) | **0.061** | **8.9** | 1024×1536 |
| zara2 P2 얼굴만 | 0.456 | 0.4230 (+0.012) | 0.387 (원본 유지) | 4.5 | 1024×1536 |
| zara2 안B(상의까지, §30) | 0.861 | 0.000 | 0.058 | 10.2 | 1024×1536 |
| zara2 경로B 얼굴패스 | 0.765 | 0.4045 | 0.042 | 8.5 | 2048×3072 |
| zara1 원본 | 0.149 | 0.3890 | 0.342 | 0.0 | 2048×3072 |
| zara1 **P1 얼굴+머리** | **0.807** | **0.4032** (+0.014) | **0.075** | **6.4** | 1024×1536 |
| zara1 P2 얼굴만 | 0.780 | 0.4025 (+0.014) | 0.061 (**원본 미유지**) | 8.1 | 1024×1536 |
| zara1 안B(상의까지) | 0.757 | 0.000 | 0.069 | 7.2 | 1024×1536 |
| zara1 경로B 얼굴패스 | 0.714 | 0.3823 | 0.036 | 4.3 | 2048×3072 |

판정 1~6 (사후 이동 없음) — **P1 두 장면 모두 통과**:
1. **옷 원본 유지** ✓ 줄무늬 원본 대비 +0.013·+0.014 로 ±0.03 안. 육안으로 녹색 팬츠·구두·목걸이 그대로.
2. **배경 |Δ|** ✓ 8.9·6.4 — §30 안 B(10.2·7.2)와 같은 수준.
3. **포즈·구도·손 위치** ✓ 원본 그대로.
4. **얼굴 SFace** ✓ 0.840·0.807, 둘 다 ≥0.70.
5. **머리** ✓ 금발 0.061·0.075(원본 0.357·0.342) — 흑발로 교체됨.
6. **이음선·뜸** ✓ 확대(`studio/P1_zoom.png`)에서 벽 그림자가 원본과 똑같이 이어지고 머리 위 사각 패치가 없다.
   같은 확대의 경로 B 는 사각 패치가 뚜렷하고 그 경계에서 벽 그림자가 잘린다.

**P2 는 대조군이라 채택 대상이 아니다.** 기록만: zara2 는 금발을 유지했지만 얼굴 교체가 약해 SFace 0.456
(<0.70). zara1 은 "원본 머리를 유지하라" 는 지시를 따르지 않고 흑발로 바꿨다(금발 0.061). 즉 **얼굴만 바꾸는
지시는 이 모델에서 불안정**하다 — 얼굴+머리를 함께 바꾸는 P1 이 안정적이다.

### 31.1 해상도 (확인만, 변경 없음)
- 출력은 **1024×1536**. 스크립트가 `image_size="1K"` 를 넘기고 `gemini_image._openai_size` 가
  `_OPENAI_SIZE["2:3"] = "1024x1536"` 로 해석한다. 원본은 2048×3072 이므로 **가로세로 1/2 로 줄어든다.**
- 코드에는 `_OPENAI_2K_SIZE["2:3"] = "1536x2304"` · `_OPENAI_4K_SIZE["2:3"] = "2336x3504"` 맵이 이미 있다.
- **`gpt-image-2.5-flare` 가 그 크기를 실제로 받는지는 확인되지 않았다.** 이 세션에서 호출해 본 적이 없고
  응답이나 문서에서 확인한 사실도 없다. 추측하지 않는다.

### 31.2 결론
**브랜드 룩북은 경로 B(얼굴 패스 타원 합성) 대신 캔버스 편집으로 간다 — 성립한다.** 같은 두 장면에서
캔버스 편집(P1)이 얼굴 0.840·0.807 로 경로 B(0.765·0.714)보다 높고, 경로 B 에 남던 머리 위 사각 패치·
잘린 벽 그림자·어깨 얼룩이 P1 에는 없으며, 옷·배경·포즈는 원본 그대로다. 단 **브랜드가 소유한 사진에만**
쓸 수 있고, 남의 룩북처럼 픽셀을 못 건드리는 경우에는 §29 `horizon-all`(참조로만, 장소는 새로 생성)이 남는다.

### 31.3 이 트랙에서 남은 미결
1. **제품화** — `base_edit` 컷 모드 신설(지금은 스크립트 전용 프롬프트)과 "브랜드 소유 사진만" 게이트 설계.
   소유 증명을 무엇으로 받을지(업로드 주체·계약 플래그)가 정해져야 한다.
2. **얼굴 패스를 이 결과에 걸지** — P1 결과는 이미 0.807~0.840 이라 §26.5 와 같은 상황(패스가 손해일 수 있음).
   측정 안 했다.
3. **해상도** — 1024×1536 출력이 원본 2048×3072 의 절반이다. 2K/4K 맵이 이 모델에서 되는지 미확인.
4. 이전 트랙 미결 그대로: 전신 슬롯(§28.C 불채택, 옷 오염 처방 3개), `bg_edit` 계약이 이 모델에서 죽은 것(§30.C),
   Phase 1 얼굴 패스 온오프(§26.5).
