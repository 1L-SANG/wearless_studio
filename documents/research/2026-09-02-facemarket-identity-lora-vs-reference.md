# FaceMarket 얼굴 정체성 — 인물별 LoRA vs 레퍼런스 주입 (멘토 답변 검토 + 판정)

- 작성: 2026-09-02. 오너가 멘토에게 던진 3문항(LoRA 데이터 양 / 베이스 모델 / 데이터 정제)에 대한 멘토 답변을 1차 문헌·공식 문서·저장소 실측으로 검증하고, FaceMarket에 맞는 해법을 정리.
- 조사 방법: 읽기전용 웹 에이전트 3개(학습 관행 / 제로샷 기법·벤치마크 / 비용·임계값·규제) + 저장소 코드·실험 기록. 확신도 표기: **high** = 페이지를 직접 열어 확인 / medium = 검색 스니펫 / low = 추론.
- 자매 문서: `2026-09-02-facemarket-body-shape-capture-solution.md`(체형), `2026-09-01-model-digitization-onboarding-systems.md` §3(온보딩 v1), `docs/personalization/prd.md` N1·N5, `docs/personalization/t2-2-face-match-research.md`(얼굴 대조 라이선스).

## 0. 한 줄 결론

> 멘토의 원칙("정체성은 베이스 모델이 아니라 **제어**의 문제", "데이터는 **배우게 할 특징만 뾰족하게**")은 맞고, 우리가 이미 그 원칙대로 가고 있다(레퍼런스 주입).
> 그러나 **"인물별 LoRA + 얼굴 레퍼런스 어댑터를 겹쳐라"는 1차 문헌 근거가 없는 조합**이고, FaceMarket에서 LoRA는 지금 필요한 도구가 아니다.
> **오너 재정의(같은 날 추가)**: 엔진을 바꾸는 게 아니라 **옷은 현행 엔진으로 입히고, 결과물의 얼굴 영역에만 모델별 LoRA를 뒤에서 덧입히는 2단계 설계**라면 이야기가 다르다. 위 반대 근거(엔진 전환)가 사라지고, LoRA의 강점(질감·미세 특징)을 의류 품질을 건드리지 않고 쓸 수 있다 → §7. 결론은 "측정 먼저"에서 "**2단계 얼굴 패스를 스파이크로 검증**"으로 바뀐다.
> 진짜 빠진 것은 여전히 **실존 얼굴에 대한 정량 측정**이다. 우리는 실존 얼굴로 정체성 유사도를 한 번도 재지 않았다. 측정 → 게이트 → (미달 세그먼트에 한해) Qwen-Image LoRA ADR 순서로 간다. 이는 개인화 PRD 비목표 N1과 같은 결정이다.

## 1. 멘토 답변 채점

