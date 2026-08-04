"""P0 상품 참조 배선 — slot 을 끝까지 들고 가 역할·권위·관측성을 보존한다.

배경(2026-07-31 재조사): 워커가 자산을 바이트로 바꿀 때 슬롯을 버려, 당시 원단 보정이
`Front + Back + Detail` 업로드에서 Detail 을 잘라 먹었다. P0 는 `ProductReference` 로 슬롯을
배선 끝까지 보존했고, 이후 generative 원단 재생성 경로 자체가 폐기되면서(blind visual 3/3
FAIL) 순수 소스 선택 계약은 `test_composite_source_reference.py` 로 이관됐다.

여기 남는 것: adjust manifest 역할·권위 계약, 편집 경로 입력 순서·tier 가드,
fresh fallback 관측성, prompt_rendered 재현성, 워커 private API 시그니처 fence.
"""

import asyncio
import contextlib
import types

import pytest

from app import repo
from app.agents.gemini_image import InlineImage
from app.agents.mannequin_adjust import build_adjust_directives, build_adjust_manifest
from app.agents.product_reference import ProductReference, order_by_role
from app.services import product_truth as pt
from app.workers import mannequin_job as mj
from conftest import make_settings

_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c626001000000ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)

PROFILE = {
    "category": "top",
    "gender": "women",
    "source": "seller",
    "axes": {"fit": "slim"},
    "version": 1,
}


def _ref(slot, asset_id=None, data=None):
    asset_id = asset_id or f"{slot.lower()}-asset"
    return ProductReference(
        slot=slot,
        asset_id=asset_id,
        image=InlineImage("image/jpeg", data or slot.lower().encode()),
    )


# ---------- adjust manifest 역할·권위 계약 (P0-A5) ----------

def test_adjust_manifest_binds_role_and_authority_per_slot():
    """generic `product photo` 반복이 아니라 슬롯별 역할·권위를 명시한다."""
    refs = order_by_role([_ref("Front"), _ref("Back"), _ref("Detail"), _ref("Fit")])
    manifest = build_adjust_manifest(refs, True)
    lines = manifest.splitlines()

    assert [line.split(".")[0] for line in lines] == [str(i + 1) for i in range(len(lines))]
    assert lines[0].startswith("1. CURRENT CUT")
    assert "DETAIL" in lines[1] and "AUTHORITATIVE" in lines[1]
    assert "FRONT" in lines[2] and "AUTHORITATIVE" in lines[2]
    assert "BACK" in lines[3]
    assert "FIT" in lines[4] and "NEVER" in lines[4], "핏 참조를 색·패턴 기준으로 쓰면 안 된다"
    assert "MATCHING BOTTOM" in lines[5]
    assert manifest.count("product photo — identity reference for the MAIN PRODUCT") == 0


def test_adjust_manifest_marks_current_cut_as_scene_only_authority():
    """현재 컷은 장면 기준일 뿐 패턴 기준이 아니다 — 부모 컷의 틀린 패턴이 정본이 되면 안 된다."""
    manifest = build_adjust_manifest([_ref("Detail")], False)
    assert "CURRENT CUT" in manifest.splitlines()[0]
    assert "NOT" in manifest.splitlines()[0] or "not" in manifest.splitlines()[0]


def test_adjust_manifest_without_match_has_no_matching_line():
    manifest = build_adjust_manifest([_ref("Front")], False)
    assert "MATCHING BOTTOM" not in manifest
    assert len(manifest.splitlines()) == 2


def test_adjust_manifest_labels_unknown_slot_without_crashing():
    manifest = build_adjust_manifest([_ref("Mystery", "m")], False)
    assert len(manifest.splitlines()) == 2 and "MAIN PRODUCT" in manifest


# ---------- 편집 경로 입력 순서·tier 가드 (P0-A5 / P0-A6) ----------

class _Conn:
    async def commit(self):
        return None


class _FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


class _SaveR2:
    def put_bytes(self, key, data, mime, cache=None):
        return None


