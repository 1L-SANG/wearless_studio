"""분석용 프로필이 운영 마네킹 경계를 넘지 않는지 검증한다."""

from copy import deepcopy
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.agents import fit_axes, mannequin, mannequin_fit_qc, mannequin_fit_edit
from app.agents.prompts import MannequinPromptContext, render_mannequin_prompt
from app.workers import mannequin_job
from app.workers import detail_page_job
from app import routes
from app import repo
from app.agents.analysis_fit import select_applied_fit_axes


def filtered(profile):
    assert hasattr(fit_axes, "mannequin_fit_profile")
    return fit_axes.mannequin_fit_profile(profile)


def test_old_profile_is_returned_verbatim_without_normalization_or_mutation():
    profile = {"version": 2, "source": "seller", "category": "top", "gender": "women",
               "axes": {"sleeve": None, "length": "basic", "fit": "regular"}, "extra": "untouched",
               "matchingFit": {"clothingId": "pants-1", "fitCategory": "pants", "axes": {"cut": "wide"}}}
    before = deepcopy(profile)
    assert filtered(profile) is profile
    assert profile == before
    assert filtered(None) is None


@pytest.mark.parametrize("category,gender,axis,value", [
    ("top", "women", "length", "slight_crop"), ("top", "men", "sleeve", "wrist"),
    ("outer", "women", "sleeve", "short"), ("dress", "women", "sleeve", "cap"),
    ("skirt", "women", "length", "knee"), ("dress", "women", "length", "floor"),
    ("pants", "men", "length", "ankle_cover"),
])
@pytest.mark.parametrize("source", ["seller", "unsure", "ai", "measure", None])
def test_analysis_scoped_values_never_reach_the_mannequin(category, gender, axis, value, source):
    profile = {"category": category, "gender": gender, "axes": {axis: value}}
    if source is not None:
        profile["axisSources"] = {axis: source}
    before = deepcopy(profile)
    out = filtered(profile)
    assert out is None if source is not None else out["axes"] == {}
    assert profile == before


@pytest.mark.parametrize("source", ["seller", "unsure", None, "ai", "measure"])
def test_old_values_only_reach_mannequins_without_analysis_provenance(source):
    profile = {"category": "top", "gender": "women", "axes": {"fit": "over", "length": "basic", "sleeve": "short"},
               "axisSources": {axis: source for axis in ("fit", "length", "sleeve")} if source else {},
               "version": 1, "matchCut": "wide"}
    out = filtered(profile)
    assert out["axes"] == (profile["axes"] if source is None else {})
    assert out["matchCut"] == "wide"
    assert "axisSources" not in out


def test_generation_qc_and_adjustment_render_the_same_legacy_targets():
    legacy = {"category": "top", "gender": "women", "axes": {"fit": "over"}, "version": 2, "source": "seller"}
    fresh = {**legacy, "axes": {"fit": "over", "length": "basic", "sleeve": "wrist"},
             "axisSources": {"length": "measure", "sleeve": "seller"}}
    assert mannequin.generation_spec({"fitProfile": fresh}) == legacy
    assert mannequin.effective_fit_profile({"fitProfile": fresh}, True) == legacy
    assert mannequin_job._fit_profile_for_match_image(fresh, True) == legacy
    assert mannequin_fit_qc.declared_axis_spec(fresh) == mannequin_fit_qc.declared_axis_spec(legacy)
    assert mannequin_fit_edit.build_adjust_directives(fresh, ("fit", "length", "sleeve")) == mannequin_fit_edit.build_adjust_directives(legacy, ("fit",))
    ctx = MannequinPromptContext(clothing_type="top", product_count=1, base_gender="women", image_manifest="1. Front product")
    template = "Dress ${baseGender} in ${clothingType}.\n${imageManifest}"
    assert render_mannequin_prompt(template, ctx, {}, {"fitProfile": fresh}) == render_mannequin_prompt(template, ctx, {}, {"fitProfile": legacy})


def test_selected_cut_and_legacy_fallback_filter_only_mannequin_profiles():
    profile = {"category": "top", "gender": "women", "axes": {"fit": "over", "length": "crop"},
               "axisSources": {"fit": "ai", "length": "ai"}}
    analysis = {"fitProfile": profile}
    for cut in (None, {}, {"fit_profile_snapshot": {"version": 1, "profile": profile}}):
        assert fit_axes.analysis_for_selected_mannequin(analysis, cut)["fitProfile"] is None
    assert fit_axes.analysis_for_selected_mannequin(analysis, None, has_mannequin=False) is analysis
    assert detail_page_job._analysis_for_selected_mannequin(analysis, None, has_mannequin=False) is analysis


