"""레퍼런스 코퍼스 시드 — 검색 증강 Phase 3 (retrieval_upgrade_prd FR-C1).

두 경로(운영자 큐레이션, 멱등 upsert):
  --from-dir <DIR>  : 로컬 이미지 파일들을 R2(seed/ref/…)에 올리고 ref_images(source='seed') 등록.
                      운영자가 고른 '이상적 스튜디오 룩' 레퍼런스를 넣는 주 경로.
  --from-cuts [N]   : 기존 mannequin_cuts(성공 생성물) 최근 N개를 ref_images(source='generated')로
                      등록(에셋 재사용 — 재업로드 없음). 파이프라인 부트스트랩·기계 검증용.

등록만 하고 임베딩은 하지 않는다 → 이어서 `python -m scripts.embed_corpus` 실행.
공통 옵션: --cut-type(기본 mannequin) --clothing-type --gender (--from-dir 시 메타 부여).

실행: cd server && .venv/bin/python -m scripts.seed_ref_images --from-cuts 20
전제: server/.env(DATABASE_URL·R2). --from-dir 는 R2 쓰기 → 운영자 승인 후.
"""
import argparse
import hashlib
import pathlib

import psycopg

from scripts._env import load_env

load_env()  # server/.env → os.environ (load_settings 전)

from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _upsert_ref(cur, *, rid, bucket, key, cut_type, clothing_type, gender, source, project_id=None):
    cur.execute(
        """
        insert into ref_images (id, r2_bucket, r2_key, cut_type, clothing_type, gender, source, project_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (id) do update set
          r2_bucket = excluded.r2_bucket, r2_key = excluded.r2_key,
          cut_type = excluded.cut_type, clothing_type = excluded.clothing_type,
          gender = excluded.gender, source = excluded.source, project_id = excluded.project_id
        """,
        (rid, bucket, key, cut_type, clothing_type, gender, source, project_id),
    )


def _norm_gender(text: str | None) -> str | None:
    """gender 신호(analyses.payload.targetGenders 텍스트 등)를 women/men 으로 정규화. 불명은 None
    (search 에서 null 은 성별 무관으로 항상 허용). 'women' 이 'men' 을 포함하므로 women 먼저."""
    s = (text or "").lower()
    if "women" in s or "female" in s or "여" in s:
        return "women"
    if "men" in s or "male" in s or "남" in s:
        return "men"
    return None


def from_cuts(conn, s, limit: int, cut_type: str, user_id: str | None) -> int:
    """기존 mannequin_cuts → ref_images(source='generated'). 에셋 재사용(재업로드 없음).
    clothing_type=products.clothing_type(정본), gender=analyses.payload.targetGenders 에서 정규화.
    user_id 지정 시 그 소유자 컷만(크로스테넌트 방지)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select mc.project_id::text, mc.candidate, mc.version,
                   p.clothing_type,
                   (select an.payload->>'targetGenders' from analyses an
                    where an.project_id = mc.project_id limit 1) as target_genders,
                   a.r2_bucket, a.r2_key
            from mannequin_cuts mc
            join assets a on a.id = mc.asset_id
            join projects pr on pr.id = mc.project_id
            left join products p on p.project_id = mc.project_id
            where a.deleted_at is null
              and (%s::uuid is null or pr.user_id = %s::uuid)
            order by mc.created_at desc
            limit %s
            """,
            (user_id, user_id, limit),
        )
        rows = cur.fetchall()
        for pid, cand, ver, clothing_type, target_genders, bucket, key in rows:
            rid = f"cut-{pid}-{cand}-{ver}"
            gender = _norm_gender(target_genders)
            _upsert_ref(cur, rid=rid, bucket=bucket, key=key, cut_type=cut_type,
                        clothing_type=clothing_type, gender=gender, source="generated", project_id=pid)
            print(f"  + {rid}  clothing={clothing_type} gender={gender}  {bucket}/{key}")
        conn.commit()
        return len(rows)


def from_dir(conn, s, directory: str, cut_type: str, clothing_type: str | None, gender: str | None) -> int:
    """로컬 이미지 → R2(seed/ref/…) 업로드 + ref_images(source='seed') 등록.
    id·key 를 **내용 해시**로 잡는다 — 파일명 키였다면 내용이 바뀌어도 같은 행이 유지돼 구 임베딩이
    남는(stale) 버그가 생긴다. 내용 해시면 내용 변경 = 새 행·새 객체 → embed_corpus 가 재임베딩."""
    r2 = R2Client(s, bucket=s.r2_bucket)
    d = pathlib.Path(directory)
    assert d.is_dir(), f"디렉터리 아님: {directory}"
    n = 0
    with conn.cursor() as cur:
        for p in sorted(d.iterdir()):
            mime = _MIME.get(p.suffix.lower())
            if not mime:
                continue
            data = p.read_bytes()
            h = hashlib.sha256(data).hexdigest()[:16]  # 내용 해시 (파일명 아님)
            key = f"seed/ref/{h}{p.suffix.lower()}"
            r2.put_bytes(key, data, mime, cache="public, max-age=31536000, immutable")
            rid = f"seed-{h}"
            _upsert_ref(cur, rid=rid, bucket=s.r2_bucket, key=key, cut_type=cut_type,
                        clothing_type=clothing_type, gender=gender, source="seed")
            print(f"  + {rid}  <- {p.name}  ({len(data)}B)")
            n += 1
        conn.commit()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-dir", metavar="DIR", help="로컬 이미지 디렉터리 → seed 등록")
    ap.add_argument("--from-cuts", nargs="?", type=int, const=20, metavar="N",
                    help="기존 마네킹컷 최근 N개 → generated 등록 (기본 20)")
    ap.add_argument("--cut-type", default="mannequin")
    ap.add_argument("--clothing-type", default=None)
    ap.add_argument("--gender", default=None)
    ap.add_argument("--user", default=None, metavar="UUID",
                    help="--from-cuts 소유자 스코프(이 유저 컷만). 크로스테넌트 방지")
    ap.add_argument("--allow-cross-tenant", action="store_true",
                    help="--from-cuts 를 --user 없이(전 유저 컷) 실행 허용 — dev 전용")
    args = ap.parse_args()
    s = load_settings()
    assert s.database_url, "DATABASE_URL 필요 (server/.env)"
    # 크로스테넌트 방지: --from-cuts 는 타 셀러의 비공개 컷을 코퍼스로 끌어와 다른 셀러의
    # 생성에 STYLE REFERENCE 로 쓰일 수 있다(리뷰 confirmed). --user 스코프 또는 명시적
    # --allow-cross-tenant 없이는 거부한다.
    if args.from_cuts is not None and not args.user and not args.allow_cross_tenant:
        ap.error("--from-cuts 는 --user <UUID> 로 소유자를 지정하거나 --allow-cross-tenant(dev) 를 명시해야 함")
    with psycopg.connect(s.database_url) as conn:
        total = 0
        if args.from_cuts is not None:
            total += from_cuts(conn, s, args.from_cuts, args.cut_type, args.user)
        if args.from_dir:
            total += from_dir(conn, s, args.from_dir, args.cut_type, args.clothing_type, args.gender)
        if args.from_cuts is None and not args.from_dir:
            ap.error("--from-dir 또는 --from-cuts 중 하나는 필요")
    print(f"[seed_ref_images] 등록 {total}건. 이어서: python -m scripts.embed_corpus")


if __name__ == "__main__":
    main()
