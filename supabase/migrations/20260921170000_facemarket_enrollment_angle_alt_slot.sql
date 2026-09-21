-- 보조 칸 sh_side_left(왼쪽 90도 옆모습)를 등록 사진 슬롯 제약에 넣는다.
--
-- 왜 sh_side 를 그냥 쓰지 않는가: sh_side 는 자산 소스 3칸이기도 하다
-- (facemarket_photos.ASSET_SOURCE_SLOTS · SLOT_CANDIDATES). 동의서 2026-09-v3 이전에
-- 통과한 등록은 공개 자산이 옛 이름 face05 로 이미 만들어져 fm_models.assets_source_hash
-- 가 그 다이제스트에 묶여 있다. 거기에 뒤늦게 sh_side 행을 넣으면 resolve_photo_rows 가
-- face05 대신 그 행을 집어 해시가 어긋나고, 그 모델의 실사 컷이 전부
-- model_assets_unavailable 로 막힌다 — 2026-09-21 운영 05caa497 에서 실제로 났고
-- 상세페이지 2건이 죽었다(되돌리려고 그 행을 지웠다).
--
-- 그래서 옛 등록의 왼쪽 옆모습은 이 보조 칸에 넣는다. 각도 교체(agents/face_angle_swap)만
-- 읽고 자산 소스 계산에는 안 들어간다. 필수 칸(PHOTO_SLOTS)이 아니라 완료 판정도 그대로다.
-- v3 이후 등록은 이 칸을 안 쓴다 — 촬영 7번이 곧 그 사진이고 sh_side 로 들어온다.
--
-- 옛 이름은 20260914090000 과 같은 이유로 남긴다. 추가만 하는 drop/add 패턴도 같다.

alter table public.fm_biometric_enrollment_photos
  drop constraint if exists fm_biometric_enrollment_photos_angle_check;

alter table public.fm_biometric_enrollment_photos
  add constraint fm_biometric_enrollment_photos_angle_check
  check (angle in (
    'sh_front','sh_smile','sh_34','sh_front2','sh_gaze_left','sh_gaze_right',
    'sh_side','sh_side_left','sh_side_right','sh_back',
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
    'sh_side','sh_side_left','sh_side_right','sh_back',
    'sl_front','sl_smile','sl_34',
    'sr_front','sr_smile','sr_34',
    'bl_front','bl_smile','bl_34',
    'face01','face02','face03','face04','face05','face06','face07','face08',
    'torso01','torso02','torso03','torso04','torso05',
    'full01','full02','full03','full04','full05',
    'front','angle45','side'
  ));
