"""ADR-0020 서버 계약. 이미지 공급자와 DB만 대역으로 둔다."""
import asyncio
import uuid
from copy import deepcopy
from io import BytesIO
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from app import repo, routes
from app.agents import asset_recipe, content_roles, garment_edit, similar_cut
from app.agents.gemini_image import GeminiError
from app.workers import editor_image_job as worker
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job
from test_context_fill_routes import JobsDB
from test_editor_generation_record import recording_runtime


A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"
SRC = f"/v1/assets/{A}/file"
FRONT = {"version": 1, "kind": "zipper", "range": "graded", "secondary": None,
         "evidenceSlots": ["Front"], "note": None}
CREATE_JOB = repo.create_job

_INNER_DEFAULT_EXPECTED = (
    " Keep any inner top already visible in the image unchanged. "
    "If the inner top is not visible in any image, show a plain white crew-neck T-shirt with no print or text."
)
_INNER_REFERENCE_EXPECTED = (
    " The second image is a photograph of the same person before the edits. Use it only as a reference for the inner top. "
    "If the inner top is already visible in the first image, keep that same inner top unchanged. "
    "Otherwise, draw the newly revealed inner top as the same garment visible in the second image, with the same type, color and neckline. "
    "If the inner top is not visible in any image, show a plain white crew-neck T-shirt with no print or text. "
    "Do not follow the jacket color, front opening, pose, background or any other details from the second image."
)


# 품질 시험을 통과한 지퍼/unknown 출력의 독립된 바이트 기준.
_ZIPPER_CLOSURE_EXPECTED = {
    "closed": (
        "Edit the image. Change ONLY how the jacket's front zipper is closed: zip it fully closed from the hem "
        "all the way up to the collar, so the inner top is covered. Use the jacket's existing zipper and collar, "
        'do not invent new hardware. Keep the jacket color exactly the same. '
    ),
    "openToChest": (
        "Edit the image. Change ONLY how far the jacket's front zipper is closed. The jacket has ONE zipper with "
        'ONE slider. The zipper must be closed continuously from the hem up to the middle of the chest (about a '
        'hand width below the collarbone), with the single slider sitting at the middle of the chest (about a '
        'hand width below the collarbone). Below the slider the two front panels are joined all the way down to '
        'the hem, with no gap and no second slider. Above the slider the front is open in a V up to the collar, '
        "showing the inner top only inside that V. Use the jacket's existing zipper and collar. Keep the jacket "
        'color exactly the same. '
    ),
    "partial": (
        "Edit the image. Change ONLY how far the jacket's front zipper is open. The jacket has ONE zipper with "
        'ONE slider. Put the single zipper slider level with the bottom of the breastbone (the solar plexus), '
        'clearly lower than the chest. Everything below the slider is zipped shut, the two front panels touching '
        'all the way down to the hem with no gap. Above the slider the front opens in a clean V up to the collar, '
        'and the inner top shows only inside that V. The V must be deep: it reaches about one third of the way '
        "from the collar to the hem. Use the jacket's existing zipper and collar. Keep the jacket color exactly "
        'the same. '
    ),
    "openToWaist": (
        "Edit the image. Change ONLY how far the jacket's front zipper is open. The jacket has ONE zipper with "
        'ONE slider. Put the single zipper slider level with the navel, just above the tops of the side pockets. '
        'Everything below the slider is zipped shut, the two front panels touching all the way down to the hem '
        'with no gap. Above the slider the front opens in a clean V up to the collar, and the inner top shows '
        'only inside that V. The V must be very deep: it reaches about two thirds of the way from the collar to '
        "the hem, and only the lowest part near the hem stays zipped. Use the jacket's existing zipper and "
        'collar. Keep the jacket color exactly the same. '
    ),
    "open": (
        "Edit the image. Change ONLY how the jacket's front is fastened: unzip it completely from the collar down "
        'to the hem so the two front panels hang open and the inner top is visible down the whole front. Use the '
        "jacket's existing zipper. Keep the jacket color exactly the same. "
    ),
}

@pytest.mark.parametrize("clothing_type", ["outer", "아우터"])
@pytest.mark.parametrize("mode,state", [("color", None), ("closure", "closed"),
    ("closure", "openToChest"), ("closure", "partial"),
    ("closure", "openToWaist"), ("closure", "open")])
@pytest.mark.parametrize("kind", ["zipper", "unknown"])
def test_outer_edit_prompt_preserves_quality_test_wording(clothing_type, mode, state, kind):
    keep = (
        "Keep everything else exactly the same as the first image: the same person, face, hair, pose, hands, body shape, "
        "camera framing and angle, background, lighting, the inner top, the pants, bag and accessories, and the jacket's "
        "shape, fit, length, collar, seams, pockets and fabric texture. Do not add logos, text or new parts. Photorealistic."
    )
    color = (
        "Edit the first image. Change ONLY the color of the jacket (the outer garment) so it matches the fabric and zipper "
        "color shown in the second image, which is a photo of the same jacket in the target color. Keep how open the "
        "zipper is exactly as in the first image. "
    )
    expected = color + keep if mode == "color" else _ZIPPER_CLOSURE_EXPECTED[state] + keep.replace("first image", "image")
    prompt = garment_edit.prompt_for(mode, clothing_type=clothing_type,
        closure_state=state, front_closure={"kind": kind})
    if mode == "closure" and state != "closed":
        assert prompt.startswith(expected)
        assert prompt[len(expected):] == _INNER_DEFAULT_EXPECTED
    else:
        assert prompt == expected


@pytest.mark.parametrize("clothing_type", ["outer", "아우터"])
@pytest.mark.parametrize("kind,fasteners", [("button", "buttons"), ("snap", "snap buttons")])
@pytest.mark.parametrize("state,markers", [
    ("closed", ["Fasten all the existing", "highest one", "below the collar"]),
    ("openToChest", ["middle of the chest (about a hand width below the collarbone)",
                     "Fasten", "above that height", "overlap", "no gap", "only inside that V"]),
    ("partial", ["level with the bottom of the breastbone (the solar plexus), clearly lower than the chest",
                 "Aim for a deep V reaching about one third of the way from the collar to the hem.",
                 "Fasten", "above that height", "overlap", "no gap", "only inside that V"]),
    ("openToWaist", ["level with the navel, just above the tops of the side pockets",
                     "Aim for a very deep V reaching about two thirds of the way from the collar to the hem",
                     "Fasten", "above that height", "overlap", "no gap", "only inside that V"]),
    ("open", ["Unfasten all", "from the collar down to the hem", "visible down the whole front",
              "visible along the front edges"]),
])
def test_button_and_snap_prompts_use_existing_fasteners_at_requested_height(clothing_type, kind, fasteners, state, markers):
    prompt = garment_edit.prompt_for("closure", clothing_type=clothing_type,
        closure_state=state, front_closure={"kind": kind, "range": "graded"})
    assert all(word not in prompt.lower() for word in ("zip", "slider", "front front"))
    assert fasteners in prompt
    hardware = f"existing {fasteners}, buttonholes and collar" if kind == "button" else "existing snap buttons and collar"
    assert hardware in prompt
    assert f"Keep the number, size, color and spacing of the {fasteners} unchanged." in prompt
    assert "Do not add any other type of fastening or new hardware." in prompt
    assert "Keep the jacket color exactly the same." in prompt
    assert "the same person, face, hair, pose, hands, body shape" in prompt
    assert all(marker in prompt for marker in markers)
    if kind == "snap":
        button = garment_edit.prompt_for("closure", clothing_type=clothing_type,
            closure_state=state, front_closure={"kind": "button", "range": "graded"})
        assert "buttonhole" not in prompt.lower()
        assert prompt == button.replace("buttons", "snap buttons").replace(", buttonholes", "")
    if state in ("openToChest", "partial", "openToWaist"):
        assert f"Fasten only the existing {fasteners}" in prompt
        assert "closest to the requested height" in prompt
        assert f"no {fasteners} at or below the requested height" in prompt
        assert "open the entire front" in prompt
        assert f"Do not create or move {fasteners}." in prompt


