"""Deterministic photo-color evidence; these tests are not camera calibration."""
from copy import deepcopy
from io import BytesIO
import math
import json

import pytest
from PIL import Image, ImageCms, ImageDraw

from app.agents import garment_color_evidence as gce
from app.agents import detail_recommendations
from app.agents import horizon_background


def photo(color=(45, 95, 155), *, mode="RGB", size=(320, 320), paint=None, **save_args):
    im = Image.new(mode, size, color)
    if paint:
        paint(im)
    out = BytesIO()
    im.save(out, format="PNG", **save_args)
    return out.getvalue()


def source(data=None, *, index=0, color_id="navy", slot="Front"):
    return {"sourceIndex": index, "colorId": color_id, "slot": slot,
            "data": photo() if data is None else data, "mime": "image/png"}


def rect(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def region(index=0, **overrides):
    return {"sourceIndex": index, "clothingType": "top", "certainty": "high",
            "colorStructure": "solid", "lighting": "neutral", "material": "matte",
            "polygons": [rect(.2, .2, .23, .23), rect(.55, .55, .23, .23)], **overrides}


def contract(rows=None, sources=None, ctype="top"):
    return gce.build_contract([region()] if rows is None else rows,
                              [source()] if sources is None else sources, clothing_type=ctype)


def first(value):
    return value["colors"][0]


def rgb(hex_value):
    return tuple(int(hex_value[i:i + 2], 16) for i in (1, 3, 5))


def test_known_main_garment_excludes_background_skin_and_other_garment():
    def paint(im):
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 319, 40), fill=(255, 255, 255))
        d.rectangle((0, 40, 45, 319), fill=(210, 145, 100))
        d.rectangle((260, 40, 319, 319), fill=(230, 30, 30))
    value = contract(sources=[source(photo(paint=paint))])
    row = first(value)
    assert row["status"] == "ready" and rgb(row["observedHex"]) == (45, 95, 155)
    assert 0 <= row["lightnessRange"][0] <= row["lightnessRange"][1] <= 1
    assert row["spread"] < .04 and 0 <= row["uncertainty"] <= 1
    assert gce.validate_contract(value) == value
    assert gce.public_summary(value) == {"version": 1, "clothingType": "top", "colors": [
        {"colorId": "navy", "status": "ready", "reason": row["reason"], "observedHex": row["observedHex"],
         "backgroundStatus": "ready", "backgroundReason": "measured-color",
         "backgroundPolicyVersion": horizon_background.POLICY["version"]}]}


def test_public_background_eligibility_uses_current_policy_not_signed_measurement(monkeypatch):
    value = contract(sources=[source(photo((199, 199, 199)))])
    row = first(value)
    assert row["status"] == "ready"
    summary = gce.public_summary(value)["colors"][0]
    assert summary["backgroundStatus"] == "reference"
    assert summary["backgroundReason"] == "lightness-ambiguous"
    assert "backgroundStatus" not in first(value)
    monkeypatch.setitem(horizon_background.POLICY, "version", "test-fresh-policy")
    assert gce.public_summary(value)["colors"][0]["backgroundPolicyVersion"] == "test-fresh-policy"
    unavailable = contract(rows=[region(lighting="cast")])
    assert gce.public_summary(unavailable)["colors"][0]["backgroundStatus"] == "reference"


def test_small_highlights_and_shadows_do_not_pull_the_representative_color():
    def paint(im):
        d = ImageDraw.Draw(im)
        for box in ((80, 80, 89, 95), (192, 192, 201, 207)):
            d.rectangle(box, fill=(250, 250, 250))
        for box in ((102, 90, 112, 100), (217, 205, 227, 215)):
            d.rectangle(box, fill=(30, 60, 100))
    row = first(contract(sources=[source(photo(paint=paint))]))
    assert row["status"] == "ready"
    assert max(abs(a - b) for a, b in zip(rgb(row["observedHex"]), (45, 95, 155))) < 6


def test_neutral_wrinkle_shading_is_measured_without_hiding_lightness_variation():
    def paint(im):
        data = im.load()
        for y in range(im.height):
            for x in range(im.width):
                v = round(190 + 35 * math.sin(x / 16) + 8 * math.cos(y / 11))
                data[x, y] = (v, v, v)
    row = first(contract(sources=[source(photo(paint=paint))]))
    assert row["status"] == "ready"
    assert max(rgb(row["observedHex"])) == min(rgb(row["observedHex"]))
    assert row["lightnessRange"][1] - row["lightnessRange"][0] > .15
    assert row["spread"] > .04  # Actual OKLab variation, not the weighted gate metric.


