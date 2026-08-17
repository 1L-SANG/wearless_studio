# 가상모델 여성 2차 배치 (mF~mN) — 배선 기록

2026-08-17. 사용자 제공 베이스컷 9장 → 아이덴티티 팩 생성 → R2 시드 → 프론트 카탈로그 배선까지.
계약은 ADR 승격 없이 기존 AG-06(`documents/ai_agent_modules.md` §3)을 그대로 따른다.

## 1. 배정

| id | sid | 표시명 | 성별 | 셀렉터 썸네일 | 얼굴 앵커 |
|----|-----|--------|------|----------------|-----------|
| mF | w3 | 하린 | women | `public/models/women/w3.webp` | `w3-face.webp` |
| mG | w4 | 세아 | women | `w4.webp` | `w4-face.webp` |
| mH | w5 | 예린 | women | `w5.webp` | `w5-face.webp` |
| mI | w6 | 다인 | women | `w6.webp` | `w6-face.webp` |
| mJ | w7 | 소윤 | women | `w7.webp` | `w7-face.webp` |
| mK | w8 | 유나 | women | `w8.webp` | `w8-face.webp` |
| mL | w9 | 채원 | women | `w9.webp` | `w9-face.webp` |
| mM | w10 | 나윤 | women | `w10.webp` | `w10-face.webp` |
| mN | w11 | Nora | women | `w11.webp` | `w11-face.webp` |

이름 규칙은 2026-08-01 오너 결정을 따랐다 — 동양인 = 짧은 한국어, 서양인 = 짧은 영문(w11만 해당).
id 는 UUID 가 아니어야 라이선스 게이트가 no-op 으로 빠진다(`server/app/agents/identity_source.py`).

## 2. 파이프라인

1. **베이스 정리** — `women_ex/base/{sid}.png` (원본 그대로, 재인코딩 없음)
2. **팩 생성** — `node spike/facepack.js --base ... --id {sid}v2 --res 4K --identity {v3|v4|v5}`
   모델당 4K 그리드 2장(세드카드 2x2 + 전신 3면) → sips 크롭 7장
3. **썸네일·앵커** — 기존 5인 실측 규격(두상 폭 ≈ 0.547W, 머리 상단 ≈ 0.04H, 360×450)으로 자동 크롭.
   배경은 베이스가 이미 회색(167~181)이라 기존 대역(153~177) 안이므로 리컬러 없음.
4. **시드** — `cd server && .venv/bin/python -m scripts.seed_virtual_models`
   → R2 `seed/models/{id}/{view}` 54개 업로드 + `virtual_models.json` 재생성 (기존 5인 30개는 skip)
5. **카탈로그** — `src/features/analysis/aiModels.js` 가 프론트의 **단일 출처**다.
   `AnalysisForm`(그리드)과 `modelSelection`(무료/유료 판정)이 함께 읽는다.
   `src/mock/db.js` 와 `server/tests/test_frontend_mock_contract.py` 가 이와 어긋나면 pytest 가 깨진다.
   목록을 손으로 두 번 적던 구조가 실제로 사고를 냈다 — §7 참조.

## 3. 프롬프트 개정 (facepack.js)

시각 QC 결과를 근거로 IDENTITY 문구를 3회 개정했다. 기본값은 v2 유지(기존 재현성 보존).

| 버전 | 변경 | 근거 |
|------|------|------|
| v3 | v2 의 "주근깨가 결정적 특징" 단정 제거 + "없는 자국 금지" 추가 | 주근깨 없는 베이스에 없던 반점을 그릴 위험 |
| v4 | v2 강조 **복원** + v3 금지 유지 + "모든 셀에서 마킹 집합 동일" | **v3 이 회귀를 만들었다** — 1차 QC 9인 중 7인 탈락, 주 사유가 front·front-smile 셀 주근깨 소실. 그 강조가 리터치를 막던 장치였다 |
| v5 | 정면 2셀을 이름으로 호명 + "정면이 프로필보다 깨끗하면 실패" 부등식 + 마크의 색·크기 등급 고정 | v4 로 7/9 통과, 남은 2건의 실패가 정면 계열에만 몰림 |

그리드 프롬프트도 2건 고쳤다.
- **세드카드 방향**: "three-quarter left / left profile" 만으로는 두 셀이 서로 반대쪽을 보는 사례 발생
  (w4·w10 — 3/4 는 코가 왼쪽, 프로필은 오른쪽). 프레임 기준 "둘 다 코가 왼쪽" 등식으로 못박음.
