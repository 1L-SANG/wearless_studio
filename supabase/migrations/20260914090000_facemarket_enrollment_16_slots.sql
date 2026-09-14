-- 등록 사진 슬롯을 LoRA 학습 촬영 스펙 그대로 16칸으로 바꾼다(2026-09-14 사용자 결정).
--
--   학습 12 = 조명 4(그늘·해가왼쪽·해가오른쪽·역광) × 컷 3(정면 무표정·정면 미소·3/4 무표정)
--   기준  3 = 그늘에서 정면 무표정 2·시선만 왼쪽·시선만 오른쪽
--             (턱 살짝 내리기는 뺐다 — 기준끼리 SFace 0.664 로 최저선 0.70 을 깬다)
--   자산  1 = 그늘 측면(공개 자산 잡이 front·angle45·side 세 장을 요구한다)
--
-- 옛 이름(face01~full05, front/angle45/side)은 **남긴다**. 운영에 legacy 3장으로 통과한 등록과
-- 18칸으로 진행 중인 등록이 있어서, 그 행들이 계속 읽히고 파기까지 가야 한다. 추가만 하는
-- drop/add 패턴은 20260911124500 과 같다.

alter table public.fm_biometric_enrollment_photos
  drop constraint if exists fm_biometric_enrollment_photos_angle_check;

alter table public.fm_biometric_enrollment_photos
  add constraint fm_biometric_enrollment_photos_angle_check
  check (angle in (
    'sh_front','sh_smile','sh_34','sh_front2','sh_gaze_left','sh_gaze_right','sh_side',
    'sl_front','sl_smile','sl_34',
    'sr_front','sr_smile','sr_34',
    'bl_front','bl_smile','bl_34',
    'face01','face02','face03','face04','face05','face06','face07','face08',
    'torso01','torso02','torso03','torso04','torso05',
    'full01','full02','full03','full04','full05',
    'front','angle45','side'
  ));

alter table public.fm_biometric_enrollment_photo_cleanup
  drop constraint if exists fm_biometric_enrollment_photo_cleanup_angle_check;

alter table public.fm_biometric_enrollment_photo_cleanup
  add constraint fm_biometric_enrollment_photo_cleanup_angle_check
  check (angle in (
    'sh_front','sh_smile','sh_34','sh_front2','sh_gaze_left','sh_gaze_right','sh_side',
    'sl_front','sl_smile','sl_34',
    'sr_front','sr_smile','sr_34',
    'bl_front','bl_smile','bl_34',
    'face01','face02','face03','face04','face05','face06','face07','face08',
    'torso01','torso02','torso03','torso04','torso05',
    'full01','full02','full03','full04','full05',
    'front','angle45','side'
  ));
