# FaceMarket을 만드는 이유 — 모델 시장의 문제점 (발표 자료용 리서치)

- 작성일: 2026-08-21
- 목적: "왜 FaceMarket인가"를 설명하는 발표 슬라이드의 **문제 정의(Problem)** 파트 근거 자료
- 관련 문서: `docs/superpowers/specs/2026-07-17-facemarket-real-person-model-design.md`, `docs/personalization/prd.md`, `documents/PRD.md §2`
- 출처 신뢰도 표기: `[1차]` 법령·판례·정부/협회 자료 / `[2차]` 언론·법무법인 해설 / `[업계]` 업체 블로그·마케팅 자료(자사 유리하게 편향될 수 있음 — 인용 시 출처 명시 필요)

---

## 0. 한 줄 요약 (발표 리드 문장)

> 모델 시장은 **셀러에게는 너무 비싸고 느리며, 모델에게는 불투명하고 불안정하다.**
> 그리고 AI가 이 시장에 들어오면서 **"동의 없이 얼굴이 복제되는"** 세 번째 문제가 새로 생겼다.
> FaceMarket은 이 셋을 동시에 푸는 **동의 기반 얼굴 라이선스 마켓**이다.

---

## 1. 문제 A — 수요측(셀러): 착용컷 한 장의 비용과 리드타임

**슬라이드 메시지: "옷 한 벌 올리는 데 촬영 한 번이 통째로 필요하다."**

| 항목 | 내용 | 출처 |
|---|---|---|
| 촬영 1회 총비용 | 모델료 + 스튜디오 대여 + 포토그래퍼 + 보정을 합치면 **한 번에 수십만 원** | [업계] 고도몰 블로그 |
| 스튜디오 대여료 | 인기 자연광 스튜디오 **시간당 5~7만 원**, 2시간이면 10~12만 원 | [업계] 고도몰 블로그 |
| 신인 모델 페이 | **회당 20~50만 원** 수준에서 시작, 경력에 따라 상승 | [업계] 나무위키 '패션 모델' |
| 기존 촬영 vs AI 비교 | 기존 패션 촬영 **약 12주 / 410만 원** → AI 모델컷 **15분 / 2만 원** | [업계] FitCL 비교 페이지 — **경쟁사 자체 마케팅 수치이므로 "업계 주장" 라벨 필수** |
| 착용컷 유무의 전환율 차이 | 모델 착용샷이 있는 상품과 없는 상품의 전환율 차이 **평균 3배 이상** | [업계] 드랩 블로그 — 출처 표기 후 인용 권장 |

**핵심 논지**: 착용컷은 "있으면 좋은 것"이 아니라 **전환율에 직결되는 필수 자산**인데, 소량 다품종·주 단위 신상 등록을 하는 1인 셀러의 원가 구조로는 매번 촬영이 불가능하다. (`documents/PRD.md §2.2` 사용자 니즈 "모델 섭외·촬영·디자인 비용을 줄이고 싶다"와 동일한 문제 정의)

---

## 2. 문제 B — 거래 구조: 계약이 없거나, 있어도 '기간·범위'가 지뢰다

**슬라이드 메시지: "촬영이 끝나도 리스크는 끝나지 않는다."**

- **모델료는 '사용 범위·기간'까지 포함해 산정된다.** 통상 관행은 *국내 비전속 / 기간 6개월 / 자사몰·SNS 한정*. 해외 사용이나 인쇄물로 범위가 넓어지면 **추가 비용**이 발생한다. → 셀러 입장에선 **6개월 뒤 상세페이지 이미지를 내리거나 재계약**해야 한다. [업계]
- **계약서가 없으면 리스크는 셀러가 진다.** 촬영 당일 모델 **노쇼**를 당해도 보상받지 못하거나, 사용 기한이 불명확하면 **모델이 콘텐츠 삭제를 요구**할 수 있다. [업계]
- **판례로도 다툼이 확인된다.** 사용 기간 약정 없이 장기간 광고에 사용한 사건에서, 법원은 "상업적 사용에는 동의했으나 **사용 기간은 2년 6개월로 제한**된다"고 보아 초상권 침해를 인정한 사례가 있다. [2차/판례 요약]
- **분쟁 유형이 정형화되어 있을 만큼 흔하다.** '모델 사용료 미지급 및 초상권 침해 내용증명' 양식이 별도로 유통될 정도. [2차]

**핵심 논지**: 시장에 **표준화된 라이선스 계층이 없다.** 매 건마다 개별 협상 → 범위·기간이 문서로 남지 않음 → 분쟁이 사후에 터진다.

---

## 3. 문제 C — 공급측(모델): 불투명한 정산과 진입장벽

**슬라이드 메시지: "모델도 이 시장의 피해자다."**

