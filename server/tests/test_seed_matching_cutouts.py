"""Only synthetic local bytes and fake DB/R2 clients; never run the seed CLI."""
import importlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image


@pytest.fixture
def seed():
    return importlib.import_module("scripts.seed_matching_cutouts")


def png(*, mode="RGBA", empty=False):
    image = Image.new(mode, (20, 30), (0, 0, 0, 0) if mode == "RGBA" else (232, 232, 230))
    if not empty:
        image.paste((20, 30, 40, 255) if mode == "RGBA" else (20, 30, 40), (4, 6, 15, 24))
    out = io.BytesIO(); image.save(out, "PNG")
    return out.getvalue()


def rows():
    return [{"id": "one", "image_asset_id": "source-one", "image_key": "seed/matching/one.png"},
            {"id": "two", "image_asset_id": "source-two", "image_key": "seed/matching/two.png"}]


def test_dry_run_prints_plan_and_performs_no_processing_or_writes(seed, tmp_path, capsys):
    local = tmp_path / "public/assets/matching/top/one.png"
    local.parent.mkdir(parents=True); local.write_bytes(png(mode="RGB"))
    manifest = [{"id": "one", "imageUrl": "/assets/matching/top/one.png"}]
    plan = seed.plan_cutouts(rows(), manifest, {"source-two": "existing.png"}, root=tmp_path)
    assert plan[0]["source"] == str(local)
    assert plan[1]["skip"] is True
    assert seed.run_plan(plan) == 0
    output = capsys.readouterr().out
    assert "DRY-RUN" in output and "isnet-general-use" in output
    assert "seed/matching/cutout/one.png" in output
    assert "SKIP two" in output
    assert "upload=1" in output and "assets=1" in output
    assert list(tmp_path.rglob("*.png")) == [local]
    assert seed.parse_args([]).apply is False


def test_plan_falls_back_to_r2_and_from_dir_skips_removal(seed, tmp_path):
    plan = seed.plan_cutouts(rows(), [], {}, root=tmp_path)
    assert [entry["source"] for entry in plan] == ["r2:seed/matching/one.png", "r2:seed/matching/two.png"]
    plan = seed.plan_cutouts(rows(), [], {}, root=tmp_path, from_dir=tmp_path / "cutouts")
    assert plan[0]["source"] == str(tmp_path / "cutouts/one.png")
    assert plan[0]["precut"] is True


def test_crop_uses_alpha_bbox_and_preserves_color(seed):
    result = seed.crop_cutout(png())
    image = Image.open(io.BytesIO(result))
    assert image.mode == "RGBA" and image.size == (11, 18)
    assert image.getpixel((0, 0)) == (20, 30, 40, 255)


@pytest.mark.parametrize("data", [png(mode="RGB"), png(empty=True)], ids=["opaque", "empty"])
def test_opaque_or_empty_masks_are_rejected_before_upload(seed, data):
    with pytest.raises(ValueError):
        seed.crop_cutout(data)


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params):
        self.calls.append((" ".join(query.split()), params))

    def fetchone(self):
        return {"id": "derived-one"}


def test_apply_precut_uploads_one_cropped_png_and_one_derived_asset(seed, tmp_path, capsys):
    (tmp_path / "one.png").write_bytes(png())
    plan = seed.plan_cutouts(rows(), [], {"source-two": "existing.png"}, from_dir=tmp_path)
    uploads, commits = [], []
    cursor = FakeCursor()
    r2 = SimpleNamespace(put_bytes=lambda *args: uploads.append(args))
    assert seed.run_plan(plan, apply=True, r2=r2, cursor=cursor, bucket="bucket",
                         commit=lambda: commits.append(True)) == 1
    assert len(uploads) == len(cursor.calls) == len(commits) == 1
    key, data, mime, _cache = uploads[0]
    assert key == "seed/matching/cutout/one.png" and mime == "image/png"
    assert Image.open(io.BytesIO(data)).size == (11, 18)
    sql, params = cursor.calls[0]
    assert "'derived'" in sql and "'public'" in sql
    metadata = params[-1].obj
    assert metadata["canonicalType"] == "catalog_cutout"
    assert metadata["sourceAssetId"] == "source-one"
    assert "APPLIED" in capsys.readouterr().out


def test_apply_validates_all_inputs_before_first_write(seed, tmp_path):
    (tmp_path / "one.png").write_bytes(png())
    (tmp_path / "two.png").write_bytes(png(mode="RGB"))
    plan = seed.plan_cutouts(rows(), [], {}, from_dir=tmp_path)
    with pytest.raises(ValueError):
        seed.run_plan(plan, apply=True, r2=SimpleNamespace(), cursor=FakeCursor(), bucket="bucket", commit=lambda: None)


def test_apply_removes_background_in_one_batch_using_selected_python(seed, tmp_path, monkeypatch):
    plan = seed.plan_cutouts(rows(), [], {}, root=tmp_path)
    seen, uploads = [], []

    def remove(sources, python):
        seen.append((sources, python))
        return {key: png() for key in sources}

    monkeypatch.setattr(seed, "remove_backgrounds", remove)
    r2 = SimpleNamespace(get_bytes=lambda key: b"rgb:" + key.encode(), put_bytes=lambda *a: uploads.append(a))
    seed.run_plan(plan, apply=True, python="/local/rembg/python", r2=r2,
                  cursor=FakeCursor(), bucket="bucket", commit=lambda: None)
    assert len(seen) == 1
    assert seen[0] == ({"one": b"rgb:seed/matching/one.png", "two": b"rgb:seed/matching/two.png"}, "/local/rembg/python")
    assert len(uploads) == 2


def test_existing_key_of_another_asset_kind_is_rejected_before_r2_write(seed, tmp_path):
    (tmp_path / "one.png").write_bytes(png())
    plan = seed.plan_cutouts(rows()[:1], [], {}, from_dir=tmp_path)
    cursor = FakeCursor()
    cursor.fetchone = lambda: None
    with pytest.raises(RuntimeError, match="another kind"):
        seed.run_plan(plan, apply=True, r2=SimpleNamespace(), cursor=cursor, bucket="bucket", commit=lambda: None)
