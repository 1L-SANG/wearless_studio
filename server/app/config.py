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
    # 생성예시 레지스트리의 상대 URL 기준. prod 상대경로는 명시 필수, dev만 dummy 기본값 허용.
    example_asset_base_url: str | None = None
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
    analysis_model_order: str = "gemini,gpt"  # 폴백 순서(기본=Gemini-first, 2026-07-02 결정). 'gpt,gemini' 등
    analysis_spike: str = "off"  # off | on — 동기 관측 하니스(임시). production 은 job
    analysis_timeout_seconds: float = 60.0  # provider 1콜 상한(폴백 트리거)
    # Gemini thinking 수준 — 분석은 분류·추출 작업이라 low로 충분(미지정 시 모델 기본이
    # 깊은 추론을 돌려 수 초 낭비). off=미전송(모델 기본). 2026-07-07 속도 개선.
    analysis_thinking_level: str = "low"  # low | medium | high | off
    mannequin_tier: str = "image_high"  # AG-04 = Gemini 3 Pro (사용자 결정 — Flash 미사용)
    mannequin_image_size: str = "1K"  # 1K | 2K | 4K (2K 서버경로 저하 시 1K)
    # 전신 세로 고정 → 컷 간 비율 일관 (gemini-3-pro-image 지원: 16:9·9:16·1:1·5:4·4:5·3:2·2:3)
    mannequin_aspect_ratio: str = "2:3"
    mannequin_max_attempts: int = 2  # QC 게이팅 시 재시도 상한 (shadow면 실질 1회)
    mannequin_qc_enabled: bool = False  # False=shadow(판정 로그만) — 캘리브레이션 후 True
    # AG-P2 이미지 동일성 검수(vision LLM "같은 옷인가"). off | shadow(판정 로그만) |
    # enforce(불일치 시 correctionPrompt로 재생성 — 마네킹 재시도 루프 재사용, max_attempts 내).
    # 키 미설정/판정 실패는 게이트 미적용(graceful). 기본 off.
    image_qc: str = "off"
    mannequin_prompt_file: str | None = None  # 없으면 server/prompts/mannequin_generate_v1.txt
    mannequin_prompt_version: str = "v1"
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
    # ---- 검색 증강 (retrieval_upgrade_prd) — 결정적 스택. flag 기본 off ----
    # 벡터/임베딩(vector·refimages)은 보류(ADR D2) — 재진입 시 flag·enum·모델설정 함께 복원.
    retrieval_matching: str = "off"  # off | tags (styleTags 친화도 v1)
    retrieval_knowledge: str = "off"  # off | static (정적 지식 블록)
    seller_text_canonicalize: str = "off"  # off | shadow | enforce (FR-D1 안전 게이트)
    input_qc: str = "off"  # off | shadow | enforce — 업로드 입력 QC (FR-D4, decode·해상도)
    # ---- FaceMarket (해커톤, 검증 실명 모델 마켓) — 기본 off 로 프로드 보호(FACEMARKET_ENABLED) ----
    # off면 라우터 자체가 미등록 → 기존 셀러 플로우 무영향(main.py 조건부 include).
    facemarket_enabled: bool = False
    fm_ci_pepper: str | None = None  # HMAC-SHA256(CI, pepper) dedup용 secret. 없으면 verify 503
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


def _image_size() -> str:
    v = os.getenv("MANNEQUIN_IMAGE_SIZE", "1K").upper()
    return v if v in {"1K", "2K", "4K"} else "1K"


def _mannequin_tier() -> str:
    t = os.getenv("MANNEQUIN_TIER", "image_high")
    return t if t in {"image_light", "image_high"} else "image_high"


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
        example_asset_base_url=(os.getenv("EXAMPLE_ASSET_BASE_URL") or "").rstrip("/") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        vertex_project=os.getenv("VERTEX_PROJECT") or None,
        vertex_location=os.getenv("VERTEX_LOCATION", "global"),
        model_image_light=os.getenv("MODEL_ROUTING_IMAGE_LIGHT", "gemini-3.1-flash-image"),
        model_image_high=os.getenv("MODEL_ROUTING_IMAGE_HIGH", "gemini-3-pro-image"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        model_text=os.getenv("MODEL_ROUTING_TEXT", "gpt-5.4-mini"),
        model_text_gemini=os.getenv("MODEL_ROUTING_TEXT_GEMINI", "gemini-3.5-flash"),
        analysis_model_order=os.getenv("ANALYSIS_MODEL_ORDER", "gemini,gpt"),
        analysis_spike=_flag("ANALYSIS_SPIKE", "off", {"off", "on"}),
        analysis_timeout_seconds=float(os.getenv("ANALYSIS_TIMEOUT_SECONDS", "60")),
        analysis_thinking_level=_flag(
            "ANALYSIS_THINKING_LEVEL", "low", {"low", "medium", "high", "off"}),
        mannequin_tier=_mannequin_tier(),
        mannequin_image_size=_image_size(),
        mannequin_aspect_ratio=os.getenv("MANNEQUIN_ASPECT_RATIO", "2:3"),
        mannequin_max_attempts=int(os.getenv("MANNEQUIN_MAX_ATTEMPTS", "2")),
        mannequin_qc_enabled=(os.getenv("MANNEQUIN_QC_ENABLED", "false").lower() == "true"),
        mannequin_prompt_file=os.getenv("MANNEQUIN_PROMPT_FILE") or None,
        mannequin_prompt_version=os.getenv("MANNEQUIN_PROMPT_VERSION", "v1"),
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
        retrieval_matching=_flag("RETRIEVAL_MATCHING", "off", {"off", "tags"}),
        retrieval_knowledge=_flag("RETRIEVAL_KNOWLEDGE", "off", {"off", "static"}),
        seller_text_canonicalize=_flag(
            "SELLER_TEXT_CANONICALIZE", "off", {"off", "shadow", "enforce"}
        ),
        input_qc=_flag("INPUT_QC", "off", {"off", "shadow", "enforce"}),
        image_qc=_flag("IMAGE_QC", "off", {"off", "shadow", "enforce"}),
        facemarket_enabled=(os.getenv("FACEMARKET_ENABLED", "false").lower() == "true"),
        personalization_enabled=(
            os.getenv("PERSONALIZATION_ENABLED", "false").lower() == "true"
        ),
        fm_ci_pepper=os.getenv("FM_CI_PEPPER") or None,
        cx_trans_base_url=(
            os.getenv("CX_TRANS_BASE_URL") or "https://cx.raonsecure.co.kr:18543"
        ).rstrip("/"),
        fm_chain_rpc_url=(os.getenv("FM_CHAIN_RPC_URL") or "").rstrip("/") or None,
        fm_chain_id=(int(os.getenv("FM_CHAIN_ID")) if os.getenv("FM_CHAIN_ID") else None),
        fm_settlement_address=os.getenv("FM_SETTLEMENT_ADDRESS") or None,
        fm_chain_private_key=os.getenv("FM_CHAIN_PRIVATE_KEY") or None,
        opendid_holder_url=(os.getenv("OPENDID_HOLDER_URL") or "").rstrip("/") or None,
    )
