-- 수동 지급의 "실제 이체" 기록을 확인서에 남긴다 (2026-09-26).
--
-- 지금까지 수동(manual) 확인서는 [송금 시작] → [지급 완료 기록] 두 버튼만 누르면 paid 가 됐다.
-- 은행 앱에서 정말 보냈는지는 원장 어디에도 남지 않았다. 이제 지급 완료를 기록하려면
-- 관리자가 은행 거래 참조번호와 이체일을 적어야 한다.
--
--   · provider_ref   — 20260922120000 이 만든 "결과를 적는 자리". 수동 지급은 은행 거래
--                      참조번호(거래번호·적요)를, 스텁은 stub-… 을 적는다.
--   · transferred_on — 은행 앱에 찍힌 이체일(관리자 입력). paid_at 은 기록한 시각 그대로 둔다.
--
-- 규칙 셋(상태머신 prepared → transfer_started → paid 와 기존 CHECK·불변 트리거는 그대로다):
--   1) transferred_on 은 paid 일 때만 있을 수 있다.
--   2) 수동 확인서는 참조번호·이체일 없이 paid 가 될 수 없다. NOT VALID — 이 규칙 이전에
--      paid 가 된 기존 행은 검사하지 않는다(그 행들은 불변 트리거로 이미 굳어 있다).
--   3) paid 가 된 뒤에는 참조번호·이체일을 바꿀 수 없다. 기존 불변 트리거(20260911170000)는
--      보호 컬럼을 명시 나열하고, 20260922120000 이 "그 목록을 건드리지 말라"고 했으므로
--      기존 함수를 고치지 않고 **별도 트리거**로 막는다.
alter table public.fm_payout_confirmations
  add column if not exists transferred_on date;

alter table public.fm_payout_confirmations
  add constraint fm_payout_transferred_on_only_when_paid
    check (transferred_on is null or status = 'paid');

alter table public.fm_payout_confirmations
  add constraint fm_payout_manual_paid_has_transfer_record
    check (status <> 'paid' or provider <> 'manual'
           or (provider_ref is not null and btrim(provider_ref) <> '' and transferred_on is not null))
    not valid;

create or replace function public.fm_payout_transfer_record_immutable() returns trigger
language plpgsql as $$
begin
  if old.status = 'paid'
     and (new.provider_ref is distinct from old.provider_ref
          or new.transferred_on is distinct from old.transferred_on) then
    raise exception 'payout transfer record is immutable once paid';
  end if;
  return new;
end;
$$;

drop trigger if exists fm_payout_transfer_record_immutable on public.fm_payout_confirmations;
create trigger fm_payout_transfer_record_immutable before update on public.fm_payout_confirmations
  for each row execute function public.fm_payout_transfer_record_immutable();
