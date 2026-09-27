-- 구 API가 배포 중 24시간 만료 시각을 명시해도 72시간을 보장한다.
-- 이미 커밋된 20:00 백필보다 먼저 적용되어 두 마이그레이션 사이의 쓰기도 잡는다.
create or replace function public.fm_enrollment_ttl_72h_on_write()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'INSERT' then
    if new.status in (
      'id_capture_pending', 'identity_pending', 'photos_pending',
      'liveness_pending', 'processing'
    ) then
      new.expires_at := greatest(new.expires_at, new.created_at + interval '72 hours');
    end if;
  elsif old.status = 'license_pending'
    and new.status in ('photos_pending', 'liveness_pending')
    and new.photo_revision > old.photo_revision then
    -- 사진 재업로드는 최초 등록일이 아니라 다시 연 시점부터 72시간이다.
    new.expires_at := greatest(new.expires_at, statement_timestamp() + interval '72 hours');
  end if;
  return new;
end;
$$;

create trigger fm_enrollment_ttl_72h_on_write
before insert or update of status, photo_revision, expires_at
on public.fm_biometric_enrollments
for each row execute function public.fm_enrollment_ttl_72h_on_write();
