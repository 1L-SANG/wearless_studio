"""Front/back-first fabric evidence and opt-in photo-evidence prompt contracts."""

import asyncio
import contextlib
import types

import pytest

from app.agents.gemini_image import InlineImage
from app.agents.product_reference import ProductReference
from app.agents.prompts import MannequinPromptContext, _product_block, render_mannequin_prompt
from app.workers import mannequin_job
from conftest import make_settings


def _ctx() -> MannequinPromptContext:
    return MannequinPromptContext(
        clothing_type="top",
        product_count=2,
        base_gender="women",
        image_manifest="1. base\n2. product",
    )


def test_product_block_deduplicates_exact_sanitized_nonempty_union_in_order():
    block = _product_block(
        {"name": "test"},
        {
            "sellingPoints": ["  glossy   knit  ", "open knit", ""],
            "aiSuggestedPoints": ["glossy knit", "open  knit", "distinct phrase"],
        },
    )

    feature_line = next(line for line in block.splitlines() if line.startswith("- Key features:"))
    assert feature_line == "- Key features: glossy knit; open knit; distinct phrase"


def test_material_policy_defaults_to_legacy_and_photo_evidence_removes_composition_priors():
    product = {"name": "test", "clothing_type": "top"}
    analysis = {
        "materials": [
            {"name": "cotton", "ratio": 95},
            {"name": "elastane", "ratio": 5},
        ]
    }

    default = _product_block(product, analysis)
    legacy = _product_block(product, analysis, material_policy="legacy")
    evidence = _product_block(product, analysis, material_policy="photo_evidence")

    assert default == legacy
    assert "Material rendering guidance" in legacy
    assert "Material rendering guidance" not in evidence
    assert "cotton" not in evidence and "elastane" not in evidence
    assert "product photos govern color, fabric and finish" in evidence
    assert "smooth" not in evidence.lower()


def test_render_mannequin_prompt_accepts_photo_evidence_and_rejects_unknown_policy():
    template = "Render ${clothingType} ${productCount} ${baseGender}.\n${imageManifest}"
    analysis = {"materials": [{"name": "polyester", "ratio": 100}]}

    prompt = render_mannequin_prompt(
        template,
        _ctx(),
        {"name": "test"},
        analysis,
        material_policy="photo_evidence",
    )

    assert "polyester" not in prompt
    assert "product photos govern color, fabric and finish" in prompt
    assert "smooth, uniform synthetic" not in prompt
    with pytest.raises(ValueError, match="material_policy"):
        render_mannequin_prompt(
            template,
            _ctx(),
            {"name": "test"},
            analysis,
            material_policy="invented",
        )
    with pytest.raises(ValueError, match="material_policy"):
        _product_block({"name": "test"}, analysis, material_policy="invented")


class _GeminiCapture:
    def __init__(self):
        self.calls = []

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        self.calls.append({"prompt": prompt, "images": images, "size": size})
        return types.SimpleNamespace(image=b"edited", mime="image/png")


async def _no_emit(*args, **kwargs):
    return None


def _fabric_settings():
    return types.SimpleNamespace(
        mannequin_fabric_pass="on",
        mannequin_max_attempts=3,
        mannequin_image_size="2K",
        mannequin_aspect_ratio="2:3",
        model_image_high="gemini-3-pro-image",
        model_image_light="gemini-3.1-flash-image",
        model_text="gpt-5.4-mini",
    )


