"""Strict product button guard: independent observations and no unverified publication."""
import asyncio
from copy import deepcopy
from functools import lru_cache
from io import BytesIO
import hashlib

from PIL import Image
from types import SimpleNamespace

import pytest

from app.agents import cut_generator as cg, vision_llm
from app.agents.gemini_image import InlineImage
from conftest import make_settings


def module():
    from app.agents import product_button_qc
    return product_button_qc


def observed(count=3, *, certainty="clear", direction="front", layout=None):
    return {"certainty": certainty, "direction": direction,
            "closure_layout": layout or ("single_row" if count else "none"),
            "counts": {"closure": count, "cuffs": 0, "pockets": 0, "shoulders": 0, "other": 0},
            "buttons": [{"region": "closure", "visibility": "full"} for _ in range(count)]}


@lru_cache(maxsize=None)
def fixture_png(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    buffer = BytesIO()
    color = tuple(hashlib.sha256(data).digest()[:3])
    Image.new("RGB", (80, 160), color).save(buffer, "PNG")
    return buffer.getvalue()


def image(data=b"SOURCE"):
    return InlineImage("image/png", fixture_png(data))


def spec(**extra):
    return {"cutType": "product", "shot": "ghost", "productPresentation": "flat", "direction": "front", **extra}


def preserved():
    return {key: "pass" for key in ("garment_identity", "non_button_construction", "color_material", "silhouette", "direction", "button_design_and_placement", "text_logo")}


def install_observations(monkeypatch, observations, preservation=None):
    calls = []
    fixtures = {**observations, **{fixture_png(key): value for key, value in observations.items()}}
    async def call(settings, model, prompt, images, schema, timeout, **kwargs):
        calls.append((prompt, images, kwargs))
        if len(images) == 1:
            return deepcopy(fixtures[images[0].data])
        return deepcopy(preservation or preserved())
    monkeypatch.setattr(vision_llm, "_call_gpt", call)
    return calls


def test_single_image_inventory_is_independent_and_validated(monkeypatch):
    calls = install_observations(monkeypatch, {b"SOURCE": observed(3)})
    result = asyncio.run(module().inspect(make_settings(), image()))
    assert result["counts"]["closure"] == 3
    assert len(calls[0][1]) == 1
    assert "expected" not in calls[0][0].lower()
    assert calls[0][2]["image_detail"] == "high"


@pytest.mark.parametrize("mutation", [
    lambda x: x["counts"].update(closure=True),
    lambda x: x["counts"].update(closure=4),
    lambda x: x["buttons"][0].update(visibility="hidden"),
    lambda x: x["buttons"][0].update(region="invented"),
    lambda x: x["buttons"].append({"region": "closure", "visibility": "full"}),
    lambda x: x.update(closure_layout="invented"),
    lambda x: x.update(direction="unknown"),
    lambda x: x.update(extra="instructions"),
])
def test_malformed_inventory_fails_closed(monkeypatch, mutation):
    raw = observed(); mutation(raw)
    install_observations(monkeypatch, {b"SOURCE": raw})
    with pytest.raises(module().ProductButtonQCError):
        asyncio.run(module().inspect(make_settings(), image()))


def test_source_selection_binds_exact_manifest_position_and_direction(monkeypatch):
    calls = install_observations(monkeypatch, {b"BACK": observed(0, direction="back")})
    refs = [image(b"MOOD"), image(b"DETAIL"), image(b"FRONT"), image(b"BACK")]
    manifest = "1. MOOD — reference\n2. PRODUCT — back-side detail close-up of the garment\n3. PRODUCT — front view of the garment\n4. PRODUCT — back view of the garment"
    result = asyncio.run(module().inventory(make_settings(), spec(direction="back"), refs, manifest))
    assert result["image"].data == image(b"BACK").data
    assert result["index"] == 4
    assert calls[0][1][0].data == image(b"BACK").data


@pytest.mark.parametrize("manifest", ["1. MOOD — reference", "1. PRODUCT — front-side detail close-up of the garment", "2. PRODUCT — front view of the garment", "1. PRODUCT — back view of the garment"])
def test_missing_or_unbound_full_direction_source_rejects(monkeypatch, manifest):
    with pytest.raises(module().ProductButtonQCError):
        asyncio.run(module().inventory(make_settings(), spec(), [image()], manifest))


def test_comparison_rejects_unknown_layout_and_new_buttons_on_zero_case():
    qc = module()
    assert qc.compare(observed(0), observed(0))["passed"] is True
    assert qc.compare(observed(0), observed(1))["passed"] is False
    assert qc.compare(observed(3), observed(3, certainty="uncertain"))["passed"] is False
    assert qc.compare(observed(3), observed(3, layout="double_row"))["passed"] is False
    assert qc.compare(observed(3), observed(3, direction="back"))["passed"] is False


class Provider:
    def __init__(self, outputs):
        self.outputs = iter(fixture_png(data) for data in outputs); self.calls = []
    async def generate_content_image(self, model, prompt, images, image_size, **kwargs):
        self.calls.append((prompt, images))
        return SimpleNamespace(image=next(self.outputs), mime="image/png")


def run_generate(provider, *, outcome=None, checkpoint=None, cut=None):
    return asyncio.run(cg.generate(make_settings(gemini_api_key="x"), provider, cut or spec(),
        {"clothingType": "top"}, [image()], manifest="1. PRODUCT — front view of the garment",
        product_button_qc_outcome=outcome, base_checkpoint=checkpoint))


def test_generation_retries_once_from_originals_and_independently_rejudges(monkeypatch):
    calls = install_observations(monkeypatch, {b"SOURCE": observed(3), b"BAD": observed(4), b"FIXED": observed(3)})
    provider = Provider([b"BAD", b"FIXED"]); outcome = {}; checkpoint = cg.BaseCheckpoint()
    assert run_generate(provider, outcome=outcome, checkpoint=checkpoint) == (fixture_png(b"FIXED"), "image/png")
    assert len(provider.calls) == 2
    assert "closure=3" in provider.calls[0][0]
    assert [x.data for x in provider.calls[1][1]] == [image().data]
    assert outcome["passed"] is True and len(outcome["attempts"]) == 2
    preservation_inputs = [images for _, images, _ in calls if len(images) > 1]
    assert [[ref.data for ref in refs] for refs in preservation_inputs] == [
        [image().data, fixture_png(b"FIXED")]]
    assert outcome["retryStrategy"] == "regenerate_from_originals"
    assert checkpoint.image == (fixture_png(b"FIXED"), "image/png")
    assert [imgs[0].data for _, imgs, _ in calls if len(imgs) == 1] == [image().data, image().data, fixture_png(b"BAD"), fixture_png(b"FIXED"), fixture_png(b"FIXED")]


def test_exhausted_or_unknown_result_never_returns_or_checkpoints(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(3), b"BAD": observed(4), b"UNKNOWN": observed(3, certainty="uncertain")})
    provider = Provider([b"BAD", b"UNKNOWN"]); checkpoint = cg.BaseCheckpoint(); outcome = {}
    with pytest.raises(module().ProductButtonQCError):
        run_generate(provider, outcome=outcome, checkpoint=checkpoint)
    assert len(provider.calls) == 2 and checkpoint.image is None
    assert outcome["passed"] is False