- **전신 시트 간격**: 세 인물이 정확히 1/3 간격이 아니면 크롭이 옆 인물 손을 물거나 본인 손을 자름
  (w10 실측). "각 인물이 자기 1/3 안에 완전히 들어올 것" 명시.

## 4. 크롭 인셋

세드카드 프롬프트가 "thin white gutters" 를 요구해 1/2 등분 크롭이 구분선 절반을 물고 들어왔다
(실측 6~20px, w11 은 ~31px). `GRIDS[sedcard].inset = 0.022` 로 사방을 깎아 제거했고,
`--recrop <runDir>` 모드를 추가해 **생성 재호출 없이** 기존 9팩에 소급 적용했다.
전신 시트는 seamless 라 인셋 미적용(깎으면 손발이 잘린다).

검증: 9모델 × 7뷰 × 4변 = 252개 경계에서 흰 띠 0px.

**기존 프로덕션 5인(mA~mE)의 팩에는 이 인셋이 적용되지 않았다** — 자산 교체는 별건이라 손대지 않았다.

## 5. QC 결과

시각 QC 를 3라운드 돌렸다(에이전트가 베이스·그리드·7크롭을 직접 열어 확대 대조).

| sid | 이름 | 라운드 | 판정 | 비고 |
|-----|------|--------|------|------|
| w3 | 하린 | 1차(v3) | PASS | minor: 정면 주근깨 약간 옅음 |
| w9 | 채원 | 1차(v3) | PASS | minor 4건 |
| w4 | 세아 | 2차(v4) | PASS | 1차 결함 3건 해소 |
| w5 | 예린 | 2차(v4) | PASS | |
| w7 | 소윤 | 2차(v4) | PASS | |
| w8 | 유나 | 2차(v4) | PASS | |
| w11 | Nora | 2차(v4) | PASS | |
| w6 | 다인 | 3차(v5) | **조건부** | 검수 2인 찬반 1:1 |
| w10 | 나윤 | 3차(v5) | **조건부** | 검수 2인 0:2 |

### 조건부 2건의 성격

남은 결함은 **정면 셀(front·front-smile)의 주근깨 밀도가 각도 셀보다 낮다**는 것 하나다.
"베이스에 없던 점이 새로 생겼는가"(아이덴티티를 실제로 오염시키는 결함)는 **두 모델 모두
검수자 전원 일치로 없음**. 그럼에도 배선한 근거:

1. AG-06 계약상 **얼굴 질감의 정본은 `face_front`(무보정 원본)** 이고 그리드는 각도·헤어의 정본이다.
   착용 컷 경로(`cut_generator.py:468`)는 `face_front + body_front` 쌍을 붙이며 `grid_sedcard` 는
   얼굴 컷에서만 쓰인다.
2. 전체 얼굴 미세질감 기준(정면/각도 셀 비율)으로 재보면 신규 9인은 0.85~1.43,
   이미 배포된 5인은 0.87~1.23 으로 **같은 대역**이다(w10 0.85 ≈ w2 0.87).
   단 이 지표는 코스 척도라 QC 의 뺨 국소 계측을 반박하지는 못한다 — 상충하는 근거로 남겨둔다.

**오너 결정 필요**: w6·w10 을 이대로 둘지, 정면 2셀만 재생성할지. 재생성은 모델당 이미지 2회.

## 6. 남은 이슈

- **성별 불균형**: 여성 11 : 남성 3. 성별 칩으로 필터링되므로(`AnalysisForm.jsx` 의 AI 그리드 `.filter(...)`)
  남성 선택 시 선택지가 3개뿐이다. 남성 배치 보강 권장.
- **외형 다양성**: 신규 9인 중 6인이 장발 흑발 동양인으로 인상이 서로 가깝다.
  뚜렷이 구분되는 건 w9(단발 뱅), w11(서구권), w3 정도.
- 전신 시트의 손가락 뭉갬은 전 모델 공통(시트 해상도 한계). 손 클로즈업 재사용엔 부적합.
- 프로필 셀 각도가 90도가 아닌 75~85도인 사례 다수 — QC 는 minor 처리.


## 7. 리뷰에서 드러난 자기 결함 3건 (수정 완료)

이 배치를 만들며 **내가 새로 만든 결함**이다. 기록해 둔다.

### 7-1. 신규 9인이 유료 '실제 모델'로 오분류 — 없는 요금 문구 노출

