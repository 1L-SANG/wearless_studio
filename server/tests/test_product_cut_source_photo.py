import asyncio

import pytest

from app.agents import content_roles, cut_generator
from app.workers import detail_page_job as dpj
from conftest import fake_worker_app, make_settings, worker_job


@pytest.fixture
def product_example(monkeypatch):
    monkeypatch.setattr(cut_generator, "load_example_asset_registry", lambda: (None, {
        "ex_product_top_ghost_01": {"cutType": "product", "direction": "front", "all": "example.png"},
        "detail-example": {"cutType": "product", "direction": "front", "all": "detail.png"},
        "worn-example": {"cutType": "horizon", "direction": "back", "all": "worn.png"},
    }))


def _product_block(direction="front", shot="ghost", **extra):
    return {"id": "product", "source": "ai", "sectionRole": "product", "cutType": "product",
            "shot": shot, "direction": direction, "colorId": "base",
            "exampleId": "ex_product_top_ghost_01" if shot == "ghost" else "detail-example", **extra}


@pytest.mark.parametrize("shot", ["ghost", "detail"])
@pytest.mark.parametrize("direction, expected", [("back", "back"), ("side", "front"), (None, "front")])
def test_product_source_direction_survives_storage_and_generation(product_example, shot, direction, expected):
    stored = content_roles.canonicalize_storyboard([
        _product_block(direction, shot, sideStyle="threeQuarter")], for_storage=True)[0]
    assert stored["direction"] == expected
    assert stored["sideStyle"] is None
    spec = cut_generator.normalize_spec(stored, clothing_type="top")
    assert spec["direction"] == expected
    assert spec["sideStyle"] is None


def test_worn_example_direction_is_preserved(product_example):
    blocks = content_roles.canonicalize_storyboard([
        {"source": "ai", "sectionRole": "studio", "cutType": "horizon",
         "direction": "front", "exampleId": "worn-example"},
    ], for_storage=True)
    assert next(b for b in blocks if b["cutType"] == "horizon")["direction"] == "back"


@pytest.mark.parametrize("different", [{"direction": "back"}, {"detailTargetId": "other-target"}])
def test_detail_duplicates_keep_source_direction_and_target(product_example, different):
    first = _product_block(shot="detail", detailTargetId="target")
    blocks = [first, {**first, "id": "different", **different}, {**first, "id": "copy"}]
    assert dpj._duplicate_source_indexes(blocks, "top") == [None, None, 0]


def test_identical_ghost_cards_still_share_one_generation(product_example):
    first = _product_block()
    assert dpj._duplicate_source_indexes([first, {**first, "id": "copy"}], "top") == [None, 0]


def test_extended_top_front_and_back_ghosts_are_generated_separately(product_example, monkeypatch):
    calls, captured = [], {}
    stored = content_roles.canonicalize_storyboard([
        _product_block("front", id="front-ghost"),
        _product_block("back", id="back-ghost"),
    ], for_storage=True)

    async def project(*args):
        return {"copywriting": False}

    async def storyboard(*args):
        return stored

    async def product(*args):
        return {"clothingType": "top", "colors": [{"id": "base", "isBase": True, "images": [
            {"id": "front-photo", "slot": "Front"}, {"id": "back-photo", "slot": "Back"}]}]}

    async def analysis(*args):
        return {}

    async def asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def example(*args, **kwargs):
        return dpj.InlineImage("image/png", b"EXAMPLE")

    async def generate(settings, gemini, spec, product, images, **kwargs):
        calls.append((spec["direction"], [img.data for img in images]))
        return spec["direction"].encode(), "image/png"

    def assemble(storyboard, cuts, *args, **kwargs):
        captured["cuts"] = cuts
        return []

    async def finalize(conn, **kwargs):
        captured["success"] = kwargs
        return {"editor_blocks": [], "available": 95}

    async def fail(*args, **kwargs):
        pytest.fail(f"Two source photos must complete: {kwargs}")

    async def emit(*args, **kwargs):
        pass

    for name, fake in [("get_project", project), ("get_storyboard", storyboard),
                       ("get_product", product), ("get_analysis", analysis),
                       ("get_asset_for_user", asset), ("finalize_detail_page_success", finalize),
                       ("finalize_detail_page_failure", fail)]:
        monkeypatch.setattr(dpj.repo, name, fake)
    monkeypatch.setattr(cut_generator, "load_example_image", example)
    monkeypatch.setattr(cut_generator, "generate", generate)
    monkeypatch.setattr(dpj.page_assembler, "assemble", assemble)
    monkeypatch.setattr(dpj, "_emit", emit)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.r2.get_bytes = lambda key: key.encode()
    job = worker_job(credits_reserved=5)
    job["metadata"] = {"perCutCost": 1, "aiCount": 2, "minCuts": 5}
    asyncio.run(dpj.run_detail_page_job(app, job))

    assert sorted(direction for direction, _ in calls) == ["back", "front"]
    assert len(calls) == 2
    assert {b"k/front-photo", b"k/back-photo"} <= {data for _, images in calls for data in images}
    cuts = {cut["blockId"]: cut for cut in captured["cuts"]}
    assert cuts["front-ghost"]["imageUrl"] != cuts["back-ghost"]["imageUrl"]
    assert len(captured["success"]["cut_assets"]) == 2


def test_retired_cut_rejects_without_overriding_saved_example_direction(product_example):
    with pytest.raises(ValueError, match="unknown_cut_type"):
        content_roles.canonicalize_storyboard([
            {"source": "ai", "sectionRole": "styling", "cutType": "mirror", "direction": "back"},
        ], for_storage=True)
