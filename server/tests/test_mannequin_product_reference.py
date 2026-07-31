"""P0 상품 참조 배선 — slot 을 끝까지 들고 가서 원단 보정이 Detail 을 잃지 않게.

배경(2026-07-31 재조사): 원단 2패스는 `prod_imgs[:2]` 로 상품 사진 앞 두 장을 골랐다.
실제 슬롯 순서는 `Front → Back → Detail → Fit` 이라, 셀러가 정상적으로 `Front + Back + Detail`
을 올리면 **패턴의 기준인 Detail 이 잘려 나가고 Front + Back 만** 편집 입력으로 들어갔다.
프롬프트(`prompts/mannequin_fabric_v1.txt`)는 Detail 을 패턴 스케일의 기준으로 지목하는데
그 이미지가 없으니, 모델이 패턴을 "비슷한 셔츠"로 다시 그려도 막을 근거가 없었다.

기존 테스트(test_mannequin_series_qc_wiring.py)는 전달 배열의 **개수**만 확인해서 이 결함을
통과시켰다. 여기서는 슬롯·자산 id·순서를 직접 본다.
"""

import asyncio
import contextlib
import types

import pytest

from app import repo
from app.agents.gemini_image import InlineImage
from app.agents.mannequin_adjust import build_adjust_directives, build_adjust_manifest
from app.agents.product_reference import (
    ProductReference,
    order_by_role,
    reference_event_payload,
    select_fabric_references,
)
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


# ---------- 순수 선택 계약 (P0-A2 / P0-A3) ----------