def test_source_unknown_stops_before_image_charge(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(3, certainty="uncertain")})
    provider = Provider([b"BAD"])
    with pytest.raises(module().ProductButtonQCError): run_generate(provider)
    assert provider.calls == []


def test_repair_cannot_change_non_button_garment(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(3), b"BAD": observed(4), b"FIXED": observed(3)},
                         {**preserved(), "non_button_construction": "fail"})
    provider = Provider([b"BAD", b"FIXED"])
    with pytest.raises(module().ProductButtonQCError, match="preservation"):
        run_generate(provider)


def test_saved_base_is_rejudged_and_not_blindly_returned(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(3), b"OLD_BAD": observed(4), b"FIXED": observed(3)})
    provider = Provider([b"FIXED"])
    assert run_generate(provider, checkpoint=cg.BaseCheckpoint((b"OLD_BAD", "image/png"))) == (fixture_png(b"FIXED"), "image/png")
    assert len(provider.calls) == 1


def test_legacy_generation_does_not_activate_guard(monkeypatch):
    provider = Provider([b"LEGACY"])
    assert run_generate(provider, cut={"cutType": "product", "shot": "ghost"}) == (fixture_png(b"LEGACY"), "image/png")


def test_final_receipt_rejects_stale_result_hash():
    with pytest.raises(module().ProductButtonQCError):
        module().require_verified({"version": module().VERSION, "passed": True,
                                  "result": {"sha256": "stale"}}, image(b"NEW"))


