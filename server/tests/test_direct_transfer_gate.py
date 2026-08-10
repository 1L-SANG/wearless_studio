"""이음매 — 렌더러와 QC 가 **같은 입력**을 보고, 미승격 픽셀은 나가지 않는다."""

import dataclasses
import inspect

import numpy as np

from app.services.hybrid_composite import direct_torso_transfer as dtt
from app.services.hybrid_composite import direct_transfer_gate as gate

from test_direct_torso_transfer import (
    FOUR_COLOUR, IDENTITY_QUAD, carrier, landmarks_for, make_panel_map,
    source_with_margin)


def _inputs(quad=IDENTITY_QUAD):
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(quad, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    return carrier(500, 700), pm, src, lm, smask


def test_a_correct_transfer_is_promoted_and_returns_pixels():
    car, pm, src, lm, smask = _inputs()
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is True, out.reasons
    assert out.image_bgr is not None
    assert out.image_bgr.shape == car.shape
    assert out.qc_checks and out.metrics["interiorPx"] > 0


def test_an_unpromoted_candidate_never_hands_back_pixels():
    """보존 ≠ 권한. 후보는 남기되 **픽셀은 내주지 않는다** — 실수로 쓸 길을 막는다."""
    car, pm, src, lm, smask = _inputs()
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm,
        source_garment_mask=None,          # 원본 근거를 검증할 수 없다 → 승격 불가
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is False
    assert out.image_bgr is None
    assert out.reasons
    # 그래도 후보와 측정은 남는다 — 왜 막혔는지 사후에 봐야 한다.
    assert out.candidate is not None
    assert out.qc_checks


def test_an_impossible_render_is_a_candidate_failure_not_an_exception():
    """렌더 불가는 잡 실패가 아니다 — typed 결과로 돌아온다."""
    car, pm, src, lm, smask = _inputs()
    # 원본 마스크를 전부 비우면 원본이 뒷받침하는 픽셀이 0 이 된다.
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm,
        source_garment_mask=np.zeros_like(smask),
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is False
    assert out.image_bgr is None
    assert gate.REASON_TRANSFER_UNAVAILABLE in out.reasons
    assert out.candidate is None


def test_the_oracle_sees_the_same_inputs_as_the_renderer(monkeypatch):
    """배선이 어긋나면 오라클이 다른 그림을 채점한다.

    소스 문자열에서 이름 **개수**를 세는 것은 시험이 아니다 — 인자를 바꿔 넘겨도
    개수는 그대로다(실측: `source_component_boxes=carrier_component_boxes` 로 바꿔도
    통과했다). 실제로 두 호출이 받은 값을 **캡처해서** 비교한다.
    """
    car, pm, src, lm, smask = _inputs()
    cboxes = {"p": np.float32([[300, 80], [352, 80], [352, 620], [300, 620]])}
    sboxes = {"p": np.float32([[100, 100], [160, 100], [160, 600], [100, 600]])}
    seen: dict = {}

    real_transfer = gate.transfer_torso_texture
    real_qc = gate.evaluate_direct_transfer

    def spy_transfer(carrier_bgr, panel_map, source_bgr, **kw):
        seen["render"] = {**kw, "_carrier": carrier_bgr, "_source": source_bgr,
                          "_panel": panel_map}
        out = real_transfer(carrier_bgr, panel_map, source_bgr, **kw)
        seen["rendered"] = out
        return out

    def spy_qc(candidate, **kw):
        seen["qc"] = kw
        seen["qc_candidate"] = candidate       # **어느 후보를** 쟀는지도 붙잡는다
        return real_qc(candidate, **kw)

    monkeypatch.setattr(gate, "transfer_torso_texture", spy_transfer)
    monkeypatch.setattr(gate, "evaluate_direct_transfer", spy_qc)
    gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE, source_sha256="s", carrier_sha256="c")

    render, qc_kw = seen["render"], seen["qc"]
    for name in ("source_landmarks", "source_garment_mask", "carrier_component_boxes",
                 "source_component_boxes", "shading", "source_sha256", "carrier_sha256"):
        assert qc_kw[name] is render[name], name
    assert qc_kw["carrier_bgr"] is render["_carrier"]
    assert qc_kw["source_bgr"] is render["_source"]
    assert qc_kw["panel_map"] is render["_panel"]
    # 그리고 **그 후보**를 쟀어야 한다. 인자만 맞고 다른 후보를 채점하면 의미가 없다
    # (실측: QC 직전에 painted 를 바꾼 변종이 12개 시험을 모두 통과했다).
    assert seen["qc_candidate"] is seen["rendered"]


