-- FaceMarket C3 관리자 처리 상태를 사용 신고에 추가한다.

alter table public.fm_usage_reports
  drop constraint if exists fm_usage_reports_status_check;

alter table public.fm_usage_reports
  add constraint fm_usage_reports_status_check
  check (status in ('open', 'closed'));