@pytest.mark.parametrize(
    ("refs", "expected_bytes", "expected_lines", "absent_roles"),
    [
        (
            [
                ProductReference("Back", "back-id", InlineImage("image/jpeg", b"back")),
                ProductReference("Front", "front-id", InlineImage("image/jpeg", b"front")),
            ],
            [b"cut", b"front", b"back"],
            [
                "1. Finished mannequin candidate — edit target.",
                "2. Front — garment-wide appearance and pattern scale.",
                "3. Back — garment-wide appearance and pattern scale.",
            ],
            ("Detail —", "BackDetail —"),
        ),
        (
            [
                ProductReference(
                    "BackDetail", "back-detail-id", InlineImage("image/jpeg", b"back-detail")
                ),
                ProductReference("Detail", "detail-id", InlineImage("image/jpeg", b"detail")),
                ProductReference("Front", "front-id", InlineImage("image/jpeg", b"front")),
                ProductReference("Back", "back-id", InlineImage("image/jpeg", b"back")),
            ],
            [b"cut", b"front", b"back", b"detail", b"back-detail"],
            [
                "1. Finished mannequin candidate — edit target.",
                "2. Front — garment-wide appearance and pattern scale.",
                "3. Back — garment-wide appearance and pattern scale.",
                "4. Detail — optional close-up; use only the feature visibly proved "
                "(it may be a label or hardware, not fabric).",
                "5. BackDetail — optional close-up; use only the feature visibly proved "
                "(it may be a label or hardware, not fabric).",
            ],
            (),
        ),
    ],
)
def test_fabric_provider_boundary_uses_front_back_then_optional_detail_roles(
    monkeypatch, refs, expected_bytes, expected_lines, absent_roles
):
    gemini = _GeminiCapture()
    monkeypatch.setattr(mannequin_job, "_emit", _no_emit)

    out, spent = asyncio.run(
        mannequin_job._apply_fabric_pass(
            pool=None,
            gemini=gemini,
            s=_fabric_settings(),
            job_id="j1",
            candidate="A",
            attempt=1,
            res=types.SimpleNamespace(image=b"cut", mime="image/png"),
            prod_imgs=[ref.image for ref in refs],
            product_refs=refs,
            calls_spent=0,
            has_fine_pattern=True,
            image_size="4K",
        )
    )

    assert spent is True and out.image == b"edited"
    call = gemini.calls[0]
    assert [image.data for image in call["images"]] == expected_bytes
    assert call["size"] == "4K"
    for line in expected_lines:
        assert line in call["prompt"]
    for role in absent_roles:
        assert role not in call["prompt"]


def test_fabric_legacy_unlabeled_call_keeps_every_image_without_guessing_slots(monkeypatch):
    gemini = _GeminiCapture()
    monkeypatch.setattr(mannequin_job, "_emit", _no_emit)
    prod_imgs = [
        InlineImage("image/jpeg", b"one"),
        InlineImage("image/jpeg", b"two"),
        InlineImage("image/jpeg", b"three"),
    ]

    asyncio.run(
        mannequin_job._apply_fabric_pass(
            pool=None,
            gemini=gemini,
            s=_fabric_settings(),
            job_id="j1",
            candidate="A",
            attempt=1,
            res=types.SimpleNamespace(image=b"cut", mime="image/png"),
            prod_imgs=prod_imgs,
            calls_spent=0,
            has_fine_pattern=True,
        )
    )

    call = gemini.calls[0]
    assert [image.data for image in call["images"]] == [b"cut", b"one", b"two", b"three"]
    assert call["prompt"].count("Unlabeled seller product photo") == 3
    assert "Front —" not in call["prompt"]
    assert "Back —" not in call["prompt"]


_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000020802000000"
    "fdd49a730000001349444154789c63fcffff3f0303031303180000240603"
    "015da24e880000000049454e44ae426082"
)


