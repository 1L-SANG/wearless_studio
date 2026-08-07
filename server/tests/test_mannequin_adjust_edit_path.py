"""마네킹 regenerate 편집 경로: 자격 폴백, 입력 순서, 모델, 메타데이터, 관측 이벤트."""

import asyncio
import contextlib
import types

import pytest

from app import repo
from app.agents import mannequin_fit_qc
from app.agents.gemini_image import InlineImage
from app.agents.mannequin_adjust import (
    ADJUST_PROMPT_VERSION,
    build_adjust_directives,
    build_adjust_manifest,
    render_adjust_prompt,
)
from app.workers import mannequin_job
from conftest import make_settings


_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000020802000000"
    "fdd49a730000001349444154789c63fcffff3f0303031303180000240603"
    "015da24e880000000049454e44ae426082"
)
PROFILE = {
    "category": "top",
    "gender": "women",
    "source": "seller",
    "axes": {"fit": "slim"},
    "version": 1,
}


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
    def __init__(self):
        self.reads = []

    def get_bytes(self, key):
        self.reads.append(key)
        return {
            "bw.png": b"base",
            "prod.png": b"product",
            "match.png": b"match",
            "parent.png": b"parent-cut",
        }[key]

    def put_bytes(self, key, data, mime, cache=None):
        return None


def _parent(**metadata_overrides):
    metadata = {
        "generationPath": "fresh",
        "editDepth": 0,
        "parentCutId": None,
        "profileCategory": "top",
        "profileGender": "women",
        "matchItemId": None,
        "promptVersion": "v1",
    }
    metadata.update(metadata_overrides)
    return {
        "id": "A-4",
        "r2_key": "parent.png",
        "mime_type": "image/png",
        "generation_metadata": metadata,
    }


def _run_worker(
    monkeypatch,
    *,
    mode="regenerate",
    snapshot=None,
    parent=None,
    adjusted_axes=("fit",),
    current_match_id=None,
):
    calls = {"run": [], "success": [], "failure": [], "emits": [], "parent_lookup": 0}
    analysis = {
        "targetGenders": ["women"],
        "fit": "regular",
        "fitProfile": PROFILE,
    }
    if current_match_id is not None:
        analysis["matchSelections"] = [{"role": "main", "clothingId": current_match_id}]

    async def get_product(conn, project_id):
        return {
            "name": "티",
            "clothing_type": "top",
            "colors": [{"isBase": True, "images": [{"id": "prod", "slot": "Front"}]}],
        }

    async def get_analysis(conn, project_id):
        return dict(analysis)

    async def get_asset_for_user(conn, user_id, asset_id):
        mapping = {
            "bw": {"id": "bw", "mime_type": "image/png", "r2_key": "bw.png"},
            "prod": {"id": "prod", "mime_type": "image/png", "r2_key": "prod.png"},
            "match-asset": {
                "id": "match-asset", "mime_type": "image/png", "r2_key": "match.png"
            },
        }
        return mapping.get(asset_id)

    # 커스텀 매칭 도입으로 소유권(user_id·project_id) 인자가 붙었다 — 큐레이션과 남의
    # 커스텀을 같은 조회로 받지 않기 위한 것이라, 스텁도 같은 시그니처를 받아야 한다.
    async def get_matching_item_asset(conn, item_id, user_id, project_id):
        return "match-asset" if item_id == current_match_id else None

    async def get_edit_parent(conn, user_id, project_id):
        calls["parent_lookup"] += 1
        return parent

    async def fake_run_candidate(**kwargs):
        calls["run"].append(kwargs)
        return {
            "asset_id": "asset-new",
            "bucket": "bucket",
            "key": "new.png",
            "mime": "image/png",
            "size": 3,
            "width": 1,
            "height": 1,
            "candidate": kwargs["candidate"],
            "base_fit": kwargs["base_fit"],
        }

    async def finalize_success(conn, **kwargs):
        calls["success"].append(kwargs)
        return {"cuts": kwargs["candidates"], "available": 7}

    async def finalize_failure(conn, **kwargs):
        calls["failure"].append(kwargs)
        return True

    async def fake_emit(pool, job_id, event_type, payload):
        calls["emits"].append((event_type, dict(payload)))

    for name, fn in (
        ("get_product", get_product),
        ("get_analysis", get_analysis),
        ("get_asset_for_user", get_asset_for_user),
        ("get_matching_item_asset", get_matching_item_asset),
        ("get_mannequin_edit_parent", get_edit_parent),
        ("finalize_mannequin_success", finalize_success),
        ("finalize_mannequin_failure", finalize_failure),
    ):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(mannequin_job, "_run_candidate", fake_run_candidate)
    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)

    settings = make_settings(
        base_mannequin_women_asset_id="bw",
        base_mannequin_men_asset_id="bm",
        r2_bucket="bucket",
        mannequin_prompt_version="fresh_v1",
    )
    r2 = _R2()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=r2, gemini=None))
    if snapshot is None:
        snapshot = {
            "version": 1,
            "profile": PROFILE,
            "adjustedAxes": list(adjusted_axes),
        }
    job = {
        "id": "j1",
        "user_id": "u1",
        "project_id": "p1",
        "lease_token": "u1:t",
        "credits_reserved": 2,
        "payload": {"mode": mode, "fitProfileSnapshot": snapshot},
    }
    asyncio.run(mannequin_job.run_mannequin_job(app, job))
    assert calls["failure"] == []
    assert len(calls["run"]) == 1 and len(calls["success"]) == 1
    return calls, r2