| 문항 | 멘토 답변 | 판정 | 근거 |
|---|---|---|---|
| Q1 데이터 양 "20~30장 시작 후 보강" | 방향 맞음, 수치는 베이스 모델 의존 | **Flux.1-dev 인물 LoRA 정석은 12~20장**(Replicate 공식 12~18, Replicate 블로그 12~20, fal 10~20, Civitai 10~20, Segmind 10~15 — 전부 high). **Qwen-Image는 20~30장 최소**(커뮤니티 가이드, medium). SDXL은 20~40. 멘토 수치는 Qwen 기준으론 정확, Flux 기준으론 넉넉. DreamBooth 논문의 "3~5장"은 풀 파인튜닝+사전보존 기준이라 LoRA 실무와 비교 불가 |
| Q1 "무엇을 학습시킬지 데이터에 드러나야, 배경·의상은 변화" | **정확** — 가장 강한 부분 | BFL 공식(FLUX.2 klein 학습 문서): "Vary poses, angles, compositions / different lighting / **mix close-ups with full scenes** / **avoid repetitive backgrounds**"(high). 2025 논문(arXiv 2511.08061): 배경까지 학습하면 identity leakage·copy-paste 효과, 마스킹 시 정체성·다양성 동시 개선(high) |
| Q2 "베이스 모델보다 제어(얼굴 레퍼런스)로 동일성 유지" | **원칙은 정확** | 학계 흐름이 "인물별 학습 → 제로샷 제어"로 이동(HyperLoRA CVPR 2025: LoRA는 사실적이나 개별 학습 필요, 어댑터는 제로샷이나 자연스러움 부족 → LoRA 가중치를 생성하는 방향). 우리 파이프라인(face_front+그리드 레퍼런스 주입)이 이미 이 원칙 |
| Q2 "LoRA 학습 + 생성 시 얼굴 레퍼런스 동시 사용" | **근거 없음, 부작용 문서화됨** | ① 어떤 논문·공식 README도 인물 LoRA+얼굴 어댑터 병용을 권장 관행으로 기재하지 않음. PhotoMaker의 "LoRA와 협업"은 스타일 LoRA 맥락, InstantID의 LoRA 예시는 LCM 가속용, **IP-Adapter FaceID의 'LoRA'는 인물별이 아니라 아키텍처 부속물**(모델 카드 high) — 이 용어 혼동이 조언의 출처일 가능성. ② InstantID 공식 사용 팁: ID 강도↑ = 텍스트 제어력↓ + 과포화(high). ③ **WithAnyone 벤치마크(2025)**: 어댑터 계열은 정체성 점수는 높지만 복붙 편향(InstantID CP 0.337, PuLID 0.315)이 커서 표정·각도 변주가 죽고, 레퍼런스 편집기(Kontext 0.099, Qwen-Image-Edit 0.093)는 복붙이 거의 없음(high) |
| Q2 "베이스 모델은 중요하지 않다"(함의) | **틀림 — 라이선스·VRAM에서 결정적** | **FLUX [dev] 비상업 라이선스 v2.0**: FLUX.1 dev·Kontext dev·FLUX.2 dev 전부 "파인튜닝 파생물 포함 비상업 전용, 최종 사용자 대면 서비스 금지, 상업 라이선스는 BFL 재량·수수료"(bfl.ai 공식, high). 출력물 상업 이용은 허용되지만 **모델을 서비스에 쓰는 것 자체가 금지**. 상업 LoRA 베이스는 **Qwen-Image(Apache-2.0)** 또는 SDXL뿐. Qwen-Image LoRA 학습은 VRAM 32~48GB(커뮤니티, medium) → 우리 실험대(5060 Ti 16GB·RAM 16GB)로 불가, 클라우드 트레이너 필수 |
| Q3 "다각도·다표정, 안경·어두운 사진 제외, 배경·포즈 변화" | **정확**, 실행 규칙 4개가 빠짐 | 빠진 것: ① **캡션 규칙** — "얼굴은 한 글자도 쓰지 말고, 옷·배경·조명·구도는 반드시 써라"(BFL "describe everything except the concept", Civitai "trigger word IS the thing you haven't captioned", high) ② 트리거 토큰은 실제 단어 금지 ③ 근접 중복 제거·손으로 얼굴 가린 컷 제한(Replicate 공식 "hands in face framing positions" 경고) ④ **과적합 판정은 loss가 아니라 샘플**("Watch the samples, not the loss", 시각 피크 750~1500 스텝 — BFL×HF 공식) |
| (미언급) 사전보존/정규화 이미지 | — | Flux·FLUX.2·Qwen 공급자 문서 어디에도 없음 → 불필요. SD1.5/SDXL 풀 DreamBooth에서만 유효 |
| (미언급) 측정 지표 | **가장 큰 공백** | LoRA든 레퍼런스든 **SimGT(같은 사람의 다른 사진 대비)·SimRef·CP(복붙 편향)** 없이는 어떤 실험도 결론이 안 남. WithAnyone: "SimRef만 보고하는 관행이 복붙을 조장" |
| (미언급) 규제 | 공백 | 1인 얼굴 LoRA 가중치 = 파생 생체정보로 취급해야 함(§4) |

## 2. 저장소 실측이 말하는 것

