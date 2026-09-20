-- 지금 쓰는 각도 교체(ComfyUI) 파드가 무엇인지 **DB 가 정본**이다. 얼굴 렌더 파드와 같은 이유로
-- id 는 바뀔 수 있는 값이다(재고 없음 → 새 파드) — 20260911100000_fm_face_render_pod.sql 참고.
--
-- 왜 얼굴 파드 표에 섞지 않는가: 두 파드는 **서로 다른 프로그램**을 말한다. 얼굴 파드는
-- diffusers 렌더 서비스(POST /render)이고 각도 파드는 ComfyUI(POST /prompt)다. 둘 다 8000
-- 포트를 쓰기 때문에 한 표에 섞이면 주소만 보고는 구분할 수 없고, 각도 컷이 얼굴 파드로 가서
-- 404 만 받는다.
--
-- 백엔드 URL 은 이 id 에서 유도한다: https://<pod_id>-8000.proxy.runpod.net
-- (얼굴 쪽과 달리 경로 접미사가 없다 — 호출자가 /prompt·/upload/image 를 직접 붙인다).

create table if not exists public.fm_angle_render_pod (
  pod_id      text primary key,
  gpu_type    text not null,
  model_id    text,
  created_at  timestamptz not null default now(),
  retired_at  timestamptz
);

comment on column public.fm_angle_render_pod.model_id is
  '이 파드가 물고 있는 실존 모델(fm_models.id). 각도 교체는 등록자별 LoRA 를 쓰지 않아 항상 null 이다 — 표 모양만 얼굴 파드와 맞춰 둔다.';

-- 살아 있는 파드는 하나뿐. coalesce 로 묶는 이유는 얼굴 표와 같다(null 끼리는 서로 다르게
-- 취급돼 미지정 행이 여러 개 살아남는다).
create unique index if not exists fm_angle_render_pod_one_active_per_model
  on public.fm_angle_render_pod ((coalesce(model_id, ''))) where retired_at is null;

alter table public.fm_angle_render_pod enable row level security;