@pytest.mark.parametrize("clothing_type", ["outer", "아우터"])
@pytest.mark.parametrize("state,effective", [
    ("closed", "closed"), ("openToChest", "closed"), ("partial", "closed"),
    ("openToWaist", "open"), ("open", "open"),
])
def test_belt_prompts_tie_or_untie_existing_belt_including_direct_intermediate_states(clothing_type, state, effective):
    prompt = garment_edit.prompt_for("closure", clothing_type=clothing_type,
        closure_state=state, front_closure={"kind": "belt", "range": "binary"})
    assert "belt" in prompt
    assert all(word not in prompt.lower() for word in ("zip", "button", "slider", "front front"))
    assert "Use the jacket's existing belt." in prompt
    assert "Do not add any other type of fastening or new hardware." in prompt
    assert "Keep the jacket color exactly the same." in prompt
    if effective == "closed":
        assert "Tie" in prompt and "at the waist" in prompt and "front panels overlap and close" in prompt
    else:
        assert "Untie" in prompt and "front panels hang open" in prompt and "belt ends hang loose" in prompt
    assert prompt == garment_edit.prompt_for("closure", clothing_type=clothing_type,
        closure_state=effective, front_closure={"kind": "belt", "range": "binary"})


@pytest.mark.parametrize("clothing_type", ["outer", "아우터"])
@pytest.mark.parametrize("kind", ["button", "snap", "belt"])
def test_non_zipper_outer_color_prompt_preserves_front_without_inventing_fastening_color(clothing_type, kind):
    prompt = garment_edit.prompt_for("color", clothing_type=clothing_type,
        closure_state=None, front_closure={"kind": kind})
    assert "zipper" not in prompt and "front fastening color" not in prompt
    assert "fabric color shown in the second image" in prompt
    assert "Keep how open or fastened the front is exactly as in the first image." in prompt


@pytest.mark.parametrize("kind", ["zipper", "unknown", "button", "snap", "belt", "none"])
@pytest.mark.parametrize("mode,state", [("color", None), ("closure", "closed"),
    ("closure", "openToChest"), ("closure", "partial"), ("closure", "openToWaist"), ("closure", "open")])
def test_outer_prompt_never_doubles_front(kind, mode, state):
    prompt = garment_edit.prompt_for(mode, clothing_type="outer", closure_state=state,
        front_closure={"kind": kind, "range": "binary" if kind == "belt" else "graded"})
    assert "front front" not in prompt


@pytest.mark.parametrize("kind,secondary", [("zipper", "snap"), ("button", "belt"), ("snap", "zipper")])
@pytest.mark.parametrize("state", ["closed", "openToChest", "partial", "openToWaist", "open"])
def test_secondary_closure_instruction_only_when_present(kind, secondary, state):
    base = garment_edit.prompt_for("closure", clothing_type="outer", closure_state=state,
                                  front_closure={"kind": kind, "secondary": None})
    prompt = garment_edit.prompt_for("closure", clothing_type="outer", closure_state=state,
                                    front_closure={"kind": kind, "secondary": secondary})
    sentence = f"Operate the existing secondary {secondary} fastening to the same height and in the same way as the main fastening. "
    assert "secondary" not in base
    assert prompt.replace(sentence, "") == base


@pytest.mark.parametrize("kind", ["zipper", "unknown", "button", "snap"])
@pytest.mark.parametrize("state", ["openToChest", "partial", "openToWaist", "open"])
@pytest.mark.parametrize("reference", [False, True])
def test_open_outer_prompt_preserves_visible_inner_and_defines_white_fallback(kind, state, reference):
    prompt = garment_edit.prompt_for("closure", clothing_type="outer", closure_state=state,
                                    front_closure={"kind": kind}, has_inner_reference=reference)
    assert prompt.endswith(_INNER_REFERENCE_EXPECTED if reference else _INNER_DEFAULT_EXPECTED)
    if reference:
        assert "Keep everything else exactly the same as the first image:" in prompt
    if kind in ("zipper", "unknown"):
        assert prompt.startswith(_ZIPPER_CLOSURE_EXPECTED[state])


@pytest.mark.parametrize("mode,state,clothing_type", [("color", None, "outer"), ("closure", "closed", "outer"),
    ("closure", "open", "top"), ("closure", "open", "bottom"), ("closure", "open", "dress")])
def test_inner_reference_has_no_effect_outside_open_outer_closure(mode, state, clothing_type):
    kwargs = dict(clothing_type=clothing_type, closure_state=state, front_closure=FRONT)
    prompt = garment_edit.prompt_for(mode, **kwargs)
    assert garment_edit.prompt_for(mode, **kwargs, has_inner_reference=True) == prompt
    assert "plain white crew-neck" not in prompt


@pytest.mark.parametrize("clothing_type,noun,possessive,feature", [
    ("top", "top", "top's", "neckline"), ("상의", "top", "top's", "neckline"),
    ("bottom", "pants", "pants'", "waistband"), ("하의", "pants", "pants'", "waistband"),
    ("dress", "dress", "dress's", "neckline"), ("원피스", "dress", "dress's", "neckline"),
    ("unknown", "garment", "garment's", None), ("", "garment", "garment's", None),
    (None, "garment", "garment's", None),
])
@pytest.mark.parametrize("kind", ["zipper", "button", "snap", "belt"])
def test_color_edit_prompt_keeps_other_clothing_without_preserving_product_color(
        clothing_type, noun, possessive, feature, kind):
    prompt = garment_edit.prompt_for("color", clothing_type=clothing_type,
        closure_state=None, front_closure={"kind": kind})
    edit, keep = prompt.split("Keep everything else exactly the same as the first image: ", 1)
    assert f"Change ONLY the color of the {noun} (the product being sold)" in edit
    assert "fabric color shown in the second image" in edit
    assert f"same {noun} in the target color" in edit
    assert "(the outer garment)" not in prompt
    assert "zipper color" not in edit and "front fastening color" not in edit
    unchanged, shape = keep.split(f"and the {possessive} ", 1)
    assert unchanged == (
        "the same person, face, hair, pose, hands, body shape, camera framing and angle, "
        "background, lighting, the other clothing, bag and accessories, "
    )
    assert f"the {noun}" not in unchanged
    assert "the inner top" not in unchanged and "the pants" not in unchanged
    features = f", {feature}" if feature else ""
    assert shape == (
        f"shape, fit, length{features}, seams, pockets and fabric texture. "
        "Do not add logos, text or new parts. Photorealistic."
    )