def test_regenerate_with_compatible_parent_uses_edit_and_increments_metadata(monkeypatch):
    calls, r2 = _run_worker(monkeypatch, parent=_parent(editDepth=1))
    run = calls["run"][0]
    assert run["generation_path"] == "edit"
    assert run["parent_cut_img"].data == b"parent-cut"
    assert run["adjust_directives"] == build_adjust_directives(PROFILE, ("fit",))
    assert "parent.png" in r2.reads
    metadata = calls["success"][0]["candidates"][0]["generation_metadata"]
    assert metadata == {
        "generationPath": "edit",
        "editDepth": 2,
        "parentCutId": "A-4",
        "profileCategory": "top",
        "profileGender": "women",
        "matchItemId": None,
        "promptVersion": ADJUST_PROMPT_VERSION,
    }


@pytest.mark.parametrize(
    ("case", "parent", "adjusted_axes", "mode", "snapshot", "current_match_id"),
    [
        ("no_parent", None, ("fit",), "regenerate", None, None),
        ("parent_asset_unreadable", {**_parent(), "r2_key": "missing.png"}, ("fit",),
         "regenerate", None, None),
        ("legacy_parent", {**_parent(), "generation_metadata": {}}, ("fit",),
         "regenerate", None, None),
        ("category_mismatch", _parent(profileCategory="pants"), ("fit",),
         "regenerate", None, None),
        ("gender_mismatch", _parent(profileGender="men"), ("fit",),
         "regenerate", None, None),
        ("match_mismatch", _parent(matchItemId="other"), ("fit",),
         "regenerate", None, "match-1"),
        ("depth_cap", _parent(editDepth=2), ("fit",), "regenerate", None, None),
        ("empty_directives", _parent(), (), "regenerate", None, None),
        ("generate_mode", _parent(), ("fit",), "generate", None, None),
        ("invalid_snapshot", _parent(), ("fit",), "regenerate",
         {"version": 2, "profile": "bad"}, None),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_ineligible_edit_cases_fall_back_fresh(
    monkeypatch, case, parent, adjusted_axes, mode, snapshot, current_match_id
):
    calls, _ = _run_worker(
        monkeypatch,
        mode=mode,
        snapshot=snapshot,
        parent=parent,
        adjusted_axes=adjusted_axes,
        current_match_id=current_match_id,
    )
    run = calls["run"][0]
    assert run["generation_path"] == "fresh", case
    assert run["parent_cut_img"] is None, case
    metadata = calls["success"][0]["candidates"][0]["generation_metadata"]
    assert metadata["generationPath"] == "fresh"
    assert metadata["editDepth"] == 0
    assert metadata["parentCutId"] is None
    assert metadata["promptVersion"] == "fresh_v1"


def test_match_compatible_parent_can_edit_and_persists_resolved_match(monkeypatch):
    calls, _ = _run_worker(
        monkeypatch,
        parent=_parent(matchItemId="match-1"),
        current_match_id="match-1",
    )
    assert calls["run"][0]["generation_path"] == "edit"
    metadata = calls["success"][0]["candidates"][0]["generation_metadata"]
    assert metadata["matchItemId"] == "match-1"


def test_edit_candidate_uses_parent_first_pro_model_adjust_prompt_axis_qc_and_safe_event(
    monkeypatch,
):
    emits = []
    judged = []

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append((event_type, dict(payload)))

    async def fake_axis_verdict(settings, products, generated, fit_profile, match_image=None):
        judged.append(generated.data)
        return {
            "identityPass": True,
            "mismatches": [],
            "axisPass": [{
                "axis": "fit",
                "target": "slim",
                "pass": True,
                "visible": True,
                "observedLandmark": "target visible",
            }],
        }

    class _Gemini:
        def __init__(self):
            self.calls = []

        async def generate_content_image(
            self, model, prompt, images, size, temperature=None, aspect_ratio=None
        ):
            self.calls.append({
                "model": model,
                "prompt": prompt,
                "images": list(images),
                "size": size,
                "aspect_ratio": aspect_ratio,
            })
            return types.SimpleNamespace(image=_PNG_1PX, mime="image/png")

    class _SaveR2:
        def put_bytes(self, key, data, mime, cache=None):
            return None

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_fit_qc, "verdict", fake_axis_verdict)
    gemini = _Gemini()
    settings = make_settings(
        r2_bucket="bucket",
        mannequin_axis_qc="shadow",
        mannequin_tier="image_light",
        mannequin_adjust_tier="",
        model_image_light="flash-test",
        model_image_high="pro-test",
        mannequin_prompt_version="fresh_v1",
    )
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_SaveR2(), gemini=gemini))
    directives = build_adjust_directives(PROFILE, ("fit",))
    parent = InlineImage("image/png", b"parent")
    product = InlineImage("image/png", b"product")
    match = InlineImage("image/png", b"match")

    result = asyncio.run(mannequin_job._run_candidate(
        app=app,
        job={
            "id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
            "payload": {"mode": "regenerate"},
        },
        candidate="A",
        base_fit="regular",
        base_gender="women",
        base_img=InlineImage("image/png", b"fresh-base"),
        prod_imgs=[product],
        match_img=match,
        product_count=2,
        template="unused fresh template",
        product={"name": "티"},
        analysis={},
        clothing_type="top",
        fit_profile=PROFILE,
        adjusted_axes=("fit",),
        fit_profile_source="payload_snapshot",
        generation_path="edit",
        parent_cut_img=parent,
        adjust_directives=directives,
        ref_imgs=(InlineImage("image/png", b"style-ref-must-not-be-sent"),),
    ))

    assert result is not None
    assert len(gemini.calls) == 1
    call = gemini.calls[0]
    assert call["model"] == "pro-test"  # 빈 adjust tier도 image_high로 강제 해석
    assert [image.data for image in call["images"]] == [b"parent", b"product", b"match"]
    assert call["prompt"] == render_adjust_prompt(
        directives, build_adjust_manifest(1, True))
    assert judged == [_PNG_1PX]  # 편집 경로의 최초 출력에도 기존 axis QC가 실행됨

    rendered = [
        payload for event_type, payload in emits
        if event_type == "step" and payload.get("status") == "prompt_rendered"
    ]
    assert len(rendered) == 1
    event = rendered[0]
    assert event["generation_path"] == "edit"
    assert event["prompt_version"] == ADJUST_PROMPT_VERSION
    assert "prompt" not in event and "raw_prompt" not in event
    assert directives not in str(event)


