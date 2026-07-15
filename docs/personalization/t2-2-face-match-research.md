> 상태: 조사 자료(PENDING REVIEW) — T2-2 사칭 방지 설계 입력. 2026-07-15 조사.
> 목적: `docs/personalization/api-spec.md` §6의 미결 항목 "사칭 방지 게이트 수단"과
> "얼굴 대조를 어디서 돌릴 수 있는가"(공급자 AUP·모델 라이선스)를 사실로 채우기 위한 1차 조사.
> **주의**: 이 문서는 조사 결과이지 결정이 아니다. 채택 전 법무 검토 필요.

# 사칭 방지(T2-2) — 얼굴 대조 AUP·모델 라이선스 조사

## 조사 결과 (확인일: 2026-07-15, 별도 표기 없으면 전부 이 날짜 기준)

먼저 로컬 저장소에 이미 관련 1차 조사가 존재함을 확인함: `/Users/nojeong-un/devs/wearless_studio/docs/personalization/phase0-license-check.md`(모델·API 라이선스표, §2 정책게이트)와 `/Users/nojeong-un/devs/wearless_studio/docs/personalization/phase0-consent-architecture.md`(§6: 연령 게이트는 CX 표준인증창으로 구현 완료, **"사칭 방지(업로드 얼굴=계정 본인)는 별개 미결"**로 명시). 이번 조사는 그 미결 과제를 채우기 위한 것이며, 로컬 문서는 Gemini/Vertex를 "이미지 생성" 용도로만 검토했고 "얼굴 대조(판별)" 용도는 이번이 최초 조사임.

---

### 1. 얼굴 대조(1:1 face verification)가 공급자 AUP상 허용되는가

**Google Gemini API / Vertex AI**
- Generative AI Prohibited Use Policy 전문(섹션별 불릿 전체 확인, WebFetch로 원문 대조)에서 "facial recognition", "biometric identification"을 다루는 별도 금지 카테고리는 **존재하지 않음**. 관련 조항은 단 하나:
  > "Violates the rights of others, including privacy and intellectual property rights -- for example, using personal data or **biometrics without legally-required consent**."
  — 즉 "적법하게 요구되는 동의 없는" 생체정보 사용만 금지. 동의 기반 사용을 별도로 금지하는 문구는 없음.
  - 출처: https://policies.google.com/terms/generative-ai/use-policy (확인일 2026-07-15)
  - 사칭 관련 별도 조항(참고): "Impersonating an individual (living or dead) without explicit disclosure, in order to deceive" — 이건 "본인 여부 판별" 용도가 아니라 "타인 사칭" 용도를 금지하는 조항이라 이번 유스케이스와 무관.
- Google Cloud Vision API는 애초에 얼굴 **식별(recognition)** 기능 자체를 제공하지 않음(face **detection**만 제공, 얼굴 위치·표정·랜드마크만). Celebrity Recognition 기능도 **2025-09-16 폐지(deprecated)**됨. Google이 정책적으로 "identify individuals" 기능을 Cloud Vision에 넣지 않기로 한 결정은 Google 임원(Jeff Dean) 공개 발언으로도 확인됨.
  - 출처: https://docs.cloud.google.com/vision/docs/deprecations, https://docs.cloud.google.com/vision/docs/detecting-faces (확인일 2026-07-15)