def test_run_candidate_carries_product_refs_into_edit_chain(monkeypatch):
    captured = {}
    refs = [
        ProductReference("Front", "front-id", InlineImage("image/png", b"front")),
        ProductReference("Back", "back-id", InlineImage("image/png", b"back")),
    ]

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            return types.SimpleNamespace(image=_PNG_1PX, mime="image/png")

    class _R2:
        def put_bytes(self, key, data, mime, cache=None):
            return None

    async def unchanged_pass(**kwargs):
        return kwargs["res"], False

    async def capture_fabric_pass(**kwargs):
        captured["product_refs"] = kwargs["product_refs"]
        return kwargs["res"], False

    async def no_series_qc(**kwargs):
        return None

    monkeypatch.setattr(mannequin_job, "_emit", _no_emit)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", unchanged_pass)
    monkeypatch.setattr(mannequin_job, "_apply_bust_pass", unchanged_pass)
    monkeypatch.setattr(mannequin_job, "_apply_fabric_pass", capture_fabric_pass)
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", no_series_qc)
    settings = make_settings(
        r2_bucket="bucket",
        mannequin_max_attempts=1,
        image_qc="off",
    )
    app = types.SimpleNamespace(
        state=types.SimpleNamespace(
            settings=settings,
            pool=_Pool(),
            r2=_R2(),
            gemini=_Gemini(),
        )
    )
    job = {
        "id": "job-1",
        "user_id": "user-1",
        "project_id": "project-1",
        "lease_token": "lease-1",
    }

    result = asyncio.run(
        mannequin_job._run_candidate(
            app=app,
            job=job,
            candidate="A",
            base_fit="regular",
            base_gender="women",
            base_img=InlineImage("image/png", b"base"),
            prod_imgs=[ref.image for ref in refs],
            product_refs=refs,
            match_img=None,
            product_count=2,
            template="Render ${clothingType} ${productCount} ${baseGender}.\n${imageManifest}",
            product={"name": "striped shirt"},
            analysis={"sellingPoints": ["stripe"]},
            clothing_type="top",
            image_manifest="1. base\n2. Front\n3. Back",
        )
    )

    assert result is not None
    assert captured["product_refs"] is refs


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


def test_run_mannequin_job_propagates_raw_product_reference_roles(monkeypatch):
    captured = {}
    slots = ("Front", "Back", "Detail", "BackDetail")

    async def get_product(conn, project_id):
        return {
            "name": "striped shirt",
            "clothing_type": "top",
            "colors": [
                {
                    "isBase": True,
                    "images": [{"id": slot.lower(), "slot": slot} for slot in slots],
                }
            ],
        }

    async def get_analysis(conn, project_id):
        return {"targetGenders": ["women"], "sellingPoints": ["pinstripe"]}

    async def get_asset_for_user(conn, user_id, asset_id):
        return {
            "id": asset_id,
            "mime_type": "image/png",
            "r2_key": f"{asset_id}.png",
        }

    async def is_job_cancelled(conn, job_id):
        return False

    async def finalize_success(conn, **kwargs):
        return {"cuts": kwargs["candidates"]}

    async def finalize_failure(conn, **kwargs):
        pytest.fail(f"unexpected failure: {kwargs}")

    async def fake_run_candidate(**kwargs):
        captured.update(kwargs)
        return {
            "asset_id": "out",
            "bucket": "bucket",
            "key": "out.png",
            "mime": "image/png",
            "size": 3,
            "width": 1,
            "height": 1,
            "candidate": kwargs["candidate"],
            "base_fit": kwargs["base_fit"],
        }

    async def no_style_refs(*args, **kwargs):
        return [], []

    for name, fn in (
        ("get_product", get_product),
        ("get_analysis", get_analysis),
        ("get_asset_for_user", get_asset_for_user),
        ("is_job_cancelled", is_job_cancelled),
        ("finalize_mannequin_success", finalize_success),
        ("finalize_mannequin_failure", finalize_failure),
    ):
        monkeypatch.setattr(mannequin_job.repo, name, fn)
    monkeypatch.setattr(mannequin_job, "_run_candidate", fake_run_candidate)
    monkeypatch.setattr(mannequin_job, "_load_style_refs", no_style_refs)
    monkeypatch.setattr(mannequin_job, "_emit", _no_emit)

    settings = make_settings(
        base_mannequin_women_asset_id="base",
        base_mannequin_men_asset_id="base-men",
        r2_bucket="bucket",
    )
    app = types.SimpleNamespace(
        state=types.SimpleNamespace(
            settings=settings,
            pool=_Pool(),
            r2=types.SimpleNamespace(get_bytes=lambda key: key.encode()),
            gemini=None,
        )
    )
    job = {
        "id": "job-1",
        "user_id": "user-1",
        "project_id": "project-1",
        "lease_token": "lease-1",
        "credits_reserved": 2,
        "payload": {"mode": "generate"},
    }

    asyncio.run(mannequin_job.run_mannequin_job(app, job))

    refs = captured["product_refs"]
    assert [ref.slot for ref in refs] == list(slots)
    assert [ref.asset_id for ref in refs] == [slot.lower() for slot in slots]
    assert captured["prod_imgs"] == [ref.image for ref in refs]
