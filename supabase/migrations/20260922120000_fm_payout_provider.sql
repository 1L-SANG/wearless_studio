-- 지급 확인서에 "무엇이 이 돈을 보냈는가"를 남긴다.
--
-- 오늘은 사람(manual)뿐이다. 데모용 스텁(stub)은 돈을 옮기지 않으므로, 그 사실이 원장에
-- 남아야 화면·알림이 "입금됐다"고 잘못 말하지 않는다. 기본값이 manual 이라 기존 행과
-- 기존 코드 경로는 손대지 않은 것과 같다.
--
-- ※ fm_payout_confirmations 의 불변 트리거(20260911170000)는 보호할 컬럼을 명시 나열한다.
--    여기서 더하는 세 컬럼은 그 목록 밖이라 트리거를 고칠 필요가 없고, 고쳐서도 안 된다.
--    provider 는 확인서를 만들 때 한 번 박히고(어느 수단으로 처리할 건지는 그때 정해진다),
--    provider_ref·failure_reason 은 결과를 적는 자리다.
alter table public.fm_payout_confirmations
  add column if not exists provider text not null default 'manual'
    check (provider in ('manual','stub')),
  add column if not exists provider_ref text,
  add column if not exists failure_reason text;
