# FaceMarket을 만드는 이유 — 모델 시장의 문제점 (발표 자료용 리서치)

- 작성일: 2026-08-21
- 목적: "왜 FaceMarket인가"를 설명하는 발표 슬라이드의 **문제 정의(Problem)** 파트 근거 자료
- 관련 문서: `docs/superpowers/specs/2026-07-17-facemarket-real-person-model-design.md`, `docs/personalization/prd.md`, `documents/PRD.md §2`
- 출처 신뢰도 표기: `[1차]` 법령·판례·정부/협회 자료 / `[2차]` 언론·법무법인 해설 / `[업계]` 업체 블로그·마케팅 자료(자사 유리하게 편향될 수 있음 — 인용 시 출처 명시 필요)

---

## 0. 한 줄 요약 (발표 리드 문장)

> 모델 시장은 **셀러에게는 너무 비싸고, 모델에게는 불투명하다.**
> 근본 원인은 하나 — **초상을 쓸 권리가 제품 밖(구두 합의·PDF 계약서)에 있다.**
> 촬영 시대엔 그게 분쟁으로 터졌고, AI 시대엔 무단 복제로 터지고 있다.
> FaceMarket은 **초상 사용권을 실행 경로 안에 넣는다** — 라이선스가 없으면 이미지가 나오지 않는다.

### 문제 4개의 역할 구분 (발표 설계 시 주의)

| | 답하는 질문 | 슬라이드 역할 |
|---|---|---|
| **A** 셀러 비용 | 누가 아픈가 (수요) | Problem |
| **C** 모델 처우 | 누가 아픈가 (공급) | Problem |
| **B+D** 라이선스 계층 부재 | **왜 하필 "라이선스 마켓" 형태인가 / 왜 지금인가** | Why Now · 해자 |

A·C는 페인포인트, **B+D는 솔루션 형태의 정당화**다. B와 D를 따로 두면 각각 약하지만, 합치면 "같은 결함의 두 시대"가 되어 서로의 근거가 된다.

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

## 2. 문제 C — 공급측(모델): 불투명한 정산과 진입장벽

**슬라이드 메시지: "모델도 이 시장의 피해자다."**

- **열정페이·미지급**: "패션계가 열정페이로 유명하지만 모델계는 그중에서도 손에 꼽을 정도"라는 평가. 패션위크 항공·숙박비를 모델에게 청구하는 에이전시가 예삿일이고, **일당조차 지급하지 않는 경우도 흔하다.** [업계]
- **선지급 = 빚**: 에이전시가 대신 낸 이동·숙박·생활비는 결국 모델의 채무로 돌아와, 흑자 전환까지 오래 걸린다. [업계]
- **에이전시의 광범위한 대리권**: Model Alliance(미국 비영리)에 따르면 전속 계약이 에이전시에 **광범위한 위임장(power of attorney)** 을 부여해, 모델을 대신해 **대금 수령·비용 공제·요율 협상·제3자 이미지 사용 허락**까지 하면서도 **모델에게 그 계약을 보여줄 의무가 없다.** [1차/단체 자료]
- **결과**: 신인·비전속 모델은 "일은 있는데 조건은 모르는" 상태로 일한다. 정보 비대칭이 시장 구조 그 자체.

---

## 3. 문제 B+D (통합) — 초상 사용권이 제품 밖에 있다

> **이 섹션이 발표의 클라이맥스다.** A·C가 "누가 아픈가"라면, 여기는 **"왜 하필 라이선스 마켓이어야 하고, 왜 지금인가"** 에 답한다.
> B(촬영 시대의 계약 분쟁)와 D(AI 시대의 무단 복제)는 다른 문제가 아니라 **같은 결함이 두 시대에 다르게 터진 것**이다.

**슬라이드 메시지: "초상 사용권이 구두 합의와 PDF에 있는 한, 촬영이든 AI든 결과는 같다."**

### 3-1. 결함의 정체 — 라이선스 계층의 부재

초상을 쓸 권리에는 원래 네 가지가 정해져야 한다. **어디까지(범위) · 무엇에(목적) · 얼마에(보수) · 언제까지(기간).**
그런데 이 시장에서 이 넷은 **어디에도 조회 가능한 형태로 존재하지 않는다.** 구두로 합의되고, 잘해야 PDF로 남고, 에이전시가 쥐고 있으며, 셀러도 모델도 나중에 확인할 방법이 없다.

### 3-2. 1막 — 촬영 시대: 결함이 '분쟁'으로 터졌다

