-- FaceMarket CX token은 CI·신원정보 재조회가 가능한 단기 capability다.
-- 컬럼명은 rolling deploy 중 구버전 task와 호환하려고 유지한다. DB trigger가 구버전의 raw insert도
-- 같은 digest로 정규화하므로 UNIQUE replay 방지는 새·구버전이 동시에 떠 있어도 유지된다.
create or replace function public.fm_digest_cx_tx_id()
returns trigger
language plpgsql
as $$
begin
  if new.cx_tx_id !~ '^sha256:[0-9a-f]{64}$' then
    new.cx_tx_id := 'sha256:' || encode(sha256(convert_to(new.cx_tx_id, 'UTF8')), 'hex');
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
 where cx_tx_id !~ '^sha256:[0-9a-f]{64}$';

alter table public.fm_identity_verifications
  drop constraint if exists fm_identity_verifications_cx_tx_id_digest;
alter table public.fm_identity_verifications
  add constraint fm_identity_verifications_cx_tx_id_digest
  check (cx_tx_id ~ '^sha256:[0-9a-f]{64}$');

comment on column public.fm_identity_verifications.cx_tx_id is
  'SHA-256 digest of the short-lived CX token; legacy name retained for rolling-deploy compatibility';