def test_finalize_persists_generation_metadata_in_asset_jsonb(monkeypatch):
    executed = []

    class _Cursor:
        def __init__(self):
            self.row = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, query, params=None):
            executed.append((query, params))
            if "select id from jobs" in query:
                self.row = {"id": "j1"}
            elif "select coalesce(max(version)" in query:
                self.row = {"v": 1}
            else:
                self.row = None

        async def fetchone(self):
            row, self.row = self.row, None
            return row

    class _RepoConn:
        def cursor(self):
            return _Cursor()

    async def fake_consume(*args, **kwargs):
        return 7

    monkeypatch.setattr(repo, "_consume_buckets", fake_consume)
    metadata = {
        "generationPath": "edit",
        "editDepth": 1,
        "parentCutId": "A-1",
        "profileCategory": "top",
        "profileGender": "women",
        "matchItemId": None,
        "promptVersion": ADJUST_PROMPT_VERSION,
    }
    result = asyncio.run(repo.finalize_mannequin_success(
        _RepoConn(),
        job_id="j1",
        lease_token="lease",
        user_id="u1",
        project_id="p1",
        candidates=[{
            "asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 3, "width": 1, "height": 1, "candidate": "A",
            "base_fit": "regular", "generation_metadata": metadata,
        }],
        reserved=2,
        charge=2,
        metadata={},
    ))
    assert result is not None
    asset_insert = next((query, params) for query, params in executed if "insert into assets" in query)
    assert "metadata" in asset_insert[0]
    assert asset_insert[1][-1].obj == metadata