def _edit_candidate(monkeypatch, *, refs, product, settings_kw=None):
    """편집 경로로 `_run_candidate` 를 실제로 돌리고 gemini 호출을 기록한다."""
    emits = []

    class _Gemini:
        def __init__(self):
            self.calls = []

        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            self.calls.append({"model": model, "prompt": prompt,
                               "images": list(images), "size": size})
            return types.SimpleNamespace(image=_PNG_1PX, mime="image/png")

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append((event_type, dict(payload)))

    monkeypatch.setattr(mj, "_emit", fake_emit)
    settings = make_settings(
        r2_bucket="bucket", model_image_high="pro-test", model_image_light="flash-test",
        **(settings_kw or {}))
    gemini = _Gemini()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_FakePool(), r2=_SaveR2(), gemini=gemini))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "payload": {"mode": "regenerate"}}
    directives = build_adjust_directives(PROFILE, ("fit",))
    result = asyncio.run(mj._run_candidate(
        app=app, job=job, candidate="A", base_fit="regular", base_gender="women",
        base_img=InlineImage("image/png", b"base"), prod_refs=refs,
        match_img=InlineImage("image/png", b"match"), product_count=len(refs) + 1,
        template="unused", product=product, analysis={}, clothing_type="top",
        fit_profile=PROFILE, adjusted_axes=("fit",), fit_profile_source="payload_snapshot",
        generation_path="edit", parent_cut_img=InlineImage("image/png", b"parent"),
        adjust_directives=directives))
    assert result is not None
    return gemini, emits, directives


def test_edit_path_input_order_is_current_cut_then_detail_first_then_match(monkeypatch):
    """편집 입력 역할 순서 = 현재 컷 → Detail → Front → Back → Fit → 매칭.

    PR #72 의 parent-first 계약(현재 컷이 image 1)은 그대로 두고, 그 뒤 상품 참조만
    역할 우선순위로 정렬한다. 매니페스트 번호와 실제 이미지 순서가 어긋나면 모델이
    "2번은 Detail" 이라는 문장을 다른 사진에 적용하게 된다.
    """
    refs = [_ref("Front", "f", b"front"), _ref("Back", "b", b"back"),
            _ref("Detail", "d", b"detail"), _ref("Fit", "fit", b"fitref")]
    gemini, _emits, directives = _edit_candidate(
        monkeypatch, refs=refs, product={"name": "티"})

    call = gemini.calls[0]
    assert [i.data for i in call["images"]] == [
        b"parent", b"detail", b"front", b"back", b"fitref", b"match"]
    manifest_lines = call["prompt"].splitlines()
    detail_line = next(line for line in manifest_lines if line.startswith("2."))
    assert "DETAIL" in detail_line
    assert directives in call["prompt"]


def test_pattern_product_keeps_high_tier_even_when_adjust_tier_is_light(monkeypatch):
    """고위험(미세 패턴) 상품의 조정 편집은 `MANNEQUIN_ADJUST_TIER=image_light` 를 무시한다."""
    refs = [_ref("Detail", "d", b"detail")]
    gemini, emits, _d = _edit_candidate(
        monkeypatch, refs=refs, product={"name": "스트라이프 셔츠"},
        settings_kw={"mannequin_adjust_tier": "image_light",
                     "mannequin_pattern_image_size": "4K", "mannequin_image_size": "1K"})

    call = gemini.calls[0]
    assert call["model"] == "pro-test", "패턴 상품을 낮은 tier 로 내리면 잔줄이 뭉개진다"
    assert call["size"] == "4K", "패턴 해상도 승급도 유지돼야 한다"
    rendered = [p for e, p in emits if e == "step" and p.get("status") == "prompt_rendered"]
    assert rendered[0]["model_tier"] == "image_high"
    assert rendered[0]["pattern_tier_guard"] is True
    assert rendered[0]["image_size"] == "4K"


def test_qa_cap_keeps_pattern_edit_provider_request_at_1k(monkeypatch):
    refs = [_ref("Detail", "d", b"detail")]
    gemini, emits, _d = _edit_candidate(
        monkeypatch, refs=refs, product={"name": "스트라이프 셔츠"},
        settings_kw={"mannequin_pattern_image_size": "4K",
                     "mannequin_image_size": "1K",
                     "mannequin_image_size_cap": "1K"})

    assert gemini.calls[0]["size"] == "1K"
    rendered = [p for e, p in emits if e == "step" and p.get("status") == "prompt_rendered"]
    assert rendered[0]["image_size"] == "1K"


def test_plain_product_still_honours_light_adjust_tier(monkeypatch):
    """무지 상품은 기존 동작 그대로 — 가드가 조정 tier 분기 자체를 없애면 안 된다."""
    refs = [_ref("Front", "f", b"front")]
    gemini, emits, _d = _edit_candidate(
        monkeypatch, refs=refs, product={"name": "무지 티"},
        settings_kw={"mannequin_adjust_tier": "image_light"})
    assert gemini.calls[0]["model"] == "flash-test"
    rendered = [p for e, p in emits if e == "step" and p.get("status") == "prompt_rendered"]
    assert rendered[0]["pattern_tier_guard"] is False