def page_run(monkeypatch, provider, *, checkpoint_store=None):
    from app.workers import detail_page_job as worker
    from conftest import FakeR2, fake_worker_app, worker_job
    class Storage(FakeR2):
        def __init__(self): self.saved = []
        def put_bytes(self, key, data, mime, cache=None): self.saved.append(data)
    async def emit(*args, **kwargs): return None
    async def unsafe_generic(*args, **kwargs):
        return {"verdict": "FAIL", "passed": False, "gates": {}}
    monkeypatch.setattr(worker, "_emit", emit)
    monkeypatch.setattr(worker.cut_output_qc, "verdict", unsafe_generic)
    monkeypatch.setattr(worker.cut_output_qc, "repair_route", lambda result: "EDIT_STAGE1")
    monkeypatch.setattr(worker.cut_output_qc, "repair_instructions", lambda result: ("preserve garment",))
    monkeypatch.setattr(worker.cut_output_qc, "compare_repair", lambda before, after: {"accepted": True})
    async def unsafe_repair(*args, **kwargs): return b"UNCHECKED_GENERIC", "image/png"
    monkeypatch.setattr(worker.cut_generator, "repair", unsafe_repair)
    r2 = Storage(); failures = {}
    app = fake_worker_app(make_settings(gemini_api_key="x", model_detail_cut="gemini-3-pro-image",
        cut_output_qc_mode="repair", detail_cut_retry_delay_seconds=0), r2=r2, gemini=provider)
    result = asyncio.run(worker._gen_cuts(app, worker_job(),
        [(spec(id="b1"), [image()], "1. PRODUCT — front view of the garment", False, [image()])],
        {"clothingType": "top"}, {}, failed_cuts=failures, cut_checkpoints=checkpoint_store))
    return result, r2, failures


def test_page_publishes_only_verified_result_even_when_generic_qc_wants_edit(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(), b"GOOD": observed()})
    result, r2, failures = page_run(monkeypatch, Provider([b"GOOD"]))
    assert r2.saved == [fixture_png(b"GOOD")] and failures == {}
    assert result[1][0]["metadata"]["product_button_qc"]["passed"] is True


def test_page_failed_guard_never_publishes_or_retries(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(), b"BAD": observed(4), b"BAD2": observed(4)})
    provider = Provider([b"BAD", b"BAD2"])
    result, r2, failures = page_run(monkeypatch, provider)
    assert r2.saved == [] and result[1] == [] and len(provider.calls) == 2
    assert failures["b1"]["reason"] == "product_button_qc_failed"


def editor_run(monkeypatch, provider):
    from app.workers import editor_image_job as worker
    from conftest import FakeR2, fake_worker_app, worker_job
    captured = {"saved": []}
    class Storage(FakeR2):
        def get_bytes(self, key): return image().data
        def put_bytes(self, key, data, mime, cache=None): captured["saved"].append(data)
    async def product(*args):
        return {"clothingType": "top", "colors": [{"id": "base", "isBase": True,
               "images": [{"id": "source", "slot": "Front"}]}]}
    async def analysis(*args): return {}
    async def asset(*args): return {"id": "source", "r2_key": "source", "mime_type": "image/png"}
    async def failure(conn, **kwargs): captured["failure"] = kwargs
    async def success(conn, **kwargs):
        captured["success"] = kwargs
        return {"id": "wardrobe"}
    async def emit(*args, **kwargs): return None
    monkeypatch.setattr(worker.repo, "get_product", product)
    monkeypatch.setattr(worker.repo, "get_analysis", analysis)
    monkeypatch.setattr(worker.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(worker.repo, "finalize_editor_image_failure", failure)
    monkeypatch.setattr(worker.repo, "finalize_editor_image_success", success)
    monkeypatch.setattr(worker, "_emit", emit)
    app = fake_worker_app(make_settings(gemini_api_key="x", model_editor_cut="gemini-3-pro-image"),
                          r2=Storage(), gemini=provider)
    asyncio.run(worker.run_editor_image_job(app, worker_job({**spec(), "mode": "new", "colorId": "base"}, credits_reserved=7)))
    return captured


def test_editor_failed_guard_releases_credit_reservation_without_storing(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(), b"BAD": observed(4), b"BAD2": observed(4)})
    captured = editor_run(monkeypatch, Provider([b"BAD", b"BAD2"]))
    assert captured["saved"] == [] and "success" not in captured
    assert captured["failure"]["reserved"] == 7
    assert captured["failure"]["metadata"]["error"] == "product_button_qc_failed"
    assert captured["failure"]["metadata"]["productButtonQc"]["passed"] is False


def test_matching_count_with_shifted_button_row_is_rejected(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(), b"SHIFTED": observed()},
        {**preserved(), "button_design_and_placement": "fail", "text_logo": "pass"})
    with pytest.raises(module().ProductButtonQCError, match="preservation_failed"):
        run_generate(Provider([b"SHIFTED"]))


