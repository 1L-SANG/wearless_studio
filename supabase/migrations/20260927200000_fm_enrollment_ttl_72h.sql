-- 2차 등록 진행 기한을 24시간에서 72시간으로 늘린다(오너 결정 2026-09-27).
-- 코드(ENROLLMENT_TTL)는 새로 시작하는 등록에만 적용되므로, 이미 진행 중인 등록의
-- 만료 시각도 '시작 + 72시간'까지 늘린다. 만료 스윕 대상 상태만 건드리고,
-- 이미 더 늦은 만료 시각(사진 다시 올리기로 갱신된 경우 등)은 줄이지 않는다.
update public.fm_biometric_enrollments
set expires_at = greatest(expires_at, created_at + interval '72 hours')
where status in (
  'id_capture_pending', 'identity_pending', 'photos_pending',
  'liveness_pending', 'processing'
);