def test_strong_neutral_bimodal_stripes_reject_even_when_falsely_marked_solid():
    def paint(im):
        draw = ImageDraw.Draw(im)
        for x in range(0, 320, 12):
            draw.rectangle((x, 0, x + 5, 319), fill=(15, 15, 15))
    row = first(contract(sources=[source(photo((245, 245, 245), paint=paint))]))
    assert row["status"] == "unavailable" and row["reason"] == "mixed_pixels"


def test_separate_dark_and_light_uniform_patches_do_not_gain_shading_exemption():
    def paint(im):
        ImageDraw.Draw(im).rectangle((160, 160, 319, 319), fill=(220, 220, 220))
    row = first(contract(sources=[source(photo((50, 50, 50), paint=paint))]))
    assert row["status"] == "unavailable" and row["reason"] == "patch_disagreement"


@pytest.mark.parametrize("flags", [
    {"certainty": "uncertain"}, {"colorStructure": "patterned"}, {"colorStructure": "multicolor"},
    {"lighting": "cast"}, {"material": "glossy"}, {"material": "sheer"},
    {"clothingType": "bottom"}, {"clothingType": "uncertain"},
])
def test_unreliable_flags_only_veto_never_prove_the_measured_color(flags):
    assert first(contract(rows=[region(**flags)]))["status"] == "unavailable"


def test_pixels_reject_pattern_even_when_provider_claims_solid():
    def paint(im):
        d = ImageDraw.Draw(im)
        for x in range(0, 320, 12):
            d.rectangle((x, 0, x + 5, 319), fill=(230, 35, 35))
    row = first(contract(sources=[source(photo(paint=paint))]))
    assert row["status"] == "unavailable" and row["reason"] == "mixed_pixels"


def test_differently_colored_uniform_patches_are_rejected():
    def paint(im):
        ImageDraw.Draw(im).rectangle((160, 160, 319, 319), fill=(210, 80, 40))
    row = first(contract(sources=[source(photo(paint=paint))]))
    assert row["status"] == "unavailable" and row["reason"] == "patch_disagreement"


def test_front_back_disagreement_and_one_unreliable_view_reject_whole_color():
    sources = [source(), source(photo((165, 60, 30)), index=1, slot="Back")]
    assert first(contract(rows=[region(), region(1)], sources=sources))["reason"] == "view_disagreement"
    sources[1] = source(index=1, slot="Back")
    assert first(contract(rows=[region(), region(1, lighting="cast")], sources=sources))["status"] == "unavailable"
    assert first(contract(rows=[region()], sources=sources))["status"] == "unavailable"


def test_transparency_inside_roi_rejects_but_outside_roi_is_masked():
    assert first(contract(sources=[source(photo((45, 95, 155, 90), mode="RGBA"))]))["status"] == "unavailable"
    def paint(im):
        ImageDraw.Draw(im).rectangle((0, 0, 40, 319), fill=(255, 0, 0, 0))
    row = first(contract(sources=[source(photo((45, 95, 155, 255), mode="RGBA", paint=paint))]))
    assert row["status"] == "ready" and rgb(row["observedHex"]) == (45, 95, 155)


@pytest.mark.parametrize("polygons", [
    [rect(.2, .2, .2, .2)],
    [rect(.2, .2, .2, .2), rect(.25, .25, .2, .2)],
    [[[.1, .1], [.4, .4], [.1, .4], [.4, .1]], rect(.6, .6, .2, .2)],
    [rect(.1, .1, .001, .001), rect(.6, .6, .001, .001)],
    [rect(-.1, .1, .2, .2), rect(.6, .6, .2, .2)],
    [[[float("nan"), .1], [.3, .1], [.3, .3]], rect(.6, .6, .2, .2)],
    [[[.25, .05], [.38, .44], [.05, .2], [.45, .2], [.12, .44]], rect(.6, .6, .2, .2)],
])
def test_bad_or_uninformative_geometry_is_unavailable(polygons):
    assert first(contract(rows=[region(polygons=polygons)]))["status"] == "unavailable"


