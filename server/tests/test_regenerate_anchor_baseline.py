import asyncio
import contextlib
import hashlib
import types

import numpy as np

from app import repo
from app.agents.gemini_image import InlineImage
from app.workers import mannequin_job as mj
from conftest import make_settings


def _png(v):
    import cv2

    img = np.full((80, 60, 3), v, np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


BASE = _png(210)
ANCHOR = _png(140)
SELECTED = _png(120)
PROD = _png(90)
OUT = _png(180)


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


class _R2:
    def __init__(self, seen):
        self.seen = seen

    def get_bytes(self, key):
        return {"base": BASE, "anchor": ANCHOR, "selected": SELECTED, "prod": PROD}[key]

    def put_bytes(self, key, data, mime, cache=None):
        self.seen["saved"].append({"key": key, "data": data, "mime": mime})

    def delete(self, key):
        return None


class _Gemini:
    def __init__(self, seen):
        self.seen = seen

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        self.seen["gemini"].append({"prompt": prompt, "images": list(images)})
        return types.SimpleNamespace(image=OUT, mime="image/png", latency_ms=1, usage=None)


BASELINE = {
    "id": "base-approved",
    "asset_id": "asset-anchor",
    "output_id": "out-approved",
    "generation_run_id": "run-approved",
    "r2_key": "anchor",
    "mime_type": "image/png",
}


def _run(monkeypatch, *, active_baseline=BASELINE, selected_parent=None):
    seen = {"gemini": [], "runs": [], "success": [], "failed": [], "saved": []}

    async def get_product(conn, project_id):
        return {
            "name": "shirt",
            "clothing_type": "top",
            "colors": [{"isBase": True, "images": [{"id": "asset-prod", "slot": "Front"}]}],
        }

    async def get_analysis(conn, project_id):
        return {"fitProfile": {"version": 2, "category": "top", "gender": "women"}}

    async def get_asset_for_user(conn, user_id, asset_id):
        if asset_id == "base-model":
            return {"id": asset_id, "mime_type": "image/png", "r2_key": "base"}
        return {"id": asset_id, "mime_type": "image/png", "r2_key": "prod"}

    async def get_active_baseline(conn, project_id):
        return active_baseline

    async def get_mannequin_edit_parent(conn, user_id, project_id):
        return selected_parent

    async def insert_generation_run(conn, **kw):
        seen["runs"].append(kw)

    async def update_generation_run(conn, **kw):
        return None

    async def update_generation_run_prompt_key(conn, **kw):
        return None

    async def finalize_success(conn, **kw):
        seen["success"].append(kw)
        return {"data": {"cuts": []}, "credits": 1}

    async def finalize_failure(conn, **kw):
        seen["failed"].append(kw)
        return True

    for name, fn in (
        ("get_product", get_product),
        ("get_analysis", get_analysis),
        ("get_asset_for_user", get_asset_for_user),
        ("get_active_baseline", get_active_baseline),
        ("get_mannequin_edit_parent", get_mannequin_edit_parent),
        ("insert_generation_run", insert_generation_run),
        ("update_generation_run", update_generation_run),
        ("update_generation_run_prompt_key", update_generation_run_prompt_key),
        ("finalize_mannequin_success", finalize_success),
        ("finalize_mannequin_failure", finalize_failure),
    ):
        monkeypatch.setattr(repo, name, fn)

    async def no_edit(**kw):
        return kw["res"], kw.get("p2"), kw["calls_spent"]

    monkeypatch.setattr(mj, "_emit", lambda *a, **k: _noop())
    monkeypatch.setattr(mj, "_apply_edits", no_edit)
    monkeypatch.setattr(mj, "_apply_hybrid_composite", lambda **kw: _noop_tuple(kw["res"], None))
    monkeypatch.setattr(mj, "_apply_series_qc", lambda **kw: _noop_value(None))
    monkeypatch.setattr(
        mj.qc,
        "evaluate_mannequin_qc",
        lambda _image: types.SimpleNamespace(verdict="pass", reasons=[], metrics={}),
    )

    settings = make_settings(
        r2_bucket="bucket",
        generation_run_log="shadow",
        base_mannequin_women_asset_id="base-model",
        mannequin_max_attempts=1,
        image_qc="off",
        mannequin_axis_qc="off",
        mannequin_bust_pass="off",
        mannequin_untuck_pass="off",
        mannequin_hybrid_composite="off",
    )
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_R2(seen), gemini=_Gemini(seen)))
    job = {
        "id": "job-1",
        "user_id": "user-1",
        "project_id": "project-1",
        "lease_token": "lease-1",
        "credits_reserved": 2,
        "payload": {
            "mode": "regenerate",
            "baselineId": "base-approved",
            "fitProfileSnapshot": {
                "version": 1,
                "profile": {
                    "version": 1,
                    "category": "top",
                    "gender": "women",
                    "axes": {"fit": "slim"},
                },
                "adjustedAxes": ["fit"],
            },
        },
    }
    asyncio.run(mj.run_mannequin_job(app, job))
    return seen