def test_route_snapshot_and_cut_response_only_expose_mannequin_profile(monkeypatch):
    profile = {"category": "outer", "gender": "women", "axes": {"fit": "over", "length": "basic", "sleeve": "short"},
               "axisSources": {"fit": "ai", "length": "measure", "sleeve": "seller"}, "source": "auto", "version": 2}
    async def analysis(*args):
        return {"fitProfile": profile}
    monkeypatch.setattr(routes.repo, "get_analysis", analysis)
    snapshot = asyncio.run(routes._fit_profile_snapshot(None, "u1", "p1", profile))
    assert snapshot["profile"] is None
    assert snapshot["adjustedAxes"] == []
    cut = {"candidate": "A", "version": 1, "asset_id": "a1", "base_fit": "regular",
           "fit_adjust": None, "length_adjust": None, "match_adjust": None,
           "fit_profile_snapshot": {"version": 1, "profile": profile}}
    assert routes._cut_to_api(cut)["fitProfile"] is None


def test_each_cut_uses_the_original_profile_without_a_mannequin():
    raw = {"fitProfile": {"category": "top", "gender": "women", "axes": {"length": "crop"},
                          "axisSources": {"length": "ai"}}}
    selected = {"fitProfile": {"category": "top", "gender": "women", "axes": {"fit": "over"}}}
    assert detail_page_job._analysis_for_cut({}, selected, has_mannequin=False, no_mannequin_analysis=raw) is raw
    assert detail_page_job._analysis_for_cut({}, raw, has_mannequin=True)["fitProfile"] is None


@pytest.mark.parametrize("sources", [{"fit": "ai", "length": "seller", "sleeve": "seller"},
                                    {"fit": "ai", "length": "unsure", "sleeve": "unsure"}])
def test_analysis_only_snapshot_and_mannequin_prompt_match_main(monkeypatch, sources):
    legacy = {"fit": "regular"}
    fresh = {**legacy, "fitProfile": {"category": "top", "gender": "women", "source": "auto", "version": 2,
        "axes": {"fit": "regular", "length": "crop", "sleeve": "elbow"}, "axisSources": sources}}
    ctx = MannequinPromptContext(clothing_type="top", product_count=1, base_gender="women", image_manifest="1. Front product")
    template = "Dress ${baseGender} in ${clothingType}.\n${imageManifest}"
    for analysis in (legacy, fresh):
        monkeypatch.setattr(routes.repo, "get_analysis", AsyncMock(return_value=analysis))
        assert asyncio.run(routes._fit_profile_snapshot(None, "u1", "p1", None)) == {
            "version": 1, "profile": None, "adjustedAxes": []}
    expected = render_mannequin_prompt(template, ctx, {}, legacy)
    assert "- Fit: regular" in expected
    assert render_mannequin_prompt(template, ctx, {}, fresh) == expected


@pytest.mark.parametrize("category,gender,value", [("outer", "women", None), ("outer", "men", "unknown"), ("dress", "men", "cap")])
def test_entirely_analysis_scoped_axes_are_removed_even_without_a_known_value(category, gender, value):
    p = {"category": category, "gender": gender, "axes": {"sleeve": value}}
    assert filtered(p)["axes"] == {}