def test_unknown_indices_are_ignored_but_duplicate_known_indices_reject():
    assert first(contract(rows=[region(7), region()]))["status"] == "ready"
    assert first(contract(rows=[region(), region()]))["status"] == "unavailable"
    assert first(contract(rows=[region(sourceIndex=True)]))["status"] == "unavailable"


def test_model_cannot_supply_the_color_identity_or_hex():
    value = contract(rows=[region(colorId="injected", observedHex="#FF0000")])
    assert [row["colorId"] for row in value["colors"]] == ["navy"]
    assert first(value)["status"] == "unavailable"


@pytest.mark.parametrize("raw", [None, {}, [], "no regions"])
def test_empty_observer_response_is_nonfatal_and_unavailable_per_color(raw):
    sources = [source(index=0, color_id="c1"), source(index=4, color_id="c2")]
    value = gce.build_contract(raw, sources, clothing_type="top")
    assert [row["status"] for row in value["colors"]] == ["unavailable", "unavailable"]
    assert gce.validate_contract(value) == value


def test_empty_source_list_is_nonfatal_but_does_not_match_evidence():
    value = gce.build_contract(None, [], clothing_type="top")
    assert value["colors"] == [] and value["sourceBindings"] == []
    assert gce.validate_contract(value) == value
    assert not gce.source_binding_matches(value, [])


def test_schema_allows_uncertain_empty_regions_without_forced_coordinates():
    assert gce.regions_schema()["items"]["properties"]["polygons"]["minItems"] == 0
    assert first(contract(rows=[region(certainty="uncertain", polygons=[])]))["status"] == "unavailable"


def test_source_binding_ignores_reenumerated_index_but_not_color_slot_or_bytes():
    sources = [source(index=4), source(index=5, slot="Back")]
    value = contract(rows=[region(4), region(5)], sources=sources)
    runtime = [dict(sources[0], sourceIndex=0), dict(sources[1], sourceIndex=1)]
    assert gce.source_binding_matches(value, runtime, clothing_type="top")
    assert not gce.source_binding_matches(value, runtime, clothing_type="bottom")
    for field, replacement in (("colorId", "other"), ("slot", "Back"), ("data", photo((10, 10, 10)))):
        changed = deepcopy(runtime)
        changed[0][field] = replacement
        assert not gce.source_binding_matches(value, changed)
    assert not gce.source_binding_matches(value, runtime[:1])


def test_signed_handoff_tamper_expiry_future_and_cross_purpose():
    secret = "test-only-secret-long-enough"
    value = contract()
    envelope = gce.issue_handoff(value, secret, now=1000)
    assert gce.verify_handoff(envelope, secret, now=1001) == value
    for mutate in (lambda v: v.update(purpose=detail_recommendations.PERSISTED_KEY),
                   lambda v: v["contract"]["colors"][0].update(observedHex="#000000"),
                   lambda v: v.update(signature="0" * 64)):
        changed = deepcopy(envelope)
        mutate(changed)
        with pytest.raises(ValueError):
            gce.verify_handoff(changed, secret, now=1001)
    with pytest.raises(ValueError):
        gce.verify_handoff(envelope, secret, now=1000 + 86401)
    with pytest.raises(ValueError):
        gce.verify_handoff(envelope, secret, now=0)
    with pytest.raises(ValueError):
        detail_recommendations.verify_handoff(envelope, secret, now=1001)


@pytest.mark.parametrize("color", [(255, 255, 255), (0, 0, 0), (45, 95, 155)])
def test_color_handoff_survives_browser_whole_number_serialization(color):
    value = contract(sources=[source(photo(color))])
    assert first(value)["status"] == "ready"
    secret = "test-color-browser-secret"
    envelope = gce.issue_handoff(value, secret, now=1000)
    # JSON.stringify writes 0.0/1.0 as 0/1. Keep fractional values exact rather
    # than round them, so this matches a browser round trip without needing Node in CI.
    def browser_number(text):
        number = float(text)
        return int(number) if number.is_integer() else number
    returned = json.loads(json.dumps(envelope), parse_float=browser_number)
    assert type(first(returned["contract"])["spread"]) is int
    assert gce.verify_handoff(returned, secret, now=1001) == value
    assert first(returned["contract"])["uncertainty"] == first(value)["uncertainty"]
    # Even the nearest representable different fraction must remain a change.
    first(returned["contract"])["uncertainty"] = math.nextafter(first(value)["uncertainty"], 1.0)
    with pytest.raises(ValueError, match="signature_invalid"):
        gce.verify_handoff(returned, secret, now=1001)