`modelSelection.js` 의 `VIRTUAL_MODEL_IDS` 는 카탈로그를 손으로 다시 적은 네 번째 사본이었고,
9인을 추가하며 그곳을 빼먹었다. 런타임 확인: `isRealModelSelection('mF') === true` →
`realModelFeeLabel` 이 `' + 실제 모델 이용료 별도'` 를 돌려주고, 마네킹 화면 CTA 에
`이대로 진행 · N 크레딧 + 실제 모델 이용료 별도` 로 붙는다. 같은 화면의 안내 문구
"가상 인물 모델이에요 · 라이선스 비용 없이 바로 쓸 수 있어요" 와 정면으로 모순된다.

**수정**: 목록을 `src/features/analysis/aiModels.js` 한 곳으로 모으고, 판정 집합을 거기서
파생시켰다(`VIRTUAL_MODEL_IDS = AI_MODEL_IDS`). 카탈로그 전체를 훑어 무료 판정을 확인하는
회귀 테스트를 추가했다 — 다음에 모델을 추가하며 같은 실수를 하면 여기서 깨진다.

### 7-2. 세드카드 프롬프트에서 미소 셀(bottom-right) 지시가 사라졌다

방향 문구(3/4·프로필이 같은 면을 보게)를 고치면서 네 번째 셀 지시를 **실수로 함께 지웠다**.
그 상태로 2·3차 재생성이 돌아, **9팩 중 7팩(mG~mN, mF·mL 제외)의 4번째 칸이 미지정 상태로
생성**됐다. 그 칸은 `front-smile.png` 라는 이름으로 저장돼 세드카드 그리드에 포함된다.
QC 가 결과 이미지를 직접 보고 통과시켰으므로 산출물 자체는 검수를 거쳤지만
(w10 은 "미소 셀이 실제로는 20~25도 돌아간 준3/4" 로 minor 기록), **과정은 잘못됐다.**

**수정**: 프롬프트에 셀 지시를 복원했다. 기존 7팩은 QC 를 통과한 상태라 재생성하지 않았다.

### 7-3. `--recrop` 이 프롬프트 기록을 기본값으로 덮어썼다

`--recrop` 은 크롭만 다시 뜨는 모드인데, 프롬프트 파일 기록이 그 분기보다 위에 있어
**무조건 다시 쓰였다.** `--identity` 를 안 주면 기본값이 v2 라, 소급 재크롭을 돌린 순간
배포된 9팩의 `prompt-sedcard.txt` 가 전부 v2 로 바뀌었다 — 실제로 쓰인 v3/v4/v5 기록이
사라졌다(그래서 아래 부록에 세 버전을 모두 옮겨 적는다).

**수정**: 크롭 모드에서는 프롬프트 파일을 건드리지 않는다.

## 8. 배포 순서 — 서버 먼저

`.github/workflows/deploy-server.yml` 기준 프론트(Vercel)와 서버(ECS)는 **별개 파이프라인**이고
서버 쪽이 느리다. 프론트가 먼저 나가면 그 사이에 신규 9인을 고른 사용자는:

- 상세페이지 경로 — `resolve_effective_model_id` 가 구 manifest 에서 mF 를 못 찾아
  `detailpage_fallback_model_id`(기본 `mB` = Leo, **남성**)로 조용히 대체한다. 경고 로그만 남는다.
- 에디터 경로 — `resolve_virtual_model_assets` 가 None 을 반환해 얼굴 참조 없이 생성된다.

**서버를 먼저 배포하고 프론트를 나중에 배포한다.**

---

## 부록. 프롬프트 원문 (2026-08-17 최종)

`spike/facepack.js` 는 `.gitignore` 의 "로컬 실험/리서치 산출물" 정책 대상이라 저장소에 싣지
않는다. 그 파일이 유실돼도 재현 가능하도록 최종 프롬프트 원문을 여기 남긴다.

실행: `node spike/facepack.js --base <베이스컷> --id <sid>v2 --res 4K --identity <v3|v4|v5>`
크롭 규칙: 세드카드 `inset = 0.022`(사방), 전신 시트 인셋 없음. `--recrop <runDir>` 로 소급 적용.
아래 두 그리드 프롬프트의 `${IDENTITY}` 자리에는 위 IDENTITY v5 전문이 그대로 치환된다.

### 모델별 사용 버전

| IDENTITY | 모델 |
|---|---|
| v3 | mF 하린 · mL 채원 |
| v4 | mG 세아 · mH 예린 · mJ 소윤 · mK 유나 · mN Nora |
| v5 | mI 다인 · mM 나윤 |

