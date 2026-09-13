-- 등록 심사(간편인증 경로) 결과 통지 메일 유형 3종을 메일 원장에 허용한다.
-- 심사 화면은 "결과는 메일로 알려 드려요" 라고 약속하는데 지금까지 아무것도 보내지 않았다.
-- 원장은 지원서 메일과 같은 테이블을 쓴다(같은 수신자·같은 재발송 도구) — 등록은 지원서에
-- 매달려 있고(application_id) 연락처도 지원서에만 있다.
-- Additive · PG16-safe.

alter table public.fm_model_application_emails
  drop constraint if exists fm_model_application_emails_email_type_check;
alter table public.fm_model_application_emails
  add constraint fm_model_application_emails_email_type_check
  check (email_type in (
    'approved', 'rejected', 'auto_rejected',
    'enrollment_review_approved', 'enrollment_review_rejected', 'enrollment_review_timeout'
  ));
