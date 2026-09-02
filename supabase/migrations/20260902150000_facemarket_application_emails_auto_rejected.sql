-- 메일 종류 구분(스펙 10): 승인 / 거절 / 정보 불일치 자동 거절. 신분증 대조 3회 실패로
-- 지원서가 자동 거절될 때 보내는 메일을 관리자 거절과 구분해 원장에 기록한다.
-- Additive · PG16-safe(CHECK drop→add).

alter table public.fm_model_application_emails
  drop constraint if exists fm_model_application_emails_email_type_check;
alter table public.fm_model_application_emails
  add constraint fm_model_application_emails_email_type_check
  check (email_type in ('approved', 'rejected', 'auto_rejected'));
