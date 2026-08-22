-- CDN purge가 진행되는 동안 같은 scope worker가 target을 union할 수 있다.
-- cleanup은 자신이 purge한 정확한 revision만 종결하도록 CAS 토큰을 둔다.
alter table public.fm_biometric_purge_manifests
  add column if not exists revision bigint not null default 1;
