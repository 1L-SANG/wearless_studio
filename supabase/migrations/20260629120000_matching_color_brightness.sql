-- matching_items 색상 밝기(정렬용). seedMatchingItems.js colorBrightness 이관.
-- append-only: 신규 forward 파일 (init.sql 수정 금지).
alter table public.matching_items
  add column color_brightness integer not null default 50;
