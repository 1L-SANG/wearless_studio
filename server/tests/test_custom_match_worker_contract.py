from pathlib import Path

from app.agents import cut_generator
from app.workers import mannequin_job


ROOT = Path(__file__).resolve().parents[1]


def test_mannequin_custom_grid_manifest_treats_cells_as_one_garment():
    manifest = mannequin_job._build_manifest(
        [{"slot": "Front"}], True, "top", match_is_custom=True,
    )
    assert "2x2 contact sheet" in manifest
    assert "ONE SAME matching garment" in manifest
    assert "empty neutral cells mean no photo" in manifest
    assert "dress one garment only" in manifest
    assert "never a collage or grid" in manifest


def test_detail_and_editor_shared_manifest_guards_only_custom_slots():
    manifest = cut_generator.build_manifest(
        [{"slot": "Front"}],
        has_mannequin=False,
        has_match=True,
        matching_count=2,
        matching_custom=[True, False],
        mood_count=0,
    )
    matching_lines = [line for line in manifest.splitlines() if "MATCHING —" in line]
    assert "2x2 contact sheet" in matching_lines[0]
    assert "never reproduce the contact sheet" in matching_lines[0]
    assert "2x2 contact sheet" not in matching_lines[1]


def test_all_production_consumers_pass_owner_scope_without_bucket_branch():
    consumers = [
        ROOT / "app/workers/mannequin_job.py",
        ROOT / "app/workers/detail_page_job.py",
        ROOT / "app/workers/editor_image_job.py",
        ROOT / "scripts/spike_volume_secondpass.py",
    ]
    for path in consumers:
        source = path.read_text(encoding="utf-8")
        assert "get_matching_item_asset(" in source
        assert "user_id, project_id" in source or "user, project" in source
        assert "custom_match_bucket" not in source

