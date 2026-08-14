"""환경 변수 → Settings. backend_integration_plan §9 (인증·CORS) 기준."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str
    supabase_url: str
    jwks_url: str
    jwt_audience: str
    cors_origins: list[str]
    database_url: str | None
    # R2 (Cloudflare, S3 호환) — 자산 저장 (§3). secret 등급, 서버 전용.
    r2_account_id: str | None
    r2_access_key_id: str | None
    r2_secret_access_key: str | None
    r2_bucket: str | None
    r2_endpoint: str | None
    r2_public_base: str | None  # images.wearless.kr 등 공개 서빙 도메인 (없으면 signed GET)
    # FaceMarket 얼굴 라이선스 = 생체 PII → 공개 도메인 미연결 전용 비공개 버킷.
    # 미설정이면 메인 버킷 폴백(개발). 게이트 라우트가 바이트 스트림 → public_url 미사용.
    r2_face_bucket: str | None = None
    # 내부 SAM2 세그멘테이션 서비스. 미설정이면 캐노니컬 전처리는 그냥 비활성 —
    # 없다고 업로드·분석·생성이 막히면 안 되는 보조 인프라다.
    sam_service_url: str | None = None
    sam_internal_token: str | None = None
    # Front+Back 실측 ~49s(warm, x86_64 Fargate). 90s 는 그 위의 여유다.
    sam_request_timeout_s: float = 90.0
    # 생성예시 레지스트리의 상대 URL 기준. prod 상대경로는 명시 필수, dev만 dummy 기본값 허용.
    example_asset_base_url: str | None = None
    # 배경-only 생성예시는 파일럿 실측 성공률이 안정화될 때까지 명시적 opt-in에서만 허용.
    genexample_bg_enabled: bool = False
    # ---- AI 에이전트 (Phase 4) ----
    # 마지막 블록 + 기본값 — 직접 생성(테스트)·미래 필드 추가에도 안 깨지게.
    # load_settings()는 아래 기본값을 env 값으로 항상 덮어쓴다.
    gemini_api_key: str | None = None  # AI Studio AIza… (서버 전용, secret)
    vertex_project: str | None = None  # 있으면 Vertex 엔드포인트, 없으면 AI Studio
    vertex_location: str = "global"
    # tier→모델 매핑 (ai_agent_modules §1 — 교체는 여기/env 한 곳)
    model_image_light: str = "gemini-3.1-flash-image"
    model_image_high: str = "gemini-3-pro-image"
    # AG-01 상품 분석 (text tier, 멀티모달 입력) — ai_agent_modules §1·§3
    openai_api_key: str | None = None  # sk-… (서버 전용, secret). GPT 경로 키
    model_text: str = "gpt-5.4-mini"  # GPT 폴백 provider 의 text/vision 모델 (openai key 있을 때만)
    model_text_gemini: str = "gemini-3.5-flash"  # text tier 정본 모델 (2026-07-02 결정 — ai_agent_modules §1)
    # AG-08 특징 발굴만 상위 tier 로 분기 (2026-08-01 사용자 결정 — 마네킹 regenerate tier 분기와 같은 패턴).
    # 분류(AG-01)는 결정성·속도 우선이라 정본 tier 유지. 미설정이면 model_text_gemini 로 폴백.
    model_text_gemini_features: str = "gemini-3.6-flash"
    analysis_model_order: str = "gemini,gpt"  # 폴백 순서(기본=Gemini-first, 2026-07-02 결정). 'gpt,gemini' 등
    analysis_spike: str = "off"  # off | on — 동기 관측 하니스(임시). production 은 job
    analysis_timeout_seconds: float = 60.0  # provider 1콜 상한(폴백 트리거)
    # Gemini thinking 수준 — 분석은 분류·추출 작업이라 low로 충분(미지정 시 모델 기본이
    # 깊은 추론을 돌려 수 초 낭비). off=미전송(모델 기본). 2026-07-07 속도 개선.
    analysis_thinking_level: str = "low"  # low | medium | high | off
    # AG-IC 입력 사진 동일성(셀러가 올린 사진들이 같은 옷인가 — input_consistency.py).
    # off=미판정 | warn=프론트 경고 노출. 기본 warn.
    # **enforce 값이 없는 것은 의도**다: 이 판정은 어떤 잡도 막지 않는다. 오탐 1건의 비용
    # (멀쩡한 사진을 지우게 함)이 미탐 1건의 비용(어제와 동일)보다 크기 때문이다.
    # **shadow(판정만 기록·무노출) 를 두지 않는 것도 의도**다(2026-08-02 오너 결정). shadow 의
    # 값은 "사람이 로그를 읽고 임계·프롬프트를 고친다"는 후속 행동에서만 나오는데, LLM 판정은
    # 그 기록으로 자동 학습되지 않는다. 읽을 사람이 정해지지 않은 기록은 호출 비용만 쓴다.
    # 오탐 분포가 궁금하면 shadow 운영 대신 `scripts/ic_calibrate.py`(라벨 픽스처 오프라인
    # 평가)를 쓴다 — 실셀러 트래픽을 태우지 않고 같은 답을 얻는다.
    # 되돌리기: INPUT_CONSISTENCY=off (재배포 없이 env 만으로 즉시 무력화).
    input_consistency: str = "warn"  # off | warn
    mannequin_tier: str = "image_high"  # AG-04 = Gemini 3 Pro (사용자 결정 — Flash 미사용)
    # 조정(:regenerate) 전용 tier. 조정과 초기 생성은 같은 워커·같은 프롬프트를 타서 env 하나로는
    # 분리가 안 된다. 빈 값이면 분기 없이 mannequin_tier 를 그대로 쓴다(기존 동작).
    # 조정 흐름에서만 다른 모델을 시험할 때 쓴다 — 초기 생성 품질을 건드리지 않고 비교한다.
    mannequin_adjust_tier: str = ""  # "" | image_light | image_high
    mannequin_image_size: str = "1K"  # 1K | 2K | 4K (2K 서버경로 저하 시 1K)
    # 전신 세로 고정 → 컷 간 비율 일관 (gemini-3-pro-image 지원: 16:9·9:16·1:1·5:4·4:5·3:2·2:3)
    mannequin_aspect_ratio: str = "2:3"
    #: 일반 generation/QC 호출 총 상한 — 최초 생성 포함 2회 고정(최초 + 재시도 1회).
    #: untuck 은 이 예산 밖의 전용 post-pass 슬롯 1회다(2026-08-12 분리 — 공유 시절
    #: attempt 소진 잡이 tuck 교정을 못 받았다). 3·5 등으로 올리지 않는다.
    mannequin_max_attempts: int = 2
    # 상세페이지 컷 동시 생성 상한. 0 = 제한 없음(콘티 컷 수만큼 동시 — 13컷이면 13개).
    # 구 상수 3은 429 실측이 아니라 보수적 추정이었다(2026-08-03 오너 결정: 전부 병렬 +
    # 제출 간격으로 버스트 완화 + 429 백오프 재시도가 안전망). 문제 시 env 로 되돌린다.
    detail_cut_concurrency: int = 0
    # 컷 제출 간격(ms) — i번째 컷을 i×간격 뒤에 시작해 순간 버스트를 평탄화한다.
    # 13컷 × 3000ms = 마지막 컷이 36초 뒤 시작(제출 속도 20건/분). 0 = 간격 없음.
    detail_cut_stagger_ms: int = 3000
    # bg 편집 컷의 장소일치 QC 재시도 총 시도 상한 — 생성은 샘플링이라 프롬프트만으로는
    # 결정적이지 않다(2026-07-20 실측). 시도당 생성 1회 + 판정 1회.
    bg_scene_qc_attempts: int = 3
    mannequin_qc_enabled: bool = False  # False=shadow(판정 로그만) — 캘리브레이션 후 True
    # AG-P2 이미지 동일성 검수(vision LLM "같은 옷인가"). off | shadow(판정 로그만) |
    # enforce(불일치 시 correctionPrompt로 재생성 — 마네킹 재시도 루프 재사용, max_attempts 내).
    # 키 미설정/판정 실패는 게이트 미적용(graceful). 기본 off.
    image_qc: str = "off"
    # AG-P2 4축 점수 임계 (플랜 Phase 2). 이진 pass/retry 로는 "얼마나 나쁜지"를 몰라
    # 자동통과/사람검수/자동재생성 3분기를 못 만든다. image_qc=enforce 일 때만 게이팅에 쓰인다.
    #
    # 2026-07-31 캘리브레이션(scripts/qc_calibrate_image.py). **생성 시점으로 층화해야 한다** —
    # 저장된 컷은 여러 시점·설정의 산출물이라 섞으면 현재 파이프라인 품질을 오독한다:
    #   07-24 구컷 26건: product_fidelity 중앙 56.5 · critical 16/26
    #   07-30 컷   4건: 중앙 82.5 · critical 1/4
    #   07-31 신규 8건: 중앙 80.0 · critical 0/8   ← 현재 파이프라인의 실제 분포
    # 초기 추측값 90/75 는 신규 분포(75~85)에서도 통과 0 이라 폐기했다. MANNEQUIN_QC_ENABLED 가
    # pass율 0% 로 전 생성을 막았던 2026-07-07 사고와 같은 조건이다.
    # 80/65 는 신규 분포에서 상위 절반이 통과하고 75점 미만만 재생성으로 간다.
    # 주의: 구컷 기준 재생성률(~55%)을 현재 품질로 읽지 말 것 — 신규 8건의 critical 은 0 이다.
    qc_score_auto_pass: int = 80   # 이상 → 자동 통과
    qc_score_review: int = 65      # 이상 → 사람 검수(출고는 하되 표시), 미만 → 자동 재생성
    # 편집(축 교정·가슴 2패스) 회귀 판정의 노이즈 마진. 등급이 내려가도 최저점 하락이 이 값
    # 이하면 편집을 살린다. 판정기는 같은 이미지에 ±30 이 나오고 컷의 23% 가 정확히 80(=경계)
    # 이라, 등급만 보면 2패스가 4~7점 노이즈에도 매번 롤백된다(2026-07-31 prod 실측:
    # 80/83/85 → 76/78/77 로 롤백 → 가슴 볼륨이 한 번도 출고되지 않음).
    qc_edit_regression_margin: int = 10
    # 미세 반복 패턴(스트라이프·체크) 상품의 출력 해상도. 'off' 면 승급 없이 mannequin_image_size 를 쓴다.
    # 2K 실측(2026-08-01): 줄 주기 8.9px → 한 주기를 이루는 요소당 2px 남짓이라 두 색 줄이 한 색으로
    # 뭉개졌다. 4K 면 주기 ~18px 로 요소당 4~5px 이 확보된다. 무지 상품은 승급하지 않는다(비용).
    mannequin_pattern_image_size: str = "4K"  # off | 1K | 2K | 4K
    # 생성 컷의 상품·로고 동일성 QC. off=미판정, shadow=판정만 기록,
    # bestof=불일치 시 원본 입력에서 후보를 더 생성해 첫 pass 또는 picker 최선을 채택.
    garment_qc_mode: str = "bestof"  # off | shadow | bestof
    garment_qc_extra_candidates: int = 2
    # 최종 컷·페이지 독립 QC v1. 먼저 shadow로 실제 통과/실패 표본을 보정한 뒤에만
    # 출고 차단 모드를 별도 결정한다. 현재 허용값은 off | shadow이며 기본 off라 추가 비용 0.
    cut_output_qc_mode: str = "off"  # off | shadow
    page_output_qc_mode: str = "off"  # off | shadow
    # P1 축 인지 QC(선언 핏 축 반영 판정 + 실패 시 편집 교정 1회 — fidelity §G·§H).
    # off | shadow(판정·이벤트만) | enforce(편집 재시도 발화). enforce는 코드 레벨 가드
    # (_MANNEQUIN_AXIS_QC_ENFORCEMENT_READY)가 풀리기 전까지 shadow로 강등(G9 규율).
    mannequin_axis_qc: str = "off"
    # 베이스 마네킹 대비 포즈·프레임 이탈 + 착장 형상 중복/돌출 판정 (off|shadow|enforce).
    # 기본 off — 관측 데이터가 쌓이기 전에는 어떤 환경에서도 조용히 켜지지 않아야 한다.
    mannequin_base_fidelity_qc: str = "off"
    # 셀러가 컷을 거부하고 재생성할 때, **거부된 컷**만 골라 베이스 충실도 관측 잡을 띄운다
    # (on|off, 기본 off). 위 플래그와 분리한 이유: 저건 생성 경로 전체에 판정을 붙이는
    # 스위치고, 이건 오류 표본만 모으는 스위치다. 하나로 묶으면 표본을 모으려는 순간
    # 전 생성에 6~17초가 붙는다.
    mannequin_base_fidelity_observe_regenerations: str = "off"
    #: 톤 에디터(색감·밝기). off = 마스크 전처리도, 에디터 API 도 열리지 않는다.
    mannequin_tone_editor: str = "off"
    matching_cutout: str = "off"  # 커스텀 매칭 의류 누끼(배경 제거). off면 잡 안 돎.
    # 누끼 성공본을 시드 카탈로그와 같은 정면 flat-lay 로 재렌더(카드 썸네일 1장, 무과금
    # 잡 안에서 이미지 호출 1회). off면 생성 자체가 없다. matching_cutout 이 켜져 있고
    # 누끼가 성공한 경우에만 의미가 있다.
    matching_flatlay: str = "off"
    mannequin_prompt_file: str | None = None  # 없으면 server/prompts/mannequin_generate_v1.txt
    mannequin_prompt_version: str = "v1"
    # 여성 기본 가슴 볼륨 2패스 (2026-07-30 스파이크). 생성된 컷에 "가슴만 바꿔라"를 단독 과제로
    # 한 번 더 돌린다 — 1패스만으로는 모델이 몸을 표준으로 정규화해 반영되지 않는다.
    # off | on. 기본 off, 실물 확인 후 on. 켜면 여성 컷당 이미지 호출이 1→2회.
    # 2패스 실패·거부는 삼키고 1패스 컷을 쓴다(잡을 죽이지 않는다).
    mannequin_bust_pass: str = "off"
    # 원단 패턴 2패스 — 미세 패턴 상품에서 표면 패턴만 상품 사진 기준으로 다시 입힌다.
    # 가슴 2패스와 같은 규약: 기본 off 로 두고 실측 확인 뒤 켠다.
    mannequin_fabric_pass: str = "off"  # off | on
    # untuck 2패스 — 상의 밑단을 하의 허리밴드 밖으로 빼는 전용 편집. 프롬프트 5회 강화와
    # QC 재생성이 모두 소진된 뒤의 구조 변경(2026-08-01). QC 검출이 불안정해 게이트로 쓰지
    # 않고 매칭 하의가 붙는 top/outer 잡마다 1회 돈다(이미 빠져 있으면 무변경 반환 지시).
    mannequin_untuck_pass: str = "off"  # off | on
    base_mannequin_women_asset_id: str | None = None  # R2 seed asset (startup 검증)
    base_mannequin_men_asset_id: str | None = None
    job_dispatcher_enabled: bool = True  # §5
    job_poll_interval_seconds: float = 3.0
    job_lease_timeout_seconds: int = 900
    job_worker_id: str = "web"
    credit_cost_version: str = "v1"  # §6 임시 단가
    credit_cost_mannequin_generate: int = 2
    credit_cost_mannequin_adjust: int = 0  # @deprecated AG-05 폐기 — fitProfile 재생성으로 통합 (프론트 CREDIT_COSTS.mannequinAdjust=0 미러)
    credit_cost_storyboard_per_cut: int = 1  # PL-4 상세페이지: AI 컷 1개당 (프론트 CREDIT_COSTS 미러)
    credit_cost_editor_image: int = 1  # PL-5 에디터 이미지 1장
    # ---- 검색 증강 (retrieval_upgrade_prd) — 결정적 스택 ----
    # 벡터/임베딩(vector·refimages)은 보류(ADR D2) — 재진입 시 flag·enum·모델설정 함께 복원.
    retrieval_matching: str = "tags"  # off | tags (styleTags 친화도 v1)
    # 스타일 정규화 점수와 색 조화 점수의 결합 가중치. 0 = 색 랭킹 즉시 롤백.
    matching_color_weight: float = 0.3
    retrieval_knowledge: str = "off"  # off | static (정적 지식 블록)
    # ---- Phase 3 재진입(ADR D2 해제, 2026-07-22): 레퍼런스 컷 검색 → 마네킹 STYLE REFERENCE 첨부 ----
    # off면 기존 생성 경로 무변화(행위 변화 0). 임베딩은 자체 호스팅 로컬 모델(ADR D2 v1.3),
    # 오프라인 배치(scripts/embed_corpus.py)로 사전 적재. 요청 경로에서 코퍼스 임베딩 금지(FR-C2).
    retrieval_refimages: str = "off"  # off | on
    ref_images_topk: int = 2  # 마네킹 생성에 첨부할 레퍼런스 컷 최대 수
    # 벡터 차원은 ref_images.image_embedding / kb_chunks.text_embedding 컬럼 차원과 반드시 일치.
    # 모델 교체 = 별도 forward 마이그레이션(차원 변경). torch/sentence-transformers 는
    # pyproject optional group [embeddings] — prod 기본 이미지 미포함(R3 완화).
    embed_image_model: str = "google/siglip-base-patch16-224"  # 이미지 임베딩(SigLIP, 768-d)
    embed_image_dim: int = 768
    embed_text_model: str = "BAAI/bge-m3"  # 텍스트 임베딩(2b 챌린저 스트레치, 1024-d)
    embed_text_dim: int = 1024
    seller_text_canonicalize: str = "off"  # off | shadow | enforce (FR-D1 안전 게이트)
    input_qc: str = "off"  # off | shadow | enforce — 업로드 입력 QC (FR-D4, decode·해상도)
    # ---- FaceMarket (해커톤, 검증 실명 모델 마켓) — 기본 off 로 프로드 보호(FACEMARKET_ENABLED) ----
    # off면 라우터 자체가 미등록 → 기존 셀러 플로우 무영향(main.py 조건부 include).
    facemarket_enabled: bool = False
    fm_ci_pepper: str | None = None  # HMAC-SHA256(CI, pepper) dedup용 secret. 없으면 verify 503
    # 상세페이지 착용컷 인물 일관성(AG-06): 실존 모델을 골랐는데 facemarket off 라 해석 불가하면
    # 컷마다 인물 참조가 0장이 되어 사람이 랜덤이 된다 → 결정적 가상모델로 폴백해 전 컷 동일 인물
    # 보장. 빈 문자열이면 폴백 비활성(기존 동작). REAL/LEGACY 경로는 폴백하지 않는다(이중 인물 방지).
    detailpage_fallback_model_id: str = "mB"
    # ---- 토스페이먼츠 크레딧 추가구매(WS3) — 시크릿 키가 있어야만 활성 ----
    # 시크릿 키는 **서버 전용**(결제 승인 API Basic auth). 없으면 checkout 이 503 으로 거절한다
    # — 키 없이 조용히 '목 성공'을 돌려주면 결제 안 하고 크레딧이 늘어나는 구멍이 된다.
    toss_secret_key: str | None = None
    toss_api_base: str = "https://api.tosspayments.com"   # 테스트에서 스텁 서버로 오버라이드
    toss_confirm_timeout: float = 15.0                     # 승인 API 타임아웃(초)
    # ---- 개인화(사용자 본인 얼굴·신체) — 기본 off 로 프로드 보호(PERSONALIZATION_ENABLED) ----
    # off면 라우터 자체가 미등록 → 생체정보 처리 코드 미배포(main.py 조건부 include).
    personalization_enabled: bool = False
    # CX 표준인증창 ENT_MID trans 검증 엔드포인트(서버발). FM-03 실측: index.html 경로.
    cx_trans_base_url: str = "https://cx.raonsecure.co.kr:18543"
    # ---- FaceMarket Chain (선택과제2, OmniOne Chain Free-Gas BESU) — record-only 정산 ----
    # 넷 다 있어야 온체인 recorder 활성(app.state.fm_chain). 하나라도 없으면 disabled(정산 no-op).
    # private_key = 컨트랙트 owner(배포자) 키 = recordSettlement 서명 주체. secret 등급, env only.
    fm_chain_rpc_url: str | None = None
    fm_chain_id: int | None = None  # 없으면 eth_chainId 로 조회
    fm_settlement_address: str | None = None  # 배포된 FaceMarketSettlement 주소(0x…)
    fm_chain_private_key: str | None = None  # owner 개인키(0x…). 절대 커밋 금지
    # ---- OpenDID 홀더(선택과제1) — 커스터디얼 홀더 MSA(로컬 :8100). 라이선스 발급 시 FaceLicense VC 발급 ----
    # 미설정이면(프로드) VC 발급 훅 no-op — 기존 라이선스 흐름 무영향. 로컬 dev 에서만 홀더 도달가능.
    opendid_holder_url: str | None = None
    # ---- 실존 모델 얼굴 대조 QC (handoff §03 필수 게이트) — OpenCV SFace/YuNet(Apache-2.0) ----
    # enabled=false면 QC 스킵(dev·shadow). 3장 pairwise 코사인 최소값 < threshold 면 자산 등록 차단.
    fm_face_qc_enabled: bool = False
    fm_face_qc_threshold: float = 0.363  # OpenCV SFace 권장 코사인 동일인 기준선(캘리브 전 잠정)
    fm_face_qc_dir: str | None = None    # SFace/YuNet onnx 디렉터리. None이면 app/data/face_models
    # ---- 이미지 실비 계측(내부용) ----
    # false 면 image_usage_events 적재를 끄고 로그만 남긴다.
    # **기본값은 app_env 가 정한다**(load_settings → _image_usage_persist): production 만 on.
    # 개발자가 리포트용 운영 접속 문자열을 로컬 .env 의 DATABASE_URL 에 붙여넣는 실수가
    # 실제로 가능한데, 그러면 로컬 실험 비용이 운영 원장에 섞여 리포트 총액이 조용히 부푼다
    # (잡 단위 집계는 job_id is not null 로 걸러지지만 총액·모델별·일자별은 안 걸러진다).
    # 로컬에서 일부러 쌓아 보려면 IMAGE_USAGE_PERSIST=true 로 명시적으로 켠다.
    image_usage_persist: bool = False
    # 리포트의 원화 환산 기준. 회계용이 아니라 감각용 — 실제 청구는 달러다.
    image_usage_krw_per_usd: float = 1400.0


def _image_usage_persist(app_env: str) -> bool:
    """운영 원장에 쓰는 것은 운영 배포뿐. 다른 환경은 명시적으로 켜야 한다.

    APP_ENV=production 은 copilot/api/manifest.yml 이 배포 컨테이너에만 넣는다. 로컬·CI 는
    dev 라 기본 off 이고, 로컬 DB 에 일부러 쌓아 보고 싶으면 IMAGE_USAGE_PERSIST=true 로
    켠다(끄는 것도 =false 로 명시 가능 — 환경변수가 있으면 그것이 언제나 우선).
    """
    raw = os.getenv("IMAGE_USAGE_PERSIST")
    if raw is not None:
        return raw.strip().lower() == "true"
    return app_env == "production"


def _bust_pass() -> str:
    """MANNEQUIN_BUST_PASS 파싱. 미지값은 off — 알 수 없는 설정으로 여성 전건에 호출이
    2배가 되는 사고를 막는다(켜는 쪽이 명시적이어야 한다)."""
    v = os.getenv("MANNEQUIN_BUST_PASS", "off").lower()
    return v if v in {"off", "on"} else "off"


def _image_size() -> str:
    v = os.getenv("MANNEQUIN_IMAGE_SIZE", "1K").upper()
    return v if v in {"1K", "2K", "4K"} else "1K"


def _mannequin_tier() -> str:
    t = os.getenv("MANNEQUIN_TIER", "image_high")
    return t if t in {"image_light", "image_high"} else "image_high"


def _mannequin_adjust_tier() -> str:
    """조정 전용 tier — 미설정·오타면 "" (분기 없음, mannequin_tier 그대로)."""
    t = os.getenv("MANNEQUIN_ADJUST_TIER", "")
    return t if t in {"image_light", "image_high"} else ""


def _flag(env: str, default: str, allowed: set[str]) -> str:
    """검색 증강 flag — 허용값 밖이면 안전하게 default(대개 'off')로 폴백."""
    v = (os.getenv(env, default) or default).strip().lower()
    return v if v in allowed else default


def load_settings() -> Settings:
    app_env = os.getenv("APP_ENV", "dev")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    jwks_url = os.getenv("SUPABASE_JWKS_URL", "")
    if not jwks_url and supabase_url:
        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"

    cors_origins = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]

    return Settings(
        app_env=app_env,
        supabase_url=supabase_url,
        jwks_url=jwks_url,
        jwt_audience=os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated"),
        cors_origins=cors_origins,
        database_url=os.getenv("DATABASE_URL") or None,
        r2_account_id=os.getenv("R2_ACCOUNT_ID") or None,
        r2_access_key_id=os.getenv("R2_ACCESS_KEY_ID") or None,
        r2_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY") or None,
        r2_bucket=os.getenv("R2_BUCKET") or None,
        r2_endpoint=(os.getenv("R2_ENDPOINT") or "").rstrip("/") or None,
        r2_public_base=(os.getenv("R2_PUBLIC_BASE") or "").rstrip("/") or None,
        r2_face_bucket=os.getenv("R2_FACE_BUCKET") or None,
        sam_service_url=(os.getenv("SAM_SERVICE_URL") or "").rstrip("/") or None,
        sam_internal_token=os.getenv("SAM_INTERNAL_TOKEN") or None,
        sam_request_timeout_s=float(os.getenv("SAM_REQUEST_TIMEOUT_S") or 90.0),
        example_asset_base_url=(os.getenv("EXAMPLE_ASSET_BASE_URL") or "").rstrip("/") or None,
        genexample_bg_enabled=(
            os.getenv("GENEXAMPLE_BG_ENABLED", "false").lower() == "true"
        ),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        vertex_project=os.getenv("VERTEX_PROJECT") or None,
        vertex_location=os.getenv("VERTEX_LOCATION", "global"),
        model_image_light=os.getenv("MODEL_ROUTING_IMAGE_LIGHT", "gemini-3.1-flash-image"),
        model_image_high=os.getenv("MODEL_ROUTING_IMAGE_HIGH", "gemini-3-pro-image"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        model_text=os.getenv("MODEL_ROUTING_TEXT", "gpt-5.4-mini"),
        model_text_gemini=os.getenv("MODEL_ROUTING_TEXT_GEMINI", "gemini-3.5-flash"),
        model_text_gemini_features=os.getenv(
            "MODEL_ROUTING_TEXT_GEMINI_FEATURES", "gemini-3.6-flash"),
        analysis_model_order=os.getenv("ANALYSIS_MODEL_ORDER", "gemini,gpt"),
        analysis_spike=_flag("ANALYSIS_SPIKE", "off", {"off", "on"}),
        analysis_timeout_seconds=float(os.getenv("ANALYSIS_TIMEOUT_SECONDS", "60")),
        analysis_thinking_level=_flag(
            "ANALYSIS_THINKING_LEVEL", "low", {"low", "medium", "high", "off"}),
        mannequin_tier=_mannequin_tier(),
        mannequin_adjust_tier=_mannequin_adjust_tier(),
        mannequin_image_size=_image_size(),
        mannequin_aspect_ratio=os.getenv("MANNEQUIN_ASPECT_RATIO", "2:3"),
        mannequin_max_attempts=int(os.getenv("MANNEQUIN_MAX_ATTEMPTS", "2")),
        detail_cut_concurrency=int(os.getenv("DETAIL_CUT_CONCURRENCY", "0")),
        detail_cut_stagger_ms=int(os.getenv("DETAIL_CUT_STAGGER_MS", "3000")),
        bg_scene_qc_attempts=int(os.getenv("BG_SCENE_QC_ATTEMPTS", "3")),
        mannequin_qc_enabled=(os.getenv("MANNEQUIN_QC_ENABLED", "false").lower() == "true"),
        mannequin_prompt_file=os.getenv("MANNEQUIN_PROMPT_FILE") or None,
        mannequin_prompt_version=os.getenv("MANNEQUIN_PROMPT_VERSION", "v1"),
        mannequin_bust_pass=_bust_pass(),
        mannequin_fabric_pass=_flag("MANNEQUIN_FABRIC_PASS", "off", {"off", "on"}),
        mannequin_untuck_pass=_flag("MANNEQUIN_UNTUCK_PASS", "off", {"off", "on"}),
        base_mannequin_women_asset_id=os.getenv("MANNEQUIN_BASE_WOMEN_ASSET_ID") or None,
        base_mannequin_men_asset_id=os.getenv("MANNEQUIN_BASE_MEN_ASSET_ID") or None,
        job_dispatcher_enabled=(os.getenv("JOB_DISPATCHER_ENABLED", "true").lower() != "false"),
        job_poll_interval_seconds=float(os.getenv("JOB_POLL_INTERVAL_SECONDS", "3")),
        job_lease_timeout_seconds=int(os.getenv("JOB_LEASE_TIMEOUT_SECONDS", "900")),
        job_worker_id=os.getenv("JOB_WORKER_ID", f"web-{os.getpid()}"),
        credit_cost_version=os.getenv("CREDIT_COST_VERSION", "v1"),
        credit_cost_mannequin_generate=int(os.getenv("CREDIT_COST_MANNEQUIN_GENERATE", "2")),
        credit_cost_mannequin_adjust=int(os.getenv("CREDIT_COST_MANNEQUIN_ADJUST", "0")),
        credit_cost_storyboard_per_cut=int(os.getenv("CREDIT_COST_STORYBOARD_PER_CUT", "1")),
        credit_cost_editor_image=int(os.getenv("CREDIT_COST_EDITOR_IMAGE", "1")),
        retrieval_matching=_flag("RETRIEVAL_MATCHING", "tags", {"off", "tags"}),
        matching_color_weight=float(os.getenv("MATCHING_COLOR_WEIGHT", "0.3")),
        retrieval_knowledge=_flag("RETRIEVAL_KNOWLEDGE", "off", {"off", "static"}),
        retrieval_refimages=_flag("RETRIEVAL_REFIMAGES", "off", {"off", "on"}),
        ref_images_topk=int(os.getenv("REF_IMAGES_TOPK", "2")),
        embed_image_model=os.getenv("EMBED_IMAGE_MODEL", "google/siglip-base-patch16-224"),
        image_usage_persist=_image_usage_persist(app_env),
        image_usage_krw_per_usd=float(os.getenv("IMAGE_USAGE_KRW_PER_USD", "1400")),
        embed_image_dim=int(os.getenv("EMBED_IMAGE_DIM", "768")),
        embed_text_model=os.getenv("EMBED_TEXT_MODEL", "BAAI/bge-m3"),
        embed_text_dim=int(os.getenv("EMBED_TEXT_DIM", "1024")),
        seller_text_canonicalize=_flag(
            "SELLER_TEXT_CANONICALIZE", "off", {"off", "shadow", "enforce"}
        ),
        input_qc=_flag("INPUT_QC", "off", {"off", "shadow", "enforce"}),
        input_consistency=_flag("INPUT_CONSISTENCY", "warn", {"off", "warn"}),
        image_qc=_flag("IMAGE_QC", "off", {"off", "shadow", "enforce"}),
        # 기본값은 dataclass 선언과 **반드시 일치**해야 한다. 실행 경로는 load_settings 라
        # 여기가 정본이고, dataclass 만 고치면 테스트는 통과하는데 실서비스는 옛 값으로 돈다
        # (2026-07-31 실측: dataclass 80 인데 로더 90 이라 enforce E2E 에서 90 이 찍혔다).
        qc_score_auto_pass=int(os.getenv(
            "QC_SCORE_AUTO_PASS", str(Settings.__dataclass_fields__["qc_score_auto_pass"].default))),
        qc_score_review=int(os.getenv(
            "QC_SCORE_REVIEW", str(Settings.__dataclass_fields__["qc_score_review"].default))),
        qc_edit_regression_margin=int(os.getenv(
            "QC_EDIT_REGRESSION_MARGIN",
            str(Settings.__dataclass_fields__["qc_edit_regression_margin"].default))),
        mannequin_pattern_image_size=_flag(
            "MANNEQUIN_PATTERN_IMAGE_SIZE", "4K", {"off", "1k", "2k", "4k"}).upper(),
        garment_qc_mode=_flag(
            "GARMENT_QC_MODE", "bestof", {"off", "shadow", "bestof"}),
        garment_qc_extra_candidates=int(os.getenv("GARMENT_QC_EXTRA_CANDIDATES", "2")),
        cut_output_qc_mode=_flag("CUT_OUTPUT_QC_MODE", "off", {"off", "shadow"}),
        page_output_qc_mode=_flag("PAGE_OUTPUT_QC_MODE", "off", {"off", "shadow"}),
        mannequin_axis_qc=_flag("MANNEQUIN_AXIS_QC", "off", {"off", "shadow", "enforce"}),
        mannequin_base_fidelity_qc=_flag(
            "MANNEQUIN_BASE_FIDELITY_QC", "off", {"off", "shadow", "enforce"}),
        mannequin_tone_editor=_flag("MANNEQUIN_TONE_EDITOR", "off", {"off", "on"}),
        mannequin_base_fidelity_observe_regenerations=_flag(
            "MANNEQUIN_BASE_FIDELITY_OBSERVE_REGENERATIONS", "off", {"off", "on"}),
        matching_cutout=_flag("MATCHING_CUTOUT", "off", {"off", "on"}),
        matching_flatlay=_flag("MATCHING_FLATLAY", "off", {"off", "on"}),
        facemarket_enabled=(os.getenv("FACEMARKET_ENABLED", "false").lower() == "true"),
        detailpage_fallback_model_id=os.getenv("DETAILPAGE_FALLBACK_MODEL_ID", "mB"),
        personalization_enabled=(
            os.getenv("PERSONALIZATION_ENABLED", "false").lower() == "true"
        ),
        fm_ci_pepper=os.getenv("FM_CI_PEPPER") or None,
        toss_secret_key=os.getenv("TOSS_SECRET_KEY") or None,
        toss_api_base=os.getenv("TOSS_API_BASE", "https://api.tosspayments.com").rstrip("/"),
        toss_confirm_timeout=float(os.getenv("TOSS_CONFIRM_TIMEOUT", "15")),
        cx_trans_base_url=(
            os.getenv("CX_TRANS_BASE_URL") or "https://cx.raonsecure.co.kr:18543"
        ).rstrip("/"),
        fm_chain_rpc_url=(os.getenv("FM_CHAIN_RPC_URL") or "").rstrip("/") or None,
        fm_chain_id=(int(os.getenv("FM_CHAIN_ID")) if os.getenv("FM_CHAIN_ID") else None),
        fm_settlement_address=os.getenv("FM_SETTLEMENT_ADDRESS") or None,
        fm_chain_private_key=os.getenv("FM_CHAIN_PRIVATE_KEY") or None,
        opendid_holder_url=(os.getenv("OPENDID_HOLDER_URL") or "").rstrip("/") or None,
        fm_face_qc_enabled=(os.getenv("FM_FACE_QC_ENABLED", "false").lower() == "true"),
        fm_face_qc_threshold=float(os.getenv("FM_FACE_QC_THRESHOLD") or "0.363"),
        fm_face_qc_dir=os.getenv("FM_FACE_QC_DIR") or None,
    )
