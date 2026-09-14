-- "셀러가 실존 모델을 골랐다" = 곧 holder(opendid)가 필요하다 → scale-to-zero 에서 미리 켠다.
--
-- 왜 fm_face_warm_pings 를 같이 쓰지 않는가(2026-09-14 운영 장애):
--   얼굴 렌더 파드 수요는 **켜진 LoRA 가 있는 모델**로 좁혀져 있다. LoRA 없는 모델로 GPU 파드를
--   켜면 돈만 쓰고 아무 일도 안 하기 때문이다. 그런데 holder 는 LoRA 와 무관하게 **모든 실존
--   모델 사용**에 필요하다(verify_license → /holder/vc/verify, FACEMARKET_VC_REQUIRED=true).
--   같은 테이블을 쓰면 둘 중 하나가 반드시 틀린다 — 그래서 신호를 나눈다.
--
-- 이게 없던 동안 실제로 난 일: 마지막 등록으로부터 30분이 지나면 opendid 가 desired=0 으로
-- 내려가고, 그 뒤 모든 셀러의 실존 모델 사용이 503 holder_unavailable 로 막혔다. 손으로
-- desired=1 을 써도 reconciler 가 60초 안에 "수요 없음"으로 되돌렸다.
--
-- 보관은 짧아도 된다(유휴 판정 창 = OPENDID_AUTOSCALE_IDLE_MINUTES). 셀러별 중복 제한은
-- 라우트가 이 테이블의 마지막 행으로 판단한다.

create table if not exists public.fm_holder_warm_pings (
  id         bigserial primary key,
  seller_id  uuid not null,
  model_id   uuid not null,
  pinged_at  timestamptz not null default now()
);

create index if not exists fm_holder_warm_pings_recent_idx
  on public.fm_holder_warm_pings (pinged_at desc);
create index if not exists fm_holder_warm_pings_seller_idx
  on public.fm_holder_warm_pings (seller_id, pinged_at desc);

-- 서비스 롤(api)만 쓴다. 정책을 따로 두지 않아 anon/authenticated 는 접근할 수 없다.
alter table public.fm_holder_warm_pings enable row level security;