- **범위·기간이 값에 포함되는데 관리되지 않는다.** 통상 관행은 *국내 비전속 / 6개월 / 자사몰·SNS 한정*이고, 해외·인쇄물로 범위가 넓어지면 추가 비용이 붙는다. → 셀러는 **6개월 뒤 상세페이지를 내리거나 재계약**해야 하는데, 그 만료일을 **추적하는 주체가 아무도 없다.** [업계]
- **기간을 안 정해두면 법원이 정한다.** 사용 기간 약정 없이 장기간 광고에 사용한 사건에서, 법원은 "상업적 사용에는 동의했으나 **사용 기간은 2년 6개월로 제한**된다"며 초상권 침해를 인정했다. [2차/판례]
- **리스크는 셀러가 진다.** 노쇼를 당해도 보상받지 못하고, 사용 기한이 불명확하면 **모델이 콘텐츠 삭제를 요구**할 수 있다. [업계]
- **분쟁이 정형화될 만큼 흔하다.** '모델 사용료 미지급 및 초상권 침해 내용증명' 양식이 별도로 유통된다. [2차]
- **모델도 자기 조건을 모른다.** 전속 계약이 에이전시에 광범위한 위임장을 주어 **제3자 이미지 사용 허락까지 대신하면서, 모델에게 그 계약을 보여줄 의무가 없다.** (§2 C와 같은 뿌리) [1차/Model Alliance]

### 3-3. 2막 — AI 시대: 같은 결함이 '무단 복제'로 터진다

- **H&M 디지털 트윈 (2025-07-02 캠페인 공개)**: 실제 모델 30명의 AI 디지털 트윈. H&M은 "모델이 **초상 권리를 보유**하고, 다른 브랜드에서도 일할 수 있으며, **매번 통상 캠페인처럼 보수를 받는다**"고 밝혔다. [2차: CNN, FashionUnited]
- **그런데도 비판이 거셌던 이유가 핵심이다**: 일자리 대체 우려와 함께, **"AI 초상 사용에 대한 업계 표준 보상 체계가 존재하지 않는다"** 는 지적. 즉 **선의를 가진 대기업조차 기댈 라이선스 계층이 없다.** 브랜드도 모델도 법적 미개척지에 있다. [2차]
- **AI는 이 결함을 증폭한다.** 촬영은 그래도 현장에 모델이 있어야 하지만, 생성은 **얼굴 3장만 있으면 무한히 재현**된다. 범위·기간이 없는 동의는 촬영 시대엔 '과잉 사용'이었지만, AI 시대엔 **사실상 무제한 사용**이 된다.

### 3-4. 3막 — 법이 결함을 정확히 지목했다 (Why Now)

- **뉴욕주 Fashion Workers Act — 2025-06-19 시행** [1차/법령]
  - 모델의 **디지털 복제본(digital replica)** 을 만들거나 쓰기 전에 **명시적 서면 동의** 필수.
  - 그 동의에 **범위(scope)·목적(purpose)·보수(rate of pay)·기간(length of time)** 을 명시해야 한다.
  - 정의: 얼굴·신체·음성을 실질적으로 복제·대체하는 AI 생성물(색보정·경미한 리터치는 제외).
  - 적용 대상은 매니지먼트사뿐 아니라 **모델을 기용하는 브랜드·광고주·프로덕션**까지.
- **한국 — 인격표지영리권(퍼블리시티권) 민법 개정안** [1차/입법예고]
  - 성명·초상·음성을 **영리적으로 이용할 권리**를 신설. **유명세와 무관하게 모든 개인**, 사후 30년 상속.
  - 동의 없는 AI 학습·상업적 이용은 침해이자 부정경쟁행위로 손해배상 대상이라는 해석. [2차]

**여기서 B와 D가 만난다**: 뉴욕 법이 서면 동의에 적으라고 요구한 네 항목은, **3-2의 분쟁들이 정확히 다투던 그 네 항목**이다. 법은 "이 넷이 없어서 시장이 깨진다"는 것을 뒤늦게 인정한 것이다.

### 3-5. FaceMarket의 답 — 네 항목을 문서가 아니라 레코드로

**법이 "적어라"고 한 것을 FaceMarket은 "실행 조건"으로 만들었다.**