- Google Cloud Platform 일반 Acceptable Use Policy(https://cloud.google.com/terms/aup)는 **WebFetch가 콘텐츠 과다로 실패**하여 원문 조항을 직접 대조하지 못함 → **확인 불가**로 남김. (Service Specific Terms는 Generative AI 서비스에 대해 위 Prohibited Use Policy를 그대로 편입한다고만 확인됨.)
- **결론(사실 기반)**: "범용 비전 모델(Gemini)로 '이 두 사진이 같은 사람인가' 판별"이 문서상 명시적으로 금지되어 있다는 근거는 찾지 못함(정책이 이 사용 형태에 대해 침묵). 그러나 명시적으로 **허용한다는 문구도 없음** — 안전지대(safe harbor) 선언이 아니라 회색지대. Cloud Vision API 경로는 기능 자체가 없어 기술적으로 배제됨.

**Replicate**
- ToS §5.1(출력물 상업 사용 허용) 및 AUP(https://replicate.com/acceptable-use-policy) 전문을 WebFetch로 대조했으나 biometric/facial recognition/surveillance를 특정하는 조항은 **발견되지 않음**. 일반 불법행위·권리침해 금지 조항으로 수렴되며, 로컬 문서(phase0-license-check.md §4)도 동일 결론(2026-07-15 확인). 즉 "명시 조항 부재 = 책임이 이용자에게 귀속"되는 구조.

---

### 2. 자체 운영 얼굴 대조 모델 (CPU-only)

- **InsightFace 코드**: MIT 라이선스, 상업 사용 제한 없음.
  - 출처: https://github.com/deepinsight/insightface (확인일 2026-07-15)
- **InsightFace 모델 가중치(buffalo_l 포함 recognition-oss-pack)**: README License 섹션에 "The training data containing the annotation (and the models trained with these data) are available for **non-commercial research purposes only**"로 명시. 상업 라이선스는 recognition-oss-pack@insightface.ai로 별도 문의 필요, 가격 비공개.
  - 출처: https://github.com/deepinsight/insightface (README License 섹션), https://www.insightface.ai/services/models-commercial-licensing (확인일 2026-07-15)
- **buffalo_l 정확도 참고치**(InsightFace 공식 가이드): LFW 1:1 정확도 약 99.85%(w600k_r50 헤드), IJB-C TAR@FAR=1e-4 약 96~97.5%, 코사인 임계값 0.30~0.45(FMR 1e-4~1e-5 기준).
  - 출처: https://www.insightface.ai/guides/choose-face-recognition-model-and-evaluate (확인일 2026-07-15)
  - **CPU-only 지연(latency) 수치는 이 문서에 없음**(GPU 처리량만 제공) → **확인 불가**, 채택 전 자체 벤치마크 필요.
- **상업적으로 명확한 대안**:
  - `timesler/facenet-pytorch`: 저장소 자체 MIT 라이선스. 단 사전학습 가중치(VGGFace2/CASIA-WebFace 학습분)의 라이선스는 별도 확인 필요 — 근거: https://github.com/timesler/facenet-pytorch/blob/master/LICENSE.md (확인일 2026-07-15)
  - `serengil/deepface` 래퍼(MIT): 내부적으로 감싼 모델 중 **VGG-Face는 비상업 라이선스라 제외 대상**, **ArcFace(InsightFace 가중치 기반)는 상업 라이선스 필요**, 반면 **Facenet/Facenet512/OpenFace/SFace/Dlib는 상업 사용 가능**하다고 언급됨.
  - 출처: https://github.com/serengil/deepface (확인일 2026-07-15) — **이 라이선스 구분은 GitHub README/이슈 기반 2차 요약이며, 이번 조사에서 각 모델의 원본 LICENSE 파일을 1차 대조하지 못함 → "확인 필요"로 플래그.**

---

### 3. 라이브니스 상용 옵션 (한국)

- 라온시큐어 공식 도메인(raonsecure.com)이 raon.com으로 **301 리다이렉트**되어(사명/브랜드 변경 가능성) 상세 제품·가격 페이지를 직접 확인하지 못함 → **확인 불가**.
- eKYC 비교 정보(알체라 자사 블로그, 2차 출처 — 이해관계자 소스이므로 가격은 교차검증 필요): 알체라·네이버클라우드·유스비·컴트루테크놀로지 4개사 모두 **안면인식(얼굴 대조)+라이브니스 통합 제공**. 가격은 대부분 비공개/계약형(네이버클라우드만 "계단식 과금" 언급, 구체 단가 없음). 알체라는 "금융결제원 신분증 안면인식 공동시스템 구축 사업 1위" 레퍼런스를 자사 주장으로 제시(1차 검증 안 됨).
  - 출처: https://www.alchera.ai/resource/blog/ekyc-authentication-api-provider (확인일 2026-07-15, **자사 블로그이므로 경쟁사 대비 객관성 낮음**)
- **금융권 비대면 실명확인 표준**: 금융위원회 비대면 실명확인 가이드라인은 신분증 사본 제출·영상통화 등 5가지 방법 중 2가지 이상 중첩 적용을 의무화하며, "안면인식 기술로 신분증 사진과 촬영 얼굴을 대조"하는 방식이 **영상통화를 대체하는 특례**로 인정됨.
  - 출처(2차 요약): https://www.alchera.ai/resource/blog/solution-non-face-to-face-identity-verification-standards (확인일 2026-07-15)
  - **가이드라인 원문(금융위원회 발표문 자체)은 이번 조사에서 1차 확보하지 못함 → "확인 필요"로 플래그.**
  - **법적 적용범위**: 이 가이드라인은 금융회사(금융실명법 적용대상)를 대상으로 한 금융당국 가이드라인이며, **비금융 서비스에 대한 법적 구속력은 확인되지 않음**. 참고 모델로 차용은 가능하나 "적용 의무"라는 근거는 찾지 못함(확인 불가).

---

### 4. 한국 법 — 미성년자·생체정보

- **개인정보보호법 제22조의2(아동의 개인정보 보호)**: 만 14세 미만 아동 개인정보 처리 시 법정대리인 동의 필요, 처리자는 법정대리인 동의 여부를 **확인할 의무**를 짐. 법정대리인 성명·연락처는 예외적으로 동의 없이 아동에게 직접 수집 가능.
  - 출처: https://casenote.kr/법령/개인정보_보호법/제22조의2 (확인일 2026-07-15). 조문 원문은 law.go.kr에서도 대조 가능하나 이번엔 casenote 요약을 근거로 삼음.
- **제23조(민감정보)와의 관계**: 생체인식정보는 민감정보로 별도 명시 동의 필요 — 이미 프로젝트 로컬 문서(`phase0-consent-architecture.md` §0)가 이를 인지하고 설계에 반영함.
- **연령 확인 수준 — "합리적 조치" vs "엄격 검증"**: 개인정보보호위원회 「아동·청소년 개인정보 보호 가이드라인」(2022.7 제정)은 "이용자가 법정 생년월일을 직접 입력하도록 하거나 만 14세 이상이라는 항목에 스스로 체크하도록 하는 등 **적절한 방법**으로 연령을 확인"하도록 규정 — 즉 **자기기재(self-report) 수준으로 충분**하다는 취지이며, "엄격 검증"을 요구하지 않음. 다만 허위 기재로 인한 부정사용을 막을 "합리적인 장치" 마련은 별도 권고사항.
  - 출처: PIPC 가이드라인 PDF(https://www.cisp.or.kr/wp-content/uploads/2022/08/아동청소년-개인정보-보호-가이드라인최종.pdf) — **PDF 용량 초과로 WebFetch 직접 대조 실패**, 위 문구는 WebSearch 요약 및 김·장 법률사무소 해설(https://www.kimchang.com/ko/insights/detail.kc?sch_section=4&idx=25475)로 교차 확인함. **가이드라인의 정확한 페이지/조 번호는 확인 불가**로 남김.
  - 참고: 프로젝트는 이미 자기기재보다 훨씬 엄격한 **CX 표준인증창(공적 신원인증)**으로 연령을 확인 중이므로(로컬 문서 §6, 구현 완료), 현재 설계는 가이드라인의 최소 기준을 상회함.

---

## 요약: 얼굴 대조를 어디서 돌려야 하는가 (사실 기반)

이번 조사에서 확인된 사실만 놓고 보면, Google Gemini/Vertex의 Generative AI Prohibited Use Policy는 "적법한 동의 없는 생체정보 사용"만 명시적으로 금지할 뿐 "1:1 본인 대조" 용도를 별도로 금지하는 조항은 없다(정책 침묵 — 명시적 허용도 아님). 다만 Cloud Vision API 경로 자체는 얼굴 식별 기능이 아예 없어 기술적으로 배제되므로, 남는 것은 범용 VLM(Gemini)에 자연어로 "같은 사람인가" 묻는 방식뿐이고 이는 공식 지원 기능이 아닌 회색지대다. 자체 운영 경로에서는 정확도가 검증된 InsightFace(buffalo_l)가 코드는 MIT지만 모델 가중치는 비상업 라이선스라 상업 이용 시 가격 비공개의 별도 계약이 필요하고, 라이선스가 명확한 대안(DeepFace 래퍼의 Facenet512/SFace 등)은 이번 조사에서 원본 LICENSE 파일까지 1차 대조하지 못해 "상업 사용 가능"이라는 2차 출처 서술로만 확보된 상태다. 국내 라이브니스 벤더(알체라·네이버클라우드·유스비 등)는 얼굴대조+라이브니스를 통합 제공하지만 가격이 계약형 비공개이고, 그 원천인 금융위 비대면 실명확인 가이드라인은 금융회사 대상이라 비금융 서비스에 법적 적용 의무는 확인되지 않는다. 결론적으로 확정 가능한 경로를 고르려면 (a) InsightFace 상업 라이선스 견적 확보, (b) DeepFace 대안 모델(Facenet512/SFace)의 원 LICENSE 파일 1차 대조, (c) Gemini에 대해 실제 "동일인 판별" 프롬프트를 실측해 refusal 여부 확인 — 이 세 가지가 남은 실사 항목이며, 이는 로컬 문서(`phase0-license-check.md` T0-2)가 이미 예정한 "약관 텍스트만으로는 실동작을 알 수 없다"는 방법론과 동일한 접근이 필요하다.

---

Face-verification legal/technical research is done. Summary below (full detail in the report above); key file paths referenced from the repo:

- `/Users/nojeong-un/devs/wearless_studio/docs/personalization/phase0-license-check.md` — already had model/API license findings (Qwen Apache-2.0, Gemini/Replicate policy gates), but only for image *generation*, not face *matching*.
- `/Users/nojeong-un/devs/wearless_studio/docs/personalization/phase0-consent-architecture.md` §6 — confirms age-gating is done via CX 표준인증창, and explicitly flags "사칭 방지(업로드 얼굴=계정 본인)" as a separate unresolved task — this research fills that gap.

**Top-line findings (all with 1차 출처, 확인일 2026-07-15):**

1. **Gemini/Vertex AUP**: no clause bans "1:1 same-person verification" specifically — only bans biometrics used *without legally-required consent* (https://policies.google.com/terms/generative-ai/use-policy). Cloud Vision API has no identification feature at all (celebrity recognition deprecated 2025-09-16). Net: gray zone, not a safe harbor.
2. **Replicate AUP**: no biometric/facial-recognition-specific clause found; risk falls on the user (https://replicate.com/acceptable-use-policy).
3. **InsightFace/buffalo_l**: code MIT, but model weights are non-commercial-research-only; commercial license requires separate paid contract (pricing undisclosed) (github.com/deepinsight/insightface). Commercially-clean alternatives (Facenet512/SFace via DeepFace wrapper) were only confirmed via secondary sources — original LICENSE files not cross-checked, flagged 확인 필요.
4. **Korean liveness vendors** (Alchera, Naver Cloud, YOUS-B, Comtrue): all bundle face-match + liveness, pricing is contract-based/undisclosed. The FSC 비대면 실명확인 standard is financial-sector-specific — no confirmed legal obligation for non-financial services.
5. **PIPA**: Art. 22-2 requires legal-guardian consent for under-14 processing; the PIPC 2022 아동·청소년 가이드라인 treats simple self-reported birthdate/checkbox as sufficient ("적절한 방법"), not strict verification — the project's existing CX-based age gate already exceeds this baseline.

Remaining open items flagged as "확인 불가" (not guessed): Google Cloud Platform general AUP full text (fetch failed — too large), Raonsecure's current product/pricing page (domain redirected to raon.com), the FSC guideline's original text, and the PIPC PDF's exact article/page numbers.

---

사칭 방지(업로드 얼굴=계정 본인) 설계를 위한 외부 사실 조사가 끝났습니다. 전체 상세 내용은 위 리포트에 있고, 핵심만 다시 정리하면 다음과 같습니다.

**1. 얼굴 대조 AUP 허용 여부**
- Google Generative AI Prohibited Use Policy(policies.google.com/terms/generative-ai/use-policy)는 "적법한 동의 없는 생체정보 사용"만 금지하고, "1:1 본인 대조" 용도를 별도로 금지하는 조항은 없음 — 단 명시적 허용도 아닌 회색지대.
- Cloud Vision API는 애초에 얼굴 식별 기능 자체가 없음(celebrity recognition도 2025-09-16 폐지).
- Replicate AUP도 생체인식 특정 조항 없이 일반 불법행위 조항으로 수렴, 책임은 이용자 귀속.

**2. 자체 모델 라이선스**
- InsightFace/buffalo_l: 코드 MIT, 모델 가중치는 비상업 연구용(상업 라이선스 별도 계약 필요, 가격 비공개).
- DeepFace 래퍼의 Facenet512/SFace/Dlib는 상업 사용 가능하다는 2차 출처 서술만 확보 — 원본 LICENSE 파일 1차 대조는 못함.

**3. 국내 라이브니스**: 알체라·네이버클라우드·유스비·컴트루 모두 얼굴대조+라이브니스 통합, 가격은 계약형 비공개. 금융위 비대면 실명확인 표준은 금융회사 대상이라 비금융 서비스 적용 의무는 미확인.

**4. 한국법**: PIPA 제22조의2(만14세 미만 법정대리인 동의), PIPC 가이드라인은 자기기재 수준 연령확인으로 충분(엄격검증 불요) — 현재 프로젝트의 CX 기반 연령게이트는 이 기준을 이미 상회.

**결론**: 확정된 안전 경로는 없으며, 남은 실사 항목은 (a) InsightFace 상업 라이선스 견적, (b) DeepFace 대안 모델 원 LICENSE 대조, (c) Gemini 실제 "동일인 판별" 프롬프트 refusal 실측 — 이 세 가지입니다.

관련 로컬 문서: `docs/personalization/phase0-license-check.md`, `docs/personalization/phase0-consent-architecture.md`(§6에 사칭 방지가 미결 과제로 명시되어 있었음).

다음 단계로 무엇을 진행할까요? 예를 들어 (1) T0-2 스파이크에 "Gemini 동일인 판별 refusal 실측"을 항목으로 추가, (2) InsightFace 상업 라이선스 견적 문의 초안 작성, (3) DeepFace 대안 모델 LICENSE 직접 대조 중 하나를 바로 시작할 수 있습니다.

---

DeepFace 대안 모델 라이선스 대조를 마쳤다. 이전 보고서의 "확인 필요" 플래그가 해소되면서 중요한 새 리스크가 드러났다.

## 추가 확인 결과 (확인일 2026-07-15)

**SFace (OpenCV Zoo)** — 가장 깨끗한 옵션으로 확인됨
- 모델 전용 LICENSE 파일이 저장소 루트가 아니라 `models/face_recognition_sface/LICENSE` 경로에 별도로 존재하며, 내용은 **Apache License 2.0** — 상업적 사용 명시적으로 허용.
- 출처: https://github.com/opencv/opencv_zoo/blob/main/models/face_recognition_sface/LICENSE (확인일 2026-07-15)
- 참고로 2021년 12월에는 라이선스 불명 이슈(https://github.com/opencv/opencv/issues/21192)가 있었으나, 현재 저장소에는 모델 전용 LICENSE 파일이 명시되어 있어 **해소된 것으로 판단**.

**Facenet (davidsandberg/facenet, facenet-pytorch 계열)** — 코드는 깨끗하나 가중치에 잠재 리스크
- 코드: MIT 라이선스 (Copyright David Sandberg 2016), 상업 사용 제한 없음. 단 LICENSE.md는 "the Software"(코드)만 명시할 뿐 사전학습 가중치는 별도로 다루지 않음. 출처: https://github.com/davidsandberg/facenet/blob/master/LICENSE.md
- **그러나 사전학습 가중치는 CASIA-WebFace와 VGGFace2로 학습됨. VGGFace2 데이터셋 자체는 CC BY-NC-SA 4.0(비상업) 라이선스.** 출처: https://www.robots.ox.ac.uk/~vgg/data/vgg_face2/ (확인일 2026-07-15)
- 즉 코드는 MIT여도, **그 코드로 학습된 가중치가 비상업 데이터셋의 파생물이라는 법적 모호성이 남음** — 이는 업계에서도 논쟁 중인 미해결 쟁점(참고: arXiv "Can I use this publicly available dataset to build commercial..." 2111.02374).

**Dlib 얼굴인식 모델 (dlib_face_recognition_resnet_model_v1)** — 동일한 리스크 패턴
- dlib 라이브러리 자체: Boost Software License 1.0, 상업 사용 허용.
- 모델 파일: 저자 Davis King이 "in the public domain, so you can use it for anything you want"라고 명시. 출처: https://blog.dlib.net/2017/02/high-quality-face-recognition-with-deep.html (확인일 2026-07-15)
- **그러나 학습 데이터는 FaceScrub(CC BY-NC 4.0, 비상업) + VGG 데이터셋(robots.ox.ac.uk 라이선스, 비상업) + 저자 직접 수집분 혼합.** FaceScrub 라이선스 출처: creativecommons.org/licenses/by-nc/4.0/legalcode
- 저자가 모델을 "public domain"이라 선언했더라도, 원 데이터셋 제공자(대학)의 비상업 조건이 파생 가중치에 승계되는지는 **법적으로 명확히 해소되지 않은 영역**.

## 결론 업데이트

이전 요약을 다음과 같이 정정·보강한다: **SFace(OpenCV Zoo, Apache-2.0)가 라이선스 사슬 전체(코드+모델 가중치 파일)가 명시적으로 깨끗한 유일한 후보**로 확인됨. Facenet-pytorch·Dlib 얼굴인식 모델은 코드/모델 파일 자체는 관대한 라이선스를 표방하지만, 학습에 쓰인 VGGFace2·FaceScrub 데이터셋이 비상업 라이선스라 상업적 파생물 지위에 대한 법적 모호성이 남는다 — 이는 InsightFace(공급자가 아예 명시적으로 비상업이라 선언한 것)보다는 약하지만 실질적인 리스크이므로, 상업 서비스에 채택 전 법무 검토가 필요하다.

**권고**: FaceMarket/개인화 기능의 얼굴 대조 자체 운영 경로를 선택한다면 **SFace를 1순위 후보**로 두고, 정확도가 부족하면 InsightFace 상업 라이선스 견적을 받는 것이 Facenet-pytorch/Dlib의 모호한 파생물 리스크를 감수하는 것보다 안전하다.

다음으로 (1) InsightFace 상업 라이선스 견적 문의 초안 작성, (2) Gemini 동일인 판별 refusal 실측 스파이크 설계, (3) SFace 기반 CPU 벤치마크 계획 중 무엇을 진행할지 알려주시면 이어서 진행하겠습니다.