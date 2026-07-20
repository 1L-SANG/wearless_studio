# Wearless 특허 지형 분석 보고서

> 작성: 2026-07-16 · Claude (멀티에이전트 조사 25개, Codex 미사용 독립 수행)
> 범위: 서비스 전체 플로우에서 특허 후보 도출 → 테마 9개 확정 → 테마별 선행특허 조사(Google Patents/KIPRIS) → **인용 특허 49건 전건 실존·정확성 검증 완료** + facemarket 명칭·시장·법제 별도 조사
> ⚠️ 이 문서는 조사 보고서이며 **변리사의 법적 판단이 아니다**. 출원 결정 전 반드시 변리사 선행조사·청구항 설계를 거칠 것.

---

## 0. 한눈에 보기

| # | 테마 | 우선순위 | 선행특허 밀도 | 등록 가능성 소견 | 권고 |
|---|---|---|---|---|---|
| 1 | 핏 축 카탈로그 + 생성-검증 공유 사전 폐루프 + retry-as-edit | **high** | 요소별 존재, 결합은 공백 | 조합 신규성 여지 **큼** | **1순위 출원 검토** |
| 2 | 생체 얼굴 데이터 경로 격리 + 참조 기반 파기 캐스케이드 | **high** | 요소별 촘촘, 핵심 구조는 공백 | 조합 신규성 여지 **큼** | **1순위 출원 검토** |
| 3 | 얼굴 라이선스 소비·검증 계약 + VC 라이프사이클 (facemarket 핵심) | **high** | 근접 선행 존재(미국 2025 등록) | 좁힌 결합 청구로 가능성 있음 | **출원 검토 (청구 좁히기 필수)** |
| 4 | 다중 참조 이미지 컨디셔닝 제어 (refScope·역할 매니페스트·아이덴티티 팩) | **high** | 모델 내부 기법은 두터움, 오케스트레이션 계층은 공백 | 시스템 청구로 여지 있음 | **출원 검토 (§101 대비 필요)** |
| 5 | AI 출력 컴플라이언스 하드 게이트 (실측 카운트 고지 분기 포함) | medium | 부분 공백 (고지 분기는 대응 문헌 없음) | 조합 여지 있음 | 2순위 또는 1~4의 종속항 |
| 6 | 온체인 canonical 정산 + DB 미러 이중장부 | medium | 알리바바 계열이 핵심 선점 | 제한 게이트웨이 확정 대체만 상대적 공백 | 2순위 (범위 좁음) |
| 7 | 크레딧 예약→확정 + 멱등 합류 + lease 자가치유 | medium | 매우 두터움 (OCS·Amazon·Oracle) | 자명성·Alice 리스크 큼 | 후순위/종속항 |
| 8 | 최소수집 본인확인 + 무인증 공개 검증 PII 봉쇄 | medium | 두터움 (Meta·Visa·MS) | 등록돼도 권리범위 좁을 것 | 후순위/종속항 |
| 9 | 닫힌 스타일태그 결정적 보완 매칭 추천 | low | 매우 두터움 + KR 유사출원 거절 사례 | 단독 곤란 | 1·4번 테마의 종속항으로 흡수 |

**핵심 결론 3줄:**
1. 개별 기법 단위로는 거의 모든 축에 선행특허가 있다. 승부처는 전부 **"결합"** — 우리 구현이 여러 요소를 하나의 계약/파이프라인으로 묶은 지점들은 동일 구성을 개시한 문헌이 발견되지 않았다.
2. 가장 강한 후보는 **기술성 짙고 BM 색채가 없는 1·2번**(핏 QC 폐루프, 생체 파기 캐스케이드). facemarket의 3번은 사업 가치가 최상위지만 2025년 등록된 근접 선행(US12423388B2)이 있어 청구항을 좁혀야 한다.
3. facemarket이라는 **사업 모델 자체를 막는 특허는 발견되지 않았고**, 법제(미 NO FAKES Act, 한 부정경쟁방지법)는 오히려 순풍. 단 명칭은 출원 전 KIPRIS/USPTO 정식 상표검색 필수, Hyperreal의 US12374044B2는 설계 시 회피 검토 대상.

---

## 1. 조사 방법과 신뢰도

- **1단계(코드·문서 리딩):** facemarket 마켓·정산, 얼굴 개인화·아이덴티티, 마네킹·핏 축, 컷 파이프라인·콘티보드, PRD 전체 플로우의 5개 영역을 병렬 분석해 특허 후보 29건 수집 → 9개 테마로 병합.
- **2단계(선행특허 조사):** 테마별로 Google Patents(KR 문헌 포함) 웹 검색, 상위 문헌은 청구항 원문까지 확인.
- **3단계(검증):** 별도 팩트체커 에이전트가 인용 특허 49건 전건의 원문 페이지를 열어 실존·제목·출원인·청구 요지를 대조. **49건 전부 실존 확인**, 서술 오류 소수(PG사 명칭, 워핑 용어 등)는 본 보고서에 정정 반영.
- **한계:** KIPRIS·USPTO TESS 정식 검색은 이 환경에서 실행 불가(웹 검색으로 대체). 비공개 상태의 최근 출원(공개 전 18개월 구간)은 원천적으로 조사 불가 — 이는 변리사 선행조사에서도 동일한 한계.

---

## 2. 출원 우선 추천 4건 (상세)

### 2.1 핏 축 카탈로그 + 생성-검증 공유 사전 폐루프 + retry-as-edit  `[1순위]`

**우리 기술:** 의류 핏(연속·주관 속성)을 카테고리×성별 이산 단계 카탈로그로 코드화하고, 각 단계 레코드에 ①생성용 고정 영어 문구 ②신체 랜드마크 기반 '관측 가능 목표' ③편집 교정 지시가 쌍으로 들어 있어 **프롬프트 컴파일러·비전 QC 판정기·편집 교정기가 같은 사전 엔트리를 소비**한다. 축 미달 시 재생성이 아니라 실패 이미지 1장+고정 편집 템플릿으로 편집 1회(retry-as-edit), 개선 실패 시 원본 유지, 선언 축은 정체성 검사 면제, 생성·편집 공유 시도 예산, 코드 레벨 enforce 가드. 소재 조성은 비율 밴드→어휘 변환 + 직조 hard override.