async def _noop():
    return None


async def _noop_tuple(*values):
    return values


async def _noop_value(value):
    return value


def test_regenerate_inserts_approved_baseline_after_canonical_base(monkeypatch):
    seen = _run(monkeypatch)

    assert seen["gemini"], "provider call was not made"
    images = seen["gemini"][0]["images"]
    assert images[0].data == BASE
    assert images[1].data == ANCHOR
    assert images[2].data == PROD

    snap = seen["runs"][0]["input_assets"]
    assert [item["role"] for item in snap[:3]] == [
        "base_mannequin",
        "approved_baseline",
        "product_reference",
    ]
    assert snap[1]["assetId"] == "asset-anchor"
    assert snap[1]["outputId"] == "out-approved"
    assert snap[1]["sha256"] == hashlib.sha256(ANCHOR).hexdigest()
    prompt = seen["gemini"][0]["prompt"]
    assert "IMAGE 1 remains the canonical mannequin base" in prompt
    assert "IMAGE 2 is the approved front baseline" in prompt
    assert "Use it only as a garment identity anchor" in prompt


def test_regenerate_anchor_lineage_points_to_approved_baseline(monkeypatch):
    seen = _run(monkeypatch)

    candidate = seen["success"][0]["candidates"][0]
    lineage = candidate["generation_lineage"]
    assert lineage["parent_output_id"] == "out-approved"
    assert lineage["baseline_id"] == "base-approved"
    assert lineage["transformation"]["anchorBaseline"] == {
        "role": "approved_front_baseline",
    }


def test_explicit_anchor_wins_over_selected_cut_edit_fallback(monkeypatch):
    selected_parent = {
        "id": "selected-cut",
        "asset_id": "asset-selected",
        "generation_output_id": "out-selected",
        "generation_run_id": "run-selected",
        "baseline_id": None,
        "r2_key": "selected",
        "mime_type": "image/png",
        "generation_metadata": {
            "generationPath": "fresh",
            "editDepth": 0,
            "profileCategory": "top",
            "profileGender": "women",
            "matchItemId": None,
        },
    }
    seen = _run(monkeypatch, selected_parent=selected_parent)

    images = seen["gemini"][0]["images"]
    assert [image.data for image in images[:3]] == [BASE, ANCHOR, PROD]
    assert SELECTED not in [image.data for image in images]
    assert [item["role"] for item in seen["runs"][0]["input_assets"][:3]] == [
        "base_mannequin",
        "approved_baseline",
        "product_reference",
    ]
    lineage = seen["success"][0]["candidates"][0]["generation_lineage"]
    assert lineage["parent_output_id"] == "out-approved"
    assert lineage["baseline_id"] == "base-approved"


def test_regenerate_rechecks_stale_anchor_before_provider_call(monkeypatch):
    seen = _run(monkeypatch, active_baseline={**BASELINE, "id": "base-new"})

    assert seen["gemini"] == []
    assert seen["success"] == []
    assert seen["failed"][0]["metadata"]["error"] == "baseline_changed"