def test_page_final_checkpoint_is_rejudged_before_publication(monkeypatch):
    from app.workers.cut_checkpoints import CutCheckpointStore
    from conftest import fake_worker_app, worker_job
    settings = make_settings(gemini_api_key="x", model_detail_cut="gemini-3-pro-image")
    store = CutCheckpointStore(fake_worker_app(settings), worker_job(), identity="test", index={})
    async def load(digest, stage):
        return SimpleNamespace(image=b"OLD_BAD", mime="image/png", key="old-key", meta={"cutQc": {"passed": True}}) if stage == "final" else None
    async def save(*args, **kwargs): return "new-key"
    async def discard(keys): return None
    monkeypatch.setattr(store, "_load", load)
    monkeypatch.setattr(store, "save", save)
    monkeypatch.setattr(store, "discard", discard)
    install_observations(monkeypatch, {b"SOURCE": observed(), b"OLD_BAD": observed(4), b"FIXED": observed()})
    provider = Provider([b"FIXED"])
    result, r2, failures = page_run(monkeypatch, provider, checkpoint_store=store)
    assert r2.saved == [fixture_png(b"FIXED")] and failures == {} and len(provider.calls) == 1
    assert result[1][0]["metadata"]["product_button_qc"]["checkpointRejudged"] is True


def test_invalid_final_checkpoint_is_never_retained_as_completed(monkeypatch):
    from app.workers.cut_checkpoints import CutCheckpointStore
    from conftest import fake_worker_app, worker_job
    settings = make_settings(gemini_api_key="x", model_detail_cut="gemini-3-pro-image")
    store = CutCheckpointStore(fake_worker_app(settings), worker_job(), identity="test", index={})
    async def load(digest, stage):
        return SimpleNamespace(image=b"OLD_BAD", mime="image/png", key="old-key", meta={}) if stage == "final" else None
    async def discard(keys): return None
    monkeypatch.setattr(store, "_load", load)
    monkeypatch.setattr(store, "discard", discard)
    install_observations(monkeypatch, {b"SOURCE": observed(), b"OLD_BAD": observed(4), b"BAD2": observed(4)})
    result, r2, failures = page_run(monkeypatch, Provider([b"BAD2"]), checkpoint_store=store)
    assert r2.saved == [] and result[1] == []
    assert store.retained_final_keys(["b1"]) == {}


def test_zero_button_source_never_permits_added_button(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(0), b"BAD": observed(1), b"FIXED": observed(0)})
    provider = Provider([b"BAD", b"FIXED"])
    assert run_generate(provider) == (fixture_png(b"FIXED"), "image/png")
    assert "closure=0" in provider.calls[0][0]


def test_preservation_uncertainty_blocks_publication(monkeypatch):
    install_observations(monkeypatch, {b"SOURCE": observed(), b"GOOD": observed()},
                         {**preserved(), "text_logo": "uncertain"})
    with pytest.raises(module().ProductButtonQCError, match="preservation_failed"):
        run_generate(Provider([b"GOOD"]))


def test_vision_unavailability_never_leaks_unchecked_result(monkeypatch):
    async def unavailable(*args, **kwargs): raise vision_llm.VisionError("unavailable")
    monkeypatch.setattr(vision_llm, "_call_gpt", unavailable)
    provider = Provider([b"UNCHECKED"])
    with pytest.raises(module().ProductButtonQCError, match="qc_unavailable"):
        run_generate(provider)
    assert provider.calls == []


@pytest.mark.parametrize("cut", [
    {"cutType": "product", "shot": "detail", "productPresentation": "flat"},
    {"cutType": "horizon", "shot": "full", "productPresentation": "ghost"},
    {"cutType": "styling", "shot": "full", "productPresentation": "natural_light"},
])
def test_guard_eligibility_leaves_detail_and_worn_recipes_unchanged(cut):
    assert module().eligible(cut) is False


@pytest.mark.parametrize("receipt", [None, {"version": "product-button-v1", "passed": False}])
@pytest.mark.parametrize("worker", ["page", "editor"])
def test_worker_storage_boundary_blocks_missing_or_failed_receipt(monkeypatch, receipt, worker):
    async def unchecked(*args, **kwargs):
        if receipt is not None:
            kwargs["product_button_qc_outcome"].update(receipt)
        return b"UNCHECKED", "image/png"
    monkeypatch.setattr(cg, "generate", unchecked)
    if worker == "page":
        result, r2, failures = page_run(monkeypatch, Provider([]))
        assert r2.saved == [] and result[1] == []
        assert failures["b1"]["reason"] == "product_button_qc_receipt_invalid"
    else:
        captured = editor_run(monkeypatch, Provider([]))
        assert captured["saved"] == [] and "success" not in captured
        assert captured["failure"]["metadata"]["error"] == "product_button_qc_receipt_invalid"
        assert captured["failure"]["reserved"] == 7