v4·v5 로 생성된 7팩은 §7-2 의 결함으로 **세드카드 bottom-right(미소) 셀 지시가 빠진 채**
생성됐다. 아래 세드카드 프롬프트는 결함을 고친 최종본이라 그 줄이 포함돼 있다 —
7팩을 바이트 단위로 재현하려면 그 줄을 빼야 한다.

### IDENTITY v3

```
The person in the reference image is the model. Preserve this exact person's facial identity, bone structure, skin tone, and hairstyle precisely in every view. Critically: reproduce the skin exactly as it appears in the reference — every freckle, mole, blemish and pore-level texture in their exact positions on the face and neck, and no marks, freckles or blemishes that are not present in the reference. Do NOT smooth, retouch, clean up, or airbrush the skin. Do not beautify, de-age, slim, or otherwise alter facial features.
```

### IDENTITY v4

```
The person in the reference image is the model. Preserve this exact person's facial identity, bone structure, skin tone, and hairstyle precisely in every view. Critically: reproduce every freckle, mole, blemish, and pore-level skin texture in their exact positions on the face and neck — these markings are this person's defining feature and must stay clearly visible in EVERY cell, including the smiling cell and the profile cell. Equally important in the other direction: do NOT invent any freckle, mole, spot, or blemish that is absent from the reference. The set of skin markings must be identical across all cells — a mark that appears in one cell and not another is a defect. Do NOT smooth, retouch, clean up, or airbrush the skin; a perfectly clean complexion in any cell is a failure. Do not beautify, de-age, slim, widen, round, or otherwise alter facial features, face shape, or eye size.
```

### IDENTITY v5

```
The person in the reference image is the model. Preserve this exact person's facial identity, bone structure, skin tone, and hairstyle precisely in every view. Critically: reproduce every freckle, mole, blemish, and pore-level skin texture in their exact positions on the face and neck — these markings are this person's defining feature. The two frontal cells (the neutral front view and the smiling front view) are the ones most often over-smoothed: they must show the SAME freckle density as the three-quarter and profile cells. If a frontal cell looks cleaner or smoother than the profile cell, the result is WRONG. Equally important in the other direction: do NOT invent any freckle, mole, spot, or blemish that is absent from the reference, and do not render any marking darker, larger, or more sharply defined than it appears in the reference — no dark beauty-mark moles may appear anywhere. The set of skin markings must be identical across all cells. Do NOT smooth, retouch, clean up, or airbrush the skin; a perfectly clean complexion in any cell is a failure. Do not beautify, de-age, slim, widen, round, or otherwise alter facial features, face shape, or eye size.
```

### 세드카드 그리드 (aspect 3:4, 2×2)

```
Create a 2x2 multipicture sedcard of the exact same person shown in the reference image, four medium close-up studio portraits arranged in a strict 2x2 grid with thin white gutters:
- top-left: frontal view, neutral relaxed expression
- top-right: three-quarter view, head turned so that the nose points toward the LEFT edge of the frame, neutral expression
- bottom-left: full profile view (90 degrees), head turned THE SAME WAY as the top-right cell so the nose again points toward the LEFT edge of the frame — both cells must show the same side of the face and must never be mirrored relative to each other, neutral expression

${IDENTITY} The person wears the same plain white t-shirt in all four cells. Identical clean white photo studio background, identical soft even lighting, identical camera distance and head scale in every cell. Photorealistic, high-end editorial quality, realistic skin texture. No text in image.
```

### 전신 시트 그리드 (aspect 4:3, 1×3)

```
A professional character reference sheet showing the same individual from the reference image with consistent facial identity, physique, and proportions, arranged in one seamless horizontal layout: three full-body standing views placed side by side in this order — front view, left profile view, back view — each in a neutral A-pose with relaxed expression and evenly balanced stance, accurate anatomy.

Layout requirement: divide the frame into three equal vertical thirds and place exactly one figure, fully contained, inside each third. Every figure must be complete from head to shoes with clear empty background margin on both sides — no hand, arm, foot, or hair may touch or cross a third boundary, and no part of one figure may appear inside another figure's third.

${IDENTITY} The person wears the same plain fitted white t-shirt and straight mid-grey trousers with plain white sneakers in all three views. Clean studio lighting on a plain neutral white background; composition evenly spaced with subtle ground shadow for realism, sharp photographic clarity, balanced exposure using soft three-point lighting, neutral white balance. No text in image.
```