- **열정페이·미지급**: "패션계가 열정페이로 유명하지만 모델계는 그중에서도 손에 꼽을 정도"라는 평가. 패션위크 항공·숙박비를 모델에게 청구하는 에이전시가 예삿일이고, **일당조차 지급하지 않는 경우도 흔하다.** [업계]
- **선지급 = 빚**: 에이전시가 대신 낸 이동·숙박·생활비는 결국 모델의 채무로 돌아와, 흑자 전환까지 오래 걸린다. [업계]
- **에이전시의 광범위한 대리권**: Model Alliance(미국 비영리)에 따르면 전속 계약이 에이전시에 **광범위한 위임장(power of attorney)** 을 부여해, 모델을 대신해 **대금 수령·비용 공제·요율 협상·제3자 이미지 사용 허락**까지 하면서도 **모델에게 그 계약을 보여줄 의무가 없다.** [1차/단체 자료]
- **결과**: 신인·비전속 모델은 "일은 있는데 조건은 모르는" 상태로 일한다. 정보 비대칭이 시장 구조 그 자체.

---

## 4. 문제 D — AI 전환기가 만든 새 문제 (FaceMarket의 존재 이유)

**슬라이드 메시지: "AI가 비용 문제는 풀었지만, 동의 문제는 오히려 키웠다."**

### 4-1. 이미 일어난 일
- **H&M 디지털 트윈 (2025)**: 실제 모델 30명의 AI 디지털 트윈을 만들어 2025년 7월 2일 첫 캠페인 공개. H&M은 "모델이 자신의 디지털 트윈에 대한 **권리를 보유**하고, 다른 브랜드에서도 일할 수 있으며, **매번 통상 캠페인처럼 보수를 받는다**"고 밝혔다. [2차: CNN, FashionUnited, nss magazine]
- **그럼에도 비판이 거셌다**: 모델·포토그래퍼·스타일리스트의 일자리 대체 우려, 그리고 **"AI 초상 사용에 대한 업계 표준 보상 체계가 존재하지 않는다"** 는 지적. 브랜드도 모델도 법적 미개척지에 있다. [2차]

### 4-2. 법이 이미 움직이고 있다 (규제 순풍 = 사업 근거)
- **뉴욕주 Fashion Workers Act (2025-06-19 시행)** [1차/법령]
  - 브랜드는 모델의 **디지털 복제본(digital replica)** 을 만들거나 사용하기 전에 **명시적 서면 동의**를 받아야 한다.
  - 서면 동의에는 **사용 범위(scope)·목적(purpose)·보수(rate of pay)·사용 기간(length of time)** 이 명시되어야 한다.
  - '디지털 복제본' 정의: 얼굴·신체·음성 등 모델의 외형을 실질적으로 복제·대체하는 AI 생성물(색보정·경미한 리터치 등 통상 후보정은 제외).
  - 적용 대상은 모델 매니지먼트사뿐 아니라 **모델을 기용하는 브랜드·광고주·프로덕션**까지.
- **한국: 인격표지영리권(퍼블리시티권) 민법 개정안** [1차/입법예고]
  - 법무부는 "성명·초상·음성 등 개인을 나타내는 인격표지를 **영리적으로 이용할 권리**"를 신설하는 민법 개정안을 입법예고.
  - **유명세와 무관하게 모든 개인**에게 인정되며, **사후 30년까지 상속** 가능한 방향으로 추진.
  - AI로 동의 없이 학습·상업적 이용하는 행위는 퍼블리시티권 침해이자 부정경쟁행위로서 손해배상 대상이라는 해석. [2차/법률 칼럼]

**핵심 논지**: 시장은 **"AI 모델 = 싸다"** 단계를 지나 **"AI 모델 = 누구 얼굴인가, 동의는 받았는가, 보수는 지급됐는가"** 단계로 이동 중이다. 규제는 이미 그 방향으로 확정됐다.

---

## 5. 시장 규모·성장 근거 (필요 시 1장)

- 패션 산업 AI 시장: **2026년 9.7억 달러 → 2035년 47.9억 달러 (CAGR 19.4%)** [Market Research Future]
- 다른 추정: **2025년 11.7억 달러 → 2033년 161.6억 달러 (CAGR 38.9%)** [Business Research Insights]
- 글로벌 패션 이커머스: **2026년 9,590억 달러 → 2035년 2조 4,097억 달러 (CAGR 10.8%)** [Business Research Insights]
- ※ 리서치 하우스별 편차가 크므로 슬라이드에는 **하나만 골라 출처와 함께** 쓰는 것을 권장.

---

## 6. 문제 → FaceMarket 해법 매핑 (발표의 전환 슬라이드)