def test_exif_upright_roi_matches_observer_frame():
    im = Image.new("RGB", (320, 200), (200, 40, 40))
    ImageDraw.Draw(im).rectangle((0, 0, 159, 199), fill=(40, 80, 160))
    exif = Image.Exif(); exif[274] = 6
    buf = BytesIO(); im.save(buf, format="PNG", exif=exif)
    data = buf.getvalue()
    normalized, mime = gce.normalize_for_vision(data, "image/png")
    with Image.open(BytesIO(normalized)) as actual:
        assert actual.size == (200, 320) and actual.getexif().get(274, 1) == 1
    patches = [rect(.1, .08, .3, .25), rect(.6, .08, .3, .25)]
    row = first(contract(rows=[region(polygons=patches)], sources=[source(data)]))
    assert row["status"] == "ready" and max(abs(a-b) for a,b in zip(rgb(row["observedHex"]), (40,80,160))) < 3


def test_bad_icc_is_nonfatal_for_observer_but_not_measurable():
    data = photo(icc_profile=b"not-a-real-color-profile")
    assert gce.normalize_for_vision(data, "image/png") == (data, "image/png")
    assert first(contract(sources=[source(data)]))["reason"] == "invalid_color_profile"


def test_valid_srgb_profile_and_noop_return_preserve_measurement():
    original = photo()
    assert gce.normalize_for_vision(original, "image/png") == (original, "image/png")
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    profiled = photo(icc_profile=profile)
    normalized, mime = gce.normalize_for_vision(profiled, "image/png")
    assert normalized != profiled and mime == "image/png"
    row = first(contract(sources=[source(profiled)]))
    assert row["status"] == "ready" and rgb(row["observedHex"]) == (45,95,155)


def test_profile_conversion_matches_observer_pixels_and_binds_original_bytes():
    srgb = ImageCms.createProfile("sRGB")
    lab = ImageCms.createProfile("LAB")
    original = Image.new("RGB", (320, 320), (70, 110, 150))
    encoded = ImageCms.profileToProfile(original, srgb, lab, outputMode="LAB")
    out = BytesIO()
    encoded.save(out, format="TIFF", icc_profile=ImageCms.ImageCmsProfile(lab).tobytes())
    data = out.getvalue()
    normalized, mime = gce.normalize_for_vision(data, "image/tiff")
    assert normalized != data and mime == "image/png"
    with Image.open(BytesIO(normalized)) as observer:
        observer_color = observer.convert("RGB").getpixel((100, 100))
        assert "icc_profile" not in observer.info
    value = contract(sources=[source(data)])
    assert rgb(first(value)["observedHex"]) == observer_color
    assert gce.source_binding_matches(value, [source(data)])
    assert not gce.source_binding_matches(value, [source(normalized)])


def test_clipped_white_stays_neutral_with_nonzero_uncertainty_range():
    row = first(contract(sources=[source(photo((255, 255, 255)))]))
    assert row["status"] == "ready" and row["observedHex"] == "#FFFFFF"
    assert .97 < row["lightnessRange"][0] < row["lightnessRange"][1] <= 1


def test_selected_color_projection_and_reordered_runtime_sources():
    sources = [source(index=0, color_id="base"), source(index=4), source(index=5, slot="Back")]
    value = contract(rows=[region(), region(4), region(5)], sources=sources)
    projection = {**value, "colors": [value["colors"][1]],
                  "sourceBindings": [row for row in value["sourceBindings"] if row["colorId"] == "navy"]}
    runtime = [dict(sources[2], sourceIndex=0), dict(sources[1], sourceIndex=1)]
    assert gce.source_binding_matches(projection, runtime)


def test_invalid_source_bytes_are_bound_but_unavailable():
    value = contract(sources=[source(b"not an image")])
    assert first(value)["reason"] == "invalid_image"
    assert gce.source_binding_matches(value, [source(b"not an image")])


def test_contract_validation_rejects_forged_summary_fields():
    value = contract()
    for field, bad in (("observedHex", "red"), ("spread", float("nan")),
                       ("lightnessRange", [1, 0]), ("uncertainty", -1)):
        changed = deepcopy(value)
        changed["colors"][0][field] = bad
        with pytest.raises(ValueError):
            gce.validate_contract(changed)
