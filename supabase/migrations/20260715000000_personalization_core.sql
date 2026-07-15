-- =============================================================
-- 20260715000000_personalization_core.sql
-- 개인화(사용자 본인 얼굴·신체) 기능 코어. docs/personalization/api-spec.md §5 계약 구현.
--   personalization_profiles     사용자당 1행(활성) — 상태머신 draft|ready|purging|purged + 신체 프로필
--   personalization_face_photos  각도 슬롯(front|side|angle45) 최대 3행 — 비공개 R2 키·digest, QC 통과분만
--   personalization_consents     append-only 동의 이력(service_use|training_use|cross_border_transfer)
--   personalization_audit_log    PII 금지 감사(event enum·카운트·메타만)
--   personalization_generations  엔진-의존 생성 기록(경로 α/β/γ) — 스케치
--
-- 보안·PII 결정(api-spec §1.4):
--   · 얼굴 원본·임베딩·digest = 생체정보. r2_key/image_digest 는 어떤 API 응답에도 비노출(백엔드 화이트리스트).
--   · 파기 캐스케이드는 face_photos 행 hard delete(§3.5-2) — digest 잔존=멤버십 테스트 벡터라 금지.
--   · 전 테이블 RLS enable + owner-select만(쓰기=service-role 전용, 백엔드 매개). FaceMarket 선례.
--   · 앱 레벨 PERSONALIZATION_ENABLED off면 라우터 미등록(main.py) — 마이그레이션 아님.
--
-- jobs 재사용 전제(api-spec §5 note): 파기·생성 잡은 기존 jobs 테이블 사용.
--   ① jobs_kind_check 에 personalization_purge/personalization_generation 추가
--   ② jobs.project_id nullable — 프로젝트 없는 개인화 잡 INSERT 허용
--   ③ 개인화 kind 는 project 기반 active-unique 에서 제외 + purge 는 (user_id) 싱글턴 dedup
-- =============================================================

-- ── personalization_profiles ─────────────────────────────────
create table if not exists public.personalization_profiles (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  status         text not null default 'draft'
                   check (status in ('draft', 'ready', 'purging', 'purged')),
  -- 신체 프로필(민감정보 준함 — 로그 금지, 응답 본인만). height/weight 필수는 앱 레벨 검증(§3.3).
  height_cm      numeric(5,2),
  weight_kg      numeric(5,2),
  body_type      text check (body_type is null or body_type in ('slim','normal','muscular','chubby','custom')),
  body_type_custom text,
  gender         text check (gender is null or gender in ('female','male','other')),
  age_range      text check (age_range is null or age_range in ('20s','30s','40s','50s_plus')),
  skin_tone      text,
  hair           text,
  clothing_size  text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  withdrawn_at   timestamptz,
  purged_at      timestamptz
);
-- 사용자당 활성 프로필 1개(파기 이력은 purged 로 보존 — 재시작 시 신규 행). purged 는 유니크에서 제외.
create unique index if not exists personalization_profiles_active_user_idx
  on public.personalization_profiles(user_id) where status <> 'purged';

-- ── personalization_face_photos ──────────────────────────────
create table if not exists public.personalization_face_photos (
  id            uuid primary key default gen_random_uuid(),
  profile_id    uuid not null references public.personalization_profiles(id) on delete cascade,
  angle         text not null check (angle in ('front', 'side', 'angle45')),
  r2_key        text not null,        -- 비공개 버킷 키 — 어떤 API 응답에도 비노출
  image_digest  text not null,        -- 'sha256-...' SRI. 생체 파생 고정 식별자 → 파기 시 행 hard delete
  mime_type     text not null,
  byte_size     integer not null,
  qc_status     text not null default 'passed' check (qc_status in ('passed')), -- 통과분만 저장(불합격=저장0)
  qc_reasons    text[] not null default '{}',  -- 방어적 컬럼(정상 통과분은 빈 배열)
  uploaded_at   timestamptz not null default now()
  -- soft delete 없음: 슬롯 삭제·파기 모두 행 hard delete(digest 잔존 금지)
);
create unique index if not exists personalization_face_photos_slot_idx
  on public.personalization_face_photos(profile_id, angle);

