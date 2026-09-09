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
