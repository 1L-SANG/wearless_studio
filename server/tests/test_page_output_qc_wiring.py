import asyncio

from app.agents.gemini_image import InlineImage
from app.workers import detail_page_job as dpj
from conftest import fake_worker_app, make_settings, worker_job


class _RecordingR2:
    def __init__(self):
        self.saved = []

    def get_bytes(self, key):
        return b"PASSTHROUGH"

    def put_bytes(self, key, data, mime, cache=None):
        self.saved.append(data)

    def delete(self, key):
        return None


def _spec(block_id, cut_type, shot):
    return {
        "id": block_id,
        "cutType": cut_type,
        "direction": "front",
        "shot": shot,
        "faceExposure": None if cut_type == "product" else "same",
        "pose": "auto",
        "refScope": "all",
    }


def _run_cuts(
    monkeypatch,
    *,
    page_qc_mode,
    prepared=None,
    fail_ids=(),
    cut_qc_mode="off",
    r2=None,
):
    product_image = InlineImage("image/png", b"PRODUCT")
    fail_ids = set(fail_ids)

    async def fake_generate(_settings, _gemini, spec, *_args, **_kwargs):
        if spec["id"] in fail_ids:
            raise RuntimeError("generation failed")
        return f"initial:{spec['id']}".encode(), "image/png"

    async def fake_best_of(_settings, _product_images, initial, _generate_candidate):
        return InlineImage(initial.mime, b"chosen:" + initial.data), None, []

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.image_qc, "best_of", fake_best_of)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    r2 = r2 or _RecordingR2()
    app = fake_worker_app(
        make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            garment_qc_mode="off",
            cut_output_qc_mode=cut_qc_mode,
            page_output_qc_mode=page_qc_mode,
        ),
        r2=r2,
    )
    if prepared is None:
        prepared = [
            (
                _spec("worn", "styling", "full"),
                [product_image],
                "1. PRODUCT — front",
                False,
                [product_image],
            ),
            (
                _spec("product", "product", "ghost"),
                [product_image],
                "1. PRODUCT — front",
                False,
                [product_image],
            ),
        ]
    result = asyncio.run(dpj._gen_cuts(
        app,
        worker_job(),
        prepared,
        {"clothingType": "top"},
        {},
    ))
    return result, r2