@pytest.fixture
def edit_route(monkeypatch, client):
    state = {"asset": {"id": A, "source": "ai", "metadata": {"cut_type": "horizon"}},
             "record": {"cutType": "horizon", "colorId": "base", "recipe": {
                 "cutType": "horizon", "colorId": "base", "outerClosureState": "open"}},
             "product": {"clothingType": "outer", "colors": [
                 {"id": "base", "images": []}, {"id": "blue", "images": [
                     {"id": B, "slot": "Back"}, {"id": C, "slot": "Front"}]}]},
             "analysis": {"frontClosure": deepcopy(FRONT)}}
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(side_effect=lambda *_: state["product"]))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(side_effect=lambda *_: state["analysis"]))
    monkeypatch.setattr(repo, "get_project_asset_for_user", AsyncMock(
        side_effect=lambda conn, uid, pid, aid: state["asset"] if aid == A else {
            "id": aid, "source": "upload", "metadata": {}, "mime_type": "image/png", "r2_key": aid}))
    monkeypatch.setattr(asset_recipe, "resolve_asset_recipes", AsyncMock(
        side_effect=lambda conn, uid, pid, ids: {A: deepcopy(state["record"])}))
    monkeypatch.setattr(repo, "get_asset_facemarket_provenance", AsyncMock(return_value=None))

    async def create(conn, **kw):
        state["job"] = kw
        return {"id": "j1", "payload": kw["payload"]}, True

    async def reserve(conn, uid, cost):
        state["reserved"] = cost
        return 81

    monkeypatch.setattr(repo, "create_job", create)
    monkeypatch.setattr(repo, "reserve_credits", reserve)
    client.app.state.settings = make_settings(credit_cost_editor_image=19)
    return state


def post_edit(client, make_token, mode="color", key="edit", **extra):
    headers = auth_headers(make_token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post("/v1/projects/p1/editor:generate-image", headers=headers, json={
        "mode": "vary", "variationMode": mode, "source": {"src": SRC},
        **({"colorId": "blue"} if mode == "color" else {"outerClosureState": "partial"}), **extra})


@pytest.mark.parametrize("mode", ["color", "closure"])
@pytest.mark.parametrize("source", ["ai", "upload"])
def test_edit_accepts_worn_and_upload_with_5_reserved(client, make_token, edit_route, mode, source):
    edit_route["asset"]["source"] = source
    if source == "upload":
        edit_route["asset"]["metadata"] = {}
        edit_route["record"] = {"upload": True, "cutType": None, "colorId": "base", "recipe": None}
    response = post_edit(client, make_token, mode)
    assert response.status_code == 202, response.text
    assert response.json() == {"jobId": "j1"}
    assert edit_route["reserved"] == edit_route["job"]["credits_reserved"] == 5
    assert edit_route["job"]["payload"]["source"]["cutType"] == ("styling" if source == "upload" else "horizon")


@pytest.mark.parametrize("mode", ["color", "closure"])
@pytest.mark.parametrize("case", ["product", "detail", "missing", "untrusted"])
def test_edit_rejects_ineligible_source_before_reserving(client, make_token, edit_route, mode, case):
    if case == "missing":
        edit_route["asset"] = None
    elif case == "detail":
        edit_route["asset"]["metadata"]["detail_recipe"] = {"version": 1}
    else:
        edit_route["record"]["cutType"] = "product" if case == "product" else None
        edit_route["asset"]["metadata"]["cut_type"] = edit_route["record"]["cutType"]
        edit_route["record"]["recipe"] = None
    response = post_edit(client, make_token, mode, source={"src": SRC, "cutType": "styling"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "edit_source_unsupported"
    assert response.json()["error"]["message"] == "이 사진에서는 색상과 열림 정도를 바꿀 수 없어요."
    assert "reserved" not in edit_route


@pytest.mark.parametrize("value", [None, "missing", [], {}])
def test_color_unknown(client, make_token, edit_route, value):
    response = post_edit(client, make_token, colorId=value)
    assert response.status_code == 400 and response.json()["error"]["code"] == "color_unknown"
    assert "reserved" not in edit_route


def test_color_requires_target_images(client, make_token, edit_route):
    edit_route["product"]["colors"][1]["images"] = []
    response = post_edit(client, make_token)
    assert response.status_code == 409
    assert response.json()["error"] == {"code": "no_selected_color_images", "message": "이 색 상품 사진이 없어요."}
    assert "reserved" not in edit_route


def test_color_same_value(client, make_token, edit_route):
    edit_route["record"]["recipe"]["colorId"] = "blue"
    response = post_edit(client, make_token)
    assert response.status_code == 400 and response.json()["error"]["code"] == "edit_same_value"
    assert "reserved" not in edit_route


@pytest.mark.parametrize("case", ["top", "none"])
def test_closure_unsupported(client, make_token, edit_route, case):
    if case == "top":
        edit_route["product"]["clothingType"] = "top"
    else:
        edit_route["analysis"]["frontClosure"].update(kind="none", range=None)
    response = post_edit(client, make_token, "closure")
    assert response.status_code == 400 and response.json()["error"]["code"] == "closure_unsupported"
    assert "reserved" not in edit_route


@pytest.mark.parametrize("value", [None, "half", [], {}])
def test_closure_requires_one_of_five_states(client, make_token, edit_route, value):
    response = post_edit(client, make_token, "closure", outerClosureState=value)
    assert response.status_code == 400 and response.json()["error"]["code"] == "closure_invalid"
    assert "reserved" not in edit_route


def test_closure_binary_normalizes_before_same_value_check(client, make_token, edit_route):
    edit_route["analysis"]["frontClosure"].update(kind="button", range="binary")
    response = post_edit(client, make_token, "closure")
    assert response.status_code == 202, response.text
    assert edit_route["job"]["payload"]["outerClosureState"] == "closed"
    edit_route.pop("reserved")
    edit_route["record"]["recipe"]["outerClosureState"] = "openToChest"
    response = post_edit(client, make_token, "closure")
    assert response.status_code == 400 and response.json()["error"]["code"] == "edit_same_value"
    assert "reserved" not in edit_route


@pytest.mark.parametrize("mode", ["color", "closure"])
@pytest.mark.parametrize("key", [None, "", "   "])
def test_edit_requires_idempotency_key(client, make_token, edit_route, mode, key):
    response = post_edit(client, make_token, mode, key)
    assert response.status_code == 400 and response.json()["error"]["code"] == "idempotency_key_required"
    assert "reserved" not in edit_route


@pytest.mark.parametrize("client_cut_type", [None, "product"])
def test_upload_similar_without_cut_type(client, make_token, edit_route, client_cut_type):
    edit_route["asset"].update(source="upload", metadata={})
    response = post_edit(client, make_token, "similar", source={"src": SRC, "cutType": client_cut_type})
    assert response.status_code == 202, response.text
    assert edit_route["job"]["payload"]["source"]["cutType"] == "styling"
    assert edit_route["reserved"] == edit_route["job"]["credits_reserved"] == 8


@pytest.mark.parametrize("mode,cost", [("color", 7), ("closure", 7), ("similar", 11)])
def test_editor_reserves_server_price_and_ignores_client_price(client, make_token, edit_route, mode, cost):
    client.app.state.settings = make_settings(credit_cost_editor_edit=7, credit_cost_editor_similar=11)
    response = post_edit(client, make_token, mode, creditCost=1, creditsCharged=1)
    assert response.status_code == 202, response.text
    assert edit_route["reserved"] == edit_route["job"]["credits_reserved"] == cost


@pytest.mark.parametrize("body", [
    {"mode": "new", "cutType": "styling"},
    {"mode": "new", "cutType": "product", "shot": "ghost", "colorId": "blue", "variationMode": "color"},
    {"mode": "new", "cutType": "product", "shot": "ghost", "direction": "back"},
    {"mode": "new", "cutType": "product", "shot": "detail", "detailTargetId": "pocket"},
    {"mode": "vary", "source": {"src": SRC}},
])
def test_other_editor_requests_keep_image_price(client, make_token, edit_route, body):
    edit_route["asset"]["metadata"]["detail_recipe"] = {"version": 1}
    response = client.post("/v1/projects/p1/editor:generate-image", headers=auth_headers(make_token), json=body)
    assert response.status_code == 202, response.text
    assert edit_route["reserved"] == edit_route["job"]["credits_reserved"] == 19


@pytest.mark.parametrize("fill", [False, True])
def test_empty_slot_similar_keeps_image_price(client, make_token, edit_route, monkeypatch, fill):
    payload = {"mode": "vary", "variationMode": "similar", "source": {"src": SRC}}
    if fill:
        monkeypatch.setattr(repo, "find_context_fill_job", AsyncMock(return_value=None))
        monkeypatch.setattr(routes, "_prepare_context_fill", AsyncMock(return_value={
            **payload, "_contextFill": {"slot": {"blockId": "b", "elementId": "e"}, "slotKey": "b:e",
                "label": "스타일링 · 비슷한 컷", "path": "similar", "cutType": "horizon", "colorId": "base"}}))
        request = {"mode": "fill", "slot": {"blockId": "b", "elementId": "e"}}
    else:
        from app.agents import horizon_background
        monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=[]))
        monkeypatch.setattr(repo, "get_editor_blocks", AsyncMock(return_value=[]))
        monkeypatch.setattr(horizon_background, "restore_failed_retry", lambda body, *_: body)
        request = {**payload, "retryBlockId": "blank"}
    headers = {**auth_headers(make_token), "Idempotency-Key": "empty"}
    response = client.post("/v1/projects/p1/editor:generate-image", headers=headers, json=request)
    assert response.status_code == 202, response.text
    assert edit_route["reserved"] == edit_route["job"]["credits_reserved"] == 19


def png():
    output = BytesIO()
    Image.new("RGB", (300, 400)).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("mode,value,marker", [("color", "blue", "second image"),
    ("closure", "closed", "fully closed"), ("closure", "openToChest", "hand width below"),
    ("closure", "partial", "solar plexus"), ("closure", "openToWaist", "navel"),
    ("closure", "open", "unzip it completely")])
@pytest.mark.parametrize("reserved", [5, 19])
def test_edit_worker_inputs_prompt_size_group_and_generation(recording_runtime, monkeypatch, mode, value, marker, reserved):
    state = recording_runtime
    state["product"]["colors"].append({"id": "blue", "images": [
        {"id": B, "slot": "Back"}, {"id": C, "slot": "Front"}]})
    state["analysis"]["frontClosure"] = deepcopy(FRONT)
    monkeypatch.setattr(repo, "list_wardrobe_images", AsyncMock(return_value=[{"asset_id": A, "color_id": "wardrobe"}]))
    state["app"].state.settings = make_settings(model_editor_cut="gpt-image-2.5-flare", detail_cut_image_size="1K")
    state["app"].state.r2.get_bytes = lambda key: png() if key == "input" else key.encode()

    async def target(conn, uid, pid, aid):
        return {"id": aid, "source": "upload", "r2_key": aid, "mime_type": "image/png", "metadata": {}}
    source_lookup = worker.repo.get_project_asset_for_user

    async def asset(conn, uid, pid, aid):
        return await source_lookup(conn, uid, pid, aid) if aid == A else await target(conn, uid, pid, aid)

    monkeypatch.setattr(repo, "get_project_asset_for_user", asset)
    calls = []

    class Gemini:
        async def generate_content_image(self, model, prompt, images, size, **kw):
            calls.append((model, prompt, images, size, kw))
            from types import SimpleNamespace
            return SimpleNamespace(image=b"GENERATED", mime="image/png")

    state["app"].state.gemini = Gemini()
    field = "colorId" if mode == "color" else "outerClosureState"
    request = {"mode": "vary", "variationMode": mode, "source": {"src": SRC, "cutType": "horizon"}, field: value}
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job(request, credits_reserved=reserved)))
    assert "failure" not in state, state.get("failure")
    model, prompt, images, size, options = calls[0]
    assert model == "gpt-image-2.5-flare" and marker in prompt
    assert "Do not add logos, text or new parts. Photorealistic." in prompt
    assert images[0].data == png()
    assert len(images) == (2 if mode == "color" else 1)
    if mode == "color":
        assert images[1].data == C.encode()
        assert "Keep how open the zipper is exactly as in the first image." in prompt
    assert options["openai_size_override"] == "1072x1440"
    assert options["aspect_ratio"] is None
    assert state["success"]["group"] == ("blue" if mode == "color" else "wardrobe")
    record = state["success"]["image"]["metadata"]["generation"]
    expected = asset_recipe.project_recipe(state["anchor"]["recipe"])
    expected[field] = value
    assert record == {"version": 1, "origin": "editor_" + mode, "autoSelected": False,
                      "sourceAssetId": A, "recipeFrom": "anchor", "recipe": expected}
    assert state["success"]["charge"] == state["success"]["reserved"] == reserved