**가장 가까운 선행특허 (전건 실존 확인):**
- `US20260017839A1` (Adobe, 2026 공개) — 속성 토큰→프롬프트 생성, 허용 어휘 제한. **검증·교정과 사전을 공유하는 폐루프 없음.**
- `US20250078361A1` (Google, 2025 공개) — 생성→오류 검출→업데이트 이미지 재생성. **판정 기준이 생성 프롬프트와 사전을 공유하지 않고, 원본 유지·시도 예산 규칙 없음.**
- `US20230230198A1` (Adobe, 2023 공개, US12148119B2로 2024 등록) — 유지하며 선택 편집. **트리거가 사람의 자연어 피드백이지 자동 QC가 아님.**
- 비특허: GarmentAligner(arXiv 2408.12352), VLM 자동평가 논문군 — 개념적 인접, 동일 구성 아님.

**차별점·전략:** "생성 문구+관측 목표+교정 지시가 한 카탈로그 엔트리로 묶여 3용도가 단일 정본을 공유하는 데이터 구조"가 청구의 축. retry-as-edit 규칙 묶음(절대 목표 판정·정체성 면제·원본 유지·공유 예산·코드 가드)은 직접 대응 문헌 없음. 36-arm 실검증 데이터가 있어 명세서의 효과 입증 자료로 활용 가능. 미국은 Alice(§101) 대응으로 랜드마크 측정·파이프라인 구조 한정 필요. 소재 비율 밴드→어휘 변환은 공백이 가장 커 별도 종속항 가치.

### 2.2 생체 얼굴 데이터 경로 격리 + 참조 기반 파기 캐스케이드  `[1순위]`

**우리 기술:** 얼굴 바이트는 전용 비공개 버킷에만 존재(공유 자산 테이블 배제, 버킷 미설정 시 503 fail-closed, 인증 게이트 no-store 스트림 서빙). **라이선스는 얼굴의 사본이 아니라 스토리지 키 '참조'로만 성립** → 원천 파기 시 별도 전파 로직 없이도 파생 라이선스 접근이 자동 404가 되되 **정산 이력 행은 보존**. 파기는 granted 동의 전체를 withdrawn으로 연쇄 기록(재온보딩 시 재사용 차단), DB 미참조 고아 객체까지 prefix 스캔 회수, 스캔 실패를 감사에서 구분.

**가장 가까운 선행특허:** OneTrust 고아 데이터 식별·삭제(`US11157654B2`)·삭제 확인 테스트(`US10607028B2`), IBM 삭제 증명+블록체인(`US11120156B2`), SAP 동의 철회 전파(`US10754932B2`), AT&T 생체 사용 규칙 집행(`US12045331B2`). 비특허로 Meta DELF(USENIX 2020, 참조 그래프 삭제 전파)가 회피 검토 필수 대상.

**차별점·전략:** 선행은 전부 분절적(컴플라이언스 스캔/삭제 후 증명/동의 동기화/소비 시점 필터링). **"사본 부재(참조 유일성)에 의한 자동 무효화 + 정산 행 보존"의 결합은 조사된 어느 특허에도 대응물이 없음.** '삭제권 vs 상거래 이력 보존 의무'라는 규제 충돌을 데이터 구조로 해결했다는 기술적 과제-해결 서사가 강력하고 BM 논란이 없다. 청구항은 이 두 요소를 필수 구성으로 좁혀 쓸 것(DRM 무효화 계열·DELF의 조합 공격 방어).

### 2.3 얼굴 라이선스 소비·검증 계약 + VC 라이프사이클 — facemarket 핵심  `[출원 검토]`

**우리 기술:** 요청 시점 verify-before-use 4단 게이트(해지→비활성→만료→홀더 라이브 VC)와 소비 시점 워커 재검증의 **시점 분리**(큐 지연 중 해지 포착), 검증 실패 시 잡을 죽이지 않는 **'얼굴 없이 생성' 강등**(근거: 얼굴은 한 번 나가면 회수 불가 → false negative만 치명적), 잡 성공 종결에만 걸리는 `job:{job_id}` 결정적 키 멱등 정산, FaceLicense VC의 비차단 발급 + '긍정 응답의 부정만 차단' 비대칭 검증 + active→revoked 전이 시 정확 1회 온체인 폐기.

**가장 가까운 선행특허:**
- `US12423388B2` (Music IP Holdings, **2025 등록**) — 생성 전/후 이중 승인 + 사용 레지스트리 + 스마트컨트랙트 정산. 팬 likeness 삽입 사용례 명시. **가장 위협적인 근접 선행.**
- `US12322402B2` (2025) — 워터마크 만료 기반 해지 + 사용 탐지 정산.
- `US11985125B2` (Mastercard, 2024) — 생체 일방향 해시를 VC 클레임에 바인딩 (우리 faceImageDigest 종속항과 정면 겹침).
- `WO2002080072A1` (IFA, 2002) — 유명인 아바타 라이선싱·사용량 정산의 상위 개념 (광의 청구를 막는 기초 문헌).

**차별점·전략:** 광의 독립항("초상 라이선스 검증 후 AI 생성·정산")은 거절 위험 큼. 살아남는 조합은 ①요청/소비 이중 검증 시점 분리 ②실패의 강등 처리(잡 성공 유지) ③성공 종결 한정 잡 ID 멱등 정산 ④비대칭 검증+정확 1회 폐기 장애 격리 — **이 4요소를 필수 구성으로 한정한 방법+시스템 청구**. 특히 ②강등 설계는 기술적 과제-해결 논리가 명확해 심사 대응력이 상대적으로 높다는 평가.

### 2.4 다중 참조 이미지 컨디셔닝 제어  `[출원 검토]`