def test_page_output_qc_off_never_calls_judge(monkeypatch):
    calls = 0

    async def forbidden(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("page QC must not run in off mode")

    monkeypatch.setattr(dpj.page_output_qc, "judge", forbidden)
    result, r2 = _run_cuts(monkeypatch, page_qc_mode="off")

    assert calls == 0
    assert result[5] is None
    assert result[6] == []
    assert r2.saved == [b"chosen:initial:worn", b"chosen:initial:product"]


def test_page_output_qc_shadow_judges_completed_cut_collection_once(monkeypatch):
    calls = []
    page_qc = {"overall": "PASS", "gates": [], "outliers": []}

    async def recording_judge(
        settings, page_plan, generated_images, *, product_truth_refs=(),
    ):
        calls.append({
            "settings": settings,
            "plan": page_plan,
            "generated": generated_images,
            "refs": product_truth_refs,
        })
        return page_qc

    monkeypatch.setattr(dpj.page_output_qc, "judge", recording_judge)
    result, r2 = _run_cuts(monkeypatch, page_qc_mode="shadow")

    assert len(calls) == 1
    call = calls[0]
    assert [item["outputIndex"] for item in call["plan"]] == [0, 1]
    assert [item["blockId"] for item in call["plan"]] == ["worn", "product"]
    assert [item["cutType"] for item in call["plan"]] == ["styling", "product"]
    assert [item["productTruthIndexes"] for item in call["plan"]] == [[0], [0]]
    assert [image.data for image in call["generated"]] == [
        b"chosen:initial:worn",
        b"chosen:initial:product",
    ]
    assert [image.data for image in call["refs"]] == [b"PRODUCT"]
    assert result[5] == page_qc
    assert result[6] == []
    assert r2.saved == [b"chosen:initial:worn", b"chosen:initial:product"]


def test_page_output_qc_keeps_middle_failure_at_its_original_index(monkeypatch):
    captured = {}
    product_image = InlineImage("image/png", b"PRODUCT")
    prepared = [
        (
            _spec(block_id, "product", "ghost"),
            [product_image],
            "1. PRODUCT — front",
            False,
            [product_image],
        )
        for block_id in ("first", "middle", "last")
    ]

    real_judge = dpj.page_output_qc.judge

    async def recording_judge(
        settings, page_plan, generated_images, *, product_truth_refs=(),
    ):
        captured.update(plan=page_plan, images=generated_images)
        return await real_judge(
            settings,
            page_plan,
            generated_images,
            product_truth_refs=product_truth_refs,
        )

    async def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("missing-output completeness must not call the provider")

    monkeypatch.setattr(dpj.page_output_qc, "judge", recording_judge)
    monkeypatch.setattr(
        dpj.page_output_qc, "analyze_with_fallback", forbidden_provider,
    )
    result, r2 = _run_cuts(
        monkeypatch,
        page_qc_mode="shadow",
        prepared=prepared,
        fail_ids={"middle"},
    )

    assert [item["blockId"] for item in captured["plan"]] == [
        "first", "middle", "last",
    ]
    assert [item["outputIndex"] for item in captured["plan"]] == [0, 1, 2]
    assert [image.data if image is not None else None for image in captured["images"]] == [
        b"chosen:initial:first",
        None,
        b"chosen:initial:last",
    ]
    assert [item["blockId"] for item in result[0]] == ["first", "last"]
    assert result[5]["overall"] == "FAIL"
    assert result[5]["outliers"] == [{
        "blockId": "middle",
        "gate": "completeness",
        "evidence": "No generated image was mapped to output index 1.",
        "correction": "Generate this planned cut before publishing the page.",
    }]
    assert result[6] == []
    assert r2.saved == [b"chosen:initial:first", b"chosen:initial:last"]


def test_page_output_qc_maps_each_color_to_deduplicated_truth_indexes(monkeypatch):
    captured = {}
    shared = InlineImage("image/png", b"SHARED")
    red = InlineImage("image/png", b"RED")
    blue = InlineImage("image/png", b"BLUE")
    red_spec = {**_spec("red", "product", "ghost"), "colorId": "red"}
    blue_spec = {**_spec("blue", "product", "ghost"), "colorId": "blue"}
    prepared = [
        (red_spec, [red], "1. PRODUCT — red", False, [shared, red]),
        (blue_spec, [blue], "1. PRODUCT — blue", False, [blue, shared]),
    ]

    async def recording_judge(
        settings, page_plan, generated_images, *, product_truth_refs=(),
    ):
        captured.update(plan=page_plan, refs=product_truth_refs)
        return {"overall": "PASS", "gates": [], "outliers": []}

    monkeypatch.setattr(dpj.page_output_qc, "judge", recording_judge)
    result, _r2 = _run_cuts(
        monkeypatch, page_qc_mode="shadow", prepared=prepared,
    )

    assert [image.data for image in captured["refs"]] == [b"SHARED", b"RED", b"BLUE"]
    assert [item["targetColor"] for item in captured["plan"]] == ["red", "blue"]
    assert [item["productTruthIndexes"] for item in captured["plan"]] == [
        [0, 1],
        [2, 0],
    ]
    assert result[6] == []


def test_passthrough_read_failure_skips_page_qc_without_completeness_failure(monkeypatch):
    class _UnreadableR2(_RecordingR2):
        def get_bytes(self, key):
            raise RuntimeError("object unavailable")

    calls = 0

    async def forbidden(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("page QC must not run without the passthrough bytes")

    product_image = InlineImage("image/png", b"PRODUCT")
    passthrough = {
        "id": "seller-detail",
        "r2_key": "seller/detail.png",
        "mime_type": "image/png",
        "width": 100,
        "height": 200,
    }
    prepared = [(
        _spec("detail", "product", "detail"),
        [product_image],
        "1. PRODUCT — detail",
        False,
        [product_image],
        None,
        False,
        passthrough,
    )]

    monkeypatch.setattr(dpj.page_output_qc, "judge", forbidden)
    result, r2 = _run_cuts(
        monkeypatch,
        page_qc_mode="shadow",
        prepared=prepared,
        r2=_UnreadableR2(),
    )

    assert calls == 0
    assert result[0][0]["blockId"] == "detail"
    assert result[1] == []
    assert result[5] is None
    assert result[6] == [{
        "blockId": "detail",
        "code": "page_output_qc_input_unavailable",
    }]
    assert r2.saved == []


def test_unexpected_page_qc_exception_is_fail_open(monkeypatch):
    async def unexpected(*_args, **_kwargs):
        raise RuntimeError("unexpected page QC bug")

    monkeypatch.setattr(dpj.page_output_qc, "judge", unexpected)
    result, r2 = _run_cuts(monkeypatch, page_qc_mode="shadow")

    assert len(result[0]) == len(result[1]) == 2
    assert result[5] is None
    assert result[6] == [{"code": "page_output_qc_unavailable"}]
    assert r2.saved == [b"chosen:initial:worn", b"chosen:initial:product"]


def test_unexpected_cut_qc_exception_is_fail_open(monkeypatch):
    async def unexpected(*_args, **_kwargs):
        raise RuntimeError("unexpected cut QC bug")

    product_image = InlineImage("image/png", b"PRODUCT")
    prepared = [(
        _spec("only", "product", "ghost"),
        [product_image],
        "1. PRODUCT — front",
        False,
        [product_image],
    )]
    monkeypatch.setattr(dpj.cut_output_qc, "verdict", unexpected)
    result, r2 = _run_cuts(
        monkeypatch,
        page_qc_mode="off",
        cut_qc_mode="shadow",
        prepared=prepared,
    )

    assert len(result[0]) == len(result[1]) == 1
    assert result[4] == []
    assert result[6] == [{"blockId": "only", "code": "cut_output_qc_unavailable"}]
    assert r2.saved == [b"chosen:initial:only"]


def test_detail_job_persists_page_qc_in_success_metadata(monkeypatch):
    captured = {}
    page_qc = {"overall": "FAIL", "gates": [], "outliers": []}

    async def fake_project(conn, uid, pid):
        return {"copywriting": False}

    async def fake_storyboard(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "product", "shot": "ghost"}]

    async def fake_product(conn, pid):
        return {
            "colors": [{
                "isBase": True,
                "images": [{"slot": "Front", "id": "a1"}],
            }],
        }

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": "product", "mime_type": "image/png"}

    async def fake_gen_cuts(*_args, **_kwargs):
        return (
            [{"blockId": "b1", "imageUrl": "/out"}],
            [{"key": "out"}],
            0,
            [],
            [],
            page_qc,
            [],
        )

    def fake_assemble(*_args, **_kwargs):
        return []

    async def fake_finalize(conn, **kwargs):
        captured.update(kwargs)
        return {"editor_blocks": [], "available": 1}

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj, "_gen_cuts", fake_gen_cuts)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(
        gemini_api_key="x",
        r2_bucket="b",
        page_output_qc_mode="shadow",
    ))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["metadata"]["pageQc"] == page_qc
    assert "cutQc" not in captured["metadata"]
    assert "garmentQc" not in captured["metadata"]
