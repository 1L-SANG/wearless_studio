-- =============================================================
-- 20260709000000_facemarket_core.sql  (FM-04 초안 — 팀원 리뷰 대상)
-- FaceMarket 코어 4테이블. 해커톤 결선(검증 실명 모델 마켓) MVP.
--   fm_models                 검증 모델 카탈로그(공급자) + 검증배지 + DID/chain_ref
--   fm_identity_verifications CX 표준인증창 결과 감사(토큰 리플레이 차단)
--   fm_licenses               얼굴 라이선스(VC 연동) — 게이트 URL·digestSRI·용도·단가·만료·revoke
--   fm_settlements            record-only 정산 미러(온체인 반환값 저장, 이중장부 CHECK)
--
-- 적대검증(v2) 반영 보안 결정:
--   · ci_hash = HMAC-SHA256(CI, pepper), **fm_models 단일 보관**(중복 저장 금지, 대입 역추적 차단)
--   · cx_tx_id UNIQUE — CX 토큰 리플레이 1토큰=1행
--   · fields = 화이트리스트 마스킹 필드만(원문 CI/생년월일 미보관)
--   · 모든 fm_ 테이블 RLS enable + owner-select만(쓰기=service-role 전용, 백엔드 매개)
--   · 카탈로그 노출은 백엔드 라우트(service-role)가 컬럼 화이트리스트로 서빙 → public select 정책 불필요
--   · fm_identity_verifications.model_id nullable(검증이 모델생성 선행 가능) — 온보딩은 원자 처리 권장
--   · 정산 canonical = 컨트랙트 산식(dust→ops). DB는 반환값 저장 + sum=total CHECK로 미러 무결성
--   · payment_id UNIQUE = 컨트랙트 중복 revert와 동일 멱등을 DB에도 강제
-- 앱 레벨: FACEMARKET_ENABLED 플래그(off면 verify/settle 훅 no-op) — 마이그레이션 아님, routes에서.
-- =============================================================

