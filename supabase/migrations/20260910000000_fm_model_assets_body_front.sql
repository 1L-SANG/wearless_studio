-- FaceMarket 실존 모델 자산에 전신(body_front) view 추가.
-- 얼굴 두 장(face_front · grid_sedcard)은 체형 권한이 0 이라(_MODEL_LABEL/_MODEL_SHEET_LABEL:
-- "ZERO authority over body shape or proportions") 실존 등록자의 컷은 체형을 텍스트 한 줄
-- (physique 블록)로만 받았다. 체형 권한이 있는 슬롯은 MODEL FULL BODY 하나뿐이고, 그 자리에
-- 붙일 자산이 없어서 지금까지 가상모델에서만 켜져 있었다.
-- Additive · PG16-safe · 기존 행 무영향(전신 없는 등록자는 그대로 2장 경로).

-- view CHECK 확장. PK 는 (model_id, view) 그대로 — 전신도 view 당 1장이다.
alter table public.fm_model_assets drop constraint if exists fm_model_assets_view_check;
alter table public.fm_model_assets add constraint fm_model_assets_view_check
  check (view in ('face_front', 'grid_sedcard', 'body_front'));

-- ★ bucket 은 반드시 'face'. 확인한 사실(2026-09-10):
--   · 모델 자산의 쓰기·복사·삭제는 전부 app.state.r2_face(비공개 버킷) 하나로만 한다
--     (fm_model_asset_job.py:282·421·448·591). 정리 경로(facemarket_enrollment.py:598)도
--     fm_model_assets 에 없는 키만 그 버킷에서 지운다.
--   · 즉 bucket='public' 행은 파일이 다른 버킷에 있는데 장부는 여기 남는 상태가 되어
--     생체 파생 자산이 정리 대상에서 빠진다.
--   · identity_source.resolve_real_model_assets 도 bucket=='face' 가 아니면 자산 전체를 거부한다.
--   아래 제약으로 그 실수를 애초에 막는다.
--   (지시에 인용된 biometric_purge.py 는 이 저장소에 없다 — 위 경로들로 대신 확인했다.)
alter table public.fm_model_assets drop constraint if exists fm_model_assets_body_front_bucket_check;
alter table public.fm_model_assets add constraint fm_model_assets_body_front_bucket_check
  check (view <> 'body_front' or bucket = 'face');