class EditLineageConn:
    """자산 조회의 실제 SQL 소유 조건을 확인하고 소유한 행만 돌려준다."""
    def __init__(self, rows):
        self.rows = rows
        self.row = None
        self.read_ids = []

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, query, params):
        assert "user_id = %s and project_id = %s and deleted_at is null" in query
        aid, uid, pid = params
        self.read_ids.append(aid)
        candidate = self.rows.get(aid)
        self.row = candidate if candidate and (candidate["user_id"], candidate["project_id"]) == (uid, pid) else None

    async def fetchone(self):
        return self.row


@pytest.mark.parametrize("case,expected_id,read_ids", [
    ("upload", None, []), ("page", None, []), ("similar", None, []),
    ("two_closure_edits", C, [B, C]), ("color_then_closure", C, [B, C]),
    ("other_user", None, [B]), ("other_project", None, [B]),
    ("cycle", B, [B]), ("self_cycle", None, []), ("invalid_id", None, []),
])
def test_edit_origin_lineage_stops_at_non_edit_origin_and_owned_boundaries(monkeypatch, case, expected_id, read_ids):
    records = {A: {"origin": "editor_closure", "sourceAssetId": B},
               B: {"origin": "editor_closure", "sourceAssetId": C},
               C: {"origin": "upload", "sourceAssetId": None}}
    rows = {aid: {"id": aid, "user_id": "u1", "project_id": "p1", "source": "upload" if aid == C else "ai"}
            for aid in (A, B, C)}
    if case in ("upload", "page", "similar"):
        records[A]["origin"] = {"upload": "upload", "page": "page", "similar": "editor_similar"}[case]
    elif case == "color_then_closure":
        records[A]["origin"] = "editor_color"
    elif case == "other_user":
        rows[B]["user_id"] = "u2"
    elif case == "other_project":
        rows[B]["project_id"] = "p2"
    elif case == "cycle":
        records[B]["sourceAssetId"] = A
    elif case == "self_cycle":
        records[A]["sourceAssetId"] = A
    elif case == "invalid_id":
        records[A]["sourceAssetId"] = "not-an-asset-id"

    async def recipes(conn, uid, pid, ids):
        assert (uid, pid) == ("u1", "p1")
        return {aid: records[aid] for aid in ids}

    monkeypatch.setattr(asset_recipe, "resolve_asset_recipes", recipes)
    conn = EditLineageConn(rows)
    result = asyncio.run(asset_recipe.resolve_edit_origin_asset(conn, "u1", "p1", A))
    assert (result or {}).get("id") == expected_id
    assert conn.read_ids == read_ids