-- ── fm_models ────────────────────────────────────────────────
create table if not exists public.fm_models (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid references auth.users(id) on delete set null, -- 모델 본인 계정(플랫폼 대행 온보딩 시 null 가능)
  display_name  text not null,
  status        text not null default 'pending'
                  check (status in ('pending', 'verified', 'suspended')), -- 검증배지
  ci_hash       text unique,          -- HMAC-SHA256(CI, pepper). dedup 단일 원천(null 다수 허용=미검증)
  did           text,                 -- 홀더 DID(FM-24 월렛 정책으로 채움)
  chain_ref     text,                 -- 온체인 modelRef = '0x'||keccak256(id) hex(기록전용, 결정적)
  cover_image_url text,               -- 카탈로그 썸네일(마케팅 컷, 라이선스 얼굴 원본 아님)
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- ── fm_identity_verifications (CX 감사 로그) ─────────────────
create table if not exists public.fm_identity_verifications (
  id          uuid primary key default gen_random_uuid(),
  model_id    uuid references public.fm_models(id) on delete set null, -- nullable: 검증 선행 가능, 온보딩 원자 권장
  cx_tx_id    text not null unique,   -- CX 토큰/trans id. UNIQUE=리플레이 차단
  fields      jsonb not null default '{}'::jsonb, -- 화이트리스트 마스킹 필드만(이름 마스킹·생년·통신사). 원문 CI/생년월일 금지
  verified_at timestamptz not null default now(),
  created_at  timestamptz not null default now()
);
-- 감사 append-only는 앱/후속 트리거로(현재 RLS+service-role로 무단변경 차단). ci_hash는 여기 저장 안 함(fm_models 단일화).

-- ── fm_licenses (얼굴 라이선스, VC 연동) ─────────────────────
create table if not exists public.fm_licenses (
  id                 uuid primary key default gen_random_uuid(),
  model_id           uuid not null references public.fm_models(id) on delete cascade,
  face_image_uri     text not null,   -- 게이트 URL(GET /v1/facemarket/licenses/{id}/face) — 공개 R2 URL 아님. VC claim도 이 URL
  face_image_key     text,            -- 내부 R2 object key(비공개)
  face_image_digest  text not null,   -- digestSRI 'sha256-...'(무결성). revoke=삭제 아님 → 접근차단은 게이트 라우트가
  allowed_use        text[] not null default '{}',
  forbidden_use      text[] not null default '{}',
  unit_price         integer not null default 10000, -- KRW/건
  license_valid_until timestamptz not null,          -- 사용권 만료(VC validUntil과 이원화)
  status             text not null default 'active'
                       check (status in ('active', 'revoked', 'expired')),
  vc_id              text,            -- 발급 VC id/참조(P210)
  vc_status_uri      text,            -- VC-Meta 상태 endpoint(revoke 판정 라이브 조회)
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index if not exists fm_licenses_model_id_idx on public.fm_licenses(model_id);

-- ── fm_settlements (record-only 정산 미러) ───────────────────
create table if not exists public.fm_settlements (
  id              uuid primary key default gen_random_uuid(),
  payment_id      text not null unique, -- job id 기반 결정적. UNIQUE=컨트랙트 중복 revert와 동일 멱등
  job_id          uuid references public.jobs(id) on delete set null, -- 상세페이지 detail_page 잡
  license_id      uuid references public.fm_licenses(id) on delete set null,
  credit_ledger_id uuid references public.credit_ledger(id) on delete set null, -- 라이선스비=크레딧 차감 참조(결제 레일)
  model_ref       text not null,      -- '0x'||keccak256(model uuid) — 온체인 modelRef 미러
  total_amount    bigint not null check (total_amount > 0),
  model_amount    bigint not null,    -- 아래 3개는 컨트랙트 반환값 저장(canonical=컨트랙트)
  platform_amount bigint not null,
  ops_amount      bigint not null,
  chain_status    text not null default 'pending'
                    check (chain_status in ('pending', 'confirmed', 'failed')),
  tx_hash         text,
  chain_id        text,
  recorded_block  bigint,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  -- 이중장부 무결성: 3분할 합 = 총액(컨트랙트가 ops=잔여로 보장 → dust에도 성립)
  constraint fm_settlements_split_sums
    check (model_amount + platform_amount + ops_amount = total_amount)
);
create index if not exists fm_settlements_license_id_idx on public.fm_settlements(license_id);

-- ── updated_at 트리거(레포 관례 set_updated_at) — 멱등(재실행 안전) ──
drop trigger if exists fm_models_set_updated_at on public.fm_models;
create trigger fm_models_set_updated_at
  before update on public.fm_models
  for each row execute function public.set_updated_at();
drop trigger if exists fm_licenses_set_updated_at on public.fm_licenses;
create trigger fm_licenses_set_updated_at
  before update on public.fm_licenses
  for each row execute function public.set_updated_at();
drop trigger if exists fm_settlements_set_updated_at on public.fm_settlements;
create trigger fm_settlements_set_updated_at
  before update on public.fm_settlements
  for each row execute function public.set_updated_at();

-- ── RLS: 전 테이블 enable + owner-select만. 쓰기=service-role(RLS bypass) ──
alter table public.fm_models                enable row level security;
alter table public.fm_identity_verifications enable row level security;
alter table public.fm_licenses              enable row level security;
alter table public.fm_settlements           enable row level security;

-- fm_models: 본인 소유 모델만 조회(카탈로그는 백엔드 라우트가 화이트리스트로 서빙)
drop policy if exists fm_models_owner_select on public.fm_models;
create policy fm_models_owner_select on public.fm_models
  for select using (user_id = (select auth.uid()));

-- fm_licenses: 소유 모델 경유
drop policy if exists fm_licenses_owner_select on public.fm_licenses;
create policy fm_licenses_owner_select on public.fm_licenses
  for select using (exists (
    select 1 from public.fm_models m
    where m.id = fm_licenses.model_id and m.user_id = (select auth.uid())));

-- fm_identity_verifications: 소유 모델 경유(model_id null 행은 비노출 — service-role만)
drop policy if exists fm_identity_verifications_owner_select on public.fm_identity_verifications;
create policy fm_identity_verifications_owner_select on public.fm_identity_verifications
  for select using (exists (
    select 1 from public.fm_models m
    where m.id = fm_identity_verifications.model_id and m.user_id = (select auth.uid())));

-- fm_settlements: 라이선스→모델 경유
drop policy if exists fm_settlements_owner_select on public.fm_settlements;
create policy fm_settlements_owner_select on public.fm_settlements
  for select using (exists (
    select 1 from public.fm_licenses l
    join public.fm_models m on m.id = l.model_id
    where l.id = fm_settlements.license_id and m.user_id = (select auth.uid())));
