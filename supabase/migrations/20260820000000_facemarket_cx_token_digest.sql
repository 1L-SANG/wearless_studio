-- FaceMarket CX token은 CI·신원정보 재조회가 가능한 단기 capability다.
-- 컬럼명 cx_tx_id는 rolling deploy 중 구버전 task와 호환하려고 유지한다.
-- 텍스트 모양으로 raw/digest를 구분하지 않는다: cx_tx_id_format이 유일한 판별자다.
alter table public.fm_identity_verifications
  add column if not exists cx_tx_id_format text not null default 'raw';

create or replace function public.fm_digest_cx_tx_id()
returns trigger
language plpgsql
as $$
begin
  if new.cx_tx_id_format = 'raw' then
    new.cx_tx_id := 'cxsha256:' || encode(sha256(convert_to(new.cx_tx_id, 'UTF8')), 'hex');
    new.cx_tx_id_format := 'sha256-v1';
  elsif new.cx_tx_id_format = 'sha256-v1' then
    if new.cx_tx_id !~ '^cxsha256:[0-9a-f]{64}$' then
      raise exception 'invalid cx_tx_id digest';
    end if;
  else
    raise exception 'invalid cx_tx_id_format';
  end if;
  return new;
end;
$$;

drop trigger if exists fm_identity_verifications_digest_cx_tx_id
  on public.fm_identity_verifications;
create trigger fm_identity_verifications_digest_cx_tx_id
  before insert or update of cx_tx_id on public.fm_identity_verifications
  for each row execute function public.fm_digest_cx_tx_id();

update public.fm_identity_verifications
   set cx_tx_id = cx_tx_id
 where cx_tx_id_format = 'raw';

alter table public.fm_identity_verifications
  alter column cx_tx_id_format set default 'raw';

alter table public.fm_identity_verifications
  drop constraint if exists fm_identity_verifications_cx_tx_id_digest;
alter table public.fm_identity_verifications
  add constraint fm_identity_verifications_cx_tx_id_digest
  check (
    cx_tx_id_format = 'sha256-v1'
    and cx_tx_id ~ '^cxsha256:[0-9a-f]{64}$'
  );

comment on column public.fm_identity_verifications.cx_tx_id is
  'SHA-256 digest of the short-lived CX token; legacy name retained for rolling-deploy compatibility';
comment on column public.fm_identity_verifications.cx_tx_id_format is
  'Explicit cx_tx_id storage format. raw input is trigger-normalized to sha256-v1.';
