alter table public.detail_publications
  alter column cleanup_after drop not null;

comment on column public.detail_publications.cleanup_after is
  '다음 삭제 정리 시각. null이면 더 이상 재확인하지 않는다.';
