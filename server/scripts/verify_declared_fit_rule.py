"""선언 핏을 QC 에 넘기는 규칙의 **인과 효과만** 분리 측정.

문제(2026-07-31 실측): 셀러가 `fit: slim`·`length: crop` 으로 조정한 오버사이즈 티에서,
생성은 **선언대로** 슬림·크롭으로 냈는데 QC 는 상품 사진과 비교해 매 시도마다
`garment fit changed` 를 붙였다. 치명오류는 점수와 무관하게 regenerate 라, 핏 조정 기능을
쓰는 상품마다 예산이 소진될 때까지 재생성하다 구제 출고로 끝났다.

판정기의 **관찰은 정확했다** — 티가 실제로 슬림 크롭으로 바뀌었다. 틀린 건 판정이다.
그게 셀러가 요청한 것이라는 정보가 없었으니까. 그래서 기준을 사진이 아니라 의도로 옮긴다.

거울 규칙 때와 같은 설계다 — 같은 이미지·같은 판정기에 **규칙만** 껐다 켜서 효과를 분리한다.
생성을 다시 돌리면 무작위 변동이 효과보다 커진다.

실행:
    cd server && DATABASE_URL=... .venv/bin/python -m scripts.verify_declared_fit_rule \
        [--project e959ab09]
읽기 전용(생성 없음). 판정 2콜.
"""
import argparse
import asyncio

import psycopg
from psycopg.rows import dict_row

from scripts._env import load_env

load_env()

from app.agents import image_qc, mannequin  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402


def _pick(cur, prefix: str | None) -> dict:
    """선언 핏이 **있는** 프로젝트를 고른다 — 없으면 이 규칙은 아무것도 안 하므로 측정 불가."""
    cur.execute(
        """select p.id::text pid, a.payload analysis from projects p
           join analyses a on a.project_id = p.id
           where a.payload -> 'fitProfile' -> 'axes' <> '{}'::jsonb
             and p.id::text like %s
             and exists (select 1 from mannequin_cuts mc where mc.project_id = p.id)
           order by p.created_at desc limit 1""",
        (f"{prefix}%" if prefix else "%",))
    row = cur.fetchone()
    assert row, "선언 핏이 있고 컷이 있는 프로젝트가 없다"
    return row


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="프로젝트 id 접두사. 생략하면 최신 1건")
    args = ap.parse_args()

    s = load_settings()
    r2 = R2Client(s)
    with psycopg.connect(s.database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        row = _pick(cur, args.project)
        fit_profile = (row["analysis"] or {}).get("fitProfile")
        cur.execute(
            """select a.r2_key from mannequin_cuts mc join assets a on a.id = mc.asset_id
               where mc.project_id = %s order by mc.created_at desc limit 1""", (row["pid"],))
        cut = r2.get_bytes(cur.fetchone()["r2_key"])
        cur.execute("select * from products where project_id = %s", (row["pid"],))
        product = cur.fetchone()
        cur.execute("select id::text id, r2_key, mime_type from assets where deleted_at is null")
        assets = {r["id"]: r for r in cur.fetchall()}

    prod_imgs = [InlineImage(assets[aid]["mime_type"], r2.get_bytes(assets[aid]["r2_key"]))
                 for _slot, aid in mannequin.base_color_images(product) if aid in assets]
    print(f"[declared_fit] {row['pid'][:8]} · 선언 축 {(fit_profile or {}).get('axes')} · "
          f"상품 원본 {len(prod_imgs)}장")

    out = {}
    for label, profile in (("OFF", None), ("ON", fit_profile)):
        v = await image_qc.verdict(
            s, prod_imgs, InlineImage("image/png", cut), scored=True, fit_profile=profile)
        out[label] = v
        print(f"  {label:3} fidelity={v.get('product_fidelity')} "
              f"critical={v.get('critical_errors')}")
        for m in (v.get("mismatches") or [])[:2]:
            print(f"       · {m[:110]}")

    def _fit_crit(v):
        return [e for e in (v.get("critical_errors") or []) if "fit" in e.lower()]

    print(f"\n  fidelity {out['OFF'].get('product_fidelity')} → {out['ON'].get('product_fidelity')}"
          f"  ·  핏 치명오류 {len(_fit_crit(out['OFF']))} → {len(_fit_crit(out['ON']))}")
    print("  ON 에서 핏 치명오류가 사라져야 한다 — 남으면 규칙이 판정기에 안 닿고 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
