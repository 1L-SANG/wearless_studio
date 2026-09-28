alter table public.fm_models
  add column secondary_use_enabled boolean not null default false;

-- 기존 두 협찬 동의 유형을 유지하면서 2차 활용 동의를 같은 이벤트 원장에 기록해요.
alter table public.fm_sponsorship_consent_events
  drop constraint if exists fm_sponsorship_consent_events_consent_type_check;
alter table public.fm_sponsorship_consent_events
  add constraint fm_sponsorship_consent_events_consent_type_check
  check (consent_type in ('sponsorship_participation', 'sponsorship_profile_collection', 'secondary_use'));
