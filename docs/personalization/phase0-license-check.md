> 상태: PENDING APPROVAL — Phase 0 산출물(스코핑 계획 하위). 실행 전 문서.

# Phase 0 T0-1 — 모델·API 라이선스/상업조건 확인표

- 작성일: 2026-07-15
- 상위 문서: [스코핑 계획](../../.omc/plans/user-face-personalization-scoping.md) T0-1 · [§6(b) 라이선스 확인 방법](../../.omc/plans/user-face-personalization-scoping.md) · [PRD NFR-6](./prd.md) · [api-spec §4 정책 게이트](./api-spec.md)
- 목적: 제로샷 신원주입 스파이크(T0-2)의 **경로별 go/no-go 게이트** 입력. 각 컴포넌트 상업 사용 가부를 `확인됨 / 조건부 / 불가` 3분류로 기록.
- 방법: HuggingFace 모델카드 + LICENSE 파일 + 공급자 약관(AUP·ToS·정책 도움말) **직접 확인**. 추정 금지 — 근거 URL 미확보 항목은 "확인 필요"로 명시.

---

## 1. 컴포넌트별 라이선스 표

판정 기준: **확인됨** = 상업 허용이 라이선스/약관에 명시. **조건부** = 상업 허용되나 준수 조건(귀속·약관·정책 게이트)이 붙음. **불가** = 상업 사용 금지 또는 명시적 제약.

| 모델/컴포넌트 | 상업 허용 | 귀속 의무 | 재배포 조건 | 판정 | 출처 URL | 확인일 |
|---|---|---|---|---|---|---|
| **Qwen-Image-2512** (base, T2V) | 허용 (Apache-2.0) | NOTICE 파일 존재 시 유지 | 라이선스 사본 첨부 + 변경 파일 표시 + 저작권·귀속 고지 유지 | **확인됨** | https://huggingface.co/Qwen/Qwen-Image-2512 | 2026-07-15 |
| **Qwen-Image** (base 원판, LICENSE 파일) | 허용 (Apache-2.0) | NOTICE 파일 존재 시 유지 | 상동(Apache-2.0 §4) | **확인됨** | https://huggingface.co/Qwen/Qwen-Image/blob/main/LICENSE | 2026-07-15 |
| **Qwen-Image 텍스트 인코더** (Qwen2.5-VL 계열, repo 내 `text_encoder/`) | 허용 (Apache-2.0) | 상동 | 상동 — repo LICENSE가 전체 서브폴더 커버, 별도 컴포넌트 라이선스 미분리 | **확인됨** | https://huggingface.co/Qwen/Qwen-Image/blob/main/LICENSE | 2026-07-15 |
| **Qwen-Image VAE** (repo 내 `vae/`) | 허용 (Apache-2.0) | 상동 | 상동 — 모델카드에 별도 라이선스 분리 표기 없음(= repo Apache-2.0 귀속) | **확인됨** | https://huggingface.co/Qwen/Qwen-Image-2512 | 2026-07-15 |
| **Qwen-Image-2512-Lightning** (distill/4-step LoRA, 서드파티 lightx2v/ModelTC) | 허용 (Apache-2.0) | NOTICE 유지 | 별도 repo — 자체 LICENSE 확인 필요(현재 Apache-2.0 명시) | **확인됨** | https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning | 2026-07-15 |
| **Qwen-Image-Lightning** (distill LoRA 원판) | 허용 (Apache-2.0) | NOTICE 유지 | 상동 | **확인됨** | https://huggingface.co/lightx2v/Qwen-Image-Lightning | 2026-07-15 |
| **Qwen-Image-Edit-2511** (제로샷 얼굴 보존 경로 β) | 허용 (Apache-2.0) | NOTICE 유지 | 상동(Apache-2.0 §4) | **확인됨** | https://huggingface.co/Qwen/Qwen-Image-Edit-2511 | 2026-07-15 |
| **Qwen-Image-Edit-2509** (Edit 원판) | 허용 (Apache-2.0) | NOTICE 유지 | 상동 | **확인됨** | https://huggingface.co/Qwen/Qwen-Image-Edit-2509 | 2026-07-15 |
| **Google Gemini / Vertex AI 이미지 생성** (경로 α, 현 nano-banana 배관) | 허용(관리형 API) — **단 실인물 likeness·person-generation은 정책 게이트 적용** | 해당 없음(관리형) | 재배포 대상 아님(가중치 비배포) — 출력물 사용은 약관 준수 | **조건부** | https://policies.google.com/terms/generative-ai/use-policy | 2026-07-15 |
| **Replicate 관리형 엔드포인트** (경로 β/γ 서빙) | 출력물 상업 사용 허용(ToS §5.1) — **책임·배상 전가, 실인물 정책 게이트 적용** | 해당 없음 | 모델 소유자 오픈소스 라이선스 준수 의무(AUP) | **조건부** | https://replicate.com/terms | 2026-07-15 |

