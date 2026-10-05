-- FaceMarket 배포본 공증에 쇼핑몰용 JPEG 한 장을 허용한다.
-- 기존 20260904000000 마이그레이션은 적용 이력이므로 수정하지 않고 제약만 전진 교체한다.

alter table public.fm_publication_records
  drop constraint if exists fm_publication_records_kind_check;

alter table public.fm_publication_records
  add constraint fm_publication_records_kind_check
  check (kind in ('long_png', 'block_png', 'zip', 'jpeg'));