def test_edit_origin_lineage_limits_depth_to_ten_hops(monkeypatch):
    ids = [str(uuid.UUID(int=i)) for i in range(1, 13)]
    rows = {aid: {"id": aid, "user_id": "u1", "project_id": "p1"} for aid in ids}
    records = {aid: {"origin": "editor_closure", "sourceAssetId": ids[i + 1]}
               for i, aid in enumerate(ids[:-1])}
    async def recipes(conn, uid, pid, requested):
        return {aid: records[aid] for aid in requested}
    monkeypatch.setattr(asset_recipe, "resolve_asset_recipes", recipes)
    conn = EditLineageConn(rows)
    result = asyncio.run(asset_recipe.resolve_edit_origin_asset(conn, "u1", "p1", ids[0]))
    assert result["id"] == ids[10]
    assert conn.read_ids == ids[1:11]


@pytest.mark.parametrize("mode,state_value,reference_readable,expected_reference", [
    ("closure", "open", True, True), ("closure", "openToChest", True, True),
    ("closure", "closed", True, False), ("color", None, True, False),
    ("closure", "open", False, False),
])
def test_edit_worker_attaches_inner_reference_only_for_open_closure(recording_runtime, monkeypatch,
        mode, state_value, reference_readable, expected_reference):
    state = recording_runtime
    state["analysis"]["frontClosure"] = deepcopy(FRONT)
    state["product"]["colors"].append({"id": "blue", "images": [{"id": C, "slot": "Front"}]})
    state["anchor"].update(origin="editor_closure", sourceAssetId=B)
    lookup = repo.get_project_asset_for_user
    async def asset(conn, uid, pid, aid):
        if aid == A:
            return await lookup(conn, uid, pid, aid)
        return {"id": aid, "source": "upload", "metadata": {}, "r2_key": aid, "mime_type": "image/png"}
    async def recipes(conn, uid, pid, ids):
        return {aid: deepcopy(state["anchor"]) if aid == A else
                {"origin": "upload", "sourceAssetId": None} for aid in ids}
    def read(key):
        if key == B and not reference_readable:
            raise OSError("reference no longer readable")
        return key.encode()
    calls = []
    async def generate(settings, gemini, source, target, cut_type, **kwargs):
        calls.append((source, target, kwargs.get("inner_reference_image")))
        return b"GENERATED", "image/png"
    monkeypatch.setattr(repo, "get_project_asset_for_user", asset)
    monkeypatch.setattr(repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    monkeypatch.setattr(asset_recipe, "resolve_asset_recipes", recipes)
    monkeypatch.setattr(garment_edit, "generate", generate)
    state["app"].state.r2.get_bytes = read
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job({"mode": "vary", "variationMode": mode,
        "source": {"src": SRC}, "outerClosureState": state_value, "colorId": "blue"}, credits_reserved=19)))
    assert "failure" not in state, state.get("failure")
    assert "success" in state
    source, target, reference = calls[0]
    assert source.data == b"input"
    assert (target.data if target else None) == (C.encode() if mode == "color" else None)
    assert (reference.data if reference else None) == (B.encode() if expected_reference else None)
    assert state["success"]["image"]["metadata"]["generation"]["sourceAssetId"] == A


@pytest.mark.parametrize("mode,closure_state,expected_images", [("closure", "open", [b"SOURCE", b"INNER"]),
    ("closure", "closed", [b"SOURCE"]), ("color", None, [b"SOURCE", b"COLOR"])])
def test_edit_generate_sends_reference_images_with_matching_prompt(mode, closure_state, expected_images):
    from app.agents.gemini_image import InlineImage
    from types import SimpleNamespace
    calls = []
    class Gemini:
        async def generate_content_image(self, model, prompt, images, *args, **kwargs):
            calls.append((prompt, [image.data for image in images]))
            return SimpleNamespace(image=b"OUTPUT", mime="image/png")
    asyncio.run(garment_edit.generate(make_settings(), Gemini(), InlineImage("image/png", b"SOURCE"),
        InlineImage("image/png", b"COLOR") if mode == "color" else None, "horizon", mode=mode,
        clothing_type="outer", closure_state=closure_state, front_closure=FRONT,
        inner_reference_image=InlineImage("image/png", b"INNER")))
    prompt, images = calls[0]
    assert images == expected_images
    if mode == "closure" and closure_state == "open":
        assert prompt.endswith(_INNER_REFERENCE_EXPECTED)
    else:
        assert "before the edits" not in prompt and "plain white crew-neck" not in prompt


def test_upload_edit_unknown_recipe_has_null_fields_and_failure_releases(recording_runtime, monkeypatch):
    state = recording_runtime
    state["anchor"] = {"recipe": None, "upload": True}
    monkeypatch.setattr(repo, "get_project_asset_for_user", AsyncMock(return_value={
        "id": A, "source": "upload", "metadata": {}, "r2_key": "input", "mime_type": "image/png"}))
    monkeypatch.setattr(repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    class Gemini:
        async def generate_content_image(self, *args, **kw):
            if state.get("error"):
                raise GeminiError("failed")
            from types import SimpleNamespace
            return SimpleNamespace(image=b"GENERATED", mime="image/png")
    state["app"].state.gemini = Gemini()
    request = {"mode": "vary", "variationMode": "closure", "source": {"src": SRC}, "outerClosureState": "closed"}
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job(request, credits_reserved=19)))
    assert "failure" not in state, state.get("failure")
    assert state["success"]["cut_type"] == "styling"
    recipe = state.pop("success")["image"]["metadata"]["generation"]["recipe"]
    assert recipe == {key: "closed" if key == "outerClosureState" else None for key in content_roles.GENERATION_RECIPE_FIELDS}
    state["error"] = True
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job(request, credits_reserved=19)))
    assert "success" not in state and state["failure"]["reserved"] == 19
    assert "charge" not in state["failure"]


@pytest.mark.parametrize("case,kind", [("upload", "upload"), ("worn", "worn"), ("ghost", "ghost"),
    ("detail", "detail"), ("detail_metadata", "detail"), ("product", "product_unknown"), ("unknown", "unknown")])
def test_asset_info_kind_and_current(client, make_token, edit_route, case, kind):
    record = {"origin": "editor_new", "cutType": "product", "colorId": "base", "realDerived": True,
              "recipe": {"cutType": "product", "colorId": "blue", "shot": "full", "direction": "back",
                         "outerClosureState": "partial", "detailTargetId": "pocket"}}
    if case == "upload":
        record.update(upload=True)
        record["recipe"] = None
        edit_route["asset"]["source"] = "upload"
    elif case == "worn":
        record["cutType"] = "horizon"
    elif case in {"ghost", "detail"}:
        record["recipe"]["shot"] = case
    elif case == "detail_metadata":
        edit_route["asset"]["metadata"]["detail_recipe"] = {"version": 1}
    elif case == "unknown":
        record.update(cutType=None, recipe=None, colorId=None)
    edit_route["asset"]["metadata"]["cut_type"] = record.get("cutType")
    edit_route["record"] = record
    edit_route["analysis"]["frontClosure"].update(kind="button", range="binary")
    response = client.post("/v1/projects/p1/editor:asset-info", headers=auth_headers(make_token), json={"assetIds": [A, A, B]})
    assert response.status_code == 200, response.text
    item = response.json()["items"][A]
    assert set(response.json()["items"]) == {A}
    assert item["kind"] == kind and item["realDerived"] is True
    assert item["recipe"] == asset_recipe.project_recipe(record["recipe"])
    expected = {"colorId": "blue", "outerClosureState": "closed", "direction": "back", "detailTargetId": "pocket"}
    if case in {"unknown", "upload"}:
        expected = {key: None for key in expected}
        if case == "upload":
            expected["colorId"] = "base"
    assert item["current"] == expected
    assert asset_recipe.resolve_asset_recipes.await_args.args[-1] == [A, B]