def test_prompt_rendered_event_reproduces_input_roles_model_and_size(monkeypatch):
    """로그만으로 '어떤 역할의 asset 이 어떤 모델·해상도로 나갔는지' 재현 가능해야 한다."""
    refs = [_ref("Front", "f", b"front"), _ref("Detail", "d", b"detail")]
    _g, emits, _d = _edit_candidate(monkeypatch, refs=refs, product={"name": "티"})
    event = next(p for e, p in emits if e == "step" and p.get("status") == "prompt_rendered")
    assert event["product_refs"] == [
        {"slot": "Detail", "asset_id": "d"}, {"slot": "Front", "asset_id": "f"}]
    assert event["model_tier"] and event["image_size"]
    assert "front" not in str(event) and "detail-bytes" not in str(event)


def test_tier_for_job_guards_pattern_products_on_regenerate():
    off = make_settings(mannequin_tier="image_high", mannequin_adjust_tier="")
    light = make_settings(mannequin_tier="image_high", mannequin_adjust_tier="image_light")
    regen = {"payload": {"mode": "regenerate"}}
    gen = {"payload": {"mode": "generate"}}

    assert mj.tier_for_job(light, regen) == "image_light", "무지 상품은 기존 분기 유지"
    assert mj.tier_for_job(light, regen, has_fine_pattern=True) == "image_high"
    assert mj.tier_for_job(light, gen, has_fine_pattern=True) == "image_high"
    assert mj.tier_for_job(off, regen, has_fine_pattern=True) == "image_high"


def test_tier_for_job_also_guards_a_low_base_tier_on_initial_generation():
    """`MANNEQUIN_TIER` 자체가 낮게 잡혀도 패턴 상품은 내려가지 않는다.

    현재 배포는 `image_high` 기본값이라 발화하지 않지만, 가드가 조정 경로에만 걸려 있으면
    기본 tier 를 낮추는 순간 최초 생성부터 잔줄이 뭉개진다(계획 §4.2).
    """
    low_base = make_settings(mannequin_tier="image_light", mannequin_adjust_tier="")
    gen = {"payload": {"mode": "generate"}}
    assert mj.tier_for_job(low_base, gen) == "image_light", "무지 상품은 설정대로"
    assert mj.tier_for_job(low_base, gen, has_fine_pattern=True) == "image_high"
    assert mj.tier_for_job(low_base, None, has_fine_pattern=True) == "image_high"


def test_adjust_edit_tier_defaults_to_high_and_guards_pattern():
    light = make_settings(mannequin_adjust_tier="image_light")
    blank = make_settings(mannequin_adjust_tier="")
    assert mj.adjust_edit_tier(blank, has_fine_pattern=False) == "image_high"
    assert mj.adjust_edit_tier(light, has_fine_pattern=False) == "image_light"
    assert mj.adjust_edit_tier(light, has_fine_pattern=True) == "image_high"