**핵심 요지**
- Qwen 계열(base·Edit·distill LoRA·VAE·텍스트 인코더)은 **전부 Apache-2.0으로 상업 사용 확인됨.** 부속 컴포넌트가 base보다 제한적인 케이스는 이번 확인 범위에서 발견되지 않음.
- 다만 Apache-2.0의 **재배포 의무(라이선스 사본·NOTICE·변경 표시)는 "가중치를 우리가 배포/서빙"할 때만 발생.** 관리형 엔드포인트(Replicate)로 호출만 하면 우리는 재배포자가 아니라 이용자 → 재배포 의무 회피, 대신 관리형 약관·정책 게이트가 지배 규범이 됨.
- **라이선스(오픈소스 상업 허용)와 사용정책(실인물 likeness 허용)은 별개 축.** Apache-2.0이라도 관리형 공급자 약관이 실인물 생성을 제약하면 그 경로는 컷된다(§2).

---

## 2. 정책 게이트 요약 — 실인물 likeness·지역 제약 (T0-2 pass/fail 게이트용)

> 스코핑 계획 T0-2의 **"정책 게이트(정량 게이트보다 선행)"** 입력. 각 경로가 *상업적 실인물 likeness 생성*을 허용하는지 + person-generation 지역 제약을 통과하는지 pass/fail로 판정. **미통과 경로는 유사도·비용·지연과 무관하게 컷.**
>
> **우리 유스케이스의 성격 (판정의 전제):** 본 기능은 *타인 재현*이 아니라 **계정 본인이 자기 얼굴 3장을 자기 동의로 등록해 자기 상품 착장 컷을 만드는 자기-likeness 생성**이다(PRD N4·US-11: "계정 본인" 전용, 사칭 방지 게이트로 타인 차단). 즉 아래 정책의 핵심 금지선인 *비동의 타인 likeness*와는 다른 범주다. 그러나 어느 공급자도 "자기 동의 자기-likeness 상업 생성"을 **명시적으로 허용(safe harbor)한다고 문서화하지 않았다** → 그래서 판정은 전부 "조건부/게이트 필요"이며, 스파이크에서 각 경로의 실제 거부(refusal) 동작을 실측해야 한다.

### 2.1 Google Gemini / Vertex AI (경로 α — 현 `gemini_image.py` 배관 재사용)

**약관상 금지 항목 (Generative AI Prohibited Use Policy)** — 근거: https://policies.google.com/terms/generative-ai/use-policy
- "Impersonating an individual (living or dead) without explicit disclosure, in order to deceive" (기만 목적 무고지 사칭)
- "Misrepresenting the provenance of generated content by claiming it was created solely by a human, in order to deceive"
- 비동의 이미지 사용/프라이버시·IP 권리 침해, 비동의 친밀 이미지 금지 — 근거: https://support.google.com/gemini/answer/16625148

**person-generation 지역·범위 제약 (Imagen 계열)** — 근거: https://ai.google.dev/gemini-api/docs/imagen · https://docs.cloud.google.com/vertex-ai/generative-ai/docs/samples/generativeaionvertexai-imagen-generate-image
- `personGeneration` 값: `dont_allow` / `allow_adult`(기본, 성인만) / `allow_all`(아동 포함).
- **`allow_all`은 EU·UK·CH(스위스)·MENA에서 불가.**
- **유명인(celebrity) 생성은 모든 설정에서 불가.**
- 사람 포함 이미지 생성이 시점에 따라 **allowlist-only(사전 승인)** 였던 이력 존재 — 근거: https://discuss.google.dev/t/imagen-3-generating-images-containing-people-is-currently-an-allowlist-only-feature/175303
- 지역 일반: EEA·스위스·UK 대상 서비스는 **유료(Paid Services) 필수** — 근거: https://ai.google.dev/gemini-api/terms