@pytest.mark.parametrize("ids", [None, "bad", ["bad"], [None], [1], [A] * 61])
def test_asset_info_rejects_invalid_or_over_60(client, make_token, edit_route, ids):
    response = client.post("/v1/projects/p1/editor:asset-info", headers=auth_headers(make_token), json={"assetIds": ids})
    assert response.status_code == 400 and response.json()["error"]["code"] == "asset_ids_invalid"


def test_asset_info_project_ownership(client, make_token, edit_route, monkeypatch):
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value=None))
    response = client.post("/v1/projects/p1/editor:asset-info", headers=auth_headers(make_token), json={"assetIds": [A]})
    assert response.status_code == 404
    asset_recipe.resolve_asset_recipes.assert_not_awaited()


def test_wardrobe_item_contains_asset_id():
    item = repo._wardrobe_image_api({"id": "w", "asset_id": A, "ai": True, "cut_type": "horizon"})
    assert item["assetId"] == A and item["src"] == SRC


@pytest.mark.parametrize("changed", [False, True])
def test_editor_key_compares_normalized_public_payload(changed):
    payload = {"mode": "vary", "variationMode": "color", "source": {"src": SRC}, "colorId": "blue"}
    db = JobsDB([{ "id": "original", "kind": "editor_image", "project_id": "p", "status": "done",
                  "idempotency_key": "key", "metadata": {}, "payload": {**payload, "_facemarket": {"licenseId": "old"}}}])
    incoming = {**payload, "_facemarket": {"licenseId": "new"}}
    if changed:
        incoming["colorId"] = "black"
    async def create():
        return await repo.create_job(db, user_id="u", project_id="p", kind="editor_image", payload=incoming,
                                     idempotency_key="key", credits_reserved=19, metadata={})
    if changed:
        with pytest.raises(Exception, match="같은 요청 번호로 다른 내용이 왔어요") as caught:
            asyncio.run(create())
        assert caught.value.code == "idempotency_key_reused" and caught.value.status == 409
    else:
        row, created = asyncio.run(create())
        assert row["id"] == "original" and not created
    assert db.inserts == 0


def test_fill_same_key_different_slot_rejected():
    db = JobsDB([{"id": "original", "kind": "editor_image", "status": "running", "idempotency_key": "key",
                  "metadata": {}, "payload": {"_contextFill": {"slotKey": "block:first"}}}])
    with pytest.raises(Exception, match="같은 요청 번호로 다른 내용이 왔어요") as caught:
        asyncio.run(repo.find_context_fill_job(db, "u", "p", "key", "block:second"))
    assert caught.value.status == 409


@pytest.mark.parametrize("mode,cost", [("color", 5), ("closure", 5), ("similar", 8)])
def test_editor_key_conflict_is_409_and_same_request_reserves_once(client, make_token, edit_route, monkeypatch, mode, cost):
    db = JobsDB([])
    async def create(conn, **kw):
        return await CREATE_JOB(db, **kw)
    monkeypatch.setattr(repo, "create_job", create)
    reserve = AsyncMock(return_value=81)
    monkeypatch.setattr(repo, "reserve_credits", reserve)
    first = post_edit(client, make_token, mode)
    client.app.state.settings = make_settings(credit_cost_editor_edit=7, credit_cost_editor_similar=11)
    repeated = post_edit(client, make_token, mode, source={"src": SRC, "cutType": "forged"}, _garmentEdit={"targetAssetId": "forged"})
    assert first.status_code == repeated.status_code == 202
    assert first.json() == repeated.json()
    conflict = post_edit(client, make_token, "closure" if mode == "color" else "color")
    assert conflict.status_code == 409
    assert conflict.json()["error"] == {"code": "idempotency_key_reused", "message": "같은 요청 번호로 다른 내용이 왔어요. 다시 눌러 주세요."}
    assert reserve.await_count == db.inserts == 1
    assert reserve.await_args.args[-1] == cost


@pytest.mark.parametrize("slot", ["Back", "Detail"])
def test_color_worker_falls_back_to_first_target_photo(recording_runtime, monkeypatch, slot):
    state = recording_runtime
    state["product"]["colors"] = [{"id": "blue", "images": [{"id": B, "slot": slot}]}]
    monkeypatch.setattr(repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    lookup = repo.get_project_asset_for_user
    async def asset(conn, uid, pid, aid):
        if aid == A:
            return await lookup(conn, uid, pid, aid)
        return {"id": aid, "mime_type": "image/png", "r2_key": "target", "source": "upload", "metadata": {}}
    monkeypatch.setattr(repo, "get_project_asset_for_user", asset)
    state["app"].state.r2.get_bytes = lambda key: key.encode()
    class Gemini:
        async def generate_content_image(self, model, prompt, images, *args, **kw):
            assert [img.data for img in images] == [b"input", b"target"]
            from types import SimpleNamespace
            return SimpleNamespace(image=b"OUTPUT", mime="image/png")
    state["app"].state.gemini = Gemini()
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job({"mode": "vary", "variationMode": "color",
        "source": {"src": SRC}, "colorId": "blue"}, credits_reserved=19)))
    assert "success" in state and "failure" not in state


@pytest.mark.parametrize("mode", ["color", "closure"])
@pytest.mark.parametrize("reserved", [5, 19])
def test_edit_provider_failure_never_charges_or_publishes(recording_runtime, monkeypatch, mode, reserved):
    state = recording_runtime
    state["product"]["colors"].append({"id": "blue", "images": [{"id": B, "slot": "Front"}]})
    monkeypatch.setattr(repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    async def generate(*args, **kw):
        raise GeminiError("provider failed")
    monkeypatch.setattr(garment_edit, "generate", generate)
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job({"mode": "vary", "variationMode": mode,
        "source": {"src": SRC}, "colorId": "blue", "outerClosureState": "closed"}, credits_reserved=reserved)))
    assert "success" not in state and state["failure"]["reserved"] == reserved
    assert "charge" not in state["failure"]


@pytest.mark.parametrize("success", [False, True])
@pytest.mark.parametrize("reserved", [8, 19])
def test_upload_similar_settles_saved_reservation(recording_runtime, monkeypatch, success, reserved):
    state = recording_runtime
    monkeypatch.setattr(repo, "get_project_asset_for_user", AsyncMock(return_value={
        "id": A, "source": "upload", "metadata": {}, "r2_key": "upload", "mime_type": "image/png"}))
    monkeypatch.setattr(repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "get_similar_cut_plan_for_job", AsyncMock(return_value=None))
    monkeypatch.setattr(repo, "list_similar_cut_plans", AsyncMock(return_value=[]))
    plan = {"actionKey": "hands_low", "actionSummary": "손 내리기", "instructions": "Lower the hands.",
            "preserve": ["garment"], "avoid": ["relocation"]}
    envelope = {"version": 1, "rootAssetId": A, "sourceAssetId": A}
    monkeypatch.setattr(repo, "reserve_similar_cut_plan", AsyncMock(return_value={
        "status": "reserved", "similar_cut": {**envelope, "plan": plan}}))
    async def analyze(*args, **kw):
        if not success:
            raise worker.VisionError("No person in the source photograph")
        return plan, "fake"
    monkeypatch.setattr(similar_cut.vision_llm, "analyze_with_fallback", analyze)
    async def generate(settings, gemini, image, changes, cut_type, **kw):
        assert success and cut_type == "styling"
        return b"OUTPUT", "image/png"
    monkeypatch.setattr(worker.cut_variator, "generate", generate)
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job({"mode": "vary", "variationMode": "similar",
        "source": {"src": SRC, "cutType": "product"}, "_similarCut": envelope}, credits_reserved=reserved)))
    if success:
        assert state["success"]["charge"] == reserved and "failure" not in state
    else:
        assert "success" not in state and state["failure"]["reserved"] == reserved
        assert "charge" not in state["failure"]
        assert "No person" in state["failure"]["metadata"]["error"]


