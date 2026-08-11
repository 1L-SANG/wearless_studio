"""Pull a frozen QC corpus out of the production record — read only, to local disk.

Phase C needs artifacts we have already looked at: real source photographs and the real
generated cut that came from them. Both already exist; what did not exist was a way to put
them side by side without paying for a generation.

This script only READS. It selects projects, reads their product rows and their
`mannequin_cuts`, and downloads the referenced R2 objects into a local directory. It opens no
transaction that writes, and it never touches credits, jobs or cuts.

  cd server && .venv/bin/python -m scripts.llm_qc_corpus_fetch --out <dir>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

from scripts._env import load_env

load_env()

SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.config import load_settings          # noqa: E402
from app.r2 import R2Client                   # noqa: E402

#: The projects the corpus is drawn from. `label` is what the case is meant to exercise;
#: it is NOT an expected verdict — the expectation is set by looking at the images.
PROJECTS = [
    ("red-rib-blouse-goldenset", "1f0ad6d8-cd87-48a4-bfd9-881e325a1667",
     "the 4ff2132f blouse's own photographs, generated in the QA account"),
    ("stripe-shirt", "c7f00166-92a1-4be2-8d47-338808fc4eca", "stripe"),
    ("check-shirt", "96610dbd-7bb5-4133-a703-3630276fa66e", "check"),
    ("lace-top", "0db50de3-ab1f-490c-8cc2-c0dff8686a3e", "lace / openwork"),
    ("sheer-top", "719996ef-d750-4c7c-9b98-05c7c45c1416", "sheer"),
    ("eyelet-puff-square-neck-tee", "88a18ce4-0eb2-47cb-94aa-cec86aa7c063",
     "structural: eyelet + puff sleeve + square neck"),
    ("stripe-crop-shirt", "502a5fc8-fa60-4451-a948-e2cf7f0afe50", "stripe, recent"),
    ("shirring-blouse", "120bf2ed-ab59-46c0-822b-15fae18a54e2", "shirring / punching"),
    ("boatneck-rib-knit", "96a04ed8-7b9c-4f81-bd36-ea58b8d32836", "rib knit boat neck"),
    ("button-pocket-tee", "23706412-844a-40d2-8bd0-23ef8ddb59fe", "buttons + pocket"),
    # the control product itself: sources only, no cut has ever been generated for it
    ("4ff2132f-control", "4ff2132f-039b-49a4-a34e-8703df85f0df",
     "CONTROL — sources only, this is what the live run generates for"),
]


async def fetch(out_dir: pathlib.Path) -> dict:
    import psycopg
    from psycopg.rows import dict_row

    settings = load_settings()
    r2 = R2Client(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"projects": []}

    async with await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            for name, project_id, note in PROJECTS:
                await cur.execute(
                    "select id, title, user_id from projects where id = %s", (project_id,))
                project = await cur.fetchone()
                if not project:
                    print(f"  ! {name}: project missing")
                    continue
                await cur.execute(
                    "select id, name, clothing_type, colors from products where project_id = %s",
                    (project_id,))
                product = await cur.fetchone()
                await cur.execute(
                    "select id, garment_spec, color_spec, pattern_spec, protected_details, "
                    "status, version from product_truth_packages "
                    "where project_id = %s and status = 'approved' order by version desc limit 1",
                    (project_id,))
                truth = await cur.fetchone()
                await cur.execute(
                    "select id, candidate, version, asset_id, created_at, qc_scores "
                    "from mannequin_cuts where project_id = %s order by version",
                    (project_id,))
                cuts = await cur.fetchall()

                case_dir = out_dir / name
                case_dir.mkdir(parents=True, exist_ok=True)
                sources = []
                for color in (product or {}).get("colors") or []:
                    for image in color.get("images") or []:
                        asset_id = image.get("id")
                        if not asset_id:
                            continue
                        await cur.execute(
                            "select r2_key, mime_type from assets where id = %s", (asset_id,))
                        asset = await cur.fetchone()
                        if not asset:
                            continue
                        slot = image.get("slot") or "Front"
                        path = case_dir / f"source_{slot}_{asset_id[:8]}.jpg"
                        path.write_bytes(
                            await asyncio.to_thread(r2.get_bytes, asset["r2_key"]))
                        sources.append({"slot": slot, "assetId": asset_id,
                                        "path": str(path), "mime": asset["mime_type"]})

                generated = []
                for cut in cuts:
                    await cur.execute(
                        "select r2_key, mime_type from assets where id = %s",
                        (cut["asset_id"],))
                    asset = await cur.fetchone()
                    if not asset:
                        continue
                    path = case_dir / f"cut_v{cut['version']}_{str(cut['id'])[:8]}.png"
                    path.write_bytes(await asyncio.to_thread(r2.get_bytes, asset["r2_key"]))
                    generated.append({
                        "cutId": str(cut["id"]), "version": cut["version"],
                        "assetId": str(cut["asset_id"]), "path": str(path),
                        "mime": asset["mime_type"],
                        "createdAt": str(cut["created_at"]),
                        "legacyOutcome": (cut["qc_scores"] or {}).get("outcome"),
                    })

                manifest["projects"].append({
                    "name": name, "note": note, "projectId": project_id,
                    "title": project["title"], "productName": (product or {}).get("name"),
                    "clothingType": (product or {}).get("clothing_type"),
                    "truthApproved": bool(truth),
                    "patternSpec": (truth or {}).get("pattern_spec"),
                    "garmentSpec": (truth or {}).get("garment_spec"),
                    "sources": sources, "generated": generated,
                })
                print(f"  {name}: {len(sources)} sources, {len(generated)} cuts")

    (out_dir / "corpus.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    asyncio.run(fetch(pathlib.Path(args.out)))
    print(f"corpus -> {args.out}/corpus.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
