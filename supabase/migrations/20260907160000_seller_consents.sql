-- 셀러 약관·개인정보 처리방침 동의 기록.
-- 로그인마다 체크박스를 요구하지 않는다. 첫 로그인 뒤 한 번 동의를 받아 여기 기록하고,
-- 문서 버전이 바뀌면(개정) 그때만 다시 묻는다. 어떤 버전에 언제 동의했는지가 증빙이다.
-- 소셜 로그인만 있어 별도 회원가입 화면이 없으므로, 동의 시점은 "첫 로그인 직후"다.

create table if not exists public.seller_consents (
  user_id uuid primary key references auth.users (id) on delete cascade,
  terms_version text not null,
  privacy_version text not null,
  age_attested boolean not null default false,
  accepted_at timestamptz not null default now(),
  -- 재동의(개정) 때 직전 기록을 밀어 넣는다: [{termsVersion, privacyVersion, acceptedAt}]
  history jsonb not null default '[]'::jsonb
);

comment on table public.seller_consents is
  '셀러 약관·처리방침 동의(1회 + 개정 시 재동의). 계정 삭제 시 auth.users 캐스케이드로 함께 삭제.';

alter table public.seller_consents enable row level security;
