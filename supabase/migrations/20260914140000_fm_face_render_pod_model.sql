-- 파드 하나 = LoRA 하나(2026-09-14 사용자 결정).
--
-- 왜 교체를 버렸나: Qwen-Image-Edit-2509 파이프라인이 ≈58GB 라 80GB 카드에 한 벌만 들어간다.
-- 교체하려면 이전 파이프라인의 참조가 전부 끊긴 뒤에 새 것을 올려야 하는데 그 사이는 두 벌이
-- 겹치는 순간이라 항상 OOM 이다 — 두 번째 모델이 영영 안 올라간다. 그래서 모델이 늘면 파드를
-- 늘리고, **어느 파드가 어느 모델의 것인지**를 여기 적는다.
alter table public.fm_face_render_pod
  add column if not exists model_id text;

comment on column public.fm_face_render_pod.model_id is
  '이 파드가 물고 있는 실존 모델(fm_models.id). null = 모델 미지정(구 단일 파드).';

-- 살아 있는 파드는 **모델당 하나**. 예전 인덱스는 전체에서 하나였다.
-- coalesce 로 묶는 이유: unique 인덱스에서 null 은 서로 다르게 취급돼 모델 미지정 행이
-- 여러 개 살아남는다 — 구 파드도 하나만 유지돼야 폴백이 흔들리지 않는다.
drop index if exists public.fm_face_render_pod_one_active;
create unique index if not exists fm_face_render_pod_one_active_per_model
  on public.fm_face_render_pod ((coalesce(model_id, ''))) where retired_at is null;