| 사실 | 출처 |
|---|---|
| 개인화 PRD가 이미 결정: **N1 per-user LoRA/DreamBooth 초기 범위 제외, 제로샷 미달 세그먼트에 한해 별도 ADR로만 재상정. N5 자체 GPU 상시 운영 금지** | `docs/personalization/prd.md:31` |
| **실존 얼굴로 정체성 유사도를 잰 적이 없다.** α(Gemini)/β(Qwen-Image-Edit-2511)/γ(SDXL+ID어댑터) 스파이크 계획만 있고 결과 기록 없음 | `docs/personalization/phase0-spike-plan.md` |
| 가상 얼굴 기준 레퍼런스 주입 정체성: C방식(face_front+그리드) 스트레스 16/16 유지, 노션 시트 A/B 3구성 모두 합격 | 메모리 `virtual-model-identity-spike`, `identity_pack_ab_notion_2026-08-27/FINDINGS.md` |
| **레퍼런스를 늘리면 좋아진다는 증거는 우리 실측에 없다** — 시트 크롭(2차 생성물)이 주근깨를 뭉갰고 로고 글리치가 늘었음(v2 팩 이전). 단 이는 *생성한 시트*를 레퍼런스로 쓴 결과이며 *실사 다장* 레퍼런스는 미측정 | 같은 메모리 |
| REAL 경로 팩 = 실사 3장(정면/측면/45도)을 2×2에 배치한 `grid_sedcard` + `face_front`, 생성 없음 | `server/app/agents/face_grid.py`, `identity_source.py` |
| 얼굴 대조 QC = OpenCV SFace 쌍대 코사인 ≥ 0.363(OpenCV 기본), **기본 비활성**, 등록 시 3장 동일인 검사에만 사용(생성 결과 미검사). InsightFace buffalo_l 가중치는 비상업, SFace는 Apache-2.0 | `server/app/agents/face_qc.py`, `config.py:286`, `t2-2-face-match-research.md` |
| 실험대: RTX 5060 Ti 16GB VRAM / RAM 16GB(병목) / Windows | 메모리 `comfy-experiment-lab` |
| 발행용 엔진 A/B: gpt-image-2 69% vs Nano Banana Pro 9~22%; **Qwen-Image·Flux는 착용컷 A/B 이력 없음** | 메모리 `genex-model-ab-imagegen2-vs-nbpro` |

→ 멘토의 "LoRA로 가자"는 N1 결정을 뒤집자는 제안인데, 뒤집을 근거(제로샷 미달 실측)가 아직 없다.

## 3. 해법: 측정 → 게이트 → 조건부 LoRA

### 3.1 Phase 0 — 실존 얼굴 정량 스파이크 (1~2주, 컷 ~60장, 약 $30~50)

- **피험자**: 동의된 실존 얼굴 3~5명(오너·팀·서면동의 지인, PRD N3). 특징 분산: 짧은 머리/긴 머리, 주근깨·점 있는 얼굴 1명 이상, 안경 착용자 1명(레퍼런스는 무안경).
- **팔(arm)**: A 현행(face_front + 2×2 실사 그리드, 2장) / B 실사 다장(정면·좌우 3/4·측면·미소 등 4~6장, 그리드 없이 개별 첨부 — gpt-image 계열 5장 예제·Nano Banana Pro 14장 공식 지원) / C B + 고충실도 옵션(엔진이 `input_fidelity`류를 제공하면). 엔진: gpt-image-2(발행 파리티) + Nano Banana Pro(미리보기 축).
- **컷 유형**: 풀정면·미디엄·측면·후면·미소·시선 돌리기(우리 순종 결함 이력) — 표정·각도 변주 강제.
- **지표(자동 + 사람)**:
  - **SimGT**: 생성물 vs **레퍼런스에 안 쓴** 같은 사람 사진(홀드아웃). SFace(상업 무관) + FaceNet512 2종 평균. 절대값이 아니라 **실사-실사 쌍 대비 비율**로 읽는다(임베더별 스케일이 다름 — InsightFace 공식 "임계값은 항상 재계산").
  - **SimRef**: 참고용만(복붙으로 부풀 수 있음).
  - **CP 대리 지표**: 레퍼런스 포즈·시선·헤어 복사율(사람 판정) — WithAnyone CP ≤ 0.15가 "복붙 아님" 기준선.
  - **미세 특징 체크리스트**: 주근깨·점·흉터 재현 여부(어댑터·레퍼런스 계열의 알려진 약점).
  - 블라인드 쌍 판정 3인, 좌우 카운터밸런스(키 밴드 실험 교훈).
- **통과 기준(사전 고정)**: 세그먼트별 SimGT 비율 ≥ 0.8(잠정, 실측 후 확정) ∧ 사람 판정 동일인 ≥ 90% ∧ CP 대리 ≤ 15%. 미달 세그먼트가 나오면 §3.3.