def test_worker_private_api_call_sites_still_bind_to_their_signatures():
    """스크립트 하니스는 테스트가 아니라 CI 가 안 돌린다 — 시그니처를 AST 로 대조한다.

    실제로 이 변경에서 `scripts/prove_mannequin_axis_qc_retry.py` 가 옛 `prod_imgs=` 인자로
    남아 첫 arm 에서 `TypeError` 로 죽었다(독립 코드리뷰가 잡음). 전체 suite 가 green 이어도
    보이지 않는 부류라, 같은 사고가 조용히 재발하지 않게 여기서 고정한다.
    """
    import ast
    import inspect
    import pathlib

    targets = {
        "_run_candidate": mj._run_candidate,
        "_apply_edits": mj._apply_edits,
        "_apply_hybrid_composite": mj._apply_hybrid_composite,
    }
    server_dir = pathlib.Path(mj.__file__).resolve().parents[2]
    checked = []
    for sub in ("app", "scripts", "tests"):
        for path in sorted((server_dir / sub).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", None))
                fn = targets.get(name)
                if fn is None:
                    continue
                if any(k.arg is None for k in node.keywords):
                    continue  # `**kwargs` 전개는 정적으로 검증할 수 없다
                try:
                    inspect.signature(fn).bind(
                        *([None] * len(node.args)), **{k.arg: None for k in node.keywords})
                except TypeError as e:
                    raise AssertionError(
                        f"{path}:{node.lineno} — {name}(...) 호출이 시그니처와 안 맞는다: {e}")
                checked.append(f"{path.name}:{node.lineno}")
    assert len(checked) >= 5, f"호출부를 거의 못 찾았다 — 스캐너 고장 의심: {checked}"


# ---------- fresh fallback 관측성 (P0-A7) ----------

def _parent(**overrides):
    metadata = {
        "generationPath": "fresh", "editDepth": 0, "parentCutId": None,
        "profileCategory": "top", "profileGender": "women", "matchItemId": None,
        "promptVersion": "v1",
    }
    metadata.update(overrides)
    return {"id": "A-4", "r2_key": "parent.png", "mime_type": "image/png",
            "generation_metadata": metadata}


@pytest.mark.parametrize(
    ("case", "parent", "profile", "match_id", "expected"),
    [
        ("no_parent_cut", None, PROFILE, None, "no_parent_cut"),
        ("legacy_parent_empty_metadata",
         {**_parent(), "generation_metadata": {}}, PROFILE, None, "legacy_parent"),
        ("legacy_parent_no_metadata",
         {**_parent(), "generation_metadata": None}, PROFILE, None, "legacy_parent"),
        ("incompatible_category", _parent(profileCategory="pants"), PROFILE, None,
         "incompatible_parent"),
        ("incompatible_gender", _parent(profileGender="men"), PROFILE, None,
         "incompatible_parent"),
        ("missing_category_only", _parent(profileCategory=None), PROFILE, None,
         "incompatible_parent"),
        ("missing_gender_only", _parent(profileGender=None), PROFILE, None,
         "incompatible_parent"),
        ("unprofiled_after_first_edit",
         _parent(profileCategory=None, profileGender=None, editDepth=1), PROFILE, None,
         "incompatible_parent"),
        ("incompatible_match", _parent(matchItemId="other"), PROFILE, "match-1",
         "incompatible_parent"),
        ("edit_depth_cap", _parent(editDepth=2), PROFILE, None, "edit_depth_cap"),
        ("invalid_profile", _parent(), None, None, "invalid_fit_profile"),
        ("non_str_category", _parent(), {**PROFILE, "category": 7}, None,
         "invalid_fit_profile"),
        ("non_str_gender", _parent(), {**PROFILE, "gender": None}, None,
         "invalid_fit_profile"),
        # editDepth 가 bool/음수/비정수면 PR #72 워커가 쓴 값이 아니다 — 세대 수를 믿을 수
        # 없으므로 편집을 태우지 않고 legacy 로 센다.
        ("bool_depth", _parent(editDepth=True), PROFILE, None, "legacy_parent"),
        ("negative_depth", _parent(editDepth=-1), PROFILE, None, "legacy_parent"),
        ("non_int_depth", _parent(editDepth="1"), PROFILE, None, "legacy_parent"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_classify_parent_edit_returns_typed_reasons(case, parent, profile, match_id, expected):
    depth, reason = mj.classify_parent_edit(parent, profile, match_id)
    assert depth is None and reason == expected, case
    assert reason in mj.EDIT_FALLBACK_REASONS


def test_classify_parent_edit_returns_depth_when_eligible():
    assert mj.classify_parent_edit(_parent(editDepth=1), PROFILE, None) == (1, None)
    assert mj.classify_parent_edit(_parent(editDepth=0), PROFILE, None) == (0, None)


def test_first_unprofiled_cut_is_eligible_for_bounded_fit_adjustment():
    """최초 컷에 명시 fit profile 이 없었어도 같은 프로젝트의 첫 조정은 편집이어야 한다.

    실 QA 재현(2026-08-04): v1 은 ``profileCategory/profileGender=null`` 로 저장됐고,
    사용자가 fit=over/length=long 만 바꿨는데 부모가 incompatible 로 분류되어 v2 가 fresh
    생성됐다. 그 결과 색·패턴·카라·커프스까지 다른 옷으로 재해석됐다.
    """
    parent = _parent(profileCategory=None, profileGender=None, editDepth=0)

    assert mj.classify_parent_edit(parent, PROFILE, None) == (0, None)


def test_compatible_parent_edit_depth_still_returns_depth_only():
    """PR #72 헬퍼의 기존 계약(int|None)은 유지 — 호출부·기존 테스트를 깨지 않는다."""
    assert mj._compatible_parent_edit_depth(_parent(editDepth=1), PROFILE, None) == 1
    assert mj._compatible_parent_edit_depth(_parent(editDepth=2), PROFILE, None) is None


def _run_worker_fallback(monkeypatch, *, parent, mode="regenerate", snapshot=..., axes=("fit",),
                         match_id=None, r2_fail=False, parent_lookup_raises=False,
                         product_name="티", truth_row=None, candidate_scores=None,
                         settings_kw=None):
    calls = {"run": [], "emits": [], "deleted": [], "finalized": []}
    analysis = {"targetGenders": ["women"], "fit": "regular", "fitProfile": PROFILE}
    if match_id is not None:
        analysis["matchSelections"] = [{"role": "main", "clothingId": match_id}]
    product = {"name": product_name, "clothing_type": "top",
               "colors": [{"isBase": True, "images": [{"id": "prod", "slot": "Front"}]}]}
    evidence = {"prod": {"id": "prod", "checksum": "prod-sha", "width": 1, "height": 1,
                         "mime_type": "image/png", "source": "upload"}}
    if truth_row and truth_row.get("source_fingerprint") == "current":
        truth_row = {**truth_row, "source_fingerprint": pt.source_fingerprint(
            product, analysis, evidence)}

    async def get_product(conn, project_id):
        return product

    async def get_analysis(conn, project_id):
        return dict(analysis)

    async def get_product_truth(conn, project_id, truth_id=None):
        return truth_row

    async def list_product_truth_asset_evidence(conn, user_id, asset_ids):
        return [evidence[asset_id] for asset_id in asset_ids if asset_id in evidence]

    async def get_asset_for_user(conn, user_id, asset_id):
        return {"bw": {"id": "bw", "mime_type": "image/png", "r2_key": "bw.png"},
                "prod": {"id": "prod", "mime_type": "image/png", "r2_key": "prod.png"},
                "match-asset": {"id": "match-asset", "mime_type": "image/png",
                                "r2_key": "match.png"}}.get(asset_id)

    async def get_matching_item_asset(conn, item_id):
        return "match-asset" if item_id == match_id else None

    async def get_edit_parent(conn, user_id, project_id):
        if parent_lookup_raises:
            raise RuntimeError("db down")
        return parent

    async def fake_run_candidate(**kwargs):
        calls["run"].append(kwargs)
        return {"asset_id": "a", "bucket": "b", "key": f"k-{kwargs['candidate']}",
                "mime": "image/png", "size": 3,
                "width": 1, "height": 1, "candidate": kwargs["candidate"],
                "base_fit": kwargs["base_fit"],
                "qc_scores": (candidate_scores or {}).get(kwargs["candidate"])}

    async def finalize_success(conn, **kwargs):
        calls["finalized"].append(kwargs)
        return {"cuts": kwargs["candidates"], "available": 7}

    async def finalize_failure(conn, **kwargs):
        raise AssertionError("잡이 실패하면 안 된다")

    async def fake_emit(pool, job_id, event_type, payload):
        calls["emits"].append((event_type, dict(payload)))

    class _R2:
        def get_bytes(self, key):
            if r2_fail and key == "parent.png":
                raise RuntimeError("r2 miss")
            return {"bw.png": b"base", "prod.png": b"product", "match.png": b"match",
                    "parent.png": b"parent-cut"}[key]

        def put_bytes(self, key, data, mime, cache=None):
            return None

        def delete(self, key):
            calls["deleted"].append(key)

    for name, fn in (("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_product_truth", get_product_truth),
                     ("list_product_truth_asset_evidence", list_product_truth_asset_evidence),
                     ("get_asset_for_user", get_asset_for_user),
                     ("get_matching_item_asset", get_matching_item_asset),
                     ("get_mannequin_edit_parent", get_edit_parent),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(mj, "_run_candidate", fake_run_candidate)
    monkeypatch.setattr(mj, "_emit", fake_emit)

    settings_kw = dict(settings_kw or {})
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=make_settings(base_mannequin_women_asset_id="bw",
                               base_mannequin_men_asset_id="bm", r2_bucket="bucket",
                               mannequin_prompt_version="fresh_v1", **settings_kw),
        pool=_FakePool(), r2=_R2(), gemini=None))
    if snapshot is ...:
        snapshot = {"version": 1, "profile": PROFILE, "adjustedAxes": list(axes)}
    payload = {"mode": mode, "fitProfileSnapshot": snapshot}
    if truth_row:
        payload["truthPackageId"] = truth_row["id"]
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": payload}
    asyncio.run(mj.run_mannequin_job(app, job))
    return calls


def _fallback_events(calls):
    return [p for e, p in calls["emits"]
            if e == "step" and p.get("status") == "edit_path_fallback"]


@pytest.mark.parametrize(
    ("case", "kwargs", "expected"),
    [
        ("no_parent_cut", {"parent": None}, "no_parent_cut"),
        ("legacy_parent", {"parent": {"id": "A-4", "r2_key": "parent.png",
                                      "mime_type": "image/png", "generation_metadata": {}}},
         "legacy_parent"),
        ("incompatible_parent", {"parent": _parent(profileCategory="pants")},
         "incompatible_parent"),
        ("edit_depth_cap", {"parent": _parent(editDepth=2)}, "edit_depth_cap"),
        ("parent_asset_load_failed", {"parent": _parent(), "r2_fail": True},
         "parent_asset_load_failed"),
        ("parent_lookup_failed", {"parent": None, "parent_lookup_raises": True},
         "parent_lookup_failed"),
        ("no_adjust_directives", {"parent": _parent(), "axes": ()}, "no_adjust_directives"),
        ("invalid_fit_snapshot", {"parent": _parent(), "snapshot": {"version": 2}},
         "invalid_fit_snapshot"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_fresh_fallback_reason_is_recorded_as_typed_event(monkeypatch, case, kwargs, expected):
    """조정 요청이 fresh 로 떨어진 이유를 숨기지 않는다 — 패턴이 다시 무작위 생성된 사실의 근거."""
    calls = _run_worker_fallback(monkeypatch, **kwargs)
    assert calls["run"][0]["generation_path"] == "fresh", case

    events = _fallback_events(calls)
    assert len(events) == 1, case
    event = events[0]
    assert event["reason"] == expected, case
    assert event["reason"] in mj.EDIT_FALLBACK_REASONS
    assert event["requested_mode"] == "regenerate"
    assert "pattern_risk" in event


def test_fresh_fallback_event_aggregates_pattern_risk(monkeypatch):
    """패턴 위험도와 함께 집계 — 고위험 상품의 silent fresh 를 우선 찾아낼 수 있어야 한다."""
    plain = _run_worker_fallback(monkeypatch, parent=None, product_name="무지 티")
    assert _fallback_events(plain)[0]["pattern_risk"] is False
    striped = _run_worker_fallback(monkeypatch, parent=None, product_name="스트라이프 셔츠")
    assert _fallback_events(striped)[0]["pattern_risk"] is True


def test_eligible_edit_emits_no_fallback_event(monkeypatch):
    calls = _run_worker_fallback(monkeypatch, parent=_parent(editDepth=1))
    assert calls["run"][0]["generation_path"] == "edit"
    assert _fallback_events(calls) == []


def test_first_unprofiled_cut_uses_parent_edit_in_worker(monkeypatch):
    parent = _parent(profileCategory=None, profileGender=None, editDepth=0)
    calls = _run_worker_fallback(monkeypatch, parent=parent)

    assert calls["run"][0]["generation_path"] == "edit"
    assert calls["run"][0]["parent_cut_img"].data == b"parent-cut"
    assert _fallback_events(calls) == []


def test_bounded_adjustment_does_not_run_global_untuck_or_bust_passes(monkeypatch):
    """핏/총장 편집 뒤 전역 후처리를 돌리면 잠근 색·패턴까지 다시 생성된다."""
    calls = {"untuck": 0, "bust": 0}

    async def forbidden_untuck(**kwargs):
        calls["untuck"] += 1
        return kwargs["res"], False

    async def forbidden_bust(**kwargs):
        calls["bust"] += 1
        return kwargs["res"], False

    monkeypatch.setattr(mj, "_apply_untuck_pass", forbidden_untuck)
    monkeypatch.setattr(mj, "_apply_bust_pass", forbidden_bust)
    _edit_candidate(
        monkeypatch,
        refs=[_ref("Front", "front")],
        product={"name": "스트라이프 셔츠"},
        settings_kw={"mannequin_untuck_pass": "on", "mannequin_bust_pass": "on"},
    )

    assert calls == {"untuck": 0, "bust": 0}


def test_candidate_with_edit_path_but_empty_inputs_reports_instead_of_silently_freshing(
        monkeypatch):
    """편집 자격을 받고도 입력이 비어 오면 조용히 fresh 로 눕지 않고 사실을 남긴다.

    현재 워커 배선으로는 도달 불가한 방어 분기다. 그래도 고정하는 이유: 이 지점이 바로
    "패턴이 다시 무작위 생성됐는데 아무도 모르는" 경로라, 나중에 호출부가 하나 늘었을 때
    침묵으로 되돌아가면 안 된다.
    """
    emits = []

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            return types.SimpleNamespace(image=_PNG_1PX, mime="image/png")

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append((event_type, dict(payload)))

    monkeypatch.setattr(mj, "_emit", fake_emit)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=make_settings(r2_bucket="bucket"), pool=_FakePool(), r2=_SaveR2(),
        gemini=_Gemini()))
    result = asyncio.run(mj._run_candidate(
        app=app, job={"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
                      "payload": {"mode": "regenerate"}},
        candidate="A", base_fit="regular", base_gender="women",
        base_img=InlineImage("image/png", b"base"),
        prod_refs=[_ref("Front", "f", b"front")], match_img=None, product_count=1,
        template="Dress ${baseGender} ${clothingType}.\n${imageManifest}",
        product={"name": "티"}, analysis={}, clothing_type="top", image_manifest="1. base",
        fit_profile=PROFILE, adjusted_axes=("fit",), fit_profile_source="payload_snapshot",
        generation_path="edit", parent_cut_img=None, adjust_directives=""))

    assert result is not None, "관측만 하고 생성 자체는 계속돼야 한다(fail-open)"
    fallback = [p for e, p in emits if e == "step" and p.get("status") == "edit_path_fallback"]
    assert len(fallback) == 1
    assert fallback[0]["reason"] == "missing_edit_inputs"
    assert fallback[0]["reason"] in mj.EDIT_FALLBACK_REASONS
    rendered = [p for e, p in emits if e == "step" and p.get("status") == "prompt_rendered"]
    assert rendered[0]["generation_path"] == "fresh"


def test_initial_generation_is_not_reported_as_fallback(monkeypatch):
    """`mode=generate` 는 폴백이 아니라 정상 최초 생성 — 이벤트로 노이즈를 만들지 않는다."""
    calls = _run_worker_fallback(monkeypatch, parent=_parent(), mode="generate")
    assert calls["run"][0]["generation_path"] == "fresh"
    assert _fallback_events(calls) == []


def test_initial_generation_persists_parent_compatibility_without_explicit_fit_profile(
        monkeypatch):
    """새 fresh 컷도 다음 bounded adjustment 에 바로 쓸 category/gender를 기록한다."""
    snapshot = {"version": 1, "profile": None, "adjustedAxes": []}
    calls = _run_worker_fallback(
        monkeypatch, parent=None, mode="generate", snapshot=snapshot)

    metadata = calls["finalized"][0]["candidates"][0]["generation_metadata"]
    assert metadata["generationPath"] == "fresh"
    assert metadata["profileCategory"] == "top"
    assert metadata["profileGender"] == "women"


def test_guarded_truth_runs_two_candidates_and_finalizes_only_policy_winner(monkeypatch):
    truth = {
        "id": "truth-1", "version": 1, "status": "approved",
        "garment_spec": {"category": "shirt"},
        "color_spec": {}, "pattern_spec": {"type": "check"},
        "protected_details": {}, "source_fingerprint": "current",
    }
    scores = {
        "A": {"structuredQC": {"overallDecision": "review"},
              "outcome": "needs_review", "product_fidelity": 95,
              "physical_naturalness": 95, "image_quality": 95},
        "B": {"structuredQC": {"overallDecision": "pass"},
              "outcome": "auto_pass", "product_fidelity": 80,
              "physical_naturalness": 80, "image_quality": 80},
    }
    calls = _run_worker_fallback(
        monkeypatch, parent=None, mode="generate", truth_row=truth,
        candidate_scores=scores,
        settings_kw={"mannequin_structured_qc": "shadow"})

    assert [call["candidate"] for call in calls["run"]] == ["A", "B"]
    assert all(call["pipeline_policy"]["lane"] == "GUARDED" for call in calls["run"])
    assert calls["deleted"] == ["k-A"]
    assert [c["candidate"] for c in calls["finalized"][0]["candidates"]] == ["B"]
    selected = [payload for event, payload in calls["emits"]
                if event == "step" and payload.get("status") == "pipeline_candidate_selected"]
    assert selected == [{"status": "pipeline_candidate_selected", "lane": "GUARDED",
                         "candidateCount": 2, "selectedCandidate": "B"}]


def test_worker_rejects_stale_queued_truth_before_storage_or_provider(monkeypatch):
    calls = {"asset": 0, "r2": 0, "provider": 0, "failure": []}
    product = {"id": "prod-1", "project_id": "p1", "name": "셔츠", "clothing_type": "top",
               "colors": [{"id": "base", "isBase": True, "images": [
                   {"id": "prod-front", "slot": "Front", "label": "Front"}]}]}
    analysis = {"buttonCount": 4}
    evidence = {"prod-front": {"id": "prod-front", "checksum": "current-sha",
                               "width": 1000, "height": 1200,
                               "mime_type": "image/png", "source": "upload"}}
    truth = pt.build_truth_draft(product, analysis, evidence)
    approved = pt.approve_snapshot(truth)
    stale_fingerprint = pt.source_fingerprint(
        product, {**analysis, "buttonCount": 5}, evidence)
    assert stale_fingerprint != approved["sourceFingerprint"]
    truth_row = {
        "id": "truth-1", "version": 1, "status": "approved",
        "garment_spec": approved["garmentSpec"],
        "color_spec": approved["colorSpec"],
        "pattern_spec": approved["patternSpec"],
        "protected_details": approved["protectedDetails"],
        "source_fingerprint": stale_fingerprint,
        "source_assets": [{"asset_id": "prod-front", "role": "FRONT", "view": "Front"}],
    }

    async def get_product(conn, project_id):
        return product

    async def get_analysis(conn, project_id):
        return analysis

    async def get_product_truth(conn, project_id, truth_id=None):
        return truth_row

    async def list_evidence(conn, user_id, asset_ids):
        assert asset_ids == ["prod-front"]
        return [evidence["prod-front"]]

    async def get_asset_for_user(conn, user_id, asset_id):
        calls["asset"] += 1
        raise AssertionError("asset lookup must happen after truth freshness preflight")

    async def finalize_failure(conn, **kwargs):
        calls["failure"].append(kwargs)
        return None

    async def forbidden_provider(**kwargs):
        calls["provider"] += 1
        raise AssertionError("provider must not run for stale truth")

    class _R2:
        def get_bytes(self, key):
            calls["r2"] += 1
            raise AssertionError("R2 must not run for stale truth")

    for name, fn in (
        ("get_product", get_product),
        ("get_analysis", get_analysis),
        ("get_product_truth", get_product_truth),
        ("list_product_truth_asset_evidence", list_evidence),
        ("get_asset_for_user", get_asset_for_user),
        ("finalize_mannequin_failure", finalize_failure),
    ):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(mj, "_run_candidate", forbidden_provider)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=make_settings(base_mannequin_women_asset_id="bw",
                               base_mannequin_men_asset_id="bm",
                               enable_product_truth="shadow"),
        pool=_FakePool(), r2=_R2(), gemini=None))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": {"mode": "generate",
                                              "truthPackageId": "truth-1"}}

    asyncio.run(mj.run_mannequin_job(app, job))

    assert calls["asset"] == calls["r2"] == calls["provider"] == 0
    assert len(calls["failure"]) == 1
    failure = calls["failure"][0]
    assert failure["reserved"] == 2
    assert failure["metadata"]["error"] == "truth_stale"


