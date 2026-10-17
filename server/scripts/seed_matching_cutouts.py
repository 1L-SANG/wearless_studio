"""Plan catalog board cutouts; only --apply processes images and writes R2/DB.

Run from server with .venv/bin/python -m scripts.seed_matching_cutouts.
--python selects a local Python with rembg, onnxruntime, Pillow and cached
isnet-general-use weights. --from-dir reads pre-cut <matching-item-id>.png files.
No paid providers, package installation, or automatic model download.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from PIL import Image
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
MODEL = "isnet-general-use"

# One process/session for the whole batch, also when the rembg environment is
# separate from the server environment. Only image files cross that boundary.
_REMBG_WORKER = '''
import os, sys
from pathlib import Path
weights = Path(os.environ.get("U2NET_HOME", "~/.u2net")).expanduser() / "isnet-general-use.onnx"
if not weights.is_file():
    raise SystemExit("Cached isnet-general-use.onnx required in U2NET_HOME or ~/.u2net; no automatic download")
from rembg import new_session, remove
session = new_session("isnet-general-use")
for source in sorted(Path(sys.argv[1]).glob("*.png")):
    (Path(sys.argv[2]) / source.name).write_bytes(remove(source.read_bytes(), session=session))
'''


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Process, upload and register (default: print plan only)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--python", default=sys.executable, help="Python with rembg and cached isnet-general-use weights")
    source.add_argument("--from-dir", type=Path, help="Pre-cut transparent <id>.png folder; rembg not needed")
    return parser.parse_args(argv)


def plan_cutouts(items, manifest, existing, *, root=ROOT, from_dir=None):
    """Pure plan with filesystem existence checks only. existing maps source asset IDs to keys."""
    seeds = {item["id"]: item for item in manifest}
    plan = []
    for item in items:
        identifier, source_id = str(item["id"]), str(item["image_asset_id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError(f"Invalid matching item ID: {identifier!r}")
        key = f"seed/matching/cutout/{identifier}.png"
        if from_dir is not None:
            source = str(Path(from_dir) / f"{identifier}.png")
        else:
            url = seeds.get(identifier, {}).get("imageUrl", "")
            local = (Path(root) / "public" / url.lstrip("/")).resolve()
            allowed = (Path(root) / "public/assets/matching").resolve()
            source = str(local) if url.startswith("/assets/matching/") and local.is_relative_to(allowed) and local.is_file() else f"r2:{item['image_key']}"
        plan.append({"id": identifier, "source_asset_id": source_id, "source": source,
                     "precut": from_dir is not None, "key": key, "skip": source_id in existing})
    return plan


def crop_cutout(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as opened:
        rgba = opened.convert("RGBA")
        alpha = rgba.getchannel("A")
        bounds = alpha.getbbox()
        if bounds is None or alpha.getextrema()[0] == 255:
            raise ValueError("Cutout must contain a foreground and a transparent background")
        cropped = rgba.crop(bounds)
    output = io.BytesIO()
    cropped.save(output, "PNG")
    return output.getvalue()


def remove_backgrounds(sources: dict[str, bytes], python: str) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="matching-cutouts-") as folder:
        inputs, outputs = Path(folder) / "in", Path(folder) / "out"
        inputs.mkdir(); outputs.mkdir()
        for identifier, data in sources.items():
            (inputs / f"{identifier}.png").write_bytes(data)
        result = subprocess.run([python, "-c", _REMBG_WORKER, str(inputs), str(outputs)],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"Local rembg failed. Use --python with rembg or --from-dir.\n{result.stderr or result.stdout}")
        return {identifier: (outputs / f"{identifier}.png").read_bytes() for identifier in sources}


def run_plan(plan, *, apply=False, python=sys.executable, r2=None, cursor=None, bucket=None, commit=None):
    pending = [entry for entry in plan if not entry["skip"]]
    print(f"{'APPLY' if apply else 'DRY-RUN'} model={MODEL} upload={len(pending)} assets={len(pending)} skip={len(plan) - len(pending)}")
    for entry in plan:
        print(f"SKIP {entry['id']} (registered)" if entry["skip"] else
              f"CREATE {entry['id']} source={entry['source']} remove={'no' if entry['precut'] else MODEL} crop=alpha-bbox upload={entry['key']}")
    if not apply or not pending:
        return 0
    # Preflight the whole batch before the first remote write. A bad/missing
    # pre-cut file cannot leave an avoidable half-applied batch.
    originals = {entry["id"]: r2.get_bytes(entry["source"][3:]) if entry["source"].startswith("r2:")
                 else Path(entry["source"]).read_bytes() for entry in pending}
    to_remove = {entry["id"]: originals[entry["id"]] for entry in pending if not entry["precut"]}
    removed = remove_backgrounds(to_remove, python) if to_remove else {}
    cropped = {entry["id"]: crop_cutout(removed.get(entry["id"], originals[entry["id"]])) for entry in pending}
    for entry in pending:
        data = cropped[entry["id"]]
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
        metadata = {"canonicalType": "catalog_cutout", "sourceAssetId": entry["source_asset_id"],
                    "matchingItemId": entry["id"], "producer": "precut" if entry["precut"] else f"rembg:{MODEL}",
                    "algorithmVersion": "catalog-cutout-v1"}
        cursor.execute(
            "insert into assets (source, visibility, r2_bucket, r2_key, mime_type, byte_size, width, height, checksum, metadata) "
            "values ('derived', 'public', %s, %s, 'image/png', %s, %s, %s, %s, %s) "
            "on conflict (r2_key) do update set byte_size = excluded.byte_size, width = excluded.width, "
            "height = excluded.height, checksum = excluded.checksum, metadata = excluded.metadata, deleted_at = null "
            "where assets.source = 'derived' and assets.visibility = 'public' "
            "and assets.user_id is null and assets.project_id is null "
            "and assets.metadata->>'canonicalType' = 'catalog_cutout' returning id",
            (bucket, entry["key"], len(data), width, height, hashlib.sha256(data).hexdigest(), Jsonb(metadata)),
        )
        if cursor.fetchone() is None:
            raise RuntimeError(f"Asset key belongs to another kind: {entry['key']}")
        r2.put_bytes(entry["key"], data, "image/png", "public, max-age=3600")
        commit()
        print(f"APPLIED {entry['id']} {width}x{height} {entry['key']}")
    return len(pending)


def main(argv=None):
    args = parse_args(argv)
    # Lazy imports keep unit tests and dry-run independent of R2 initialization/rembg.
    import psycopg
    from psycopg.rows import dict_row
    from app.config import load_settings
    from app.r2 import R2Client

    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL required (dry-run reads only)")
    if args.apply and not settings.r2_public_base:
        raise SystemExit("R2_PUBLIC_BASE required for public board URLs")
    manifest = json.loads((ROOT / "server/seed/matching_items.json").read_text())
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.read_only = not args.apply
        with conn.cursor() as cur:
            cur.execute(
                "select mi.id, img.id::text as image_asset_id, img.r2_key as image_key "
                "from matching_items mi join assets img on img.id = mi.image_asset_id "
                "and img.source = 'seed' and img.visibility = 'public' and img.deleted_at is null "
                "where mi.is_active and mi.owner_user_id is null and mi.project_id is null order by mi.id")
            items = cur.fetchall()
            cur.execute(
                "select metadata->>'sourceAssetId' as source_asset_id, r2_key from assets "
                "where source = 'derived' and visibility = 'public' and deleted_at is null "
                "and user_id is null and project_id is null and metadata->>'canonicalType' = 'catalog_cutout' "
                "and metadata->>'sourceAssetId' = any(%s)",
                ([item["image_asset_id"] for item in items],))
            existing = {row["source_asset_id"]: row["r2_key"] for row in cur.fetchall()}
            plan = plan_cutouts(items, manifest, existing, from_dir=args.from_dir)
            run_plan(plan, apply=args.apply, python=args.python,
                     r2=R2Client(settings) if args.apply else None, cursor=cur,
                     bucket=settings.r2_bucket, commit=conn.commit)


if __name__ == "__main__":
    main()