| 시장의 문제 | FaceMarket의 답 | 구현 근거 |
|---|---|---|
| A. 촬영 1회당 수십만 원 · 주 단위 신상 대응 불가 | 얼굴 자산 1회 등록 → **컷 단위 생성**으로 한계비용 절감 | `cut_generator` 아이덴티티 주입 파이프라인 |
| B. 사용 범위·기간이 구두 합의, 6개월 뒤 삭제 리스크 | **라이선스가 데이터로 존재**. 라이선스 미활성 시 컷 생성 자체가 `REJECTED` — 조용한 폴백 금지 | 설계 v2 §C3 아이덴티티-소스 상태머신 |
| C. 모델 정산 불투명·미지급 | 사용 건 단위로 기록되는 **모델 카탈로그 + 정산 경로** | `fm_models`, 라이선스·정산 슬롯 |
| D. 동의 없는 디지털 복제 | **본인확인(CI/DID) → 본인 사진 3장만 사용(얼굴 신규 생성 금지) → 얼굴 대조 QC로 타인 얼굴 스푸핑 차단** | 설계 v2 §2, §C2 (OpenCV SFace/YuNet, pairwise 코사인 동일인 게이트) |
| D'. 생체정보 보호 의무 | 얼굴 = 생체 PII. **비공개 버킷 전용, 공개 폴백 금지**, 로그·이벤트·API 응답에 얼굴 바이트·임베딩·서명 URL 미포함 | Plan Global Constraints, `docs/personalization/api-spec.md §1.4` |

**한 문장 결론**: 뉴욕 Fashion Workers Act가 요구하는 **"범위·목적·보수·기간이 명시된 서면 동의"** 를 FaceMarket은 계약서가 아니라 **제품의 실행 경로 자체**로 구현한다. 라이선스가 없으면 이미지가 나오지 않는다.

---

## 7. 슬라이드 구성 제안 (4~5장)

1. **"착용컷은 필수, 촬영은 불가능"** — 문제 A. 비용/리드타임 숫자 1~2개 + 셀러 페르소나(지수, `docs/personalization/prd.md §3 P1`)
2. **"계약은 없고 분쟁만 남는다"** — 문제 B. 6개월 관행 / 노쇼 / 2년 6개월 판례
3. **"모델도 손해를 본다"** — 문제 C. 열정페이 + 에이전시 위임장(Model Alliance)
4. **"AI가 새 문제를 만들었다"** — 문제 D. H&M 사례 + NY FWA + 인격표지영리권 (규제 타임라인 형태로)
5. **"그래서 FaceMarket"** — §6 매핑 표

---

## 8. 보강하면 좋을 1차 자료 (미확보 / 후속 조사 필요)

- [ ] 한국모델협회·한국패션산업협회의 **모델 표준계약서/표준요율표** 실물 (문체부 표준계약서 목록에 '미술 분야 모델계약서'는 있으나 **패션 촬영 모델용 표준계약서는 확인되지 않음** → "표준계약서 부재" 자체가 근거로 쓸 수 있는 논점)
- [ ] 통계청/공정위 기준 **국내 온라인 의류 셀러 수·평균 매출** (TAM 산정용)
- [ ] 문체부 **대중문화예술산업 실태조사**의 모델 직군 평균 수입·계약서 작성률 (매년 발표, 1차 통계)
- [ ] 뉴욕 FWA 원문 조문 인용 (현재는 법무법인 해설 기준)
- [ ] 인격표지영리권 민법 개정안의 **현재 국회 계류 단계** (발표일 기준으로 재확인 필요 — 2026-08 시점 상태 미확인)

---

## 9. 출처