def test_a_forged_candidate_cannot_reach_the_product_path(monkeypatch):
    """QC 를 통과하지 못한 픽셀은 어떤 경로로도 `image_bgr` 로 나오지 않는다."""
    car, pm, src, lm, smask = _inputs()
    real = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    # 실루엣 밖까지 칠하고 그것을 주장하는 후보.
    dirty = real.image_bgr.copy()
    outside = np.nonzero(pm.garment_mask == 0)
    dirty[outside[0][:300], outside[1][:300]] = (0, 0, 255)
    painted = real.painted.copy()
    painted[outside[0][:300], outside[1][:300]] = 255
    forged = dataclasses.replace(real, image_bgr=dirty, painted=painted)
    monkeypatch.setattr(gate, "transfer_torso_texture", lambda *a, **k: forged)

    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is False, out.reasons
    assert out.image_bgr is None
    assert out.candidate is forged            # 보존은 된다


def test_the_gate_makes_no_io_calls():
    """구조로 고정한다: 이 경로에 DB·provider·R2 가 끼어들 자리가 없다."""
    src_text = inspect.getsource(gate)
    for banned in ("requests", "httpx", "boto3", "psycopg", "await ", "async def",
                   "open(", "genai", "r2", "upload"):
        assert banned not in src_text, banned


def test_a_renderer_exception_becomes_a_candidate_failure():
    """이음매에서 예외가 새면 잡이 죽는다 — 후보 실패로 봉인한다."""
    car, pm, src, lm, smask = _inputs()
    mismatched = make_panel_map(IDENTITY_QUAD, w=499, h=700)   # carrier 와 폭이 다르다
    out = gate.run_gated_direct_transfer(
        car, mismatched, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is False
    assert out.image_bgr is None
    assert gate.REASON_TRANSFER_UNAVAILABLE in out.reasons
    assert out.metrics.get("reason") == "transfer_raised"


def test_a_qc_exception_is_not_read_as_a_pass():
    """측정이 사라지면 승격도 없다."""
    car, pm, src, lm, smask = _inputs()
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm,
        source_garment_mask=np.ones((700, 499), np.uint8) * 255,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is False
    assert out.image_bgr is None


def test_the_lineage_hashes_reach_the_oracle():
    """호출자 계보를 QC 로 전달하지 않으면 옳은 렌더가 provenance 불일치가 된다."""
    car, pm, src, lm, smask = _inputs()
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE, source_sha256="lineage-src",
        carrier_sha256="lineage-car")
    assert out.promoted is True, out.reasons
    prov = out.qc_checks["provenance"]
    assert prov["complete"] is True, prov
    assert prov["mismatchedKeys"] == [], prov


def test_a_component_the_garment_mask_clips_is_judged_on_what_was_painted():
    """행렬식을 원본 quad 전체에서 세면 그리지도 않은 픽셀이 섞인다.

    실측(v11 초안): 실제로 칠한 10,439 px 은 전부 양수인데 quad 전체에서는 80,081 px 이
    음수라고 보고돼 정상 렌더가 `mapping_is_mirrored` 로 막혔다.
    """
    car, _pm, src, lm, smask = _inputs()
    mask = np.zeros((700, 500), np.uint8)
    mask[100:220, 100:200] = 255
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700, mask=mask)
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes={"p": np.float32([[100, 100], [400, 100],
                                                  [120, 460], [100, 500]])},
        source_component_boxes={"p": np.float32([[100, 100], [300, 100],
                                                 [300, 500], [100, 500]])},
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is True, out.reasons


def test_a_component_fully_covered_by_a_later_one_does_not_block():
    """겹치면 뒤 이름이 좌표를 덮는다 — 소유도 함께 넘어가야 한다.

    실측: 반사된 `a` 가 0 px 을 그렸는데(전부 `b` 가 덮음) `a` 의 반사 때문에 보류됐다.
    """
    car, pm, src, lm, smask = _inputs()
    box = np.float32([[100, 100], [300, 100], [300, 300], [100, 300]])
    mirrored = np.float32([[300, 100], [100, 100], [100, 300], [300, 300]])
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes={"a": mirrored, "b": box},
        source_component_boxes={"a": box, "b": box},
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is True, out.reasons