**우리 기술:** 참조 범위(전부/포즈만/배경만)별 **사전 생성 전용 파생 자산**(누끼·빈 무대 플레이트) 레지스트리 선택 첨부 + 텍스트 가드 + 서버측 스펙 강등 + 첨부 생략의 계층 방어로 픽셀 전이(의류·소품 유입)를 구조적으로 차단. 첨부 순서와 1:1 대응하는 상수 라벨 **역할 매니페스트**가 이미지별 권한(정체성만/조명만/포즈만)을 선언하며 그 문자열이 프롬프트 조립기의 분기 신호로 재사용되는 이중 용도 데이터 구조. 2x2 그리드 1회 생성→크롭 분해 아이덴티티 팩, 정면 앵커+그리드 시트 원자 쌍 첨부(라이선스 얼굴과 상호배제).

**가장 가까운 선행특허:** Tencent `US12333627B2`(포즈만 분리 전이, 2025 등록 — refScope '포즈만'과 정면 충돌 축), Snap `US11830118B2`(의류 스왑+신원 보존), Snap `US20240265498A1`(얼굴 정체성 보존 블렌딩), Google `US20250157093A1`(반복 파인튜닝 일관 캐릭터). 비특허: IP-Adapter, MagicPose, InstantID, Adobe Firefly 구조/스타일 레퍼런스.

**차별점·전략:** 선행은 전부 **모델 내부**(특징 분해·잠재 블렌딩·파인튜닝) 구현. 우리는 모델을 블랙박스로 두고 **바깥 오케스트레이션 계층**에서 통제 — 이 층위의 특허는 검색상 공백. 리스크는 프롬프트 규칙의 '추상적 아이디어' 취급(§101) → 레지스트리·서버 강등 등 기술 구성과 묶은 시스템/데이터 구조 청구로 좁힐 것. Tencent·Snap 특허의 균등론상 침해 회피 검토도 병행 필요.

---

## 3. 차순위 테마 (요약)

- **AI 출력 컴플라이언스 하드 게이트 (medium):** 실측 치수를 AI 스키마에서 필드째 폐기(소유권 분리)+3중 하드 게이트, 그리고 **AI 고지 문구를 실제 성공 컷의 실측 카운트(face_cuts/total)로 자동 분기** — 후자는 가장 가까운 선행 문헌이 발견되지 않은 공백. EU AI Act 50조(2026-08 시행)·캘리포니아 SB 942 등 규제 시의성도 높음. 단독 출원 또는 2.3의 종속항.
- **온체인 미러 정산 (medium):** '온체인 기준 오프체인 수정'은 알리바바 `US10789598B2`가 선점. 상대적 공백은 "receipt·gas estimation·pending nonce 차단 게이트웨이에서 자기 컨트랙트 상태 폴링을 확정 신호로 대체 + Lock 직렬화 latest-nonce 불변식" — 다만 환경 종속적이라 권리범위가 좁다.
- **크레딧 예약→확정 (medium):** OCS(통신 과금)·Amazon·Salesforce·Oracle·Google 선행이 매우 두텁고 Stripe 멱등 키가 상용 공지. 지연 버킷 바인딩·부분 성공 과금(성공 컷 수×단가 스냅샷)·원장 부재 기반 스위프는 문헌에 없으나 자명성 공격에 취약 — 출원한다면 좁은 결합 청구, 아니면 노하우(영업비밀)로 유지.
- **최소수집 본인확인+공개 검증 (medium):** Meta `US10706277B2`(해시 단일 보관·중복 키)가 정면 선행. '연도-단독 보관→안전측 만 나이 하한' 규칙과 무인증 capability URL 3중 봉쇄 조합만 공백 — 등록 가능하나 권리범위 좁을 전망.
- **보완 매칭 추천 (low):** Google `US10580057B2`(태그 추천+착장 표시, 2031 만료)가 두 축을 이미 청구. 국내 유사 출원(KR20210052813A) 거절 사례가 단독 청구의 약함을 방증. **독립 출원 비추천, 1·4번 테마의 종속항으로 흡수 권장.**

---

## 4. facemarket 분석

### 4.1 명칭·상표
- "facemarket" 명칭 사용자는 있으나 **얼굴 라이선싱 분야에는 없음**: 이라크 쇼핑 앱 FaceMarket(iOS/Android 등재), 영국 컨설팅사 FaceMarkets, LinkedIn 등재 소형 광고사 FACEMARKET. `facemarket.com`은 파킹 매물 상태(구매 협상 가능).
- USPTO 검색상 "FACEMARKET" 등록·출원 미발견(단 TESS 정식 검색 아님). **명칭 확정 전 KIPRIS('페이스마켓'/'FACEMARKET', 9·35·42·45류)와 USPTO TESS 직접 조회 필수.** 'face+market' 결합상표의 식별력 약함 판단 가능성, 'Facebook Marketplace'(통칭 '페북 마켓') SEO 혼동 리스크도 인지할 것.

### 4.2 경쟁 지형 (실서비스)
- **LIKN**(패션 브랜드 특화 초상 라이선싱, 탤런트 건별 승인·85% 배분) — **우리와 가장 유사한 포지셔닝.**
- Authentic/thatsmyface.co(모델 500+, 80% 배분), PersonaShare·Twinnin 등 2025~26 신생 군집(75~85% 배분·건별 승인·옵트아웃 공통 패턴).
- 유명인 축: Metaphysic PRO(DNEG/Brahma 인수, 기업가치 $1.43B), Vermillio(TraceID 상표만 확인), Loti AI(무단 사용 탐지·삭제), Synthesia(배우 3년 정액+사용 건별 보상), H&M×Uncut(모델 30명 디지털 트윈, 모델이 권리 보유).
- **한국에는 실제 사람 얼굴 라이선싱 마켓플레이스가 검색상 부재** (딥브레인AI·클레온 등은 AI휴먼/가상인간 축) → 국내 선점 여지.

### 4.3 특허 지형
- 마켓플레이스 사업 모델 자체를 봉쇄하는 특허는 발견되지 않음. 사업자들의 해자는 특허보다 계약·동의 프로세스.
- **주의 1건: Hyperreal `US12374044B2` "Creation and Use of Digital Humans" (2025-07-29 등록)** — 캡처→보안 DB→'권리 지불 사용자만 접근+사용 제한 메타데이터' 청구. 유사한 '캡처→권리조건 메타데이터→유료 접근' 아키텍처 설계 시 회피 검토 대상.
- 우리 3번 테마(라이선스 소비 계약)의 근접 선행 US12423388B2도 facemarket 관점에서 모니터링 대상.

