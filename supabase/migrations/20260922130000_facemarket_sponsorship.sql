-- 협찬은 현재 모델의 공개 설정이며 등록 회차나 라이선스 증서의 조건이 아니에요.
alter table public.fm_models
  add column sponsorship_enabled boolean not null default false,
  add column instagram_handle text,
  add column instagram_followers integer check (instagram_followers >= 0),
  add column instagram_followers_reported_at timestamptz,
  add column size_top text check (size_top in ('XS', 'S', 'M', 'L', 'XL', 'FREE')),
  add column size_bottom_waist integer check (size_bottom_waist between 24 and 34),
  -- 프로필 정보 수집 동의(04 동의서 E-2b). 켤 때 필수, 끄면 프로필과 함께 지워요.
  add column sponsorship_profile_consent_at timestamptz,
  add constraint fm_models_sponsorship_complete check (
    not sponsorship_enabled or (
      instagram_handle is not null and btrim(instagram_handle) <> ''
      and instagram_followers is not null
      and instagram_followers_reported_at is not null
      and size_top is not null and size_bottom_waist is not null
      and sponsorship_profile_consent_at is not null
    )
  );

create table public.fm_sponsorship_interest (
  id uuid primary key default gen_random_uuid(),
  seller_user_id uuid not null references auth.users(id) on delete cascade,
  model_id uuid not null references public.fm_models(id) on delete cascade,
  created_at timestamptz not null default now(),
  unique (seller_user_id, model_id)
);
create index fm_sponsorship_interest_model_id_idx
  on public.fm_sponsorship_interest(model_id);

-- 읽기와 쓰기는 인증 및 공개 여부를 검사하는 백엔드로만 처리해요.
alter table public.fm_sponsorship_interest enable row level security;