def test_structured_qc_uses_detail_for_pattern_and_front_for_color_semantics(monkeypatch):
    calls = []

    def fake_run_quantitative(**kwargs):
        calls.append(kwargs)
        return [{"check": "color_fidelity", "status": "pass", "score": 0.9},
                {"check": "pattern_fidelity", "status": "pass", "score": 0.9}]

    class _R2:
        def put_bytes(self, key, data, mime, cache=None):
            return None

    monkeypatch.setattr(mj, "run_quantitative_fidelity_qc", fake_run_quantitative)
    res = types.SimpleNamespace(image=_PNG_1PX, mime="image/png")
    detail = _ref("Detail", "detail", b"detail-bytes")
    front = _ref("Front", "front", b"front-bytes")

    out = asyncio.run(mj._save_cut(
        s=make_settings(r2_bucket="bucket", mannequin_structured_qc="shadow"),
        r2=_R2(), user_id="u1", project_id="p1", job_id="j1", candidate="A",
        base_fit="regular", res=res, qc_scores={},
        product_truth={"id": "truth-1", "status": "approved",
                       "patternSpec": {"type": "STRIPE"}},
        pattern_sources=[front, detail], color_source=front,
        base_image=InlineImage("image/png", b"base-bytes")))

    assert [call["source_bytes"] for call in calls] == [b"front-bytes", b"detail-bytes"]
    assert all(call["base_bytes"] == b"base-bytes" for call in calls)
    structured = out["qc_scores"]["structuredQC"]
    assert structured["sourceSemantics"]["patternSource"] == {
        "slot": "Detail", "assetId": "detail"}
    assert structured["sourceSemantics"]["colorSource"] == {
        "slot": "Front", "assetId": "front"}
