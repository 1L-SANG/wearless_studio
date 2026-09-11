-- 지금 쓰는 얼굴 렌더 파드가 무엇인지 **DB 가 정본**이다.
--
-- 왜 env 가 아닌가(2026-09-10 실측): 멈춘 파드를 다시 켤 때 호스트에 GPU 가 없으면
-- "not enough free GPUs on the host machine" 로 실패하고, 새로 만들려 해도 그 데이터센터에
-- 재고가 없으면 "no instances currently available" 이 난다. 그래서 파드 id 는 바뀔 수 있는
-- 값이고, 바뀔 때마다 매니페스트를 고쳐 재배포할 수는 없다. FACE_RUNPOD_POD_ID 는 초기값·폴백.
--
-- 백엔드 URL 은 이 id 에서 유도한다: https://<pod_id>-8000.proxy.runpod.net/render
-- (프록시 주소는 파드 id 기반이라 재시작해도 유지된다 — TCP 포트 매핑만 바뀐다).

create table if not exists public.fm_face_render_pod (
  pod_id      text primary key,
  gpu_type    text not null,
  created_at  timestamptz not null default now(),
  retired_at  timestamptz
);

-- 살아 있는 파드는 하나뿐. 새 파드를 만들면 이전 파드는 retired_at 을 채운다.
create unique index if not exists fm_face_render_pod_one_active
  on public.fm_face_render_pod ((retired_at is null)) where retired_at is null;

alter table public.fm_face_render_pod enable row level security;