### 3.2 Phase 1 — 통과 시 (레퍼런스 유지 + 게이트)

1. **생성 결과 SFace 게이트 켜기**: 등록 시 3장 검사에만 쓰던 QC를 착용컷 결과에도 적용. 임계값은 0.363 기본값이 아니라 **자사 데이터 FMR 1e-4 지점으로 재계산**(InsightFace 공식 방법론). 측면·저해상도·표정 컷은 오탐이 급증하므로 **정면·얼굴 크기 조건을 선행 필터**로 두고 나머지는 abstain → 사람 판정.
2. 미달 시 qc_corrections 재롤 1회(AG-06 인프라), 전탈락은 HOLD(조용한 채택 금지).
3. B팔이 A팔을 이기면 **REAL 경로 팩을 실사 다장으로 확장**(계약 개정: `has_model_sheet` 위치에 개별 실사 N장). 우리 이전 실측의 "레퍼런스 늘리면 노이즈"는 생성 시트 기준이므로 실사에서 재검증 필요.
4. 온보딩 14장 구성 보정(Agent A 진단): **중간 프레이밍(상반신) 0장이 최대 결함** → 얼굴 7 + 상반신 4 + 전신 2(체형 파이프라인용) + 얼굴 디테일 1로 재배분. 근접 중복·손으로 얼굴 가린 컷·필터 컷 제외.

### 3.3 Phase 2 — 미달 세그먼트가 있을 때만: 인물별 LoRA ADR

| 항목 | 결정 | 근거 |
|---|---|---|
| 베이스 | **Qwen-Image(Apache-2.0)** 단독. FLUX 계열 금지 | FLUX [dev] 라이선스 v2.0(파생물 포함 비상업, high); Qwen 계열 Apache-2.0 확인(`phase0-license-check.md`) |
| 학습 위치 | **클라우드 관리형 트레이너**(fal.ai Qwen Image Trainer $0.002/스텝 → 1,000스텝 $2, Replicate Flux 트레이너 $1.46/회 참고). 로컬 실험대 불가(Qwen LoRA 32~48GB VRAM) | PRD N5 + 비용 조사 |
| 규모 비용 | 50명 × $2~4 = **$100~200**. 비용은 장벽이 아님. 장벽은 **엔진 전환**(gpt-image-2 대비 Qwen-Image 착용컷 품질 미검증) | genex A/B |
| 데이터 | 16~20장(Qwen 하한). 얼굴 8각도 + 상반신 4~6 + 전신 2 + 얼굴 디테일 1, 다른 날·다른 옷·다른 장소 최소 2세션 | Agent A 합의 |
| 캡션 | 희귀 토큰 트리거 + 얼굴 묘사 0 + 의상·배경·조명·구도 전부 서술. 정규화 이미지 없음 | BFL/Civitai 공식 |
| 하이퍼 | 1,000~2,000스텝, LR 1e-4(얼굴 뭉개지면 5e-5), rank 16~32, batch 1, 250스텝마다 체크포인트 → **샘플로 선택**(피크 750~1,500) | BFL×HF, ai-toolkit 기본 config |
| 조합 | **LoRA 단독**으로 A/B(얼굴 어댑터 병용 금지). 비교군은 §3.1 레퍼런스 경로. 같은 지표(SimGT·CP·미세 특징) | WithAnyone·InstantID 팁 |
| 서빙 | 관리형 추론(fal/Replicate). 인물별 LoRA 파일을 격리 저장·즉시 삭제 가능 구조 | N5 + §4 |
| 판정 | LoRA가 미세 특징(주근깨·점)에서만 이기고 CP가 나쁘면 채택 안 함. 이기면 해당 세그먼트에만 적용(전면 전환 아님) | — |

### 3.4 ComfyUI 실험대의 역할

- **추론 실험**은 가능: 16GB VRAM에서 SDXL/Flux 추론, PuLID-FLUX·InfiniteYou 비교 — 단 FLUX 기반은 **연구 비교용만**(비상업). 
- **학습은 불가**: Flux.1-dev LoRA조차 공식 경로 24GB 최소, 16GB 프로파일은 1024px 기준 9~10시간/개 + RAM 16GB 스와핑 위험(4060 Ti 16GB 실측 유사, medium). Qwen-Image LoRA는 32GB+.
- 즉 멘토의 "ComfyUI에서 제어" 조언은 실험대 용도로는 유효하지만 **프로덕션 경로 변경의 근거는 아니다**.