@pytest.mark.parametrize("cut_type,enabled,expected", [("styling", True, True), ("horizon", True, True),
    ("product", True, False), ("styling", False, False)])
def test_edit_face_pass_matches_similar_rules(monkeypatch, cut_type, enabled, expected):
    from app.agents.gemini_image import InlineImage
    from types import SimpleNamespace
    face_pass = AsyncMock(return_value=(b"FACE", "image/png"))
    monkeypatch.setattr(garment_edit.face_identity, "apply_face_pass", face_pass)
    class Gemini:
        async def generate_content_image(self, *args, **kw):
            assert "openai_size_override" not in kw
            return SimpleNamespace(image=b"GENERATED", mime="image/png")
    result = asyncio.run(garment_edit.generate(make_settings(face_identity_enabled=enabled), Gemini(),
        InlineImage("image/png", b"SOURCE"), None, cut_type, mode="closure", clothing_type="outer",
        closure_state="closed", front_closure=FRONT, face_identity_spec=object()))
    assert result[0] == (b"FACE" if expected else b"GENERATED")
    assert face_pass.await_count == int(expected)


def test_prompt_adapts_garment_noun_and_preserves_front_in_color_edit():
    prompt = garment_edit.prompt_for("color", clothing_type="dress", closure_state=None,
                                     front_closure={**FRONT, "kind": "button"})
    assert "the dress" in prompt and "jacket" not in prompt and "zipper" not in prompt
    outer = garment_edit.prompt_for("color", clothing_type="outer", closure_state=None,
                                    front_closure={**FRONT, "kind": "button"})
    assert "Keep how open or fastened the front is exactly as in the first image." in outer
    assert "zipper" not in outer


def test_asset_info_omits_missing_or_foreign_assets(client, make_token, edit_route, monkeypatch):
    monkeypatch.setattr(asset_recipe, "resolve_asset_recipes", AsyncMock(return_value={
        A: edit_route["record"], B: edit_route["record"]}))
    monkeypatch.setattr(repo, "get_project_asset_for_user", AsyncMock(side_effect=lambda conn, uid, pid, aid:
        edit_route["asset"] if aid == A else None))
    response = client.post("/v1/projects/p1/editor:asset-info", headers=auth_headers(make_token), json={"assetIds": [A, B, C]})
    assert response.status_code == 200 and set(response.json()["items"]) == {A}


@pytest.mark.parametrize("ids", [[], [A] * 60])
def test_asset_info_accepts_empty_and_60_ids(client, make_token, edit_route, ids):
    response = client.post("/v1/projects/p1/editor:asset-info", headers=auth_headers(make_token), json={"assetIds": ids})
    assert response.status_code == 200
    assert asset_recipe.resolve_asset_recipes.await_args.args[-1] == list(dict.fromkeys(ids))


@pytest.mark.parametrize("reserved", [5, 8, 19])
def test_success_wardrobe_result_and_event_include_asset_id(monkeypatch, reserved):
    from test_asset_privacy_metadata import _Conn
    conn = _Conn()
    monkeypatch.setattr(repo, "_consume_buckets", AsyncMock(return_value=81))
    result = asyncio.run(repo.finalize_editor_image_success(conn, job_id="j", lease_token="l", user_id="u", project_id="p",
        image={"asset_id": A, "bucket": "r2", "key": "out.png", "mime": "image/png"}, group="blue", cut_type="styling",
        reserved=reserved, charge=reserved, metadata={}))
    assert result["assetId"] == A
    envelope = next(params[0].obj for sql, params in conn.statements if sql.startswith("update jobs set status = 'done'"))
    event = next(params[1].obj for sql, params in conn.statements if sql.startswith("insert into job_events"))
    assert event == envelope and envelope["data"]["assetId"] == A
    assert envelope["creditsCharged"] == reserved and envelope["credits"] == 81
    assert repo._consume_buckets.await_args.kwargs["action_key"] == "editorImage"


@pytest.mark.parametrize("mode", ["color", "closure"])
@pytest.mark.parametrize("allowed", [False, True])
def test_real_derived_edit_uses_existing_license_gate(client, make_token, edit_route, monkeypatch, mode, allowed):
    license_row = {"id": C, "model_id": B}
    monkeypatch.setattr(repo, "get_asset_facemarket_provenance", AsyncMock(return_value={
        "real_derived": True, "cut_type": "horizon", "facemarket": {"modelId": B, "licenseId": C}}))
    monkeypatch.setattr(routes.facemarket, "consent_license", AsyncMock(return_value=license_row))
    monkeypatch.setattr(routes, "_reject_facemarket_cutover_closed", AsyncMock())
    def verify(app, row, **kw):
        assert row == license_row and kw["model_id"] == B
        if not allowed:
            raise routes.facemarket._err("model_unavailable", "사용할 수 없는 모델입니다.", status=409)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", verify)
    response = post_edit(client, make_token, mode)
    if allowed:
        assert response.status_code == 202
        assert edit_route["job"]["payload"]["_facemarket"] == {"modelId": B, "licenseId": C}
    else:
        assert response.status_code == 409 and response.json()["error"]["code"] == "model_unavailable"
        assert "reserved" not in edit_route and "job" not in edit_route


@pytest.mark.parametrize("mode", ["color", "closure"])
def test_real_edit_worker_rechecks_license_and_preserves_provenance(recording_runtime, monkeypatch, mode):
    from types import SimpleNamespace
    state = recording_runtime
    state["source_metadata"] = {"cut_type": "horizon", "facemarket_real_derived": True}
    state["product"]["colors"].append({"id": "blue", "images": [{"id": B, "slot": "Front"}]})
    monkeypatch.setattr(repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    license_row = {"id": C, "model_id": B}
    license_lookup = AsyncMock(return_value=license_row)
    monkeypatch.setattr(worker.facemarket, "wait_for_holder", AsyncMock())
    monkeypatch.setattr(worker.facemarket, "resolve_model_license", license_lookup)
    monkeypatch.setattr(worker.facemarket, "verify_license", AsyncMock())
    monkeypatch.setattr(worker.facemarket, "verify_license_local", lambda *a, **kw: None)
    monkeypatch.setattr(repo, "lock_facemarket_writer_boundary", AsyncMock())
    monkeypatch.setattr(worker.identity_source, "resolve_enabled_lora", AsyncMock(return_value={}))
    spec = object()
    monkeypatch.setattr(worker.face_identity, "face_identity_from_lora_row", lambda row: spec)
    monkeypatch.setattr(worker.identity_source, "reference_face_bytes", AsyncMock(return_value=[]))
    monkeypatch.setattr(worker.fm_fingerprint_store, "record_cut_fingerprints", AsyncMock())
    face_pass = AsyncMock(return_value=(b"FACE", "image/png"))
    monkeypatch.setattr(worker.face_identity, "apply_face_pass", face_pass)
    state["app"].state.settings = make_settings(face_identity_enabled=True)
    class Gemini:
        async def generate_content_image(self, *args, **kw):
            return SimpleNamespace(image=b"GENERATED", mime="image/png")
    state["app"].state.gemini = Gemini()
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job({"mode": "vary", "variationMode": mode,
        "source": {"src": SRC}, "colorId": "blue", "outerClosureState": "closed",
        "_facemarket": {"modelId": B, "licenseId": C}}, credits_reserved=19)))
    assert "failure" not in state, state.get("failure")
    assert face_pass.await_count == 1 and face_pass.await_args.args[3] is spec
    assert license_lookup.await_count == 2 and license_lookup.await_args.kwargs["for_update"] is True
    image = state["success"]["image"]
    assert image["metadata"]["facemarket_real_derived"] is True
    assert image["provenance"] == {"license_id": C, "model_id": B}