### 4.4 법제 동향 (순풍)
- **미국 NO FAKES Act(S.4591):** 2026-06-18 상원 법사위 만장일치 통과, 본회의 대기. 디지털 레플리카에 연방 IP권 신설 + 플랫폼 통지-삭제 의무 → **'권리자 승인 기록을 시스템에 남기는' 우리 설계가 곧 컴플라이언스**가 되는 구조.
- **한국 부정경쟁방지법 2조 1호 타목(2022.4 시행):** 유명인 초상·성명의 무단 상업 사용을 부정경쟁행위로 규정. 정식 라이선스 계약을 대행하는 플랫폼의 존재 이유를 법이 뒷받침.

---

## 5. 실행 제언

1. **변리사 상담 1회로 1·2번(+여력 시 3·4번) 묶음 검토** — 본 보고서와 코드 근거(각 테마의 evidence 파일)를 그대로 가져가면 발명 신고서 초안 수준의 자료가 된다. 4건은 발명자·기술 축이 겹쳐 패밀리 설계(우선권 주장 묶음)가 가능할 수 있다.
2. **출원 전 공개 관리:** 데모데이·블로그·IR에서 위 메커니즘의 '구현 방식'(강등 규칙, 공유 사전 구조, 참조 기반 파기 등)을 상세 공개하지 말 것. 한·미는 12개월 공지예외가 있으나 유럽은 없음.
3. **상표 먼저, 특허는 다음:** facemarket 명칭의 KIPRIS/USPTO 정식 검색과 국내 상표 출원(9·35·42·45류)은 비용이 작고 빠르니 즉시 진행 가치.
4. **모니터링 목록:** US12423388B2(Music IP Holdings), US12374044B2(Hyperreal), Tencent US12333627B2, 심사 중인 Vetir US20250037185A1.
5. **선택과 집중:** 7·8·9번은 출원 대신 영업비밀·선사용권 자료(개발 기록 타임스탬프) 보존으로 갈음하는 것이 비용 대비 합리적.

---

## 6. 마네킹컷·콘티보드 페이지 UI 출원 분석 (2차 조사, 2026-07-16)

> 2차 조사: 두 페이지의 실제 구현(`src/features/mannequin/Mannequin.jsx`, `src/features/storyboard/Storyboard.jsx`)을 코드 레벨로 분석해 UI 특허 테마 5개를 도출하고, **인용 특허 25건 전건 실존 검증** + 한국 화상디자인(GUI 디자인권) 제도·비용 조사를 수행.

### 6.0 핵심 결론

**"페이지"는 두 갈래로 보호한다.** 화면의 생김새·전환 애니메이션은 특허가 아니라 **화상디자인**(2021.10 시행 개정 디자인보호법 — 기기 없이 화상 자체 등록 가능, 동적 전환도 순차 도면으로 등록 가능)이 맞는 수단이고, 건당 실부담 수십만 원(중소기업 감면 70%+복수디자인 묶음) 수준으로 특허보다 훨씬 싸고 빠르다(심사 10~14개월, 우선심사 1~3개월). 특허는 화면 뒤의 상호작용+데이터 처리 메커니즘만 대상이 되며, 조사 결과 5개 테마 중 **출원 실익이 있는 것은 2개**(T1 마네킹컷 조정 루프, T4 저장 파이프라인)다.

### 6.1 UI 특허 테마 5개 — 조사 결과

| 테마 | 페이지 | 선행 밀도 | 소견 | 권고 |
|---|---|---|---|---|
| T1. 무료 축 조정 상태머신 + 카탈로그 직역 + 스냅샷 고정 재생성 루프 | 마네킹컷 | Adobe·Google이 요소 선점, 결합은 공백 | **중~중상** | **출원 검토 (UI 쪽 1순위)** |
| T4. 저장 직렬 체인 + 실패 스냅샷 '기준선 동등성 게이트' 조건부 복원 | 콘티보드 | 요소별 선행 존재, 게이트 복원은 공백 | **중간** | 출원 검토 (독립항=조건부 복원 게이트) |
| T3. 진입 자동 생성의 클라이언트+서버 이중 멱등 (정확 1회 과금) | 마네킹컷 | Amazon·MS·IBM이 선점, react-query류 공지 | 낮음~중간 | 단독 비추천 — 본편 테마7과 묶은 시스템 청구항으로 |
| T2. 섹션·행의 '연속 run 필드' 인코딩 + 단일 정규화 불변식 | 콘티보드 | IBM·MS 근접 선행, 진보성 취약 | 낮음 | **방어적 공개** (기술 문서로 공지화해 타사 선점 차단) |
| T5. WeakMap 계보 추적 행위자 구분 실행취소 | 콘티보드 | 큰 아이디어는 IBM이 2000년 선점 | 낮음 (침해 탐지도 불가) | **방어적 공개** |

**T1 (마네킹컷 조정 루프) 상세** — 순차 확인 상태머신(pending→keep|changing→picked)으로 변경 집합을 확정하고, 축 조정은 무료 로컬 draft로만 처리하며 변경 ≥1건일 때만 유료 재생성 잡을 생성(불필요 GPU 잡 방지), 잡 생성 시점 fitProfileSnapshot 고정, 결과는 누적 버전으로 보존하고 사용자 선택 버전이 하위 컷 생성의 1번 참조로 영속되는 엔드투엔드 체인. 근접 선행 Adobe `US20260017839A1`(UI 속성→LLM 프롬프트 생성·어휘 제한)·Adobe `US12148119B2`(변경분만 반영 반복 편집)·Google `US20250157093A1`(선택 결과 기반 일관성)은 각각 요소만 커버 — **"조정 무료/재생성 과금 게이팅 + 스냅샷 고정 + 버전-선택-참조 체인" 전체 조합을 개시한 문헌은 발견되지 않았다.** 청구 전략: 카탈로그 데이터 계약+스냅샷-버전-참조 체인을 중심 청구항으로, 과금 게이팅은 종속항으로(단독으로는 BM 취급 위험). Adobe와의 구분 포인트 = 언어 모델 개입 없는 **결정적 카탈로그 직역**임을 명세서에 명시.

