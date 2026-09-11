-- "셀러가 FaceMarket 모델을 골랐다" = 곧 얼굴 렌더가 필요하다 → 파드를 미리 켠다.
--
-- SAM 선례와 같은 신호 구조다(DemandSnapshot.last_upload_at). 메모리에 두지 못하는 이유는
-- api 태스크가 여러 개라 어느 태스크가 핑을 받을지 모르기 때문이다 — reconciler 는 다른
-- 태스크에서 돌 수 있다.
--
-- 보관은 짧아도 된다(유휴 판정 창 = FACE_AUTOSCALE_IDLE_MINUTES). 셀러별 60초 1회 제한은
-- 라우트가 이 테이블의 마지막 행으로 판단한다.

create table if not exists public.fm_face_warm_pings (
  id         bigserial primary key,
  seller_id  uuid not null,
  model_id   uuid not null,
  pinged_at  timestamptz not null default now()
);

create index if not exists fm_face_warm_pings_recent_idx
  on public.fm_face_warm_pings (pinged_at desc);
create index if not exists fm_face_warm_pings_seller_idx
  on public.fm_face_warm_pings (seller_id, pinged_at desc);

alter table public.fm_face_warm_pings enable row level security;
