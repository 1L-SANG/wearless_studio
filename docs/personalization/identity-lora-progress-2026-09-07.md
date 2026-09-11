# 인물 LoRA(ohwx man) 진행 기록 — 2026-09-04 ~ 09-07

목표: FaceMarket 등록 인물이 착용컷에서 **실제 본인과 같게, 컷마다 일관되게** 나오게 한다. 지표는 prod 와 같은 SFace(YuNet 정렬) 코사인 유사도, 홀드아웃 실사 6장 대비 중앙값.

## 0. 기준선 (눈금)

| 대상 | 중앙 | p10 | 비고 |
|--|--|--|--|
| 실사끼리(같은 사람) | 0.853 | 0.736 | 천장 |
| 타인끼리 | 0.384 | — | 최대 0.710 |
| prod 인물컷(Gemini, 가상모델 31컷) | 0.763 | 0.681 | 0.542~0.887 |
| prod 경로로 만든 이 사람 컷 6장 | 0.73 | — | 참조사진+옷 → gemini-3-pro-image, 카페 장면 |

측정 창: yaw ≤ 0.16(v2) / 0.25(v3 이후), 눈간격 40~200px. 측면·후면은 지표가 붕괴하므로 육안.

## 1. 데이터

- 원본: `~/Downloads/train_1024` 63장(동일 남성, 회색 티 1벌) → 학습 57 + 홀드아웃 6(`person_holdout`). HEIC 회전 함정 2겹.
- v3부터 79장: 57 + HEIC 원본 재크롭 22(정면 반신 → 머리·어깨 768×1024).
- 옷 누끼 control(`lora_train_v1/edit/control`) 30장, 홀드아웃 옷 5벌(`lora_holdout_v1`).
- v4c: 79 → 얼굴폭×3 정사각 크롭 1024² 78장(후면 tw_062 제외).
- v5: 78 + 조명 증강 78 + 신규 촬영 40(HEIC 48 중 홀드아웃 8 제외, 폴더=조명 4종) + 그 증강 40 = **236쌍**. 증강 = R/B 목표 역산(U[1.19,1.43] → 결과 중앙 1.276, prod 카페 1.27 과 일치), 밝기 ×0.8~1.2, 방향광 0.08~0.20, target·control 동일 파라미터.
- 캡션(v5): 표정 라벨 neutral 54 / slight smile 19 / smiling 5, 프로필·미검출 11장은 표정 없음. 원본 `soft even indoor lighting`, 증강 `warm indoor light`. 옷 문구 제거.

## 2. 학습 이력

모델 Qwen-Image-Edit-2509(ai-toolkit `qwen_image_edit_plus`), rank 16, lr 1e-4, batch 1·accum 1, bf16, gradient checkpointing, A100 80GB.

| 런 | 데이터 | control | 핵심 설정 | 300 | 600 | 900 | 1200 | 1500 | 결론 |
|--|--|--|--|--|--|--|--|--|--|
| v2 (armA_63) | 57 | 옷 누끼 | 상수 lr, 마스크 없음 | 0.242 | 0.188 | 0.377 | **0.516** | 0.498 | 1200 최적. 없던 옷 전이 실패, 얼굴↔옷 상충(strength 스윕) |
| v3 | 79 | 옷 누끼 | 얼굴 마스크 1.0/0.1, inverted prior 0.5, cosine | 0.253 | 0.528 | 0.619 | 0.663 | **0.673** | 마스크가 옷 결합을 끊음. 없던 옷 단추 정확, 체형 4/4. 전체 생성 상한 ≈0.67 |
| v4 (09-06) | 79→52 | 인물 재렌더 | — | — | — | — | — | — | **보류**. control 재렌더가 프레이밍을 못 지킴(정렬 35/79), zero-shot inpaint 관문 미달 |
| **v4c** | 78 크롭 | 얼굴 블러 σ0.5 | 1024², 타원 마스크 floor 0, prior 0.5, cosine 1500 | 0.270 | 0.467 | 0.639 | 0.667 | **0.671** | prod 컷 inpaint **0.62**, holdout **0.746**(상승 중). 파이프라인 확정 |
| **v5** | 236 | 얼굴 블러 σ0.5 | v4c-1500 가중치 이어받기, 2000 cosine, 표본 500 마다 16 | — | — | — | — | — | 표본 500/1000/1500/2000 은 아래. **2000: holdout 0.850 · prod 0.729(14장 0.723) → Gemini 수준**, 재촬영 불필요 |

v4c 세부(1500): prod 6 = weak_sit 0.49 · good_front 0.65 · good_cafe 0.67 · good_close 0.59(weak_34 는 3/4 회전, weak_full 은 표본 프롬프트 결함으로 탈락). holdout 6 = 0.67~0.78.

v5 표본(홀드아웃 6 / 14장 두 열): 500 = holdout 0.806/0.770 · prod 0.656/0.641 · 표정 0.704/0.679 → 1000 = 0.837/0.796 · 0.654/0.647 · 0.722/0.712 → 1500 = 0.856/0.812 · 0.721/0.711 · 0.743/0.726 → **2000 = 0.850/0.807 · 0.729/0.723 · 0.757/0.742**. 파드1($5 계정)이 step ≈1000 에서 사망 → R2 900 체크포인트로 파드2 재개(optimizer 복원, cosine 이어짐). 산출물 `~/Downloads/lora_runs/v5_ckpt/`(R2 md5 대조 완료), 세부는 handoff 문서 §8.

