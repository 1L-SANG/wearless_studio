"""생성 프롬프트 레터링 규칙 A/B — 로고·텍스트 재현이 실제로 좋아지는지 측정으로 판정.

2026-07-31 캘리브레이션에서 critical_errors 30건 중 17건이 "text or logo altered" 였다.
육안 확인 결과 QC 가 맞았다(원본 `thisisneverthat®` → 생성본 첫 글자 뭉개짐). 현 생성
프롬프트는 로고를 "재현할 그래픽"으로만 말하고 **글자를 옮기라는 지시가 없다**.

같은 프로젝트·같은 입력으로 규칙 없이/있게 각각 생성해 `image_qc`(scored)로 채점한다.
프롬프트는 서비스의 가장 위험한 변경 지점이라, 근거 없이 고치지 않고 여기서 먼저 잰다.

실행:
    cd server && DATABASE_URL=...54322 .venv/bin/python -m scripts.ab_lettering_prompt --projects 4

비용: 프로젝트당 생성 2콜 + 채점 2콜.
"""
import argparse
import asyncio
import json
import pathlib

from scripts._env import load_env

load_env()

from app.agents import image_qc, mannequin  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.prompts import (  # noqa: E402
    MannequinPromptContext, load_prompt_template, render_mannequin_prompt,
)
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "ab_out/lettering_prompt"

# 시험 대상 규칙. 로고를 '모양'이 아니라 '글자'로 다루게 만드는 것이 핵심이다.
LETTERING_RULE = """
- LETTERING: any text on the garment — brand wordmark, slogan, number, ® or © symbol — is
  copied character by character, not redrawn from memory. Read it from the product photos first
  and keep the exact characters, their order, their count, the spacing between them and the
  letterforms. Do not correct, complete, translate or restyle a word you think you recognise.
  If a character is too small or too blurred to read, render that part softly out of focus at
  the same size and position rather than inventing a letter that was never there.
"""


def _patch(template: str) -> str:
    """<instruction> 블록 안, 첫 규칙 바로 뒤에 레터링 규칙을 끼운다."""
    anchor = "- Fit and proportion precedence, axis by axis:"
    assert anchor in template, "템플릿 구조가 바뀌었다 — 앵커를 갱신하라"
    return template.replace(anchor, LETTERING_RULE.strip() + "\n" + anchor, 1)


async def _generate(gemini, s, model, template, ctx, product, analysis, images):
    prompt = render_mannequin_prompt(template, ctx, product, analysis)
    res = await gemini.generate_content_image(
        model, prompt, images, s.mannequin_image_size,
        aspect_ratio=s.mannequin_aspect_ratio)
    return res


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", type=int, default=4)
    args = ap.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    s = load_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    base_tpl = load_prompt_template(s)
    lettering_tpl = _patch(base_tpl)
    gemini = GeminiImageClient(s)
    model = resolve_model(s, "image_high")

    # 로고·텍스트가 있는 상의 위주 — 레터링 규칙의 효과가 드러나는 표본.
    with psycopg.connect(s.database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select p.id::text project_id, p.user_id::text user_id, pr.clothing_type, pr.colors
            from projects p join products pr on pr.project_id = p.id
            where exists (select 1 from analyses a where a.project_id = p.id)
              and jsonb_array_length(coalesce(pr.colors, '[]'::jsonb)) > 0
              and pr.clothing_type = 'top'
            order by p.created_at desc limit %s
            """, (args.projects,))
        rows = cur.fetchall()
        for row in rows:
            ids = [aid for _slot, aid in mannequin.base_color_images({"colors": row["colors"]})]
            cur.execute("select id::text, r2_bucket, r2_key, mime_type from assets "
                        "where id = any(%s::uuid[]) and deleted_at is null", (ids,))
            row["assets"] = {a["id"]: a for a in cur.fetchall()}
            row["asset_order"] = ids
        cur.execute("select r2_bucket, r2_key, mime_type from assets where id = %s",
                    (s.base_mannequin_women_asset_id,))
        base_row = cur.fetchone()

    r2 = R2Client(s)
    base_img = InlineImage(base_row["mime_type"],
                           R2Client(s, bucket=base_row["r2_bucket"]).get_bytes(base_row["r2_key"]))

    results = []
    for row in rows:
        prods = [InlineImage(a["mime_type"], R2Client(s, bucket=a["r2_bucket"]).get_bytes(a["r2_key"]))
                 for aid in row["asset_order"] if (a := row["assets"].get(aid))]
        if not prods:
            continue
        manifest = "\n".join(
            ["1. Base mannequin — the canvas to dress (keep it identical)"]
            + [f"{i + 2}. view of the garment" for i in range(len(prods))])
        ctx = MannequinPromptContext(
            clothing_type="top", product_count=len(prods), base_gender="women",
            image_manifest=manifest, fit_profile=None)
        product = {"name": "레터링 A/B", "clothing_type": "top"}
        pid = row["project_id"]
        rec = {"project_id": pid}
        for label, tpl in (("OFF", base_tpl), ("ON", lettering_tpl)):
            res = await _generate(gemini, s, model, tpl, ctx, product, {}, [base_img, *prods])
            (OUT / f"{pid[:8]}_{label}.png").write_bytes(res.image)
            p2 = await image_qc.verdict(s, prods, InlineImage(res.mime, res.image), scored=True)
            rec[label] = {"product_fidelity": p2["product_fidelity"],
                          "critical_errors": p2["critical_errors"],
                          "mismatches": p2["mismatches"]}
            print(f"  {pid[:8]} [{label}] fidelity={p2['product_fidelity']} "
                  f"critical={len(p2['critical_errors'])}")
            for m in p2["mismatches"][:2]:
                print(f"      · {m[:110]}")
        results.append(rec)

    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    # ── 집계 ─────────────────────────────────────────────────────────────────
    def _avg(label, key):
        vals = [r[label][key] for r in results if isinstance(r.get(label, {}).get(key), int)]
        return sum(vals) / len(vals) if vals else None

    def _crit(label):
        return sum(1 for r in results if r.get(label, {}).get("critical_errors"))

    def _text_crit(label):
        """**text/logo 치명오류만** 센다 — 이 규칙이 겨냥하는 결함이다.

        직전 판정(2026-07-31)은 fidelity 평균 80.8 vs 80.0 을 보고 규칙을 기각했다. 그 뒤
        같은 판정기가 같은 이미지에 ±30 을 낸다는 것이 측정됐다 — 0.8 차이는 노이즈였고
        그 기각은 근거가 없었다. 이산 신호인 치명오류 발생률이 올바른 종점이다.
        """
        n = 0
        for r in results:
            errs = r.get(label, {}).get("critical_errors") or []
            if any(k in e.lower() for e in errs for k in ("text", "logo", "letter")):
                n += 1
        return n

    print(f"\n[집계] n={len(results)}")
    for label in ("OFF", "ON"):
        f = _avg(label, "product_fidelity")
        print(f"  {label}: fidelity 평균 {f if f is None else round(f, 1)}"
              f"  ·  critical 보유 {_crit(label)}/{len(results)}"
              f"  ·  **text/logo 치명오류 {_text_crit(label)}/{len(results)}**")
    print("  (fidelity 평균은 판정기 노이즈 ±30 때문에 이 표본 크기에서 무의미하다 — "
          "text/logo 발생률로 판단할 것)")
    print(f"  → {OUT / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