**게이트 판정:** **조건부(PASS with conditions).** 자기 동의·성인 한정(A4·N2와 정합) 유스케이스는 위 금지선(기만적 사칭·비동의·유명인)에 저촉되지 않으나, ① 정책이 자기-likeness를 명시 허용하지 않고 ② 모델이 "실제 인물 사진 기반 생성"을 안전필터로 거부할 수 있음 → **스파이크에서 실제 refusal율·필터 반려를 실측**하고, 아동 생성 경로는 원천 차단(성인 게이트)해야 pass 유지.

### 2.2 Replicate (경로 β/γ 관리형 서빙)

**ToS/AUP** — 근거: https://replicate.com/terms · https://replicate.com/acceptable-use-policy
- **출력물 상업 사용 허용**: ToS §5.1 — "you may use Output for commercial purposes such as sale or publication" (단, third-party 모델 라이선스 준수 조건부).
- **책임·배상 전가**: 고객이 Content·입력 데이터에 단독 책임(§3.1·§8.6), Replicate에 대한 **광범위 면책·배상 의무**(§10.1). 출력물로 법 위반·권리 침해·금지 콘텐츠 생성 불가(§2.7(d)).
- AUP: 불법행위·IP 침해·비방/음란 콘텐츠 금지, **모델 소유자 오픈소스 라이선스 위반 금지**. 단, 실인물 likeness/deepfake/비동의 이미지에 대한 **명시적 별도 조항은 부재** → 판단·책임이 전적으로 우리에게 귀속.

**게이트 판정:** **조건부(PASS with conditions).** 상업 사용은 명시 허용되나, Replicate은 **적극적 안전 항구(safe harbor)를 제공하지 않고 리스크를 이용자에게 전가**한다. 따라서 (a) 동의·연령·사칭방지 게이트를 **우리 서버측에서** 강제하고, (b) 사용하는 Qwen 모델의 Apache-2.0 준수를 유지해야 pass. 실인물 조항 부재는 "허용"이 아니라 "우리 책임"으로 읽어야 한다.

### 2.3 정책 게이트 종합

| 경로 | 상업 라이선스 | 실인물 likeness 정책 | 지역 제약 | 게이트 판정 |
|---|---|---|---|---|
| α Gemini/Vertex (nano-banana) | 관리형(가중치 무관) | 조건부 — 기만·유명인·비동의·아동 금지, 자기 동의는 명시허용 아님 | `allow_all` EU/UK/CH/MENA 불가·유명인 전면 불가·EEA/UK/CH 유료 필수 | **조건부** |
| β Replicate + Qwen-Image-Edit-2511 | Apache-2.0(모델) + ToS §5.1(출력물) | 조건부 — 명시 조항 없음, 책임 전가 | AUP 일반 규정 외 특이 지역제약 미발견(확인 필요) | **조건부** |
| γ Replicate + SDXL/FLUX+ID어댑터(대조군) | 각 모델 라이선스 개별 확인 필요 | 조건부 — β와 동일 | 상동 | **조건부(모델별 재확인)** |

> **T0-2 착수 규칙:** 세 경로 모두 라이선스상 "즉시 불가"는 없음 → 스파이크 진입 가능. 단, 진입 전 각 경로에서 **본인/합성 얼굴로 실제 생성 시도 → refusal·안전필터 반려 로그**를 pass/fail 증거로 남길 것(약관 텍스트만으로는 실동작을 알 수 없음). 아동 경로·유명인 프롬프트는 실험에서도 배제.

---

## 3. 리스크·권고