**T4 (저장 파이프라인) 상세** — 모듈 스코프 단일 프로미스 체인으로 디바운스 자동저장·이탈 플러시·CTA 저장을 직렬화하고, 저장 실패 시 스냅샷을 보류했다가 **"서버 현재 상태 == 마지막 성공 기준선"일 때만 복원-재저장, 다르면 폐기+고지**하는 조건부 복원 게이트 + 키 정렬 안정 직렬화로 '응답만 유실된 가짜 실패'를 판별. Oracle `US11102313B2`(저장 큐 직렬화)·Google `US7882072B1`(무조건 복원)·MS `US8805924B2`(내용 동등성 거짓 충돌 해소)·SAP `US20170331915A1`(오프라인 큐+ETag)이 각 축을 선점했지만 **기준선 동등성 게이트 복원은 직접 선행이 없음** — 이걸 독립항으로 세우는 것이 승산 최대.

### 6.2 화상디자인(디자인권) 출원 후보 — 두 페이지의 "생김새"

성립요건은 "기기 조작에 이용되거나 기능이 발휘되는 화상"(순수 장식은 불가) — 우리 후보는 전부 조작/기능 표시라 요건 충족. 물품류 제14류(14-04군), 물품명은 "…조작용 화상"/"…표시용 화상" 형식.

| 순위 | 후보 | 유형 | 비고 |
|---|---|---|---|
| 1 | 생성 대기 **의류 재봉 인포그래픽** (인트로→12초 루프, 카테고리별 실루엣 3종) | 동적 화상 | 식별력 최고, 카테고리 변형은 관련디자인으로 |
| 1 | 콘티보드 **섹션 부채꼴 카드 덱**과 펼침 전환 | 동적 화상 | 조작용 화상, 전환 프레임 순차 도면 |
| 2 | **레이아웃 칩 픽토그램 세트** (1열/2단/3단/2×2/컬러 비교 색점) | 정적 세트 | 색점 동적 채움은 변형 도면으로 |
| 2 | **샷 종류 크롭 픽토그램** (실루엣 viewBox 크롭, 의류별 절단 변형) + 아우터 열림 3상태 아이콘 | 정적 세트 | 크롭 기법 조형이 독특 |
| 3 | 확인 칩 고스트→채움 전환, refScope 호버 오버레이(3버튼+배지 잔류) | 동적 화상 | 일반 패턴과의 거리 확인 후 |
| 제외 | 버전 썸네일 스트립, 비교 레이아웃 전환(컷 고정+우측 패널) | — | 통상 표현/화면 배치 그 자체 — T1 특허 명세서의 실시예 도면으로 활용 |

**비용·절차:** 관납 94,000원/건(전자·심사대상), 복수디자인 묶음 시 추가 건당 ~1만원, 중소기업 감면 최대 70%, 변리사 대행 포함 실부담 건당 대략 40만~70만 원대. 등록 후 존속 20년.

**⚠️ 가장 급한 실무 — 신규성 12개월 시한:** 이미 공개(배포·데모·스크린샷 공유)된 화면은 **최초 공개일부터 12개월 이내** 출원하면서 신규성 상실 예외(디자인보호법 §36)를 주장해야 한다(증명서류는 출원일부터 30일 내). 판례상 이메일 공유도 '공지'로 인정된 사례가 있음. → **지금 할 일: 화면별 최초 공개일 목록화 + 일자 있는 캡처·배포 기록 보존.** 아직 미공개인 개편 UI(콘티보드 섹션 덱 등)는 공개 전 출원이 원칙.

### 6.3 UI 트랙 실행 제언

1. 화상디자인 1순위 2건(재봉 인포그래픽·섹션 덱)+아이콘 세트를 **복수디자인 한 출원으로 묶어** 상표(facemarket)와 함께 변리사에게 일괄 의뢰 — 본편 특허 상담과 같은 자리에서 가능.
2. UI 특허는 T1만 본편 1~4번과 같은 급으로 검토(마네킹컷 페이지의 실질 방어). T4는 여력 시. T2·T5는 기술 블로그/문서 공개로 방어적 공개 처리.
3. KIPRIS 14-04군 선행 화상디자인 조사(네이버·카카오·토스·어도비 출원인 조합)는 이 환경에서 자동 수집이 안 돼 변리사 조사에 포함시킬 것.

---

## 7. 전략 확정 (2026-07-17): "메커니즘 모방 방지" 중심 재편

> 창업자 결정: 화상디자인(화면 생김새) 트랙은 **보류** — 목표는 "우리 메커니즘을 다른 곳에서 따라하지 못하게" 하는 것. §6.2는 참고자료로만 유지한다.

### 7.1 판단 기준 — 특허는 만능이 아니다

메커니즘 모방 방지 관점에서 보호 수단은 3개이고, 갈림길은 **"그 메커니즘이 밖에서 보이는가"**다.

1. **특허**: 출원 18개월 후 명세서가 전 세계에 공개된다(공개가 독점의 대가). 따라서 ①밖에서 관찰 가능해서 어차피 베껴질 수 있고 ②경쟁사가 베꼈을 때 우리가 그 사실을 탐지할 수 있는 메커니즘에만 실익이 있다. **서버 내부에만 있는 메커니즘을 특허 내면 레시피만 공개하고 침해 입증은 못 하는 최악수.**
2. **영업비밀** (부정경쟁방지법): 공개 의무가 없고 기한도 없다. 단 '비밀관리성' 요건(접근통제·NDA·비밀 표기)을 갖춰야 하고, 독자 개발·역공학에는 무력하다. **서버 내부 메커니즘의 기본값.**
3. **방어적 공개**: 특허성이 약하거나 침해 탐지가 불가능한 것은, 우리가 먼저 공지화해서 **남이 특허로 우리 길을 막는 것**을 차단한다(공개일 증거 확보).

추가 유의: **프론트엔드 코드는 번들로 사용자 브라우저에 배포되는 순간 사실상 반공개**라 영업비밀이 성립하기 어렵다. 프론트 메커니즘은 특허 아니면 방어적 공개 중에서 골라야 하고, 대신 경쟁사 번들을 열어 침해를 탐지할 수 있다는 장점이 있다.

