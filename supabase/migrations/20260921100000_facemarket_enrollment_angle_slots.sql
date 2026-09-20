-- 각도 2칸(sh_side_right·sh_back)을 등록 사진 슬롯 제약에 넣는다.
--
-- **코드와 DB 가 갈려 있었다.** 2026-09-15 에 facemarket_photos.PHOTO_SLOTS 가 18칸으로
-- 늘었고(그늘 9칸에 옆모습 오른쪽·뒷모습 추가) 촬영 가이드·동의서 2026-09-v3 도 그 3칸을
-- 필수로 요구하는데, 제약은 20260914090000 의 16칸 그대로였다. 그래서 새 등록자가 그 두 칸을
-- 올리면 DB 가 거부한다 — 2026-09-21 에 각도 사진을 넣다가 CheckViolation 으로 드러났다.
--
-- 이 두 칸이 없으면 옆·뒷모습 컷(agents/face_angle_swap)이 no_angle_photo 로 전부 빈다.
--
-- 옛 이름은 20260914090000 과 같은 이유로 **남긴다** — 운영에 legacy 3장·18칸으로 통과한
-- 등록이 있고 그 행들이 계속 읽히고 파기까지 가야 한다. 추가만 하는 drop/add 패턴도 같다.

alter table public.fm_biometric_enrollment_photos
  drop constraint if exists fm_biometric_enrollment_photos_angle_check;

alter table public.fm_biometric_enrollment_photos
  add constraint fm_biometric_enrollment_photos_angle_check
  check (angle in (
    'sh_front','sh_smile','sh_34','sh_front2','sh_gaze_left','sh_gaze_right',
    'sh_side','sh_side_right','sh_back',
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
    'sh_front','sh_smile','sh_34','sh_front2','sh_gaze_left','sh_gaze_right',
    'sh_side','sh_side_right','sh_back',
    'sl_front','sl_smile','sl_34',
    'sr_front','sr_smile','sr_34',
    'bl_front','bl_smile','bl_34',
    'face01','face02','face03','face04','face05','face06','face07','face08',
    'torso01','torso02','torso03','torso04','torso05',
    'full01','full02','full03','full04','full05',
    'front','angle45','side'
  ));