@pytest.mark.parametrize("mode", ["raw", "separate", "matching_pruned"])
def test_filtered_snapshot_preserves_a_separate_analysis_profile_for_success(monkeypatch, mode):
    p = {"category": "top", "gender": "women", "axes": {"fit": "over", "length": "slight_crop", "sleeve": "back_of_hand"},
         "axisSources": {"fit": "ai", "length": "seller", "sleeve": "ai"}, "source": "auto", "version": 2}
    if mode == "matching_pruned":
        p["matchingFit"] = {"clothingId": "gone", "fitCategory": "pants", "axes": {"cut": "wide"}}
    executed = []
    class Cursor:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def execute(self, query, params=None):
            executed.append((query, params))
            self.row = {"id": "j1"} if "select id from jobs" in query else {"v": 1}
        async def fetchone(self):
            return self.row
    class Conn:
        def cursor(self):
            return Cursor()
    async def consume(*args, **kwargs):
        return 7
    monkeypatch.setattr(repo, "_consume_buckets", consume)
    snapshot_profile = filtered(p) if mode != "raw" else p
    if mode == "matching_pruned":
        snapshot_profile = {key: value for key, value in snapshot_profile.items() if key != "matchingFit"}
    snapshot = {"version": 1, "profile": snapshot_profile, "adjustedAxes": []}
    result = asyncio.run(repo.finalize_mannequin_success(Conn(), job_id="j1", lease_token="lease", user_id="u1", project_id="p1",
        candidates=[{"asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png", "candidate": "A", "base_fit": "regular"}],
        reserved=1, charge=1, metadata={}, fit_profile_snapshot=snapshot,
        **({"analysis_fit_profile": p} if mode != "raw" else {})))
    stored = next(params[1].obj for sql, params in executed if "insert into analyses" in sql)
    expected = {key: value for key, value in p.items() if key != "matchingFit"} if mode == "matching_pruned" else p
    assert stored == expected
    assert select_applied_fit_axes(stored) == [("fit", "over", "ai"), ("length", "slight_crop", "seller"), ("sleeve", "back_of_hand", "ai")]
    metadata = next(params[-1].obj for sql, params in executed if "insert into assets" in sql)
    assert metadata["fitProfileSnapshot"]["profile"] == filtered(snapshot_profile)
    assert result["cuts"][0]["fitProfile"] == filtered(snapshot_profile)


def test_job_profile_storage_intent_is_not_silently_joined_with_a_different_source_map():
    base = {"mode": "generate", "fitProfileSnapshot": {"version": 1, "profile": {}, "adjustedAxes": []},
            "analysisFitProfile": {"axes": {"length": "crop"}, "axisSources": {"length": "seller"}}}
    incoming = {**base, "analysisFitProfile": {"axes": {"length": "crop"}, "axisSources": {"length": "ai"}}}
    assert routes._mannequin_payload_matches({"status": "pending", "payload": base}, incoming) is False


def test_saving_a_selected_mannequin_record_does_not_erase_the_analysis_profile():
    original = {"category": "top", "gender": "women", "version": 2, "source": "auto",
                "axes": {"fit": "over", "length": "slight_crop"}, "axisSources": {"fit": "ai", "length": "seller"}}
    legacy = filtered(original)
    cut = {"fit_profile_snapshot": {"version": 1, "profile": legacy}}
    assert hasattr(fit_axes, "profile_for_mannequin_selection_save")
    save_profile = fit_axes.profile_for_mannequin_selection_save
    assert save_profile(original, legacy, cut) is original
    assert save_profile(original, legacy, None) is legacy
    assert save_profile(legacy, legacy, cut) is legacy
    edited = {**original, "axes": {"fit": "slim", "length": "crop"}, "axisSources": {"fit": "seller", "length": "seller"}}
    assert save_profile(original, edited, cut) is edited
    different = {"category": "top", "gender": "women", "version": 2, "source": "auto", "axes": {"fit": "regular"}}
    assert save_profile(original, different, cut) is different


def test_analysis_save_after_mannequin_selection_retains_the_no_mannequin_inputs(client, make_token, monkeypatch):
    from test_analysis_server_owned_fields import _Conn
    from conftest import auth_headers
    original = {"category": "top", "gender": "women", "version": 2, "source": "auto",
                "axes": {"fit": "over", "length": "slight_crop"}, "axisSources": {"fit": "ai", "length": "seller"}}
    selected = {"candidate": "A", "version": 1,
                "fit_profile_snapshot": {"version": 1, "profile": filtered(original)}}
    conn = _Conn({"fitProfile": original})
    @asynccontextmanager
    async def connection(request):
        yield conn
    monkeypatch.setattr(routes, "get_conn", connection)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1", "selected_mannequin_id": "A-1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={"clothing_type": "top"}))
    monkeypatch.setattr(repo, "list_mannequin_cuts", AsyncMock(return_value=[selected]))
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token), json={"fitProfile": filtered(original)})
    assert response.status_code == 200, response.text
    assert conn.saved["fitProfile"] == original
    assert response.json()["fitProfile"] == original
