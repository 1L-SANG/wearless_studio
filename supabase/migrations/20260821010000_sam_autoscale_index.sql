-- sam2 온디맨드 reconciler(app/workers/sam_autoscaler.py)가 60초마다 읽는
-- "마지막 업로드 시각" 조회용. assets 는 user_id/project_id 인덱스뿐이라 전역 max() 가
-- 테이블 증가에 따라 비싸진다. 업로드 원본만 partial 로 건다 — 파생 asset 은 SAM 수요가 아니다.
create index if not exists assets_upload_created_idx
  on public.assets (created_at desc)
  where source = 'upload';