def test_product_reference_keeps_slot_asset_id_and_image():
    """참조는 bytes 가 아니라 (slot, asset_id, image) 로 다닌다 — 배선 도중 역할이 사라지지 않게."""
    image = InlineImage("image/jpeg", b"bytes")
    ref = ProductReference(slot="Detail", asset_id="a-1", image=image)
    assert (ref.slot, ref.asset_id, ref.image) == ("Detail", "a-1", image)
    with pytest.raises(Exception):  # frozen — 중간 단계가 역할을 덮어쓰지 못한다
        ref.slot = "Front"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("case", "slots", "expected"),
    [
        ("front_back_detail_fit", ["Front", "Back", "Detail", "Fit"], ["Detail", "Front"]),
        ("front_back_detail", ["Front", "Back", "Detail"], ["Detail", "Front"]),
        ("front_back", ["Front", "Back"], ["Front", "Back"]),
        ("front_only", ["Front"], ["Front"]),
        ("detail_only", ["Detail"], ["Detail"]),
        ("back_detail", ["Back", "Detail"], ["Detail", "Back"]),
        ("fit_front", ["Fit", "Front"], ["Front", "Fit"]),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_select_fabric_references_orders_detail_front_back_fit(case, slots, expected):
    """`Detail → Front → Back → Fit` 우선순위로 최대 2개. 입력 슬롯 순서와 무관하다."""
    selected = select_fabric_references([_ref(s) for s in slots])
    assert [r.slot for r in selected] == expected, case
    assert len(selected) <= 2


def test_select_fabric_references_puts_detail_first_whenever_present():
    """Detail 이 있으면 **항상** 첫 번째 상품 참조 — 패턴 색·간격의 기준이라 순서가 곧 권위다."""
    for slots in (
        ["Front", "Detail"], ["Front", "Back", "Detail"], ["Front", "Back", "Detail", "Fit"],
        ["Fit", "Back", "Detail"], ["Detail", "Front", "Back", "Fit"],
    ):
        selected = select_fabric_references([_ref(s) for s in slots])
        assert selected[0].slot == "Detail", slots


def test_select_fabric_references_dedupes_the_same_asset_across_slots():
    """같은 asset 이 여러 슬롯에 걸려 있으면 **한 번만** 전달한다.

    같은 바이트를 두 번 넣으면 편집 입력의 두 자리를 한 사진이 차지해, 실제로 다른 각도를
    보여줄 두 번째 참조가 밀려난다.
    """
    refs = [
        _ref("Front", "same-asset", b"one"),
        _ref("Detail", "same-asset", b"one"),
        _ref("Back", "back-asset", b"back"),
    ]
    selected = select_fabric_references(refs)
    assert [r.asset_id for r in selected] == ["same-asset", "back-asset"]
    assert [r.slot for r in selected] == ["Detail", "Back"], "중복은 더 높은 우선순위 슬롯으로 남는다"
    assert len({r.asset_id for r in selected}) == len(selected)


def test_select_fabric_references_respects_limit_and_empty_input():
    assert select_fabric_references([]) == ()
    refs = [_ref(s) for s in ("Front", "Back", "Detail", "Fit")]
    assert [r.slot for r in select_fabric_references(refs, limit=3)] == ["Detail", "Front", "Back"]
    assert [r.slot for r in select_fabric_references(refs, limit=1)] == ["Detail"]


def test_order_by_role_keeps_unknown_slots_last_and_stable():
    """알 수 없는 슬롯은 버리지 않고 뒤로 — 새 슬롯이 생겨도 참조가 조용히 사라지지 않는다."""
    refs = [_ref("Mystery", "m1"), _ref("Front", "f"), _ref("Other", "o1"), _ref("Detail", "d")]
    assert [r.asset_id for r in order_by_role(refs)] == ["d", "f", "m1", "o1"]


def test_reference_event_payload_is_metadata_only():
    """이벤트에 남는 것은 slot/asset_id/priority 뿐 — 이미지 바이트·base64·URL 은 없다."""
    refs = [_ref("Front", "f", b"front-bytes"), _ref("Detail", "d", b"detail-bytes")]
    selected = select_fabric_references(refs)
    payload = reference_event_payload(selected, all_refs=refs)
    assert payload["refs"] == [
        {"slot": "Detail", "asset_id": "d", "priority": 1},
        {"slot": "Front", "asset_id": "f", "priority": 2},
    ]
    assert payload["detail_missing"] is False
    blob = str(payload)
    assert "front-bytes" not in blob and "detail-bytes" not in blob


def test_reference_event_payload_flags_missing_detail():
    refs = [_ref("Front", "f"), _ref("Back", "b")]
    payload = reference_event_payload(select_fabric_references(refs), all_refs=refs)
    assert payload["detail_missing"] is True
    assert [r["slot"] for r in payload["refs"]] == ["Front", "Back"]


# ---------- 원단 2패스 배선 (P0-A3 / P0-A4) ----------

def _fabric_settings(**kw):
    return make_settings(
        mannequin_fabric_pass="on", mannequin_max_attempts=3, mannequin_image_size="2K",
        mannequin_aspect_ratio="2:3", **kw)


def _run_fabric(refs, *, monkeypatch, calls_spent=0, fail=False):
    sent = {"events": []}

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            sent["images"] = list(images)
            sent["prompt"] = prompt
            if fail:
                raise RuntimeError("refused")
            return types.SimpleNamespace(image=b"edited", mime="image/png")

    async def fake_emit(pool, job_id, et, payload):
        sent["events"].append(dict(payload))

    monkeypatch.setattr(mj, "_emit", fake_emit)
    res = types.SimpleNamespace(image=b"cut", mime="image/png")
    out, spent = asyncio.run(mj._apply_fabric_pass(
        pool=None, gemini=_Gemini(), s=_fabric_settings(), job_id="j1", candidate="A",
        attempt=1, res=res, prod_refs=refs, calls_spent=calls_spent,
        has_fine_pattern=True, image_size="4K"))
    return out, spent, sent


def test_fabric_pass_sends_detail_first_not_the_first_two_uploads(monkeypatch):
    """`Front + Back + Detail` 에서 편집 입력은 [생성본, Detail, Front] 다.

    수정 전에는 `prod_imgs[:2]` 라 [생성본, Front, Back] 이 나가 Detail 이 통째로 빠졌다.
    """
    refs = [_ref("Front", "f", b"front"), _ref("Back", "b", b"back"), _ref("Detail", "d", b"detail")]
    out, spent, sent = _run_fabric(refs, monkeypatch=monkeypatch)

    assert spent is True and out.image == b"edited"
    assert [i.data for i in sent["images"]] == [b"cut", b"detail", b"front"]
    assert sent["images"][1].data == b"detail", "Detail 이 첫 번째 상품 참조여야 한다"


def test_fabric_pass_event_records_selected_slot_id_priority_and_detail_presence(monkeypatch):
    refs = [_ref("Front", "f", b"front"), _ref("Back", "b", b"back"), _ref("Detail", "d", b"detail")]
    _out, _spent, sent = _run_fabric(refs, monkeypatch=monkeypatch)

    applied = [e for e in sent["events"] if e.get("status") == "fabric_pass"
               and e.get("outcome") == "applied"]
    assert len(applied) == 1
    event = applied[0]
    assert event["refs"] == [
        {"slot": "Detail", "asset_id": "d", "priority": 1},
        {"slot": "Front", "asset_id": "f", "priority": 2},
    ]
    assert event["detail_missing"] is False


def test_fabric_pass_event_records_detail_missing_when_seller_has_none(monkeypatch):
    refs = [_ref("Front", "f", b"front"), _ref("Back", "b", b"back")]
    _out, _spent, sent = _run_fabric(refs, monkeypatch=monkeypatch)
    event = next(e for e in sent["events"] if e.get("status") == "fabric_pass")
    assert event["detail_missing"] is True
    assert [r["slot"] for r in event["refs"]] == ["Front", "Back"]


def test_fabric_pass_events_never_carry_image_bytes_or_urls(monkeypatch):
    """민감 데이터 비기록 — 바이트·base64·서명 URL·R2 키가 이벤트에 들어가면 안 된다."""
    import base64

    secret = b"detail-pattern-bytes"
    refs = [_ref("Detail", "d", secret), _ref("Front", "f", b"front")]
    for fail in (False, True):
        _out, _spent, sent = _run_fabric(refs, monkeypatch=monkeypatch, fail=fail)
        blob = str(sent["events"])
        assert secret.decode() not in blob
        assert base64.b64encode(secret).decode() not in blob
        assert "http://" not in blob and "https://" not in blob
        assert ".png" not in blob and ".jpg" not in blob


def test_fabric_pass_budget_exhausted_still_reports_detail_presence(monkeypatch):
    refs = [_ref("Front", "f", b"front")]
    out, spent, sent = _run_fabric(refs, monkeypatch=monkeypatch, calls_spent=3)
    assert spent is False and out.image == b"cut"
    event = next(e for e in sent["events"] if e.get("status") == "fabric_pass")
    assert event["outcome"] == "budget_exhausted"
    assert event["detail_missing"] is True


def test_fabric_pass_gate_uses_refs_as_product_evidence(monkeypatch):
    """참조가 하나도 없으면 돌지 않는다(근거 없는 편집 금지) — 게이트가 refs 를 본다."""
    out, spent, sent = _run_fabric([], monkeypatch=monkeypatch)
    assert spent is False and out.image == b"cut"
    assert not [e for e in sent["events"] if e.get("status") == "fabric_pass"]


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


def test_adjust_edit_tier_defaults_to_high_and_guards_pattern():
    light = make_settings(mannequin_adjust_tier="image_light")
    blank = make_settings(mannequin_adjust_tier="")
    assert mj.adjust_edit_tier(blank, has_fine_pattern=False) == "image_high"
    assert mj.adjust_edit_tier(light, has_fine_pattern=False) == "image_light"
    assert mj.adjust_edit_tier(light, has_fine_pattern=True) == "image_high"


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
        ("incompatible_match", _parent(matchItemId="other"), PROFILE, "match-1",
         "incompatible_parent"),
        ("edit_depth_cap", _parent(editDepth=2), PROFILE, None, "edit_depth_cap"),
        ("invalid_profile", _parent(), None, None, "invalid_fit_profile"),
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


def test_compatible_parent_edit_depth_still_returns_depth_only():
    """PR #72 헬퍼의 기존 계약(int|None)은 유지 — 호출부·기존 테스트를 깨지 않는다."""
    assert mj._compatible_parent_edit_depth(_parent(editDepth=1), PROFILE, None) == 1
    assert mj._compatible_parent_edit_depth(_parent(editDepth=2), PROFILE, None) is None


def _run_worker_fallback(monkeypatch, *, parent, mode="regenerate", snapshot=..., axes=("fit",),
                         match_id=None, r2_fail=False, parent_lookup_raises=False,
                         product_name="티"):
    calls = {"run": [], "emits": []}
    analysis = {"targetGenders": ["women"], "fit": "regular", "fitProfile": PROFILE}
    if match_id is not None:
        analysis["matchSelections"] = [{"role": "main", "clothingId": match_id}]

    async def get_product(conn, project_id):
        return {"name": product_name, "clothing_type": "top",
                "colors": [{"isBase": True, "images": [{"id": "prod", "slot": "Front"}]}]}

    async def get_analysis(conn, project_id):
        return dict(analysis)

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
        return {"asset_id": "a", "bucket": "b", "key": "k", "mime": "image/png", "size": 3,
                "width": 1, "height": 1, "candidate": kwargs["candidate"],
                "base_fit": kwargs["base_fit"]}

    async def finalize_success(conn, **kwargs):
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

    for name, fn in (("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_asset_for_user", get_asset_for_user),
                     ("get_matching_item_asset", get_matching_item_asset),
                     ("get_mannequin_edit_parent", get_edit_parent),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(mj, "_run_candidate", fake_run_candidate)
    monkeypatch.setattr(mj, "_emit", fake_emit)

    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=make_settings(base_mannequin_women_asset_id="bw",
                               base_mannequin_men_asset_id="bm", r2_bucket="bucket",
                               mannequin_prompt_version="fresh_v1"),
        pool=_FakePool(), r2=_R2(), gemini=None))
    if snapshot is ...:
        snapshot = {"version": 1, "profile": PROFILE, "adjustedAxes": list(axes)}
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": {"mode": mode, "fitProfileSnapshot": snapshot}}
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