### 7.2 전체 메커니즘 재분류

| 메커니즘 | 밖에서 보이는가 (침해 탐지) | 확정 권고 |
|---|---|---|
| 핏 축 카탈로그 폐루프 + **조정 무료/재생성 과금 루프** (본편1 + UI T1 통합) | **보임** — 축 단계 UI·버전 스트립·결과물 특성 | **특허 1순위** (통합 패밀리로) |
| facemarket 라이선스 소비 계약 (4단 게이트·'얼굴 없이 생성' 강등·정산) (본편3) | **보임** — 마켓 참여자(모델·셀러)가 플로우를 직접 경험 | **특허** |
| AI 고지 문구의 실측 카운트 분기 (본편5 일부) | **결과물에 그대로 보임** — 경쟁사 상세페이지만 봐도 탐지 | **특허** (탐지 용이성 최고, 1~3번의 종속항 또는 소형 단독) |
| 다중 참조 컨디셔닝 refScope (본편4) | 절반 — 스코프 선택 UI는 보임, 파생 자산 레지스트리는 안 보임 | **특허** (보이는 스코프 선택 축을 청구항 앞단에) |
| 생체 파기 캐스케이드 (본편2) | 직접은 안 보이나, 경쟁사가 신뢰 마케팅·B2B 실사·심사에서 스스로 드러내는 축 | **특허** (규제 신뢰 해자 — 모방 방지 + 실사 대응 겸용) |
| 콘티보드 저장 파이프라인 (UI T4) | 프론트 번들 검사로 탐지 가능 (영업비밀은 불가) | 특허 차순위 — 여력 없으면 방어적 공개 |
| 크레딧 원장 (본편7) · 본인확인 PII 봉쇄 (본편8) · 온체인 정산 (본편6) | **안 보임** — 서버 내부, 침해 입증 불가 | **영업비밀** (출원 금지 — 공개 역효과) |
| 프롬프트 템플릿·QC 세부 규칙·소재 블록 사전·correctionPrompt 문구 | 안 보임 — 우리 품질의 실질 원천 | **영업비밀 핵심 자산** |
| 섹션 run 인코딩 (UI T2) · undo 계보 추적 (UI T5) | 번들에 이미 사실상 공개, 특허성 약함 | **방어적 공개** |

### 7.3 실행 플랜

**즉시 (이번 주 안에 가능한 것):**
1. **임시명세서 출원으로 우선일 잠금** — 특허청 임시명세서 제도는 자유형식 문서(본 보고서 해당 섹션 + 코드 발췌 그대로)로 즉시 출원일을 확보할 수 있고, 비용이 매우 낮다. 정규 명세서는 1년 내 우선권 주장(또는 1년 2개월 내 보정)으로 전환. → 특허 권고 4건(핏 루프 통합·facemarket 계약·파기 캐스케이드·refScope)을 먼저 잠그고 변리사와 청구항을 다듬는 순서가 "빨리 막고 천천히 다듬기"에 최적.
2. **영업비밀 원본증명 등록** — 영업비밀로 분류한 자산(프롬프트 템플릿, QC 규칙, 크레딧 원장 설계 등)의 전자문서에서 전자지문을 추출해 원본증명기관(영업비밀보호센터/한국특허정보원 등)에 등록하면 "등록 시점에 그 내용을 보유"한 것으로 추정받는다. 분쟁 시 보유 시점 증거가 된다(추정력은 강하지 않으므로 관리 요건과 병행).
3. **비밀관리성 요건 정비** — 해당 문서·저장소에 비밀 표기, 접근 권한 분리, 직원·외주(개발자 포함) NDA 확인. 이게 없으면 영업비밀 자체가 법적으로 성립하지 않는다.

**이후:**
4. 임시명세서 → 정규 출원 전환 시 변리사와 청구항 설계 (본 보고서 §2·§6.1의 청구 전략 참조). 출원 후 서비스·IR에 "특허 출원 중(patent pending)" 표기 — 그 자체로 억지 효과.
5. T2·T5 및 특허 안 가는 공개-불가피 항목은 기술 블로그/문서로 방어적 공개 (공개일 타임스탬프 확보).
6. 백스톱 인지: 특허·영업비밀이 안 닿는 통짜 모방에는 부정경쟁방지법 성과물 도용 조항이 마지막 방어선 (카카오페이 vs 삼성화재 사례처럼 UI·플로우 모방 분쟁에서 실제로 쓰임).
7. 출원 전 공개 관리 유지 — 특히 임시명세서로 잠그기 전에는 데모·IR에서 메커니즘 세부(강등 규칙, 공유 사전, 스냅샷 계약) 설명 금지.

---

## 부록: 인용 특허 전체 목록 (49건 · 전건 실존 검증 완료)