## 4. 규제·계약 함의 (LoRA를 하게 되면)

| 관할 | 내용 | 확신도 |
|---|---|---|
| EU EDPB Opinion 28/2024 | 개인정보로 학습한 AI 모델은 일률적으로 익명이 아니며 사례별 판단. 익명 요건 = "모델에서 개인정보 추출 가능성 negligible" ∧ "쿼리로 얻을 위험 insignificant". **1인 얼굴 LoRA는 설계 목적이 그 사람 재현이라 요건과 정면 충돌 → 개인정보로 봐야 함**. 시정조치로 모델 자체 삭제 상정 | 문서 high / 적용 추론 medium |
| 한국 생체정보 가이드라인 | 원본에서 추출·생성한 **특징정보도 민감정보**(제23조). 유추하면 LoRA 가중치 = 파생 생체정보. PIPC의 가중치 명시 입장은 미확인 → **법률 자문 필수** | medium / 추론 low |
| 한국 개인화 동의 구조 | 우리 동의 아키텍처는 "서비스 이용 ↔ **학습 활용**"을 분리(PRD G2). 레퍼런스 주입은 학습이 아니지만 **LoRA는 학습 활용 → 별도 동의 분기 발동** | 저장소 |
| NY Fashion Workers Act | digital replica = "AI로 생성·강화된 모델 초상의 표현(얼굴·몸·음성)", 범위·목적·보수·기간 서면 동의, 캠페인별 재동의, 위임장으로 대체 불가. 철회 시 파기 의무는 조문·FAQ 침묵 | high |
| 설계 반영 | **파기 캐스케이드에 LoRA 가중치·학습 캐시·체크포인트를 명시** 추가(현행 "원본·팩·그리드·크롭 파생물"에 더해). 온보딩 v1 §3.3 계약 문안에 "학습 파생물 포함 30일 내 파기" 반영 | — |

## 5. 오너 결정 (추천안 포함)

1. **LoRA 착수 여부** — 추천: 지금은 아니오. N1 유지. §3.1 측정 스파이크를 먼저(비용 ~$50, 1~2주). 멘토에게는 "레퍼런스 경로 정량 측정 후 미달 세그먼트에 Qwen-Image LoRA를 A/B하겠다"고 회신.
2. **스파이크 피험자 확보** — 추천: 오너 본인 + 서면동의 2~4명, 주근깨/점·긴 머리 포함.
3. **생성 결과 SFace 게이트 도입** — 추천: 예. 스파이크 데이터로 임계값 재계산 후 shadow → 적용.
4. **온보딩 14장 재배분** — 추천: 얼굴 7 / 상반신 4 / 전신 2 / 얼굴 디테일 1 (체형 문서의 바디 베이스 세트와 정합).
5. **계약·동의 문안 선반영** — 추천: LoRA를 안 하더라도 "학습 파생물(가중치 포함) 파기"를 표준계약에 넣어 둔다(리크루팅 소구점, 나중에 문안 바꾸는 비용 회피).

## 6. 출처 (에이전트가 직접 연 페이지 위주)

