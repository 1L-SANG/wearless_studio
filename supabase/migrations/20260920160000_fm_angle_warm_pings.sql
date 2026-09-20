-- 셀러가 실존 모델을 고른 순간 = 옆·뒷모습 컷도 곧 필요하다는 신호.
--
-- 얼굴 파드는 fm_face_warm_pings 로 그 순간 미리 뜬다. 각도 파드가 그 신호를 안 쓰면 첫 옆·뒤
-- 컷에서 콜드스타트(2026-09-20 실측 2~8.5분)를 그대로 물고, 그동안 셀러는 얼굴 컷만 나오고
-- 옆·뒤만 늦게 채워지는 화면을 본다. 두 파드를 **같이** 깨워 그 시차를 없앤다.
--
-- 왜 얼굴 표를 같이 쓰지 않는가: 조건이 다르다. 얼굴은 **켜진 LoRA**가 있어야 걸리고, 각도는
-- **등록자 각도 사진**이 있어야 걸린다(동의서 2026-09-v3 이전 등록은 사진이 없다). 한 표로
-- 묶으면 사진 없는 등록자의 선택이 매번 각도 파드를 켜고, 그 파드는 할 일이 없다.
-- 조건 판정은 핑을 쓰는 라우트에서 한 번만 하고, 수요 판정(60초 주기)은 이 표만 읽는다.

create table if not exists public.fm_angle_warm_pings (
  id         bigserial primary key,
  seller_id  text not null,
  model_id   text not null,
  pinged_at  timestamptz not null default now()
);

-- 수요 판정은 max(pinged_at) 하나만 읽는다.
create index if not exists fm_angle_warm_pings_pinged_at
  on public.fm_angle_warm_pings (pinged_at desc);
-- 중복 억제(같은 셀러의 60초 내 재선택)를 위한 조회.
create index if not exists fm_angle_warm_pings_seller_pinged
  on public.fm_angle_warm_pings (seller_id, pinged_at desc);

alter table public.fm_angle_warm_pings enable row level security;