| 뉴욕 FWA 요구 항목 | FaceMarket 구현 | 근거 |
|---|---|---|
| 범위 (scope) | `allowed_use` / `forbidden_use` text[] | `fm_licenses` |
| 목적 (purpose) | `allowed_use` 용도 목록 | `fm_licenses` |
| 보수 (rate of pay) | `unit_price` (KRW/건) | `fm_licenses` |
| 기간 (length of time) | `license_valid_until` timestamptz | `fm_licenses` |
| 서면 동의의 진정성 | VC 발급(`vc_id`, `vc_status_uri`) + 얼굴 `face_image_digest` (SHA-256 무결성) | `fm_licenses` |
| 보수의 **실지급** | 온체인 정산 영수증 — 모델/플랫폼/운영 **70/20/10**, `tx_hash`로 검증. 3분할 합 = 총액 DB 제약 | `fm_settlements` |
| 동의 철회 | `revoke` 즉시 반영 — 얼굴 게이트 차단 + 재생성 시 **409 `license_revoked`** | `revokeLicense()` |
| 제3자 검증 | **무인증 QR 공개 검증** `GET /v1/facemarket/verify/{id}` → `valid, status, allowedUse, forbiddenUse, unitPrice, validUntil, vcId` (얼굴·CI·생년월일은 애초에 안 실림) | `verifyLicensePublic()` |

**결정적 한 줄**:
> 기존 시장에서 "동의를 받았다"는 건 **서랍 속 PDF**다.
> FaceMarket에서 "동의를 받았다"는 건 **누구나 QR로 조회 가능한 상태값**이고,
> 그 상태가 active가 아니면 **이미지 생성 자체가 거부된다**(`REJECTED` — 조용한 폴백 금지).

### 3-6. 예상 반박과 답변 (Q&A 대비)

| 반박 | 답변 |
|---|---|
| "FaceMarket 라이선스도 만료되면 셀러가 이미지를 내려야 하는 건 똑같지 않나?" | 차이는 **만료가 있느냐가 아니라 만료를 아느냐**다. 기존 시장은 만료일을 추적하는 주체가 없어 **숨은 부채**로 남지만, FaceMarket은 `license_valid_until`이 시스템에 있어 사전 고지·갱신이 가능한 **관측 가능한 상태**다. 리스크의 크기가 아니라 성격이 바뀐다. |
| "그냥 존재하지 않는 AI 얼굴 쓰면 라이선스가 아예 필요 없잖아 (드랩·FitCL)" | 그 시장은 이미 있고 우리가 경쟁할 곳이 아니다. FaceMarket의 수요는 **특정 실존 얼굴이 필요한 경우** — 1인 셀러 본인(`docs/personalization/prd.md` P1 '지수'), 얼굴로 팔리는 브랜드, 인플루언서 협업. 그 순간 라이선스는 선택이 아니라 **필수 인프라**가 된다. |
| "남의 얼굴을 몰래 등록하면?" | 등록 경로가 **본인확인(CI/DID) → 본인 사진 3장 → 얼굴 대조 QC(SFace pairwise 코사인 동일인 게이트)** 로 막혀 있고, **얼굴 신규 생성은 금지**(업로드 합성만)다. 스푸핑 차단이 설계의 1급 목표. |
| "생체정보 수집은 리스크 아닌가?" | 얼굴 = 생체 PII로 취급. **비공개 버킷 전용·공개 폴백 금지**, 로그·이벤트·API 응답·잡 결과에 얼굴 바이트·임베딩·서명 URL 미포함. 분리 동의와 철회 시 파생물 연쇄 파기까지 설계됨. |

### 3-7. ⚠️ 발표 전 확정 필요 (현재 미정)

