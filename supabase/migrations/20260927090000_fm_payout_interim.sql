-- 중간 정산(interim) — 마감 전인 이번 달의 체인 확정 정산을 "기준 시각(cutoff)"까지 먼저
-- 지급 확인서로 묶는다 (2026-09-27).
--
-- 확인서·항목·상태머신·CHECK·불변 트리거는 그대로 쓴다. 더하는 것은 "어떤 실행이 만든
-- 확인서인가"를 적는 두 칸뿐이다.
--   · kind      — 'monthly'(월말 정산·단건 지급 확인, 기존 행 전부) | 'interim'(중간 정산)
--   · cutoff_at — 중간 정산의 기준 시각. 이 시각 전에 생긴 확정 정산만 담았다는 표시다.
--
-- 이중 지급 방지는 기존 장치가 한다: 정산 한 건은 풀리지 않은(released_at is null) 항목에
-- 한 번만 들어갈 수 있고(fm_payout_one_active_allocation), 항목은 취소된 확인서에서만 풀린다
-- (fm_payout_allocation_immutable). 그래서 중간 정산으로 지급한 정산은 월말 정산이 다시
-- 담을 수 없고, 월말 정산은 남은 몫만 모은다.
--
-- 기존 불변 트리거(20260911170000)는 보호 컬럼을 명시 나열하고 고치지 않기로 했으므로
-- (20260922120000·20260926230000 참고), kind·cutoff_at 도 **별도 트리거**로 굳힌다.
alter table public.fm_payout_confirmations
  add column if not exists kind text not null default 'monthly',
  add column if not exists cutoff_at timestamptz;

alter table public.fm_payout_confirmations
  add constraint fm_payout_confirmation_kind check (kind in ('monthly', 'interim'));

alter table public.fm_payout_confirmations
  add constraint fm_payout_interim_has_cutoff check ((kind = 'interim') = (cutoff_at is not null));

create or replace function public.fm_payout_kind_immutable() returns trigger
language plpgsql as $$
begin
  if new.kind is distinct from old.kind or new.cutoff_at is distinct from old.cutoff_at then
    raise exception 'payout confirmation kind and cutoff are immutable';
  end if;
  return new;
end;
$$;

drop trigger if exists fm_payout_kind_immutable on public.fm_payout_confirmations;
create trigger fm_payout_kind_immutable before update on public.fm_payout_confirmations
  for each row execute function public.fm_payout_kind_immutable();