**국내 촬영·모델 비용**
- [의류 쇼핑몰 제품 사진 촬영 팁ㆍ레퍼런스 총정리 (고도몰)](https://www.godo.co.kr/main/blog/32/%EC%9D%98%EB%A5%98-%EC%87%BC%ED%95%91%EB%AA%B0-%EC%A0%9C%ED%92%88-%EC%82%AC%EC%A7%84-%EC%B4%AC%EC%98%81-%ED%8C%81-%EC%9D%B4%EB%AF%B8%EC%A7%80-%EB%A0%88%ED%8D%BC%EB%9F%B0%EC%8A%A4-%EC%B4%9D%EC%A0%95%EB%A6%AC-4915)
- [쇼핑몰 모델 촬영 비용 410만원 vs 2만원 (FitCL)](https://fitcl.ai/compare)
- [제품 사진 촬영 평균 비용 (숨고)](https://soomgo.com/prices/%EA%B8%B0%EC%97%85-%EC%83%81%EC%97%85%EC%9A%A9-%EC%82%AC%EC%A7%84-%EC%B4%AC%EC%98%81)
- [제품 사진 촬영 비용 완벽 가이드 (GENCY Studio)](https://blog.gencystudio.com/product-photography-cost-guide)
- [패션 모델 (나무위키) — 신인 페이·열정페이·에이전시 비용 구조](https://namu.wiki/w/%ED%8C%A8%EC%85%98%20%EB%AA%A8%EB%8D%B8)
- [모델 섭외 적정 비용은? 에이전시 vs 플랫폼 vs 인스타그램 (스포트라이트)](https://spotlite.global/blog-kr/model-casting-cost-comparison)
- [모델 섭외, 계약서 없이 촬영했다가 큰일 납니다 (스포트라이트)](https://spotlite.global/blog-kr/no-contract-model-shoot-risk)

**초상권·계약 분쟁**
- [모델 사진 무단 사용 초상권 침해 손해배상 — 서울고법 2021나2027933 (엘파인드)](https://lfind.kr/cases/%EC%84%9C%EC%9A%B8%EA%B3%A0%EB%93%B1%EB%B2%95%EC%9B%90/2021%EB%82%982027933)
- [모델 초상권 침해, 무단 사용에 대한 법적 대응 방안 (로톡)](https://www.lawtalk.co.kr/qna/644401)
- [모델 사용료 미지급 및 초상권 침해 내용증명 양식 (참지마요)](https://www.chamjimayo.com/form/request-model-and-portrait-rights)

**퍼블리시티권·표준계약서**
- ['인격표지영리권(퍼블리시티권)' 신설 민법 개정안 입법예고 (법률신문)](https://lawtimes.co.kr/news/184408)
- [퍼블리시티권의 법제화와 AI 시대의 새로운 과제 (일간스포츠)](https://isplus.com/article/view/isp202605060026)
- [인공지능(AI)·딥페이크 생성물의 퍼블리시티권 침해 연구 (KCI)](https://www.kci.go.kr/kciportal/mobile/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003160255)
- [문화체육관광부 분야별 표준계약서 목록](https://www.mcst.go.kr/site/s_data/generalData/dataList.jsp?pMenuCD=0405050000)

**AI 모델·해외 규제**
- [Fashion giant H&M plans to use AI clones of its models. Not everyone is happy (CNN)](https://www.cnn.com/2025/03/28/style/h-and-m-ai-models-intl-scli)
- [H&M turns to AI 'digital twins' in new campaign (FashionUnited)](https://fashionunited.com/news/fashion/h-m-turns-to-ai-digital-twins-in-new-campaign-as-fashion-grapples-with-blurred-realities/2025070466982)
- [AI in Fashion: Revolution or Threat? The Case of H&M's Digital Twins (nss magazine)](https://www.nssmag.com/en/fashion/40536/model-artificial-intelligence-hm-marketing-fashion)
- [Seeing Double: NY Fashion Workers Act Creates New Consent Requirements for Digital Replicas (Benesch Law)](https://www.beneschlaw.com/insight/seeing-double-new-york-fashion-workers-act-creates-new-consent-requirements-for-use-of-generative-ai-tools-to-create-models-digital-replicas/)
- [Fashion Workers Act (Model Alliance)](https://www.modelalliance.org/fwa)
- [The Future of Fashion: Understanding the New York Fashion Workers Act (Davis+Gilbert)](https://www.dglaw.com/the-future-of-fashion-understanding-the-new-york-fashion-workers-act/)
- [How AI, Digital Doubles, and New Laws Are Rewriting Fashion and Beauty (Foley & Lardner)](https://www.foley.com/insights/publications/2026/03/how-ai-digital-doubles-and-new-laws-are-rewriting-fashion-and-beauty/)

**시장 규모**
- [AI in Fashion Market Size, Trends (Market Research Future)](https://www.marketresearchfuture.com/reports/ai-in-fashion-market-31618)
- [AI in Fashion Market Size, Share | Global Forecast 2026-2035 (Business Research Insights)](https://www.businessresearchinsights.com/market-reports/ai-in-fashion-market-122273)
- [Fashion E-commerce Market Size | Forecast 2026-2035 (Business Research Insights)](https://www.businessresearchinsights.com/market-reports/fashion-e-commerce-market-118179)

**경쟁·시장 동향**
- [AI 모델 피팅 & 쇼핑몰 썸네일 AI 모델 촬영 대행 (드랩)](https://draph.art/ko/overview/model_generation)
- [AI 모델이 뜬다! 쇼핑몰 모델 섭외 고민 끝 (드랩)](https://draph.ai/shopping_mall_ai_model_easy_solution/)
- [AI 모델, 패션 산업의 새로운 얼굴인가? (패션포스트)](https://www.fpost.co.kr/board/bbs/board.php?bo_table=special&wr_id=1605)