1. **단일 모델 종속 회피.** Qwen 계열이 전부 Apache-2.0으로 깨끗하지만, 경로를 Qwen 하나에 묶지 말 것. 정책 게이트(§2)는 라이선스와 독립축이라 특정 공급자 약관 변경 한 번에 경로가 죽을 수 있다. **α(Gemini) + β(Qwen-Edit) 최소 2경로를 항상 병행 확보**(스코핑 Pre-mortem 1·4).
2. **애매하면 관리형 API 경로 우선.** 자체 가중치 서빙은 Apache-2.0 **재배포 의무(라이선스 사본·NOTICE·변경 표시)**가 발생하고 GPU 예산 0(A2)과 충돌. 관리형 호출은 재배포자 지위를 회피해 라이선스 의무가 가벼워진다. 단, 이때는 **관리형 약관·정책 게이트가 지배 규범**이 됨을 명심.
3. **부속 컴포넌트가 base보다 제한적일 수 있음 — 이번엔 아니었지만 상시 경계.** 이번 확인에서 Qwen VAE·텍스트 인코더·distill LoRA는 모두 base와 동일 Apache-2.0. 그러나 **서드파티 distill/lightning LoRA(lightx2v 등)는 별도 repo**라 향후 라이선스 변경 시 base와 어긋날 수 있다 → 채택 시점에 **LoRA repo LICENSE를 재확인**하고 버전 고정(pin). SDXL/FLUX 경로(γ)는 base 모델 라이선스가 Apache가 아닐 수 있어(FLUX는 비상업 변형 존재) **모델별 개별 확인 필수**.
4. **라이선스 ≠ 정책 통과.** Apache-2.0은 "코드/가중치 사용" 허가일 뿐 "실인물 얼굴 생성" 허가가 아니다. 실인물 축은 공급자 약관 + 한국 개인정보보호법(생체정보) + 초상권으로 별도 규율됨(§2 및 T0-3 컴플라이언스 설계와 연동).
5. **"실인물 조항 부재 = 허용"으로 오독 금지.** Replicate·Qwen 모두 실인물 likeness를 명시 금지하지 않지만, 이는 안전 항구가 아니라 **책임의 우리 귀속**을 뜻함. 동의·연령·사칭방지 게이트를 우리 서버가 강제하는 것이 전제(PRD FR-8·FR-9).

---

## 4. 미확인·확인 필요 항목

- **Replicate의 실인물/딥페이크 전용 조항**: AUP·ToS에서 별도 명문 조항 **미발견**(일반 불법·권리침해 규정으로 수렴). 실인물 생성에 대한 적극적 허용/금지 문구가 없다는 사실 자체를 리스크로 기록 — 추가 확인 필요(공급자 문의 또는 개별 모델 페이지 약관).
- **경로 γ(SDXL/FLUX + InstantID/PuLID) 기반 모델·어댑터 라이선스**: 미확인. FLUX 계열은 비상업 라이선스 변형이 존재하므로 **채택 검토 시 모델별 개별 확인 필수**(현재 대조군이라 미조사).
- **Qwen-Image-2512 repo의 NOTICE 파일 실제 유무**: Apache-2.0 귀속 의무는 NOTICE 존재 시에만 발생 — 자체 서빙 채택 시 repo에 NOTICE 동봉 여부를 실파일로 확인 필요(현재 관리형 우선이라 미조사).
- **Gemini(nano-banana, `gemini-3-pro-image`)의 사진-기반 실인물 생성 refusal 실동작**: 약관은 확인했으나 **모델의 실제 안전필터 반려 여부는 문서로 불명** → T0-2 스파이크에서 실측(약관 확인만으로 pass 확정 불가).
- **경로 β Qwen-Edit-2511의 Replicate 상 지역 제약**: Replicate 일반 AUP 외 person-generation 지역 게이팅은 미발견 — Vertex Imagen 같은 명시적 지역 제약이 있는지 추가 확인 필요.

---

## 참고 URL (실인용)

1. Qwen-Image LICENSE (Apache-2.0 원문) — https://huggingface.co/Qwen/Qwen-Image/blob/main/LICENSE
2. Qwen-Image-2512 모델카드 (apache-2.0, 2025-12-31 공개) — https://huggingface.co/Qwen/Qwen-Image-2512
3. Qwen-Image-Edit-2511 모델카드 (얼굴/캐릭터 일관성 강화, Apache-2.0) — https://huggingface.co/Qwen/Qwen-Image-Edit-2511
4. Qwen-Image-Edit-2509 모델카드 — https://huggingface.co/Qwen/Qwen-Image-Edit-2509
5. lightx2v/Qwen-Image-2512-Lightning (distill LoRA, Apache-2.0) — https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning
6. Google Generative AI Prohibited Use Policy — https://policies.google.com/terms/generative-ai/use-policy
7. Gemini Prohibited Use Policy (도움말 요약) — https://support.google.com/gemini/answer/16625148
8. Imagen personGeneration·지역 제약 (EU/UK/CH/MENA) — https://ai.google.dev/gemini-api/docs/imagen
9. Vertex AI Imagen personGeneration 값 설명 — https://docs.cloud.google.com/vertex-ai/generative-ai/docs/samples/generativeaionvertexai-imagen-generate-image
10. Gemini API Additional Terms (EEA/UK/CH Paid Services) — https://ai.google.dev/gemini-api/terms
11. Replicate Terms of Service (§5.1 상업 사용·§10.1 배상) — https://replicate.com/terms
12. Replicate Acceptable Use Policy — https://replicate.com/acceptable-use-policy
