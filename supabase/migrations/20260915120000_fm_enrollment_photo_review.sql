-- 등록 사진을 **학습 전에 관리자가 확인**한다(2026-09-15 사용자 결정).
--
-- 왜: 지금은 셀러가 올린 사진이 곧바로 학습셋이 된다. 반려할 사진(흐림·안경·각도 미달)이
-- 가중치에 들어가면 되돌릴 수 없다 — LoRA 를 다시 학습해야 한다. 그래서 확인 상태를 행에 둔다.
--
-- reshoot_slots 는 [{slot, reason}] — 어느 칸을 왜 다시 찍어야 하는지. 모델 화면이 그대로 읽는다.
alter table public.fm_biometric_enrollments
  add column if not exists photo_review_status text
    check (photo_review_status in ('pending', 'approved', 'reshoot_requested')),
  add column if not exists photo_reviewed_at timestamptz,
  add column if not exists photo_reviewed_by uuid,
  add column if not exists reshoot_slots jsonb;

comment on column public.fm_biometric_enrollments.photo_review_status is
  '관리자 사진 확인: pending | approved | reshoot_requested. 학습 내보내기는 approved 만.';

-- ★ 이미 통과한 등록은 approved 로 채운다. 안 채우면 운영 중인 모델(05caa497 등)의 재학습·
--   내보내기가 막히고, 관리자 열람 범위 판정도 "확인 대기"로 잘못 열린다.
update public.fm_biometric_enrollments
   set photo_review_status = 'approved',
       photo_reviewed_at = coalesce(photo_reviewed_at, completed_at, now())
 where photo_review_status is null
   and decision = 'passed';

-- 나머지(진행 중·실패)는 pending 으로 시작한다.
update public.fm_biometric_enrollments
   set photo_review_status = 'pending'
 where photo_review_status is null;

alter table public.fm_biometric_enrollments
  alter column photo_review_status set default 'pending';
