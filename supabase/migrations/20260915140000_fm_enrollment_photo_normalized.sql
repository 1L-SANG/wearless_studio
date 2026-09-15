-- 등록 사진: 받은 **원본**은 그대로 두고, 읽을 **정규화본**(EXIF 적용 무손실 PNG)을 함께 둔다.
--
-- 왜: 등록 사진이 곧 LoRA 학습셋인데, 프런트가 셀러 상품 사진용 규칙(긴 변 4000px·JPEG 0.85)
-- 으로 다시 인코딩해 올리고 있었다 — 48MP 원본이 12MP 손실본으로 학습에 들어갔다.
-- 이제 원본 바이트를 그대로 저장한다. 대신 HEIC 는 cv2 가 아예 못 읽고 JPEG 는 EXIF
-- orientation 을 PIL/cv2 가 다르게 다루므로, 서버가 한 번 정규화한 PNG 를 함께 저장하고
-- 그 뒤의 모든 읽기(QC·관리자 열람·학습 내보내기)는 정규화본을 쓴다.
--
-- 기존 행은 정규화본이 없다(null) — 읽는 쪽은 "없으면 원본" 으로 물러난다.
-- ★ 파기(biometric_purge)는 두 키를 **모두** 지워야 한다.
alter table public.fm_biometric_enrollment_photos
  add column if not exists normalized_r2_key text,
  add column if not exists normalized_byte_size bigint,
  add column if not exists normalized_width int,
  add column if not exists normalized_height int;

comment on column public.fm_biometric_enrollment_photos.normalized_r2_key is
  'EXIF 를 픽셀에 적용한 무손실 PNG. QC·관리자 열람·학습 내보내기가 읽는 정본. 없으면 원본(r2_key)으로 물러난다.';