@pytest.mark.parametrize("mode", ["new", "vary"])
def test_fill_join_key_cannot_be_reused_for_another_request(mode):
    db = JobsDB([{"id": "fill", "kind": "editor_image", "status": "done", "idempotency_key": "original",
        "metadata": {"editorFillRequestKeys": ["joined"]},
        "payload": {"mode": "new", "cutType": "product", "_contextFill": {"slotKey": "block:first"}}}])
    async def create():
        return await CREATE_JOB(db, user_id="u", project_id="p", kind="editor_image",
            payload={"mode": mode, "variationMode": "color", "colorId": "blue"},
            idempotency_key="joined", credits_reserved=19, metadata={})
    with pytest.raises(repo.IdempotencyKeyReused):
        asyncio.run(create())
    assert db.inserts == 0


@pytest.mark.parametrize("mode", ["color", "closure"])
def test_immutable_product_cut_type_wins_over_modified_storyboard(client, make_token, edit_route, mode):
    edit_route["asset"]["metadata"]["cut_type"] = "product"
    response = post_edit(client, make_token, mode)
    assert response.status_code == 400 and response.json()["error"]["code"] == "edit_source_unsupported"
    assert "reserved" not in edit_route


def test_asset_info_does_not_call_original_product_a_worn_cut(client, make_token, edit_route):
    edit_route["asset"]["metadata"]["cut_type"] = "product"
    response = client.post("/v1/projects/p1/editor:asset-info", headers=auth_headers(make_token), json={"assetIds": [A]})
    assert response.status_code == 200
    assert response.json()["items"][A]["kind"] == "product_unknown"
    assert response.json()["items"][A]["cutType"] == "product"


def test_retry_join_key_cannot_join_an_active_fill_slot():
    class RetryJobsDB(JobsDB):
        async def execute(self, sql, params=None):
            if "idempotency_key = %s" in sql and "editorRetryRequestKeys" in sql:
                self.calls.append((sql, params))
                key = params[2]
                self.current = next((row for row in self.jobs if row.get("idempotency_key") == key
                    or key in row["metadata"].get("editorRetryRequestKeys", [])
                    or key in row["metadata"].get("editorFillRequestKeys", [])), None)
            else:
                await super().execute(sql, params)
    db = RetryJobsDB([
        {"id": "retry", "status": "done", "idempotency_key": "original-retry",
         "metadata": {"editorRetryRequestKeys": ["joined"]}, "payload": {"mode": "new", "retryBlockId": "planned"}},
        {"id": "fill", "status": "running", "idempotency_key": "original-fill", "metadata": {},
         "payload": {"_contextFill": {"slotKey": "block:empty"}}}])
    with pytest.raises(repo.IdempotencyKeyReused):
        asyncio.run(repo.find_context_fill_job(db, "u", "p", "joined", "block:empty"))
    assert "editorFillRequestKeys" not in db.jobs[1]["metadata"]


def test_verified_detail_recipe_wins_over_changed_ghost_storyboard(client, make_token, edit_route):
    edit_route["asset"]["metadata"] = {"cut_type": "product", "detail_recipe": {"version": 1}}
    edit_route["record"]["cutType"] = "product"
    edit_route["record"]["recipe"]["shot"] = "ghost"
    response = client.post("/v1/projects/p1/editor:asset-info", headers=auth_headers(make_token), json={"assetIds": [A]})
    assert response.status_code == 200 and response.json()["items"][A]["kind"] == "detail"


def test_similar_key_compares_selected_descendant_not_only_root():
    public = {"mode": "vary", "variationMode": "similar", "source": {"src": SRC, "cutType": "horizon"}}
    original = {**public, "_similarCut": {"version": 1, "rootAssetId": A, "sourceAssetId": B}}
    changed = {**public, "_similarCut": {"version": 1, "rootAssetId": A, "sourceAssetId": C}}
    db = JobsDB([{"id": "original", "kind": "editor_image", "project_id": "p", "status": "done",
                  "idempotency_key": "key", "metadata": {}, "payload": original}])
    with pytest.raises(repo.IdempotencyKeyReused):
        asyncio.run(CREATE_JOB(db, user_id="u", project_id="p", kind="editor_image", payload=changed,
                               idempotency_key="key", credits_reserved=19, metadata={}))
    assert db.inserts == 0


def test_route_similar_key_rejects_another_descendant_of_same_root(client, make_token, edit_route, monkeypatch):
    assets = {aid: {"id": aid, "source": "ai", "metadata": {"cut_type": "horizon",
        **({"similar_cut": {"version": 1, "rootAssetId": A}} if aid != A else {})}} for aid in (A, B, C)}
    monkeypatch.setattr(repo, "get_project_asset_for_user", AsyncMock(
        side_effect=lambda conn, uid, pid, aid: assets.get(aid)))
    db = JobsDB([])
    async def create(conn, **kw):
        return await CREATE_JOB(db, **kw)
    monkeypatch.setattr(repo, "create_job", create)
    reserve = AsyncMock(return_value=81)
    monkeypatch.setattr(repo, "reserve_credits", reserve)
    first = post_edit(client, make_token, "similar", source={"src": f"/v1/assets/{B}/file"})
    repeat = post_edit(client, make_token, "similar", source={"src": f"/v1/assets/{B}/file"})
    changed = post_edit(client, make_token, "similar", source={"src": f"/v1/assets/{C}/file"})
    assert first.status_code == repeat.status_code == 202 and first.json() == repeat.json()
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_key_reused"
    assert db.jobs[0]["payload"]["_similarCut"]["sourceAssetId"] == B
    assert reserve.await_count == db.inserts == 1


def test_similar_rejects_legacy_mirror_provenance_before_reservation(client, make_token, edit_route, monkeypatch):
    edit_route["asset"]["metadata"] = {}
    edit_route["record"] = {"cutType": "mirror", "recipe": None}
    monkeypatch.setattr(repo, "get_asset_facemarket_provenance", AsyncMock(return_value={
        "real_derived": False, "cut_type": "mirror",
    }))
    response = post_edit(client, make_token, "similar", source={"src": SRC, "cutType": "styling"})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unknown_cut_type"
    assert "job" not in edit_route and "reserved" not in edit_route


def test_queued_similar_rejects_legacy_mirror_recipe_and_releases_reservation(recording_runtime, monkeypatch):
    state = recording_runtime
    state["source_metadata"] = {}
    state["anchor"] = {"cutType": "mirror", "recipe": None, "realDerived": False}
    monkeypatch.setattr(repo, "list_wardrobe_images", AsyncMock(return_value=[]))
    planner = AsyncMock(side_effect=AssertionError("must reject before planning"))
    generate = AsyncMock(side_effect=AssertionError("must reject before image generation"))
    monkeypatch.setattr(worker, "_plan_similar_cut", planner)
    monkeypatch.setattr(worker.cut_variator, "generate", generate)
    asyncio.run(worker.run_editor_image_job(state["app"], worker_job({
        "mode": "vary", "variationMode": "similar", "source": {"src": SRC, "cutType": "styling"},
        "_similarCut": {"version": 1, "rootAssetId": A, "sourceAssetId": A},
    }, credits_reserved=8)))
    assert "success" not in state and state["failure"]["reserved"] == 8
    assert state["failure"]["metadata"]["error"] == "unknown_cut_type"
    planner.assert_not_awaited()
    generate.assert_not_awaited()