## 3. 09-06 실험에서 확정된 사실

1. **LoRA 는 diffusers 에서 정상 작동**(ai-toolkit `diffusion_model.*` 키 그대로 로드, 840/840 모듈). Plus 파이프라인 0.123 → 0.653(fuse 0.731). scale 1.5~2 는 악화.
2. **zero-shot 얼굴 inpaint 는 실패**: 입력이 조건이라 denoise ≤0.75 에선 원본 얼굴 복원(on≈off). 얼굴을 지우고 denoise 1.0 이어야 LoRA 가 먹는데 상한 0.47~0.51. 변형 6종(블러·회색·노이즈·scale·sharp 프롬프트) 전부 S(on) <0.47.
3. **크롭 1024 zero-shot**: 회색·블러 채움을 모델이 "물체"로 유지(4/6 실패), good_front 만 0.61. → 학습으로 해결한 것이 v4c(300 step 부터 12/12 정상 얼굴).
4. control 을 EditPlus 재렌더로 만들면 정체성은 좋아도(0.84) 구도가 자유로워 학습 쌍으로 부적합(CFG 무관). 얼굴박스 유사변환 보정으로 56 까지 회복 가능하나 미사용.
5. prod 경로는 로컬에서 재현 가능: `cut_generator.generate()` 직접 호출(매니페스트 MODEL FACE + MODEL FULL BODY + PRODUCT Front, gemini-3-pro-image 1K 2:3). Gemini 호출 6회.

## 4. 추론 파이프라인(09-07 실측으로 정정: Plus 전체생성 + 합성)

prod 컷 → YuNet 얼굴 박스 → 얼굴폭×3 정사각 크롭 → 1024² → 얼굴+머리 타원(박스 위 0.6h·옆 0.3w·아래 1.15h) 안 가우시안 블러 σ=0.5×얼굴폭 → **`QwenImageEditPlusPipeline`(image=[블러 크롭], 25 step, cfg 4, LoRA 로드) 전체 생성** → 타원 영역만 페더로 크롭에 합성 → 원크기로 → 되붙임. 그 뒤 **게이트(얼굴 검출 & yaw ≤0.25 & 눈간격 40~200) 미달이면 seed 바꿔 재시도(최대 3)**.

`QwenImageEditInpaintPipeline`(비-Plus 템플릿 + 마스크 잠재 혼합)은 학습 조건 경로와 달라 블러 덩어리를 그대로 두는 증상(v4c 실측 4/6 탈락, 0.567) → **폐기**. v5 최종으로 C 경로 prod 6장: good 3 = 0.749/0.703/0.699(중앙 0.703, 같은 3장 Gemini 0.714/0.773/0.710), weak 3 은 seed 42~44 에서 탈락하나 weak_sit 은 seed 102·103 에서 게이트 통과. 프롬프트는 크롭 구도 문구로 고정(전신·3/4 문구는 회전·축소 유도).

## 5. 비용·인프라

- RunPod 지출: 09-04 파드(v2+v3) $19.7 · 09-06 파드1(v4 실험) $4.0 · 파드2(크롭 실험+v4c) $10.8 · 09-07 v5 파드1($5 계정, 소진) $5.0 + 파드2($15 계정, community $1.39/h) ≈$6.5 → **≈$46**. $15 계정 잔액 ≈$8.5. A100 SXM secure $1.59/h. 1024² 학습 9.1~9.3 s/step, 768×1024 7.0 s/step. A40 48GB 는 모델 로드 OOM.
- 산출물: `~/Downloads/lora_runs/{final_A63.tgz(v2), v3_ckpt/, v4c_ckpt/, v5_ckpt/, v4_session_2026-09-06/, v5_launch_kit/, v5_dataset.tgz}`, `~/Downloads/lora_train_v5/`. R2 `wearless-face/lora/{ohwx_man_v4c_1500.safetensors, v5_dataset.tgz, v5/ohwx_man_v5*.safetensors, v5/optimizer.pt(900)}`.
- 함정: `/workspace` FUSE(파이썬 멈춤) · `hf download`(구 CLI 제거) · 맥→RunPod 업로드 0.15~0.5MB/s(맥→R2 4MB/s, 파드는 R2 presigned 로 받기) · tar 추출이 `/root` 소유자를 바꿔 sshd 잠김(`--no-same-owner`) · pgrep 패턴 자기매칭 · zsh 배열 ssh 옵션.

## 6. 다음

1. 블라인드 육안 판정(`scratchpad/blind_final.png`, 정답 키 별도) → 재촬영 여부 확정(수치상 불필요: 14장 기준 prod 0.723 ≥ 0.64).
2. 서비스 배선: `gemini_image.py:231` provider 분기에 `local-*` 추가 → §4 경로(Plus + 합성 + 게이트/재시도), 인물별 LoRA 등록(R2 `lora/v5/ohwx_man_v5.safetensors`).
3. 연장은 이득 작음(holdout 1500→2000 정체). 하려면 가중치만 이어받아 새 cosine(optimizer.pt 는 900 시점).
