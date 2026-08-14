# AG-01 상품분석 — 모델 · thinking A/B 실측 (2026-08-14)

오너 질문 두 개에 답하기 위한 실측이다. ① 깊이 생각하기(thinking)를 켜면 특징을 더 잘 잡나,
그때 얼마나 느려지나 ② 분석 1회당 비용이 모델별로 얼마나 차이나나.

- 하니스: `server/scripts/ab_analysis_thinking.py` · 집계 `..._report.py`
- 원본: `documents/research/analysis_thinking_ab_20260814.jsonl` (130콜, 실패 0)
- 표본: `reference/upload_examples/` 26벌 (상의 11 · 아우터 6 · 하의 5 · 원피스 4), 사진 2~4장
- 프로덕션 경로 재현: 같은 프롬프트(`build_prompt`)·같은 responseSchema·같은 축소
  (`analyze_job.shrink_for_vision`, 최장변 1024 q82). **모델과 thinkingLevel 만 다르다.**
- 단가: ai.google.dev/gemini-api/docs/pricing (2026-08-14 조회).
  3.7 flash `$0.75/$3.75` per 1M, 3.1 pro-preview `$2.00/$12.00`. **출력 단가에 thinking 토큰 포함.**

## 결과

| arm | 종류정확 | 성별정확 | 특징 2개 온전 | 지연(중앙) | p90 | thinkTok | $/1회 |
|---|---|---|---|---|---|---|---|
| 3.1 pro · low **(당시 prod)** | 73% | 94% | 25/26 | 5.20s | 6.1s | 0 | 0.01369 |
| 3.1 pro · high | 73% | 89% | 24/26 | 15.73s | 20.2s | 1367 | 0.03011 |
| **3.7 flash · low** | 73% | **100%** | **26/26** | **3.42s** | 5.3s | 211 | **0.00577** |
| 3.7 flash · medium | 73% | **100%** | **26/26** | 5.11s | 6.3s | 594 | 0.00721 |
| 3.7 flash · high | 69% | **100%** | 24/26 | 7.00s | 8.4s | 1449 | 0.01037 |

## 결론

**1. thinking 승급은 이득이 없다.** flash low→high 에서 옷 종류 판정은 26벌 중 25벌이 동일,
핏은 24/26 동일 — 구조적 판단이 사실상 안 바뀐다. 특징도 새로 찾는 게 아니라 어순만 바뀐다
(`전면 절개 라인 / 여유로운 와이드 핏` low·high 동일). 대신 특징 2개를 다 채우는 비율이
26/26 → 24/26 으로 **떨어지고** 지연 +3.6s, 비용 +80%. pro·high 는 15.7s(p90 20.2s)로 논외.
→ `ANALYSIS_THINKING_LEVEL=low` 유지.

**2. 비용은 0.42배, 지연은 -1.8s.** 분석 1회 $0.0137 → $0.0058. 1,000회당 약 $7.9 절감.

**3. AG-01 자체 특징 문구는 flash 가 더 구체적이다.** 평균 10.0자 vs 8.4자.
pro 는 "포켓 디테일"·"배색 포인트 디자인" 같은 범용어를, flash 는 "밧줄 자수"·"세로 절개 스티치"
같은 그 옷 고유의 단서를 쓴다. **단 프로덕션 영향은 제한적이다** — `analyze_job.analyze_image_bytes`
는 AG-08(feature_extractor)이 성공하면 `aiSuggestedPoints` 를 덮어쓴다. 즉 셀러가 보는 특징은
평소 AG-08 산출물이고, AG-01 특징은 **AG-08 실패 시 폴백 경로**에서만 노출된다.
전환의 실이익은 비용·지연·성별 정확도이고, 특징 품질은 폴백 품질 개선으로 계산해야 한다.

## 별개로 드러난 문제 (모델 교체로 안 고쳐짐)

옷 종류 정확도 73% 의 오답 7벌은 **5개 arm 전부 동일하게** 틀린다 = 모델이 아니라 프롬프트/분류 규칙 이슈.

- 셔츠형 아우터 4벌(반팔 데님 셔츠·체크셔츠 등) → 전부 `top/shirt`. 계약상 `shirt` 는 top·outer
  공유 subCategory 라 명백한 오답은 아니다. **규칙 미정의에 가깝다.**
- 원피스 2벌(`흰색 원피스`→`top/shirt`, `스파게티 스트랩 원피스`→`top/tshirt`) → **진짜 오답.**
  셀러가 매번 손으로 고쳐야 한다.
- 상의 11벌·하의 5벌은 전 arm 100%.

→ **별도 트랙에서 해결(같은 날). 아래 참조.**

## 후속: clothingType 분류 규칙 (2026-08-14, 같은 세션)