- **`revoke`/만료 시 이미 생성된 산출물의 처리.** 현재 코드에서 revoke는 **재생성을 차단**하지만(409), **이미 다운로드된 상세페이지 이미지**의 사후 처리 정책은 문서에 없다. 날카로운 심사위원이 반드시 묻는 지점 → "생성 시점의 라이선스가 유효했다면 산출물은 유효" 같은 원칙을 **미리 정하고 들어갈 것**.
- **인격표지영리권 개정안의 현재 국회 계류 단계** — 2026-08 기준 상태 미확인. 발표 직전 재확인 필요.

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
| **A.** 촬영 1회당 수십만 원 · 주 단위 신상 대응 불가 | 얼굴 자산 1회 등록 → **컷 단위 생성**으로 한계비용 절감 | `cut_generator` 아이덴티티 주입 파이프라인 |
| **C.** 모델 정산 불투명·미지급 | **온체인 정산 영수증** — 70/20/10 분할, `tx_hash` 검증, 3분할 합=총액 DB 제약. 에이전시를 거치지 않고 **모델이 직접 조건을 정하고 확인** | `fm_settlements`, `getJobSettlement()` |
| **B+D.** 범위·목적·보수·기간이 어디에도 조회 가능한 형태로 없음 | 네 항목을 **레코드로 보유** + **QR 무인증 공개 검증** + 미활성 시 생성 `REJECTED`(조용한 폴백 금지) | `fm_licenses`, `verifyLicensePublic()`, 설계 v2 §C3 |
| **B+D.** 동의 없는 디지털 복제 | **본인확인(CI/DID) → 본인 사진 3장만 사용(얼굴 신규 생성 금지) → 얼굴 대조 QC로 스푸핑 차단** | 설계 v2 §2·§C2 (SFace/YuNet pairwise 코사인 게이트) |
| **B+D.** 생체정보 보호 의무 | 얼굴 = 생체 PII. **비공개 버킷 전용, 공개 폴백 금지**, 로그·이벤트·API 응답·잡 결과에 얼굴 바이트·임베딩·서명 URL 미포함 | Plan Global Constraints, `docs/personalization/api-spec.md §1.4` |

**한 문장 결론**: 뉴욕 Fashion Workers Act가 요구하는 **"범위·목적·보수·기간이 명시된 서면 동의"** 를 FaceMarket은 계약서가 아니라 **제품의 실행 경로 자체**로 구현한다. 라이선스가 없으면 이미지가 나오지 않는다.

---

## 7. 슬라이드 구성 제안 (4장)

| # | 슬라이드 | 내용 | 근거 |
|---|---|---|---|
| 1 | **"착용컷은 필수, 촬영은 불가능"** | 셀러 비용·리드타임 숫자 1~2개 + 페르소나 '지수' | §1 (A) |
| 2 | **"모델도 손해를 본다"** | 열정페이 + 에이전시 위임장 → **양면 시장의 페인** | §2 (C) |
| 3 | **"진짜 문제는 초상 사용권이 제품 밖에 있다는 것"** | 판례(2년 6개월) → H&M 논란 → NY FWA 4항목. **한 장에 시간축으로** | §3 (B+D) |
| 4 | **"그래서 FaceMarket"** | FWA 4항목 ↔ `fm_licenses` 컬럼 1:1 대응표 + QR 검증 데모 | §3-5, §6 |

**3번 슬라이드 시각화 권장**: 가로 시간축 한 줄 —
`촬영 시대: 구두 합의 → 분쟁(판례 2년 6개월)` → `AI 시대: 무제한 복제(H&M 논란)` → `2025.06 NY FWA 시행: 범위·목적·보수·기간 서면 동의 의무` → `한국: 인격표지영리권 입법 중` → **`FaceMarket: 그 네 항목을 레코드로`**
문제-규제-해법이 한 장에서 이어져서, 4번 슬라이드가 "우리가 미리 만들어둔 것"으로 읽힌다.

**발표 시 가장 강한 대비 문장**:
> 기존: "동의를 받았습니다" → **서랍 속 PDF, 아무도 만료일을 모름**
> FaceMarket: "동의를 받았습니다" → **QR 스캔 한 번, 만료되면 생성이 거부됨**

---

## 8. 보강하면 좋을 1차 자료 (미확보 / 후속 조사 필요)

- [ ] 한국모델협회·한국패션산업협회의 **모델 표준계약서/표준요율표** 실물 (문체부 표준계약서 목록에 '미술 분야 모델계약서'는 있으나 **패션 촬영 모델용 표준계약서는 확인되지 않음** → "표준계약서 부재" 자체가 근거로 쓸 수 있는 논점)
- [ ] 통계청/공정위 기준 **국내 온라인 의류 셀러 수·평균 매출** (TAM 산정용)
- [ ] 문체부 **대중문화예술산업 실태조사**의 모델 직군 평균 수입·계약서 작성률 (매년 발표, 1차 통계)
- [ ] 뉴욕 FWA 원문 조문 인용 (현재는 법무법인 해설 기준)
- [ ] 인격표지영리권 민법 개정안의 **현재 국회 계류 단계** (발표일 기준으로 재확인 필요 — 2026-08 시점 상태 미확인)
- [ ] **[제품 결정 필요]** revoke·만료 시 **이미 생성·다운로드된 산출물**의 사용 가능 여부 정책 (§3-7). 리서치가 아니라 오너 결정 사항이며, 정해지지 않으면 §3-6의 첫 번째 반박을 방어할 수 없다.

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