| 공보번호 | 제목(요약) | 출원인 | 연도 | 관련 테마 |
|---|---|---|---|---|
| US12423388B2 | Multi-stage approval and controlled distribution of AI-gener | Music IP Holdings MIH Inc | 2025 | face-license-consumption-pipeline |
| US12322402B2 | AI-generated music derivative works | Daniel A. Drolet (개인) | 2025 | face-license-consumption-pipeline |
| US11985125B2 | Biometrically-enhanced verifiable credentials | Mastercard International Inc | 2024 | face-license-consumption-pipeline |
| WO2019191213A1 | Digital credential authentication | Workday Inc | 2019 | face-license-consumption-pipeline |
| US12561751B2 | Digital copyright creation module for digital content create | AisoluteCo Ltd | 2026 | face-license-consumption-pipeline |
| WO2002080072A1 | Method for licensing three-dimensional avatars | IFA LLC | 2002 | face-license-consumption-pipeline |
| US10789598B2 | Blockchain transaction reconciliation method and apparatus,  | Alibaba Group Holding Ltd (현 Advanced New Technologies Co., Ltd) | 2020 (우선일 2018-05-29) | onchain-mirror-settlement |
| US11095431B2 | Blockchain transaction manager | DLT Global Inc (현 KNNX Corp) | 2021 (우선일 2019-12-13) | onchain-mirror-settlement |
| WO2019231965A1 | Blockchain-based transaction processing method and apparatus | Alibaba Group Holding Ltd (한국 패밀리 KR102337170B1 등 다수국 등록) | 2019 공개 (우선일 2018-05-29) | onchain-mirror-settlement |
| US10540344B2 | Utilizing nonce table to resolve concurrent blockchain trans | Alibaba Group Holding Ltd | 2020 등록 (공개 US20190243820A1, 우선일 2018-11-30) | onchain-mirror-settlement |
| KR102101370B1 | 스토리 창작 기여에 따른 리워드 분배 시스템 | 이준수 (개인) | 2020 등록 (우선일 2018-03-27) | onchain-mirror-settlement |
| US11157654B2 | Data processing systems for orphaned data identification and | OneTrust LLC | 2021 | biometric-purge-cascade-isolation |
| US10607028B2 | Data processing systems for data testing to confirm data del | OneTrust LLC | 2020 | biometric-purge-cascade-isolation |
| US11120156B2 | Privacy preserving data deletion | International Business Machines Corporation (IBM) | 2021 | biometric-purge-cascade-isolation |
| US10754932B2 | Centralized consent management | SAP SE | 2020 | biometric-purge-cascade-isolation |
| US12045331B2 | Device and network-based enforcement of biometric data usage | AT&T Intellectual Property I, L.P. | 2024 | biometric-purge-cascade-isolation |
| US10706277B2 | Storing anonymized identifiers instead of personally identif | Facebook, Inc. (현 Meta Platforms) | 2020 | minimal-identity-public-verify |
| US20220021537A1 | Privacy-preserving identity attribute verification using pol | Visa International Service Association | 2022 | minimal-identity-public-verify |
| US20240346501A1 | Pseudonymous persona code-based age verification token gener | National Association of Convenience Stores | 2024 | minimal-identity-public-verify |
| KR101948541B1 | 연계정보를 이용한 메시지 발송 중계 시스템 및 그 방법 | 김영환 (2020년 페이민트 주식회사로 양수) | 2019 | minimal-identity-public-verify |
| US9768962B2 | Minimal disclosure credential verification and revocation | Microsoft Technology Licensing, LLC | 2017 | minimal-identity-public-verify |
| KR101601636B1 | QR 코드를 이용한 본인확인 시스템 | 주식회사 렛츠온 | 2016 | minimal-identity-public-verify |
| US20260017839A1 | Image generation based on a generated prompt | Adobe Inc. | 2026 (공개, 출원 2024) | fit-axis-catalog-qc-loop |
| US20250078361A1 | Using generative artificial intelligence to edit images base | Google LLC | 2025 (공개) | fit-axis-catalog-qc-loop |
| US20230230198A1 | Utilizing a generative neural network to interactively creat | Adobe Inc. | 2023 (공개, 2024 등록 계열 존재) | fit-axis-catalog-qc-loop |
| KR102318952B1 | 인공지능을 활용한 의류 추천 및 구매 방법, 장치 및 시스템 | 임정현 (개인) | 2021 (등록, 2024 등록료 미납으로 소멸) | fit-axis-catalog-qc-loop |
| US20240193877A1 | Virtual production (마네킹 착장 이미지의 사실적 인물화) | Mannequin Technologies Inc. | 2024 (공개, 출원 2023) | fit-axis-catalog-qc-loop |
| US12333627B2 | Artificial intelligence-based image generation method, devic | Tencent Technology (Shenzhen) Co., Ltd. | 2025 (우선일 2020) | multi-reference-conditioning-control |
| US11830118B2 | Virtual clothing try-on | Snap Inc. | 2023 | multi-reference-conditioning-control |
| US20240265498A1 | Face identity preservation for image-to-image models using s | Snap Inc. | 2024 (공개) | multi-reference-conditioning-control |
| US20250157093A1 | Visual Object Consistency in Image Generation Models | Google LLC | 2025 (공개, 출원 2024) | multi-reference-conditioning-control |
| KR102318952B1 | 인공지능을 활용한 의류 추천 및 구매 방법, 장치 및 시스템 | 임정현 → 주식회사 오즈에이티 (권리 이전) | 2021 | multi-reference-conditioning-control |
| US10031948B1 | Idempotence service | Amazon Technologies, Inc. | 2018 | credit-reserve-confirm-idempotent-jobs |
| US11880835B2 | Prevention of duplicate transactions across multiple transac | Salesforce, Inc. | 2024 | credit-reserve-confirm-idempotent-jobs |
| US8064579B2 | Prepaid services accounts with multi-user customers and indi | Verizon Patent and Licensing (현 양수인 Rakuten Group) | 2011 | credit-reserve-confirm-idempotent-jobs |
| US8930946B1 | Leasing prioritized tasks | Google LLC | 2015 | credit-reserve-confirm-idempotent-jobs |
| EP2928160B1 | Idempotence for database transactions | Oracle International Corp. | 2017 | credit-reserve-confirm-idempotent-jobs |
| US20140337229A1 | Online charging system | Telefonaktiebolaget LM Ericsson | 2014 | credit-reserve-confirm-idempotent-jobs |
| US12468899B2 | Hallucination prevention for natural language insights | Adobe Inc. | 2025 | ai-output-compliance-gates |
| US12073180B2 | Computer implemented methods for the automated analysis or u | Unlikely Artificial Intelligence Ltd | 2024 | ai-output-compliance-gates |
| US12192372B2 | Systems and methods for provable provenance for artificial i | Credo AI Corp | 2025 | ai-output-compliance-gates |
| US12111754B1 | Dynamically validating AI applications for compliance | Citibank, N.A. | 2024 | ai-output-compliance-gates |
| KR20250041452A | 생성형 AI를 이용하여 생성된 디지털 콘텐츠의 디지털 저작권 생성 모듈, 및 이를 이용한 디지털 콘텐츠 유통 | 주식회사 아이솔트 | 2025 | ai-output-compliance-gates |
| US10580057B2 | Photorealistic recommendation of clothing and apparel based  | Google LLC | 2020 | complementary-matching-recommendation |
| US12288242B2 | Generative apparel recommendations using images of a person | Pyxer Inc | 2025 | complementary-matching-recommendation |
| US7617016B2 | Computer system for rule-based clothing matching and filteri | myShape Inc (현 MIPSO Ltd) | 2009 (등록, 현재 만료) | complementary-matching-recommendation |
| US9773270B2 | Method and system for recommending products based on a ranki | Fredhopper BV | 2017 | complementary-matching-recommendation |
| KR10-2021-0052813 (KR20210052813A) | 코디네이션 패턴 분석을 통한 의류상품 추천 시스템 및 그 방법 | 주식회사 옷딜 | 2021 (공개, 2025년 거절 확정) | complementary-matching-recommendation |
| US20250037185A1 | Integrated AI shopping and personal wardrobe management plat | Vetir Inc | 2025 (공개, 심사 중) | complementary-matching-recommendation |