**루트 원인.** `prompts/product_analyst_v1.txt` 에 clothingType 을 **어떻게 고르는지에 대한 규칙이
아예 없었다.** enum 토큰만 나열하고, 규칙은 전부 고른 *뒤에* 적용되는 것들(dress 면 subCategory
null, dress 면 성별 women)이었다. `shirt` 는 top·outer 양쪽 그룹에 다 들어 있는데 타이브레이크도
없었다. 모델이 자기 사전지식으로 메웠고, 그 사전지식이 일관돼서 5개 조합이 똑같이 틀렸다.

**영향 조사 (clothingType 이 실제로 좌우하는 것).**
- 원피스→top: `matching.complementary_type` 이 None 대신 bottom 을 돌려줘 **매칭 하의가 붙는다**
  — 2026-08-01 셀러 보고 사고(`matching._NO_MATCH` 주석)의 재현. untuck 패스도 헛돌고,
  마네킹 성별 여성 강제도 풀린다. **심각.**
- 셔츠형 아우터→top: 매칭(`_TOP_SIDE` 가 top·outer 동일)·untuck(`_TUCKABLE` 동일)·성별 전부 무영향.
  유일한 차이는 `prompts.py` 의 `${outerwearInnerLine}`(이너 받쳐입기 지시)과 생성예시 카탈로그.
  **경미**, 게다가 모델은 customCategory 에 "데님 셔츠"라고 정확히 적었다 — 사진으로는 결정 불가한
  판매 의도의 문제다. → 오너 결정 2026-08-14: **셔츠형은 outer 로 고정.**

**수정.** 프롬프트에 순서 있는 clothingType 판정 블록 추가.
dress → bottom → outer → top 순으로 stop-at-first-match, dress 판정이 shirt→outer 보다 **먼저**
(셔츠 원피스가 outer 로 새지 않게), top↔dress 동점은 **dress 로 기울인다**(틀리는 방향의 비용이
비대칭이라 — 위 참조).

**1차 시도는 실패했다.** "총장이 어깨너비 2배 이상" 같은 비율 잣대와 "허리에서 끝나는 퍼진 상의는
원피스가 아니다"라는 부정 조건을 넣었더니, 원래 맞던 `드롭웨이스트 원피스`가 top 으로 깨졌다
(73% → 88%, 고쳐짐 6·깨짐 2). 비율은 플랫레이에서 읽을 수 없고, 부정 조건이 빠져나갈 구실이 됐다.
→ 비율을 버리고 **구조**(어깨~밑단이 끊기지 않는 한 벌, 허리·엠파이어·드롭웨이스트 절개 아래로
스커트 패널이 이어지면 dress)로 바꾸고 동점 타이브레이크를 명시.

**검증 (수정 전 26벌 전체를 flash37-low 로 재실행, `analysis_typerule_after2_20260814.jsonl`).**

| | 수정 전 | 1차 시도 | 최종 |
|---|---|---|---|
| 종류 정확도 | 19/26 (73%) | 23/26 (88%) | **25/26 (96%)** |
| 고쳐짐 / 깨짐 | — | 6 / 2 | **7 / 1** |
| 성별 정확 | 18/18 | — | 18/18 |
| 특징 2개 온전 | 26/26 | — | 26/26 |

남은 깨짐 1건은 `상의/스트라이프 셔츠`(여성 단독 상의로 파는 셔츠) → outer. **오너가 확정한
셔츠 규칙의 대가**이고, 표본 안에서 셔츠 규칙은 5벌을 고치고 1벌을 깬다(순 +4).

회귀 가드: `tests/test_product_analyst.py::test_build_prompt_declares_clothing_type_decision_order`
(판정 블록 존재 + dress 가 outer 보다 먼저 + 타이브레이크 문구).

## 반영

- `Settings.model_text_gemini_analysis` 신설 (env `MODEL_ROUTING_TEXT_GEMINI_ANALYSIS`, 기본 `gemini-3.7-flash`).
  `product_analyst.analyze` 가 provider 오버라이드로 전달 — AG-08 분기와 같은 규약.
- prod(`copilot/api/manifest.yml`)는 `MODEL_ROUTING_TEXT_GEMINI=gemini-3.1-pro-preview` **유지**.
  이 값은 게이팅 QC(IMAGE_QC·MANNEQUIN_AXIS_QC·MANNEQUIN_BASE_FIDELITY_QC = enforce)가 함께 쓰기 때문 —
  판정이 무뎌지면 다른 옷 컷이 출고된다. 오너 결정 2026-08-14.

## 한계

- 표본 26벌·arm 당 1회. 지연은 네트워크 변동 포함이고 rep=1 이라 중앙값·p90 은 방향값이다.
- 정답 라벨은 폴더명(카테고리·`여성)/남성)` 접두)에서 온 약한 라벨이다. 특징 문구 품질은
  정량 지표가 아니라 `mockups/analysis-thinking-ab.html` 의 눈검수로 판정했다.
- 실험 총지출 약 $1.7.
