"""레퍼런스 코퍼스 배치 임베딩 — 검색 증강 Phase 3 (retrieval_upgrade_prd FR-C2, NFR-6).

ref_images 중 임베딩이 없거나(embed_model 이 현재 모델과 다른) 행을 골라 R2 에서 바이트를
읽어 SigLIP 임베딩 후 저장한다. 멱등: 재실행 시 이미 현재 모델로 임베딩된 행은 스킵.
요청 경로 아님(운영자 1회성 배치, FR-C2). service-role: DATABASE_URL 직접 연결.

실행: cd server && .venv/bin/python -m scripts.embed_corpus
전제: server/.env(DATABASE_URL·R2 자격증명), [embeddings] 의존 설치(torch/transformers).
"""
import psycopg

from scripts._env import load_env

load_env()  # server/.env → os.environ (load_settings 전)

from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.services import embeddings as E  # noqa: E402


def main() -> None:
    s = load_settings()
    assert s.database_url, "DATABASE_URL 필요 (server/.env)"
    model_id, dim = s.embed_image_model, s.embed_image_dim
    r2_cache: dict[str, R2Client] = {}

    def r2_for(bucket: str) -> R2Client:
        if bucket not in r2_cache:
            r2_cache[bucket] = R2Client(s, bucket=bucket)
        return r2_cache[bucket]

    with psycopg.connect(s.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            "select id, r2_bucket, r2_key from ref_images "
            "where is_active and (image_embedding is null or embed_model is distinct from %s) "
            "order by id",
            (model_id,),
        )
        rows = cur.fetchall()
        print(f"[embed_corpus] {len(rows)} row(s) need embedding (model={model_id}, dim={dim})")
        done = 0
        for rid, bucket, key in rows:
            try:
                data = r2_for(bucket).get_bytes(key)
            except Exception as e:  # R2 미스는 스킵(멱등 — 다음 실행에서 재시도)
                print(f"  SKIP {rid}: R2 get 실패 {bucket}/{key}: {e}")
                continue
            vec = E.embed_image(data, model_id=model_id, expected_dim=dim)
            cur.execute(
                "update ref_images set image_embedding = %s::vector, embed_model = %s where id = %s",
                (E.to_pgvector(vec), model_id, rid),
            )
            conn.commit()  # 행 단위 커밋 = 중단돼도 진척 보존(멱등)
            done += 1
            print(f"  embedded {rid} ({bucket}/{key})")
        print(f"[embed_corpus] done: {done}/{len(rows)}")


if __name__ == "__main__":
    main()
