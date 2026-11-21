import json
from pathlib import Path

import numpy as np

from app.services import garment_color_target as target


def test_tone_linear_matches_all_browser_reference_cases():
    fixture = json.loads((Path(__file__).parent / "fixtures/garment_color_tone_js_reference.json").read_text())
    assert fixture["toneVersion"] == target.TONE_VERSION
    assert len(fixture["cases"]) == 1224
    for case in fixture["cases"]:
        np.testing.assert_allclose(target.srgb8_to_linear(case["srgb8"]), case["linear"], atol=1e-9, rtol=0)
        np.testing.assert_allclose(target.tone_linear(case["linear"], **case["sliders"]), case["out"],
                                   atol=1e-9, rtol=0)
