-- 파기 재시도 원장. R2 list로만 발견된 orphan은 origin 삭제 뒤 다시 찾을 수 없으므로,
-- 삭제 전에 전체 target manifest를 commit하고 CDN purge + DB cleanup 성공 tx에서 지운다.
create table if not exists public.fm_biometric_purge_manifests (
  scope_key text primary key,
  target_manifest jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

drop trigger if exists fm_biometric_purge_manifests_set_updated_at
  on public.fm_biometric_purge_manifests;
create trigger fm_biometric_purge_manifests_set_updated_at
  before update on public.fm_biometric_purge_manifests
  for each row execute function public.set_updated_at();

alter table public.fm_biometric_purge_manifests enable row level security;
revoke all on public.fm_biometric_purge_manifests from anon, authenticated;
