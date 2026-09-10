"""등록자별 LoRA 시드 — 가중치를 r2_face 에 올리고 fm_model_loras 행을 맞춘다 (멱등).

얼굴 패스는 fm_model_loras 의 **켜진 행**이 있어야만 걸린다(agents/identity_source.py).
학습 산출물(.safetensors)은 로컬에만 있으므로 이 스크립트가 그 둘을 잇는다.

멱등 규칙:
  · R2 키는 (model_id, version, 파일명)으로 결정된다. 같은 내용이 이미 있으면 다시 안 올린다.
  · 행은 unique(model_id, version) 기준 upsert.
  · --enable 을 주면 이 버전만 enabled=true 로 두고 같은 모델의 다른 버전을 끈다
    (partial unique index fm_model_loras_one_enabled_uidx 가 두 개를 허용하지 않는다).

머리·얼굴형 값은 **등록자의 현재 모습이 아니라 이 LoRA 가 학습한 모습**을 넣는다
(마이그레이션 20260910100000 주석). enum 은 app/facemarket_physique.py 와 같아야 한다.

실행:
    cd server && .venv/bin/python -m scripts.seed_model_lora \
        --model-id <uuid> --ckpt ~/Downloads/lora_runs/v6_ckpt/ohwx_man_v6_000001500.safetensors \
        --version 1 --trigger "ohwx man" --hair-length short --hair-color black \
        --hair-texture straight --face-shape oval --jaw-line defined --trained-steps 1500 \
        --metrics '{"frontal_median":0.717,"refset_ceiling":0.885}' --enable     # 확인만
    ... --apply                                                                   # 실제 쓰기

DB 는 server/.env 의 DATABASE_URL 을 따른다. 대상 DB 에 마이그레이션
20260910100000_fm_model_loras.sql 이 먼저 적용돼 있어야 한다.
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import uuid

import psycopg
from psycopg.types.json import Json

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.facemarket_physique import (  # noqa: E402
    FACE_SHAPES,
    HAIR_COLORS,
    HAIR_LENGTHS,
    HAIR_TEXTURES,
    JAW_LINES,
)
from app.r2 import R2Client  # noqa: E402

MIME = "application/octet-stream"


def _key(model_id: str, version: int, ckpt: pathlib.Path) -> str:
    return f"facemarket/models/{model_id}/loras/v{version}_{ckpt.name}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", required=True, help="fm_models.id (uuid)")
    ap.add_argument("--ckpt", required=True, help="학습 산출물 .safetensors 경로")
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--trigger", default="ohwx man")
    ap.add_argument("--base-model", default="Qwen/Qwen-Image-Edit-2509")
    ap.add_argument("--hair-length", choices=HAIR_LENGTHS)
    ap.add_argument("--hair-color", choices=HAIR_COLORS)
    ap.add_argument("--hair-texture", choices=HAIR_TEXTURES)
    ap.add_argument("--face-shape", choices=FACE_SHAPES)
    ap.add_argument("--jaw-line", choices=JAW_LINES)
    ap.add_argument("--trained-steps", type=int)
    ap.add_argument("--source-enrollment-id")
    ap.add_argument("--metrics", help="jsonb 로 넣을 JSON 문자열")
    ap.add_argument("--enable", action="store_true", help="이 버전을 켠다(다른 버전은 끈다)")
    ap.add_argument("--apply", action="store_true", help="없으면 확인만 하고 아무것도 안 쓴다")
    a = ap.parse_args()

    try:
        uuid.UUID(a.model_id)
    except ValueError:
        print(f"model-id 가 uuid 가 아니다: {a.model_id}")
        return 2
    ckpt = pathlib.Path(a.ckpt).expanduser()
    if not ckpt.is_file():
        print(f"체크포인트가 없다: {ckpt}")
        return 2
    metrics = json.loads(a.metrics) if a.metrics else None

    s = load_settings()
    if not s.r2_face_bucket:
        print("R2_FACE_BUCKET 미설정 — 얼굴 자산 버킷 없이는 올릴 수 없다")
        return 2
    r2 = R2Client(s, bucket=s.r2_face_bucket, public_base=None)
    key = _key(a.model_id, a.version, ckpt)
    data = ckpt.read_bytes()
    digest = hashlib.md5(data).hexdigest()
    head = r2.head(key)
    print(f"ckpt   {ckpt} ({len(data)/2**20:.1f}MB md5={digest})")
    print(f"key    {key}  (이미 있음: {bool(head)})")
    print(f"db     {'apply' if a.apply else 'dry-run'}  enable={a.enable} version={a.version}")

    if not a.apply:
        print("\n--apply 없이 실행 — 아무것도 쓰지 않았다.")
        return 0

    if head is None or int(head.get("size") or 0) != len(data):
        r2.put_bytes(key, data, MIME)
        print("uploaded")
    else:
        print("upload skipped (같은 크기의 객체가 이미 있다)")

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL 미설정")
        return 2
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn, conn.cursor() as cur:
        cur.execute("select to_regclass('public.fm_model_loras') as t")
        if not (cur.fetchone() or {}).get("t"):
            print("fm_model_loras 가 없다 — 마이그레이션 20260910100000 을 먼저 적용할 것")
            return 2
        cur.execute("select 1 from fm_models where id = %s", (a.model_id,))
        if cur.fetchone() is None:
            print(f"fm_models 에 {a.model_id} 가 없다 — 등록된 사람만 LoRA 를 가질 수 있다")
            return 2
        if a.enable:
            # partial unique(model_id) where enabled — 다른 버전을 **먼저** 꺼야 insert 가 통과한다.
            cur.execute(
                "update fm_model_loras set enabled = false "
                "where model_id = %s and version <> %s and enabled",
                (a.model_id, a.version),
            )
        cur.execute(
            """insert into fm_model_loras
                 (model_id, version, status, enabled, base_model, lora_r2_key, bucket,
                  trigger_token, hair_length, hair_color, hair_texture, face_shape, jaw_line,
                  trained_steps, source_enrollment_id, metrics)
               values (%s, %s, 'ready', %s, %s, %s, 'face', %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (model_id, version) do update set
                 status = 'ready', enabled = excluded.enabled, base_model = excluded.base_model,
                 lora_r2_key = excluded.lora_r2_key, trigger_token = excluded.trigger_token,
                 hair_length = excluded.hair_length, hair_color = excluded.hair_color,
                 hair_texture = excluded.hair_texture, face_shape = excluded.face_shape,
                 jaw_line = excluded.jaw_line, trained_steps = excluded.trained_steps,
                 source_enrollment_id = excluded.source_enrollment_id, metrics = excluded.metrics
               returning id::text as id""",
            (a.model_id, a.version, a.enable, a.base_model, key, a.trigger,
             a.hair_length, a.hair_color, a.hair_texture, a.face_shape, a.jaw_line,
             a.trained_steps, a.source_enrollment_id,
             Json(metrics) if metrics is not None else None),
        )
        row_id = (cur.fetchone() or {}).get("id")
        conn.commit()
    print(f"row    {row_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