- Replicate ostris/flux-dev-lora-trainer README — https://replicate.com/ostris/flux-dev-lora-trainer/readme
- Replicate 블로그 Fine-tune FLUX — https://replicate.com/blog/fine-tune-flux · 공식 docs(8×H100, ~$1.46/회) — https://replicate.com/docs/get-started/fine-tune-with-flux
- ostris/ai-toolkit — https://github.com/ostris/ai-toolkit (24GB 예제 config)
- BFL FLUX.2 klein 학습 문서 — https://docs.bfl.ml/flux_2/flux2_klein_training · BFL×HF LoRA 블로그 — https://huggingface.co/blog/black-forest-labs/flux-2-klein-lora
- **FLUX [dev] Non-Commercial License v2.0** — https://bfl.ai/legal/non-commercial-license-terms
- fal.ai flux-lora-fast-training($2/회) — https://fal.ai/models/fal-ai/flux-lora-fast-training · Qwen Image Trainer V2 — https://fal.ai/models/fal-ai/qwen-image-trainer-v2
- Civitai Flux 데이터셋 가이드 — https://civitai.com/articles/7777/detailed-flux-training-guide-dataset-preparation
- HF DreamBooth 문서/블로그 — https://huggingface.co/docs/diffusers/main/en/training/dreambooth · https://huggingface.co/blog/dreambooth
- arXiv 2511.08061(identity leakage/masking) — https://arxiv.org/html/2511.08061
- **WithAnyone**(SimGT/SimRef/CP) — https://arxiv.org/html/2510.14975v1 · InfiniteYou — https://arxiv.org/html/2503.16418v1 · HyperLoRA — https://arxiv.org/abs/2503.16944 · PuLID — https://arxiv.org/html/2404.16022v1
- InstantID — https://github.com/instantX-research/InstantID · PhotoMaker — https://github.com/TencentARC/PhotoMaker · IP-Adapter-FaceID 모델 카드 — https://huggingface.co/h94/IP-Adapter-FaceID
- Nano Banana Pro(14장·5인) — https://blog.google/technology/ai/nano-banana-pro/ · OpenAI 이미지 가이드 — https://developers.openai.com/api/docs/guides/images-vision
- Qwen-Image-Edit-2509 — https://github.com/QwenLM/Qwen-Image/blob/main/Qwen-Image-Edit-2509.md
- InsightFace 임계값 가이드 — https://www.insightface.ai/guides/choose-face-recognition-model-and-evaluate · OpenCV SFace 튜토리얼(0.363) — https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html (에이전트 페치 403, 저장소 config 주석과 일치)
- EDPB Opinion 28/2024 — https://www.edpb.europa.eu/our-work-tools/our-documents/opinion-board-art-64/opinion-282024-certain-data-protection-aspects_en (IAPP 해설 경유)
- NY DOL Fashion Workers Act 정의/FAQ — https://dol.ny.gov/new-york-state-fashion-workers-act-definitions · https://dol.ny.gov/new-york-state-fashion-workers-act-faqs
- Mirror Mirror AI("trained AI model built from photographs") — https://mirrormirrorai.com/blog/ai-model-likeness-licensing-digital-twins
- 미확인/추론 항목: Qwen-Image LoRA VRAM(32~48GB, 커뮤니티), 5060 Ti 실측 학습 시간(4060 Ti 대리), PIPC 가중치 입장, EDPB 문단 번호.


## 7. 오너 재정의 — 2단계 얼굴 적용 설계 ("옷은 현행 엔진, 얼굴만 LoRA로 덧입히기")

### 7.1 판정

**된다. 그리고 이 형태라면 LoRA는 FaceMarket에 맞는 도구다.** 근거 셋.

