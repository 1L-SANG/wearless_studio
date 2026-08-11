"""Fill prep.json with the SAME material metadata production would have.

The qa6 corpus is built from local photo files, so those garments were never uploaded and
have no row in `analyses`. That is why `prep.json` carries no `materials` / `subCategory`, and
why the knit arm of the legacy A/B had nothing to compare: with no `- Material:` line,
`material_guidance()` never runs and the knit wording — old or new — is absent from every arm.

Two ways to get the real values, in this order:

  1. REUSE — if the garment already has a production analysis (prep carries a `projectId`, or
     one is passed in), read `analyses.payload` and use it verbatim. Nothing is generated.
  2. DERIVE — otherwise call the production analyst, `product_analyst.analyze()`, on the same
     source photographs, with the same `product` shape route `POST /analysis` builds. The
     values are that agent's real output for this garment, in the production schema.

What this never does is invent a plausible fibre mix. A hand-written "울 100%" would make the
knit arm measure a fabric the garment does not have.

Experiment-only. Production reads none of this.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv                                          # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")

from app.agents import product_analyst                                  # noqa: E402
from app.agents.vision_llm import InlineImage                           # noqa: E402
from app.config import load_settings                                    # noqa: E402

PREP = pathlib.Path("ab_out/qa6/prep.json")
CLOTHING_LABEL = {"top": "상의", "bottom": "하의", "outer": "아우터"}

#: Only these two keys are copied into prep.json. `material_guidance()` reads exactly them,
#: and a wider copy would let unrelated analysis fields drift into the experiment.
KEEP = ("materials", "subCategory")


def analysis_section(payload: dict) -> dict:
    """The `analysis` half of a payload.

    `product_analyst.distribute()` returns {product, analysis, intermediate} — materials and
    subCategory live under `analysis`, which is also exactly what `analyses.payload` stores.
    Reading the top level instead silently yields no materials.
    """
    inner = payload.get("analysis")
    return inner if isinstance(inner, dict) else payload


async def from_database(project_id: str) -> dict | None:
    """A real production analysis, verbatim. None when the project has none."""
    import psycopg
    from psycopg.rows import dict_row
    s = load_settings()
    async with await psycopg.AsyncConnection.connect(s.database_url,
                                                     row_factory=dict_row) as conn:
        await conn.set_read_only(True)
        async with conn.cursor() as cur:
            await cur.execute("select payload from analyses where project_id = %s",
                              (project_id,))
            row = await cur.fetchone()
    payload = (row or {}).get("payload") or {}
    return payload or None


async def from_analyst(g: dict) -> dict:
    """The production analyst's own output for these photographs.

    Same call the `POST /projects/{id}/analysis` route makes: same agent, same prompt builder,
    same validation, same `distribute()`. The only difference is where the bytes come from.
    """
    s = load_settings()
    product = {"name": g["garment_name"],
               "clothing_type": CLOTHING_LABEL.get(g["category"], g["category"])}
    images = []
    for slot in ("Front", "Back", "Detail"):
        v = (g.get("views") or {}).get(slot)
        if not v:
            continue
        p = pathlib.Path(v["jpeg"])
        images.append(InlineImage("image/jpeg", p.read_bytes()))
    if not images:
        raise SystemExit(f"{g['garment_id']}: no source jpegs on disk")
    distributed, provider = await product_analyst.analyze(s, product, images)
    distributed["_provider"] = provider
    return distributed


async def main_async(args) -> int:
    prep = json.loads(PREP.read_text(encoding="utf-8"))
    idx = {g["garment_id"]: g for g in prep}
    target = args.garment
    if target not in idx:
        raise SystemExit(f"{target} not in {PREP} — have: {', '.join(idx)}")
    g = idx[target]

    project_id = args.project_id or g.get("projectId")
    source, payload = None, None
    if project_id:
        payload = await from_database(project_id)
        if payload:
            source = f"analyses.payload (project {project_id})"
    if payload is None:
        if args.no_analyst:
            raise SystemExit(f"{target}: no stored analysis and --no-analyst was passed")
        payload = await from_analyst(g)
        source = f"product_analyst.analyze via {payload.pop('_provider', '?')}"

    picked = {k: analysis_section(payload).get(k) for k in KEEP}
    if not picked.get("materials"):
        raise SystemExit(f"{target}: analysis returned no materials — refusing to write a "
                         f"metadata-free entry that would silently disable the knit arm")

    print(json.dumps({"garmentId": target, "source": source, **picked},
                     ensure_ascii=False, indent=2))
    if args.dry_run:
        print("\n(dry run — prep.json not written)")
        return 0

    g.update(picked)
    g["materialsSource"] = source
    PREP.write_text(json.dumps(prep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {PREP}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("garment")
    ap.add_argument("--project-id", default="",
                    help="reuse this project's stored production analysis instead of calling "
                         "the analyst")
    ap.add_argument("--no-analyst", action="store_true",
                    help="fail instead of spending a vision call")
    ap.add_argument("--dry-run", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    os.chdir(pathlib.Path(__file__).resolve().parents[1])
    raise SystemExit(main())