def test_initial_generation_is_not_reported_as_fallback(monkeypatch):
    """`mode=generate` 는 폴백이 아니라 정상 최초 생성 — 이벤트로 노이즈를 만들지 않는다."""
    calls = _run_worker_fallback(monkeypatch, parent=_parent(), mode="generate")
    assert calls["run"][0]["generation_path"] == "fresh"
    assert _fallback_events(calls) == []


# ---------- 워커 → 원단 2패스 통합 (P0-A3) ----------

def test_worker_carries_detail_slot_all_the_way_into_the_fabric_pass(monkeypatch):
    """`Front + Back + Detail` 업로드 → 원단 2패스 입력에 Detail 이 첫 상품 참조로 들어간다.

    이 경로 전체(자산 로드 → prod_refs → _run_candidate → _apply_edits → _apply_fabric_pass)를
    실제로 태운다. 중간 어느 한 단계라도 슬롯을 버리면 여기서 깨진다.
    """
    gemini_calls = []
    emits = []

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            gemini_calls.append({"model": model, "images": list(images), "size": size,
                                 "prompt": prompt})
            return types.SimpleNamespace(image=_PNG_1PX, mime="image/png")

    class _R2:
        def get_bytes(self, key):
            return {"bw.png": b"base", "front.png": b"front-bytes",
                    "back.png": b"back-bytes", "detail.png": b"detail-bytes"}[key]

        def put_bytes(self, key, data, mime, cache=None):
            return None

    async def get_product(conn, project_id):
        return {"name": "스트라이프 셔츠", "clothing_type": "top", "colors": [{
            "isBase": True,
            "images": [{"id": "front", "slot": "Front"}, {"id": "back", "slot": "Back"},
                       {"id": "detail", "slot": "Detail"}],
        }]}

    async def get_analysis(conn, project_id):
        return {"targetGenders": ["women"], "fit": "regular"}

    async def get_asset_for_user(conn, user_id, asset_id):
        return {"bw": {"id": "bw", "mime_type": "image/png", "r2_key": "bw.png"},
                "front": {"id": "front", "mime_type": "image/png", "r2_key": "front.png"},
                "back": {"id": "back", "mime_type": "image/png", "r2_key": "back.png"},
                "detail": {"id": "detail", "mime_type": "image/png",
                           "r2_key": "detail.png"}}.get(asset_id)

    async def get_matching_item_asset(conn, item_id):
        return None

    async def finalize_success(conn, **kwargs):
        return {"cuts": kwargs["candidates"], "available": 7}

    async def finalize_failure(conn, **kwargs):
        raise AssertionError("잡이 실패하면 안 된다")

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append((event_type, dict(payload)))

    for name, fn in (("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_asset_for_user", get_asset_for_user),
                     ("get_matching_item_asset", get_matching_item_asset),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(mj, "_emit", fake_emit)

    settings = make_settings(
        base_mannequin_women_asset_id="bw", r2_bucket="bucket",
        mannequin_fabric_pass="on", mannequin_max_attempts=3, image_qc="off",
        mannequin_pattern_image_size="4K", mannequin_image_size="1K")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_FakePool(), r2=_R2(), gemini=_Gemini()))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": {}}
    asyncio.run(mj.run_mannequin_job(app, job))

    assert len(gemini_calls) == 2, "1패스 생성 + 원단 2패스"
    generation, fabric = gemini_calls
    # 최초 생성 입력 순서는 기존 그대로 — 슬롯 순서(Front→Back→Detail)를 바꾸지 않는다.
    assert [i.data for i in generation["images"]] == [
        b"base", b"front-bytes", b"back-bytes", b"detail-bytes"]
    # 원단 2패스만 역할 우선순위로 고른다.
    assert [i.data for i in fabric["images"]][1:] == [b"detail-bytes", b"front-bytes"]
    assert fabric["size"] == "4K"

    event = next(p for e, p in emits if e == "step" and p.get("status") == "fabric_pass")
    assert event["refs"] == [{"slot": "Detail", "asset_id": "detail", "priority": 1},
                             {"slot": "Front", "asset_id": "front", "priority": 2}]
    assert event["detail_missing"] is False