1. **표준 기법이다.** ComfyUI Impact Pack의 FaceDetailer가 정확히 이 패턴이다 — 얼굴 검출 → 여유 포함 크롭 → 확대 → 캐릭터 LoRA를 건 채 **낮은 denoise(0.3~0.45)로 얼굴 영역만 재생성** → 되붙이기. "기존 생성물 뒤에 얼굴만 다시 앵커링해 샷 간 일관성을 조인다"는 용도로 2023년부터 널리 쓰인다. (https://github.com/ltdrdata/ComfyUI-Impact-Pack, https://www.runcomfy.com/comfyui-nodes/ComfyUI-Impact-Pack/FaceDetailer — medium)
2. **§3의 반대 근거가 사라진다.** 엔진 전환 불필요(의류는 gpt-image-2 69% 승자 그대로), 마스크 밖은 픽셀 단위로 불변이라 **의류 무결성이 구조적으로 보장**된다. 그리고 §1에서 걱정한 복붙 편향(CP)은 포즈·표정·시선을 1단계가 이미 정해 두고 2단계는 다듬기만 하므로 거의 발생하지 않는다 — LoRA를 쓰기에 가장 이상적인 자리다.
3. **라이선스에서 LoRA가 오히려 깨끗하다.** 제로샷 얼굴 어댑터(InstantID·PuLID·IP-Adapter FaceID)와 스왑기(inswapper/ReActor/FaceFusion)는 추론 때 **InsightFace 얼굴 임베더(antelopev2/buffalo, 비상업 가중치)** 에 의존하고, FLUX Fill/Kontext dev는 라이선스 자체가 비상업이다. LoRA는 추론 시 얼굴 임베더가 필요 없다 → 베이스만 Apache-2.0(Qwen-Image 계열)이면 끝.
4. **관리형으로 서빙된다(PRD N5 충족).** fal.ai: `fal-ai/qwen-image-edit/inpaint`(프롬프트+이미지+마스크), `fal-ai/qwen-image-edit-2509-lora`(LoRA 최대 3개·scale), Qwen Image Edit Trainer($4/1,000스텝). (https://fal.ai/models/fal-ai/qwen-image-edit/inpaint, https://fal.ai/models/fal-ai/qwen-image-edit-2509-lora, https://fal.ai/models/fal-ai/qwen-image-edit-trainer — high, 페이지 열람)

단 **"된다"는 기법 수준의 사실이고, 커머스 2K 컷에서 이음새·조명·질감이 합격선인지는 우리 컷으로 재야 한다.** 그래서 §3.1 스파이크의 팔(arm)을 이 설계로 바꾼다.

### 7.2 파이프라인

```
[1단계 현행] 상품·매칭·마네킹·예시 + 얼굴 레퍼런스 → gpt-image-2 2K 착용컷
       │  (1-A 실사 얼굴 레퍼런스 유지 = 헤어·피부톤·두상이 이미 근접)
       │  (1-B 실사 얼굴을 외부 엔진에 안 보내고 '대역 얼굴'만 → 프라이버시 변형, §7.4)
[2단계 신규] 얼굴 패스
       ├ YuNet 검출(저장소 보유) → 여유 2~3배 크롭(헤어라인·목 경계 포함) → 1024 확대
       ├ Qwen-Image-Edit(-2509) + 모델별 LoRA(트리거 토큰) 낮은 강도
       │   지시: "make this the face of <token>; keep pose, expression, gaze, lighting, hair, clothing"
       ├ 축소 → 페더 마스크로 되붙이기 → 색 매칭
       └ 마스크 밖 픽셀 불변(의류·배경 보장)
[3단계 게이트] SFace SimGT(홀드아웃 실사 대비, Apache) + 표정·시선 유지(VLM 랜드마크) + 이음새/조명 QC
       → 미달 시 2단계만 재롤(~$0.03) / 2단계가 더 나쁘면 1단계 원본 채택
```

- **뒷모습·머리가 프레임 밖인 컷**은 2단계를 건너뛴다(정체성은 헤어·체형만). 측면 컷은 LoRA 학습 데이터에 측면이 있어야 한다(온보딩 14장에 좌우 측면 포함됨).
- **게이트가 2단계 실행 여부도 결정**: 1단계 결과가 이미 통과면 2단계 생략(비용 절감).

### 7.3 스파이크 팔 (같은 1단계 컷 위에서 비교)

| 팔 | 내용 | 실행 위치 | 학습 |
|---|---|---|---|
| A0 | 1단계 원본(현행) | — | 없음 |
| A1 | SDXL 인페인트 + 인물 LoRA FaceDetailer | **로컬 실험대**(5060 Ti 16GB로 SDXL LoRA 학습·추론 모두 가능, SDXL은 상업 허용 OpenRAIL-M) | 16~20장, 1,500~2,400스텝 |
| A2 | Qwen-Image-Edit + 인물 LoRA | fal 관리형 | Qwen 트레이너 $2~4/명 |
| A3 | Qwen-Image-Edit 제로샷(실사 2~3장 참조, 학습 없음) | fal 관리형 | 없음 |

- A1은 성숙한 경로라 빨리 돌려 "이 기법이 우리 컷에서 통하나"를 먼저 보고, A2가 프로덕션 후보다. **첫 기술 질문**: Qwen-Image(t2i) LoRA가 Edit 모델에 그대로 실리는지, 아니면 Edit 트레이너로 쌍 데이터 학습이 필요한지 — 착수 시 확인.
- 피험자·컷·지표는 §3.1과 동일(SimGT·표정/시선 유지·미세 특징 체크리스트·블라인드 3인) + **이음새/조명 결함 0**, **마스크 밖 픽셀 동일** 자동 검사.
- 규모: 3명 × 6컷 × 4팔 ≈ 72장, 학습 3명 × 2종 ≈ $20, 추론 <$10, 실험대 시간 1~2일.

### 7.4 1-B 변형(프라이버시)의 의미

1단계에서 실사 얼굴을 OpenAI/Google에 보내지 않고 **모델과 헤어·피부톤·연령대만 맞춘 '대역 얼굴'** 로 생성한 뒤 2단계에서 진짜 정체성을 입히면, 생체정보가 **우리 관리형 인프라 밖으로 나가지 않는다**(국외이전·제3자 제공 범위 축소, 폐쇄 엔진의 실인물 정책 거부 회피). 대가는 두상·헤어라인 불일치가 커져 2단계 마스크가 헤어까지 포함해야 한다는 것. 1-A로 먼저 검증하고 1-B는 후속 변형으로 둔다.

### 7.5 한계·리스크

- 풀샷 2K에서 얼굴은 150~300px — 크롭 확대로 다루지만 주근깨 같은 미세 특징은 그 축척에선 원래 몇 픽셀이다. 미세 특징의 진짜 무대는 미디엄·클로즈업 컷.
- 2단계가 표정·시선을 흔들 수 있다 → 낮은 강도 + 명시 지시 + 랜드마크 검사 + 재롤.
- 거울샷(폰이 얼굴 가림)·손 가림 컷은 마스크가 복잡 → 초기엔 건너뛰기.
- 체형은 여전히 모델 본인이 아니다 → 체형 문서의 실사 전신 시트와 결합해야 "디지털화"가 완성된다.
- 규제·계약(§4)은 그대로 적용: LoRA = 학습 활용 동의 분기, 가중치는 파기 캐스케이드에 명시.

### 7.6 §5 결정 항목 갱신

1. **N1 개정 ADR**: FaceMarket에 한해 "LoRA는 엔진이 아니라 **후처리 얼굴 패스**로만" 허용 — 추천: 예(원래 N1의 논거였던 엔진 전환이 이 설계엔 없음).
2. **1단계 모드**: 1-A 실사 레퍼런스 유지로 시작 — 추천: 예. 1-B는 프라이버시 변형으로 후속.
3. **스파이크 착수(§7.3)**: 추천: 즉시. 피험자 3명(오너 포함) 서면동의가 선결.
4. 생성 결과 SFace 게이트·14장 재배분·계약 선반영은 §5 3~5번 그대로.


## 8. 추가 (2026-09-03) — 대여 GPU 기준 Qwen 품질 경로 + PuLID/InstantID 점수 정정

- **오너 지적 반영**: 실험대(16GB)가 아니라 RunPod 대여 GPU 기준으로 재평가. RunPod Secure Cloud 기준 RTX 5090(32GB) $0.99/h, A100 80GB $1.39/h, H100 80GB $2.89/h, 초 단위 과금. ai-toolkit 저자(ostris)가 **5090 한 장에서 6bit 양자화로 Qwen-Image 인물 LoRA 학습** 튜토리얼 공개 → 32GB로 가능, 여유는 A100. PRD N5("자체 GPU 상시 운영 금지")는 배치 대여와 충돌하지 않음(상시 운영이 아님).
- **품질 경로**: 얼굴 = Qwen-Image LoRA(16~20장, 1,000~2,000스텝, 체크포인트 샘플 선택) → Qwen-Image-Edit + LoRA 얼굴 패스(fal 관리형 또는 RunPod 서버리스). 몸 = SMPL 마네킹 → depth/keypoint 틀 → Qwen-Image-Edit(체형 문서 §9 강한 길). 옷 = **gpt-image-2 vs Qwen-Image-Edit A/B가 선결**(착용컷 옷 재현 미비교). 50명 LoRA 학습 GPU 비용 ≈ $25 수준.
- **PuLID/InstantID 점수 정정**: SimGT(닮음)는 높을수록, CP(복붙)는 **낮을수록** 좋다. InstantID SimGT 0.464/CP 0.337, PuLID 0.452/0.315 = 닮지만 레퍼런스 각도·표정·헤어를 복사. 착용컷처럼 컷마다 표정·각도가 달라야 하는 용도엔 부적합. 단 §7 얼굴 패스처럼 포즈가 고정된 자리에선 CP 문제가 작아지므로 **비교군 A4(PuLID-SDXL 얼굴 패스)**로 추가. 걸림돌은 InsightFace 비상업 가중치(상업 계약 필요·가격 비공개)와 Qwen용 공식 포트 부재.
- 쉬운 설명 페이지: `mockups/몸과얼굴-쉬운설명.html`.
