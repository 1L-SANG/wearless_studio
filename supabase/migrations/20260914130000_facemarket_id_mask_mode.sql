-- 신분증 마스킹 기하 검증(facemarket_id_mask_verify)의 판정을 영구 기록한다.
-- 검증은 이미 존재하고 이미 로그로 남지만, 관리자 심사 카드는 그 사실을 모른다 —
-- 심사자가 "이 건은 서버가 마스킹 위치를 확인했는가"를 알 방법이 없다.
--
-- 값은 클라이언트가 선언하지 않는다(클라는 어느 경로를 탔는지 거짓말할 수 있다) —
-- 서버가 upload_id_document 에서 검증 자체의 판정으로부터 채운다:
--   'auto'   — 기하 검증(mask_is_applied)을 통과함
--   'manual' — 검증을 돌렸지만 통과하지 못함(shadow 모드라 업로드 자체는 막지 않음)
--   NULL     — 검증이 아예 안 돌았음(fm_id_mask_verify=off, 또는 이 컬럼이 생기기 전
--              업로드된 기존 행). NULL 을 'auto'로 채우면 검증 안 한 걸 통과로,
--              'manual'로 채우면 통과 못 한 걸로 거짓 기록하는 셈이라 어느 쪽도 안 한다.
--
-- Additive · PG16-safe. 기존 행 백필 없음 — 검증 판정이 없던 시절의 행은 정말로
-- 판정이 없다(추측해서 채우면 그 자체가 거짓 기록이다).
alter table public.fm_biometric_enrollments
  add column if not exists mask_mode text;
