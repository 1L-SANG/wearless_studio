"""등록 사진 12장 → LoRA 학습셋 tgz → R2 (멱등, 기본 dry-run).

규칙·기하·캡션의 정본은 `app/services/lora_dataset.py` 다 — 이 스크립트는 **껍데기**다.
학습 잡(PR 3)이 같은 모듈을 부르므로, 손으로 돌린 것과 자동으로 돈 것이 같은 산물이어야 한다.

등록 사진은 생체정보다. 이 스크립트는 사진을 **디스크에 안 쓴다** — R2 에서 바이트로 읽어
메모리에서 tgz 를 만들고 R2 로 올린다. 맥에 남는 건 아무것도 없다.

실행:
    cd server && .venv/bin/python -m scripts.fm_build_training_set --model-id <uuid>   # 확인만
    ... --apply                                                                        # 실제 업로드

DB 는 server/.env 의 DATABASE_URL 을 따른다.
"""
import argparse
import sys
import uuid

import psycopg
from psycopg.rows import dict_row

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.services import lora_dataset  # noqa: E402


def fetch(dsn: str, model_id: str) -> tuple[str | None, str | None, list[dict]]:
    """(등록 id, 사진 확인 상태, 사진 행들). 읽기 전용 트랜잭션."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "select id::text as id, coalesce(photo_review_status, 'pending') as photo_review_status "
                "from fm_biometric_enrollments "
                "where model_id = %s and decision = 'passed' "
                "order by completed_at desc nulls last limit 1",
                (model_id,))
            enrollment = cur.fetchone()
            if enrollment is None:
                return None, None, []
            cur.execute(
                "select angle, r2_key, normalized_r2_key, mime_type, qc_status, storage_state "
                "from fm_biometric_enrollment_photos where enrollment_id = %s",
                (enrollment["id"],))
            return enrollment["id"], enrollment["photo_review_status"], [dict(r) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-id", required=True, help="fm_models.id (uuid)")
    ap.add_argument("--run-id", default=None, help="학습 런 id. 없으면 새로 만든다")
    ap.add_argument("--apply", action="store_true", help="없으면 만들어 보기만 하고 R2 에 안 올린다")
    a = ap.parse_args()

    try:
        uuid.UUID(a.model_id)
    except ValueError:
        print(f"model-id 가 uuid 가 아니다: {a.model_id}")
        return 2

    settings = load_settings()
    if not settings.r2_face_bucket:
        print("R2_FACE_BUCKET 미설정 — 얼굴 자산 버킷 없이는 읽을 수 없다")
        return 2
    import os

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL 미설정")
        return 2

    enrollment_id, review, rows = fetch(dsn, a.model_id)
    if enrollment_id is None:
        print(f"통과한 등록이 없다: model {a.model_id}")
        return 2
    run_id = a.run_id or uuid.uuid4().hex
    r2 = R2Client(settings, bucket=settings.r2_face_bucket, public_base=None)
    try:
        result = lora_dataset.build_and_upload(
            r2, model_id=a.model_id, run_id=run_id, rows=rows, review_status=review,
            model_dir=settings.fm_face_qc_dir, apply=a.apply)
    except lora_dataset.DatasetBuildError as exc:
        print(f"만들 수 없다: {exc}")
        return 1

    print(f"enrollment {enrollment_id[:8]} · review {review}")
    print(f"run        {run_id}")
    print(f"files      {result['files']} (train {result['train']} · samples {result['samples']})")
    print(f"tgz        {result['bytes'] / 2 ** 20:.1f}MB sha256 {result['sha256'][:12]}…")
    print(f"key        {result['key']}")
    if not a.apply:
        print("\n--apply 없이 실행 — R2 에 아무것도 안 올렸다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