-- ── personalization_consents (append-only 이력) ───────────────
create table if not exists public.personalization_consents (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  profile_id    uuid references public.personalization_profiles(id) on delete cascade,
  consent_type  text not null
                  check (consent_type in ('service_use', 'training_use', 'cross_border_transfer')),
  action        text not null check (action in ('granted', 'withdrawn')),
  doc_version   text,
  created_at    timestamptz not null default now()
);
create index if not exists personalization_consents_user_type_idx
  on public.personalization_consents(user_id, consent_type, created_at desc);

-- ── personalization_identity_verifications (연령 게이트 — T2-1) ──
-- CX 표준인증창 본인확인 결과에서 **파생 불리언만** 남긴다(최소수집).
--   · 생년월일·CI·이름 **미저장** — 서버가 CX trans 에서 birth 를 받아 만 나이를 계산한 뒤
--     is_adult 만 기록하고 원문은 메모리에서 폐기(개인정보 최소수집 원칙).
--   · cx_tx_hash = sha256(CX token). **원본 토큰 미저장** — 토큰은 CX 에서 CI·생년월일을 재조회할
--     수 있는 라이브 capability 라, 보관하면 위 "CI·생년월일 미저장" 불변식이 무효화된다.
--     해시만으로 UNIQUE 리플레이 차단 의미론은 동일하게 성립한다.
--   · 파기 캐스케이드 대상 — 전체 철회 시 함께 삭제(재온보딩은 재인증 필요).
create table if not exists public.personalization_identity_verifications (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  cx_tx_hash  text not null unique,
  is_adult    boolean not null,
  verified_at timestamptz not null default now(),
  created_at  timestamptz not null default now()
);
-- 개명 멱등 처리 — 구버전(cx_tx_id 원본 토큰 보관)이 적용된 DB 를 해시 컬럼으로 승격.
-- 기존 행의 값은 원본 토큰이라 해시로 환산 불가 → 재인증을 요구하는 편이 안전(행 삭제).
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'personalization_identity_verifications'
      and column_name = 'cx_tx_id'
  ) then
    delete from public.personalization_identity_verifications;  -- 원본 토큰 보관분 파기
    alter table public.personalization_identity_verifications rename column cx_tx_id to cx_tx_hash;
  end if;
end $$;
create index if not exists personalization_identity_verifications_user_idx
  on public.personalization_identity_verifications(user_id, verified_at desc);

-- ── personalization_audit_log (PII 금지) ─────────────────────
create table if not exists public.personalization_audit_log (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  profile_id  uuid references public.personalization_profiles(id) on delete set null,
  event_type  text not null check (event_type in (
                'consent_granted','consent_withdrawn','photo_uploaded','photo_deleted',
                'qc_rejected','purge_started','purge_completed','generation_started','generation_done')),
  detail      jsonb not null default '{}'::jsonb,  -- 사유코드·카운트·backup_purge_due_at 등 비-PII 메타만
  created_at  timestamptz not null default now()
);
create index if not exists personalization_audit_log_profile_idx
  on public.personalization_audit_log(profile_id, created_at desc);