> 검증 방법: 팩트체커 에이전트가 각 특허의 Google Patents 원문 페이지를 직접 열어 제목·출원인·연도·청구 요지를 대조. 전건 실존(exists=true) 확인. 서술 정정 사항은 본문에 반영 완료.

## 부록2: UI 조사 인용 특허 목록 (25건 · 전건 실존 검증 완료, 본편과 일부 중복)

| 공보번호 | 제목(요약) | 출원인 | 연도 | 관련 테마 |
|---|---|---|---|---|
| US20260017839A1 | Image generation based on a generated prompt | Adobe Inc. | 출원 2024 / 공개 2026 | T1-fit-adjust-catalog-prompt |
| US20230230198A1 (등록 US12148119B2) | Utilizing a generative neural network to interactively creat | Adobe Inc. | 출원 2022 / 공개 2023 / 등록 2024 | T1-fit-adjust-catalog-prompt |
| US20250157093A1 | Visual Object Consistency in Image Generation Models | Google LLC | 출원 2024 / 공개 2025 | T1-fit-adjust-catalog-prompt |
| US20250238638A1 | System and method for modifying prompts using a generative l | Shopify Inc. | 출원 2024 / 공개 2025 | T1-fit-adjust-catalog-prompt |
| US12017142B2 | System and method for real-time calibration of virtual appar | 개인 발명자 (Pritesh Kanani) | 출원 2021 / 등록 2024 | T1-fit-adjust-catalog-prompt |
| US7296228B2 | Document editing by blocks and groups | IBM (International Business Machines Corp) | 2007 (출원 2002) | T2-flat-run-section-encoding |
| US8935602B2 | Hierarchical drag and drop structure editor for web sites | Adobe Inc (Adobe Systems) | 2015 (계열 원출원은 2000년대 초) | T2-flat-run-section-encoding |
| US6502101B1 | Converting a hierarchical data structure into a flat data st | Microsoft Corporation (현 Microsoft Technology Licensing LLC) | 2002 (만료: 2020) | T2-flat-run-section-encoding |
| US9245011B2 | Data model versioning for document databases | Red Hat Inc | 2016 | T2-flat-run-section-encoding |
| US6470363B1 | System and method for processing ordered sections having dif | Microsoft Corporation (현 Microsoft Technology Licensing LLC) | 2002 | T2-flat-run-section-encoding |
| US11386351B2 (공개 US20190050756A1) | Machine learning service | Amazon Technologies Inc | 2018 출원 / 2019 공개 / 2022 등록 | T3-dual-idempotent-auto-trigger |
| US10031948B1 | Idempotence service | Amazon Technologies Inc | 2013 출원 / 2018 등록 | T3-dual-idempotent-auto-trigger |
| US8046432B2 (공개 US20100268789A1) | Network caching for multiple contemporaneous requests | Microsoft Corp (현 Microsoft Technology Licensing LLC) | 2009 출원 / 2010 공개 / 2011 등록 | T3-dual-idempotent-auto-trigger |
| US7788316B2 (공개 US20030187995A1) | Efficient server handling of multiple requests from a web br | International Business Machines Corp (IBM) | 2003 출원 / 2010 등록 | T3-dual-idempotent-auto-trigger |
| US8990154B2 | Request de-duplication for enterprise service bus | International Business Machines Corp (IBM) | 2013 출원 / 2015 등록 | T3-dual-idempotent-auto-trigger |
| US11102313B2 | Transactional autosave with local and remote lifecycles | Oracle International Corp | 2021 | T4-serialized-autosave-recovery |
| US8805924B2 | Optimistic concurrency utilizing distributed constraint enfo | Microsoft Technology Licensing LLC | 2014 | T4-serialized-autosave-recovery |
| US7882072B1 | Autosave functionality for web browser | Google LLC | 2011 | T4-serialized-autosave-recovery |
| US20170331915A1 | Providing an offline mode for applications and interfaces ac | SAP SE | 2017 | T4-serialized-autosave-recovery |
| US10783326B2 | System for tracking changes in a collaborative document edit | Workshare Ltd | 2020 | T4-serialized-autosave-recovery |
| US6668338B1 | Dynamic shortcut to reverse autonomous computer program acti | International Business Machines Corp (현 Google LLC) | 2000 출원 / 2003 등록 | T5-provenance-aware-undo |
| US8732575B2 | Word processing system and method with automatic undo operat | 개인 출원 (Mark E. Nusbaum) | 2012 출원 / 2014 등록 | T5-provenance-aware-undo |
| US7900142B2 | Selective undo of editing operations performed on data objec | Microsoft Corporation | 2007 출원 / 2011 등록 | T5-provenance-aware-undo |
| US10063603B2 | Method and system for concurrent collaborative undo operatio | Microsoft Technology Licensing LLC (원출원 LiveLoop Inc) | 2015 출원 / 2018 등록 | T5-provenance-aware-undo |
| US20110035727A1 | Undo/redo architecture across multiple files | Microsoft Corporation | 2002 원출원 / 2014 포기(Abandoned, 미등록) | T5-provenance-aware-undo |

> 2차 검증 메모: US8935602B2의 계열 원출원은 1996-07-29(선행성 오히려 강화), US11386351B2의 멱등 메커니즘은 명세서 개시이며 등록 청구항 미포함(선행 인용은 유효). 나머지는 원문과 일치.