def test_a_full_canvas_garment_mask_is_not_a_silhouette_leak():
    """실루엣 밖 픽셀이 하나도 없으면 밖의 위반도 0 이다 — None 을 위반으로 읽으면 안 된다."""
    car, _pm, src, lm, smask = _inputs()
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700,
                        mask=np.full((700, 500), 255, np.uint8))
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is True, out.reasons
    assert out.qc_checks["containment"]["alphaNonZeroOutsideGarmentPx"] == 0


def test_pixels_are_returned_only_for_a_promoted_verdict(monkeypatch):
    """어떤 보류 사유든 **픽셀은 나가지 않는다** — 사유별로 확인한다.

    실측(v12 초안): 특정 사유에만 반응하도록 바꾼 변종이 12개 시험을 전부 통과하면서
    미승격 판정에 281,600 px 을 내보냈다.
    """
    from app.services.hybrid_composite import direct_transfer_promotion as promo
    car, pm, src, lm, smask = _inputs()
    # **모든** 사유를 모듈에서 뽑는다. 손으로 고른 목록은 새 사유가 생길 때마다 구멍이
    # 된다(실측: 15개 중 6개만 덮여 있었고, 빠진 사유로 픽셀을 흘리는 변종이 통과했다).
    reasons = [v for k, v in vars(promo).items()
               if k.startswith("REASON_") and isinstance(v, str)]
    assert len(reasons) >= 10, reasons
    for reason in reasons:
        monkeypatch.setattr(
            gate, "evaluate_direct_transfer_promotion",
            lambda _r, _reason=reason: promo.PromotionVerdict(False, (_reason,), {}))
        out = gate.run_gated_direct_transfer(
            car, pm, src, source_landmarks=lm, source_garment_mask=smask,
            shading=dtt.SHADING_RAW_SOURCE)
        assert out.promoted is False, reason
        assert out.image_bgr is None, reason


def test_a_raising_qc_is_sealed_as_unmeasured(monkeypatch):
    """`inputs` 거절이 아니라 **진짜 예외**로 확인한다."""
    car, pm, src, lm, smask = _inputs()

    def boom(*a, **k):
        raise RuntimeError("qc exploded")

    monkeypatch.setattr(gate, "evaluate_direct_transfer", boom)
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is False
    assert out.image_bgr is None
    assert out.metrics.get("reason") == "qc_raised"


def test_a_raising_promotion_is_sealed(monkeypatch):
    car, pm, src, lm, smask = _inputs()

    def boom(*a, **k):
        raise RuntimeError("promotion exploded")

    monkeypatch.setattr(gate, "evaluate_direct_transfer_promotion", boom)
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is False
    assert out.image_bgr is None
    assert out.metrics.get("reason") == "promotion_raised"


def test_pixels_returned_are_the_pixels_that_were_assessed(monkeypatch):
    """후보 배열은 가변이다 — 측정 뒤에 바뀌면 승격 도장이 다른 그림에 찍힌다.

    실측(v13 초안): QC 직후 `candidate.image_bgr[:] = carrier_bgr` 한 줄을 넣은 변종이
    63개 시험을 전부 통과했다(잰 것은 281,600 px 변경, 내보낸 것은 0 px 변경).
    """
    car, pm, src, lm, smask = _inputs()
    real_qc = gate.evaluate_direct_transfer

    def mutate_after_qc(candidate, **kw):
        out = real_qc(candidate, **kw)
        candidate.image_bgr[:] = car          # 측정 뒤에 그림을 바꿔치기한다
        return out

    monkeypatch.setattr(gate, "evaluate_direct_transfer", mutate_after_qc)
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is False, out.reasons
    assert out.image_bgr is None
    assert out.metrics.get("reason") == "candidate_mutated_during_assessment"


def test_the_returned_image_does_not_alias_the_candidate():
    """반환된 그림이 후보 배열을 **가리키기만** 하면, 반환 뒤 변조가 제품에 그대로 간다.

    실측(v15 초안): 스냅샷 대신 `np.asarray(candidate.image_bgr)` 를 돌려주는 변종이
    181개 시험을 전부 통과했고, 반환 뒤 후보를 바꾸자 출력 350,000 px 이 따라 바뀌었다.
    """
    car, pm, src, lm, smask = _inputs()
    out = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    assert out.promoted is True, out.reasons
    assert not np.shares_memory(out.image_bgr, out.candidate.image_bgr)

    before = out.image_bgr.copy()
    out.candidate.image_bgr[:] = car          # 반환 **뒤** 후보를 바꾼다
    assert np.array_equal(out.image_bgr, before), "반환된 그림이 후보를 따라 바뀌었다"
