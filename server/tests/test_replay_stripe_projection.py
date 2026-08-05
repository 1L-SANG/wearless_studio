"""offline replay 하네스 — 유료 호출 없이 합성기를 반복 검증하기 위한 계약.

replay 가 프로덕션과 다른 코드를 타면 그 통과는 아무것도 보증하지 않는다. 그래서
여기서 검증하는 것은 결과값이 아니라 **경로의 동일성과 재현성**이다:
같은 입력이 같은 해시를 내는가, 프로덕션 진입점을 쓰는가, 외부 호출이 정말 0인가.
"""

import hashlib
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import replay_stripe_projection as replay  # noqa: E402

from hybrid_stripe_fixtures import render_carrier  # noqa: E402


# ── 외부 호출 금지 계약 ───────────────────────────────────────────────────────

def test_replay_module_imports_no_io_surfaces():
    """DB·R2·provider·worker 를 끌어오면 replay 가 무비용이라는 전제가 깨진다.

    문자열 검색이 아니라 import 구문을 파싱한다 — 주석이나 docstring 에 이름이
    등장하는 것과 실제로 끌어오는 것은 다르다.
    """
    import ast
    tree = ast.parse(Path(replay.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = ("mannequin_job", "psycopg", "boto3", "asyncpg", "vision_llm",
              "gemini_image", "r2", "repo")
    for mod in imported:
        for bad in banned:
            assert bad not in mod, f"replay 가 {mod} 를 import 하면 무비용이 아니다"


def test_replay_uses_production_entry_points():
    """워커가 부르는 것과 같은 함수를 같은 이름으로 부른다."""
    src = Path(replay.__file__).read_text()
    for fn in ("build_panel_map", "composite_stripe", "verify_composite",
               "extract_stripe_model_scan"):
        assert fn in src, f"프로덕션 진입점 {fn} 을 쓰지 않음"
    # 합성 로직을 replay 안에 다시 구현하지 않았는지 — 대표 상수의 재정의 금지
    for leaked in ("MAX_ASSIGN_COST", "INNER_FEATHER_PERIODS", "MIN_DECAL_SHORT_SIDE_PX"):
        assert f"{leaked} =" not in src, f"{leaked} 를 replay 가 다시 정의함(복제 구현)"


def test_run_projection_signature_matches_worker_contract():
    sig = inspect.signature(replay.run_projection)
    assert list(sig.parameters) == ["carrier", "source", "geo"]


# ── geometry 계약 ─────────────────────────────────────────────────────────────

def _write_dataset(tmp_path: Path, *, landmarks=None, mask=None) -> Path:
    import cv2
    cx = render_carrier("G1_regular", 0)
    d = tmp_path / "ds"
    d.mkdir()
    cv2.imwrite(str(d / "carrier.png"), cx["image"])
    cv2.imwrite(str(d / "source_front.png"), cx["image"])
    if mask is not None:
        cv2.imwrite(str(d / "garment_mask.png"), mask)
    geo = {
        "schema": replay.GEOMETRY_SCHEMA,
        "carrier_landmarks": landmarks,
        "source_inventory": {"collar": True, "placket": True, "cuffs": True},
        "carrier_inventory": {"collar": True, "placket": True, "cuffs": True},
        "target_period_px": 30.0,
        "garment_axis": "horizontal",
        "stripe_model": {"source_roi": [0, 0, cx["image"].shape[1], cx["image"].shape[0]]},
        "mode": "enforce",
        "carrier_preflight_inputs": {
            "carrier_evidence": {"garment_categories": ["top"]},
            "canonical_evidence": {"expected_categories": ["top"]},
            "matching_evidence": {"matched": True},
            "landmarks": (landmarks or {
                "shoulder_l": [0.28, 0.18], "shoulder_r": [0.72, 0.18],
                "hem_l": [0.30, 0.72], "hem_r": [0.70, 0.72],
                "confidence": 0.9,
            }),
            "carrier_inventory": {
                "collar": True, "placket": True, "cuffs": True,
                "garment_categories": ["top"],
            },
            "canonical_inventory": {
                "collar": True, "placket": True, "cuffs": True,
                "garment_categories": ["top"],
            },
            "vision_observations": {},
            "require_vision": False,
            "matching_expected": False,
        },
    }
    (d / "geometry.json").write_text(json.dumps(geo))
    return d


def test_missing_geometry_is_a_hard_stop(tmp_path):
    import cv2
    cx = render_carrier("G1_regular", 0)
    d = tmp_path / "empty"
    d.mkdir()
    cv2.imwrite(str(d / "carrier.png"), cx["image"])
    with pytest.raises(SystemExit) as e:
        replay.load_geometry(d, cx["image"])
    assert "geometry.json" in str(e.value)


def test_unknown_geometry_schema_is_rejected(tmp_path):
    d = _write_dataset(tmp_path, landmarks={"shoulder_l": [0.3, 0.2]})
    path = d / "geometry.json"
    geo = json.loads(path.read_text())
    geo["schema"] = "something_else"
    path.write_text(json.dumps(geo))
    with pytest.raises(SystemExit) as e:
        replay.load_geometry(d, np.zeros((4, 4, 3), np.uint8))
    assert "schema" in str(e.value)


def test_captured_landmarks_are_used_verbatim(tmp_path):
    lm = {"shoulder_l": [0.3, 0.2], "shoulder_r": [0.7, 0.2],
          "hem_l": [0.32, 0.7], "hem_r": [0.68, 0.7]}
    d = _write_dataset(tmp_path, landmarks=lm)
    geo, prov = replay.load_geometry(d, np.zeros((4, 4, 3), np.uint8))
    assert prov["landmarks"] == "captured"
    assert geo["carrier_landmarks"] == lm


def test_landmarks_are_reconstructed_only_when_absent(tmp_path):
    """캡처 이전 데이터셋은 mask 에서 복원하되, 그 사실이 결과에 표시돼야 한다."""
    cx = render_carrier("G1_regular", 0)
    mask = np.zeros(cx["image"].shape[:2], np.uint8)
    mask[200:900, 250:600] = 255
    d = _write_dataset(tmp_path, landmarks=None, mask=mask)
    geo, prov = replay.load_geometry(d, cx["image"])
    assert prov["landmarks"] == "reconstructed_from_mask"
    lm = geo["carrier_landmarks"]
    assert lm["shoulder_l"][0] < lm["shoulder_r"][0]
    assert lm["shoulder_l"][1] < lm["hem_l"][1]


def test_reconstruction_without_a_mask_is_a_hard_stop(tmp_path):
    d = _write_dataset(tmp_path, landmarks=None, mask=None)
    with pytest.raises(SystemExit) as e:
        replay.load_geometry(d, np.zeros((4, 4, 3), np.uint8))
    assert "replay 불가" in str(e.value)


def test_production_replay_requires_captured_carrier_preflight(tmp_path, monkeypatch):
    lm = {"shoulder_l": [0.28, 0.18], "shoulder_r": [0.72, 0.18],
          "hem_l": [0.30, 0.72], "hem_r": [0.70, 0.72]}
    d = _write_dataset(tmp_path, landmarks=lm)
    geo, _ = replay.load_geometry(d, np.zeros((4, 4, 3), np.uint8))
    geo.pop("carrier_preflight_inputs")
    called = False

    def should_not_extract(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("preflight 전에 stripe extraction을 호출하면 안 된다")

    monkeypatch.setattr(replay.hc_stripe, "extract_stripe_model_scan", should_not_extract)
    result = replay.run_projection(np.zeros((32, 32, 3), np.uint8),
                                   np.zeros((32, 32, 3), np.uint8), geo)
    assert result["stage"] == "carrier_preflight"
    assert result["failure"] == "preflight_evidence_missing"
    assert called is False


def test_replayed_carrier_preflight_rejects_before_projection(tmp_path, monkeypatch):
    lm = {"shoulder_l": [0.28, 0.18], "shoulder_r": [0.72, 0.18],
          "hem_l": [0.30, 0.72], "hem_r": [0.70, 0.72]}
    d = _write_dataset(tmp_path, landmarks=lm)
    geo, _ = replay.load_geometry(d, np.zeros((4, 4, 3), np.uint8))
    inputs = geo["carrier_preflight_inputs"]
    inputs["canonical_evidence"] = {
        "expected_categories": ["top", "pants"], "expected_lower": True}
    called = False

    def should_not_extract(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("거절 carrier에 projection을 호출하면 안 된다")

    monkeypatch.setattr(replay.hc_stripe, "extract_stripe_model_scan", should_not_extract)
    result = replay.run_projection(np.zeros((32, 32, 3), np.uint8),
                                   np.zeros((32, 32, 3), np.uint8), geo)
    assert result["stage"] == "carrier_preflight"
    assert result["failure"] == "carrier_preflight_rejected"
    assert "expected_lower_missing" in result["detail"]
    assert called is False


# ── 복원 충실도 게이트 ────────────────────────────────────────────────────────

class _FakePanelMap:
    def __init__(self, mask):
        self.garment_mask = mask


def test_reconstruction_fidelity_is_verified_against_the_captured_mask(tmp_path):
    import cv2
    mask = np.zeros((400, 300), np.uint8)
    mask[100:300, 80:220] = 255
    d = tmp_path / "ds"
    d.mkdir()
    cv2.imwrite(str(d / "garment_mask.png"), mask)

    same = replay.verify_reconstruction(_FakePanelMap(mask.copy()), d)
    assert same["checked"] and same["execution_replay_ok"]
    assert same["mask_iou"] == 1.0

    shifted = np.zeros_like(mask)
    shifted[100:300, 150:290] = 255          # 절반쯤 어긋난 mask
    poor = replay.verify_reconstruction(_FakePanelMap(shifted), d)
    assert poor["checked"] and not poor["execution_replay_ok"], poor
    assert poor["mask_iou"] < replay.MIN_MASK_RECONSTRUCTION_IOU


def test_reconstructed_geometry_is_never_marked_visual_reliable(tmp_path):
    """코드 경로 replay 가능과 육안 A/B 가능은 다른 계약이다."""
    import cv2
    mask = np.zeros((240, 180), np.uint8)
    mask[30:210, 30:150] = 255
    d = tmp_path / "ds"
    d.mkdir()
    cv2.imwrite(str(d / "garment_mask.png"), mask)
    cv2.imwrite(str(d / "painted.png"), mask)

    recon = replay.verify_reconstruction(_FakePanelMap(mask.copy()), d, painted=mask.copy())
    reliability = replay.classify_replay_reliability(
        recon, {
            "landmarks": "reconstructed_from_mask",
            "carrier_preflight": "captured",
        })

    assert reliability["execution_replay_ok"] is True
    assert reliability["visual_replay_reliable"] is False
    assert reliability["visual_replay_reason"] == "landmarks_reconstructed"


def test_missing_captured_mask_reports_unchecked(tmp_path):
    d = tmp_path / "ds"
    d.mkdir()
    out = replay.verify_reconstruction(_FakePanelMap(np.zeros((4, 4), np.uint8)), d)
    assert out == {"checked": False}


# ── 단위 변환은 워커 규약과 같아야 한다 ───────────────────────────────────────

def test_boxes_are_denormalized_per_image():
    norm = {"collar_box": [[0.0, 0.0], [0.5, 0.0], [0.5, 0.25], [0.0, 0.25]]}
    small = replay._boxes_to_pixels(norm, width=400, height=800)
    large = replay._boxes_to_pixels(norm, width=1000, height=2000)
    assert small["collar_box"][1] == [200.0, 0.0]
    assert large["collar_box"][1] == [500.0, 0.0]
    assert replay._boxes_to_pixels({}, width=10, height=10) == {}


# ── 재현성 ────────────────────────────────────────────────────────────────────

def test_same_inputs_produce_identical_output_and_metric_hashes(tmp_path):
    """같은 입력이 다른 해시를 내면 replay 로 회귀를 판정할 수 없다."""
    lm = {"shoulder_l": [0.28, 0.18], "shoulder_r": [0.72, 0.18],
          "hem_l": [0.30, 0.72], "hem_r": [0.70, 0.72],
          "sleeve_l_end": [0.16, 0.52], "sleeve_r_end": [0.84, 0.52]}
    d = _write_dataset(tmp_path, landmarks=lm)
    import cv2
    carrier = cv2.imread(str(d / "carrier.png"))
    source = cv2.imread(str(d / "source_front.png"))
    geo, _ = replay.load_geometry(d, carrier)

    first = replay.run_projection(carrier, source, geo)
    second = replay.run_projection(carrier, source, geo)
    assert first["stage"] == second["stage"]

    def fingerprint(res):
        art = res.get("artifacts")
        qc = res.get("qc")
        img = hashlib.sha256(art.image_bgr.tobytes()).hexdigest() if art else res.get("failure")
        met = hashlib.sha256(json.dumps(
            {k: v for k, v in (qc.metrics if qc else res.get("metrics", {})).items()
             if k != "failure_details"},
            sort_keys=True, default=str).encode()).hexdigest()
        return img, met

    assert fingerprint(first) == fingerprint(second)


def test_replay_writes_report_metrics_and_no_other_side_effects(tmp_path):
    lm = {"shoulder_l": [0.28, 0.18], "shoulder_r": [0.72, 0.18],
          "hem_l": [0.30, 0.72], "hem_r": [0.70, 0.72]}
    d = _write_dataset(tmp_path, landmarks=lm)
    out = tmp_path / "out"

    class _Args:
        dataset = str(d)
    _Args.out = str(out)
    code = replay.cmd_replay(_Args())
    assert code in (0, 2, 3)
    assert (out / "replay_report.html").exists()
    metrics = json.loads((out / "replay_metrics.json").read_text())
    assert "hashes" in metrics and "landmark_provenance" in metrics
    produced = {p.name for p in out.iterdir()}
    assert produced <= {"replay_report.html", "replay_metrics.json", "replay_composite.png"}


def test_qc_failure_is_a_nonzero_gate_and_keeps_failure_details(tmp_path, monkeypatch):
    """리포트를 썼다는 사실이 QC 통과로 오인되면 유료 호출 gate가 열린다."""
    lm = {"shoulder_l": [0.28, 0.18], "shoulder_r": [0.72, 0.18],
          "hem_l": [0.30, 0.72], "hem_r": [0.70, 0.72]}
    d = _write_dataset(tmp_path, landmarks=lm)
    out = tmp_path / "out"
    failure_details = [{
        "code": "pattern_metric_failed",
        "panel": "collar_box",
        "detail": "component phase error 0.454 > 0.12",
    }]
    qc = SimpleNamespace(
        passed=False,
        failures=("pattern_metric_failed",),
        metrics={"failure_details": failure_details},
    )
    monkeypatch.setattr(replay, "run_projection", lambda *_: {"stage": "qc", "qc": qc})

    args = SimpleNamespace(dataset=str(d), out=str(out), allow_qc_fail=False)
    assert replay.cmd_replay(args) == 3
    saved = json.loads((out / "replay_metrics.json").read_text())
    assert saved["failure_details"] == failure_details

    args.out = str(tmp_path / "diagnostic")
    args.allow_qc_fail = True
    assert replay.cmd_replay(args) == 0


def test_report_embeds_images_without_urls_or_tokens(tmp_path):
    lm = {"shoulder_l": [0.28, 0.18], "shoulder_r": [0.72, 0.18],
          "hem_l": [0.30, 0.72], "hem_r": [0.70, 0.72]}
    d = _write_dataset(tmp_path, landmarks=lm)
    out = tmp_path / "out"

    class _Args:
        dataset = str(d)
    _Args.out = str(out)
    replay.cmd_replay(_Args())
    html = (out / "replay_report.html").read_text()
    assert "data:image/jpeg;base64," in html
    for banned in ("http://", "https://", "Bearer", "postgres"):
        assert banned not in html, f"리포트에 {banned} 가 새면 안 된다"