-- ── personalization_generations (엔진-의존, 스케치) ───────────
create table if not exists public.personalization_generations (
  id               uuid primary key default gen_random_uuid(),
  profile_id       uuid not null references public.personalization_profiles(id) on delete cascade,
  job_id           uuid references public.jobs(id) on delete set null,
  status           text not null default 'pending'
                     check (status in ('pending', 'running', 'done', 'error')),
  -- 산출물 R2 키(비공개 얼굴 버킷). 공유 assets 테이블에 넣지 않는다 — 무인증 /v1/assets/{id}/file
  -- capability 라우트가 얼굴 담긴 산출물을 서빙하게 되는 누출을 원천 차단(§4 하드룰). 전용 게이트만 서빙.
  result_keys      text[] not null default '{}',
  result_asset_ids uuid[] not null default '{}',  -- @deprecated 미사용(누출 방지로 assets 테이블 미적재)
  engine           text,                          -- 확정 경로값(α/β/γ)
  options          jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists personalization_generations_profile_idx
  on public.personalization_generations(profile_id, created_at desc);
-- result_keys 는 위 create table 이후에 추가된 컬럼 → `create table if not exists` 가 스킵되는
-- (이미 테이블이 있는) DB 에서도 반드시 획득되도록 별도 ALTER 로 멱등 보장(선례: 20260713000000).
alter table public.personalization_generations
  add column if not exists result_keys text[] not null default '{}';

-- ── updated_at 트리거(레포 관례 set_updated_at) — 멱등 ──
drop trigger if exists personalization_profiles_set_updated_at on public.personalization_profiles;
create trigger personalization_profiles_set_updated_at
  before update on public.personalization_profiles
  for each row execute function public.set_updated_at();
drop trigger if exists personalization_generations_set_updated_at on public.personalization_generations;
create trigger personalization_generations_set_updated_at
  before update on public.personalization_generations
  for each row execute function public.set_updated_at();

-- ── jobs 재사용 전제 ─────────────────────────────────────────
-- ① kind CHECK 확장(현행: analyze/mannequin/mannequin_adjust/detail_page/editor_image)
alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge'));

-- ② project_id nullable — 프로젝트 없는 개인화 잡 허용(생성 Body projectId 도 null 허용)
alter table public.jobs alter column project_id drop not null;

-- ③ dedup: 개인화 kind 는 project 기반 active-unique 에서 제외(project_id null → 원래 안 걸리나 명시).
--    현행 idx 재정의(init.sql:200 predicate 보존 + 개인화 kind 제외).
drop index if exists public.jobs_active_unique_idx;
create unique index jobs_active_unique_idx on public.jobs (project_id, kind)
  where status in ('pending', 'running')
    and kind not in ('editor_image', 'personalization_generation', 'personalization_purge');
-- 파기 잡은 사용자당 싱글턴(중복 파기 방지). 생성 잡은 idempotency_key(전역 unique)로 dedup.
create unique index if not exists jobs_personalization_purge_singleton_idx
  on public.jobs (user_id)
  where status in ('pending', 'running') and kind = 'personalization_purge';

-- ── RLS: 전 테이블 enable + owner-select만. 쓰기=service-role(RLS bypass) ──
alter table public.personalization_profiles    enable row level security;
alter table public.personalization_face_photos enable row level security;
alter table public.personalization_consents    enable row level security;
alter table public.personalization_audit_log   enable row level security;
alter table public.personalization_generations enable row level security;
alter table public.personalization_identity_verifications enable row level security;

-- profiles: 본인 소유만 조회
drop policy if exists personalization_profiles_owner_select on public.personalization_profiles;
create policy personalization_profiles_owner_select on public.personalization_profiles
  for select using (user_id = (select auth.uid()));

-- face_photos: 소유 프로필 경유(단 r2_key/digest 는 백엔드가 화이트리스트로만 서빙 — 직접 노출 안 함)
drop policy if exists personalization_face_photos_owner_select on public.personalization_face_photos;
create policy personalization_face_photos_owner_select on public.personalization_face_photos
  for select using (exists (
    select 1 from public.personalization_profiles p
    where p.id = personalization_face_photos.profile_id and p.user_id = (select auth.uid())));

-- consents: 본인 소유만
drop policy if exists personalization_consents_owner_select on public.personalization_consents;
create policy personalization_consents_owner_select on public.personalization_consents
  for select using (user_id = (select auth.uid()));

-- audit_log: 본인 소유만
drop policy if exists personalization_audit_log_owner_select on public.personalization_audit_log;
create policy personalization_audit_log_owner_select on public.personalization_audit_log
  for select using (user_id = (select auth.uid()));

-- generations: 소유 프로필 경유
drop policy if exists personalization_generations_owner_select on public.personalization_generations;
create policy personalization_generations_owner_select on public.personalization_generations
  for select using (exists (
    select 1 from public.personalization_profiles p
    where p.id = personalization_generations.profile_id and p.user_id = (select auth.uid())));

-- ── MINOR-F: PostgREST 직접 접근 차단(생체 파생 식별자 노출 방지) ──
-- 개인화 데이터는 전량 백엔드 API(service_role, RLS bypass)로만 매개된다. 프론트는 PostgREST
-- (supabase-js)로 이 테이블을 직접 읽지 않는다. anon/authenticated 의 SELECT 권한을 회수해
-- owner-select RLS 로도 r2_key·image_digest(생체 파생 고정 식별자)가 직접 노출되지 않게 한다.
-- (service_role 은 grant/RLS 를 우회하므로 백엔드 무영향.)
revoke select on public.personalization_profiles    from anon, authenticated;
revoke select on public.personalization_face_photos from anon, authenticated;
revoke select on public.personalization_consents    from anon, authenticated;
revoke select on public.personalization_audit_log   from anon, authenticated;
revoke select on public.personalization_generations from anon, authenticated;
revoke select on public.personalization_identity_verifications from anon, authenticated;
