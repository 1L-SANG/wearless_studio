"""마네킹 출고 배관의 alpha 전용 강제 게이트.

기존 Pillow 크롭·유령 휴리스틱을 enforce로 승격하지 않고, 실제 투명
픽셀만 초기 생성에서 거절하고 편집 체인 끝에서 되돌린다.
"""

import asyncio
import io
import types

from PIL import Image, ImageDraw

from app.services.qc import QcResult
from app.workers import mannequin_job
from conftest import make_settings


class _Gemini:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        self.calls.append(prompt)
        return types.SimpleNamespace(image=self.outputs.pop(0), mime="image/png")


class _R2:
    def __init__(self):
        self.puts = []

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime))


async def _no_series(**kwargs):
    return None


def _rgb_sheer_png() -> bytes:
    """RGB 안에 배경색이 비쳐 보이는 저대비 원단 근사."""
    size = (700, 1050)
    image = Image.new("RGB", size, (248, 246, 244))
    draw = ImageDraw.Draw(image)
    draw.rectangle((280, 63, 420, 1018), fill=(233, 231, 229),
                   outline=(205, 203, 201), width=6)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _run_initial(monkeypatch, *, outputs, verdicts=None):
    emits = []
    if verdicts is not None:
        verdicts = list(verdicts)
        monkeypatch.setattr(
            mannequin_job.qc, "evaluate_mannequin_qc", lambda _data: verdicts.pop(0))
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", _no_series)

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append(dict(payload))

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)

    gemini = _Gemini(outputs)
    r2 = _R2()
    settings = make_settings(
        r2_bucket="bucket",
        image_qc="off",
        mannequin_axis_qc="off",
        mannequin_max_attempts=len(outputs),
        mannequin_bust_pass="off",
        mannequin_untuck_pass="off",
        mannequin_fabric_pass="off",
    )
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=object(), r2=r2, gemini=gemini))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "payload": {}}

    result = asyncio.run(mannequin_job._run_candidate(
        app=app,
        job=job,
        candidate="A",
        base_fit="regular",
        base_gender="women",
        base_img=types.SimpleNamespace(mime="image/png", data=b"base"),
        prod_imgs=[],
        match_img=None,
        product_count=0,
        template="${baseGender} ${clothingType} ${imageManifest}",
        product={},
        analysis={},
        clothing_type="top",
    ))
    return result, gemini, r2, emits


def test_initial_transparent_canvas_is_rerolled_even_when_other_pillow_gates_are_shadow(
        monkeypatch):
    result, gemini, r2, emits = _run_initial(
        monkeypatch,
        outputs=[b"transparent", b"opaque"],
        verdicts=[
            QcResult("retry", ["transparent_canvas"], {"transparentPixelCount": 100}),
            QcResult("pass", [], {}),
        ],
    )

    assert result is not None
    assert len(gemini.calls) == 2
    assert r2.puts[0][1] == b"opaque"
    assert [event["status"] for event in emits].count("canvas_alpha_rejected") == 1


def test_all_transparent_attempts_drop_candidate_without_r2_salvage(monkeypatch):
    transparent = QcResult(
        "retry", ["transparent_canvas"], {"transparentPixelCount": 100})
    result, gemini, r2, emits = _run_initial(
        monkeypatch,
        outputs=[b"transparent-1", b"transparent-2"],
        verdicts=[transparent, transparent],
    )

    assert result is None
    assert len(gemini.calls) == 2
    assert r2.puts == []
    assert [event["status"] for event in emits].count("canvas_alpha_rejected") == 2


def test_rgb_sheer_appearance_passes_worker_gate_without_reroll(monkeypatch):
    rgb_sheer = _rgb_sheer_png()
    result, gemini, r2, emits = _run_initial(
        monkeypatch, outputs=[rgb_sheer])

    assert result is not None
    assert len(gemini.calls) == 1
    assert r2.puts[0][1] == rgb_sheer
    assert not any(event.get("status") == "canvas_alpha_rejected" for event in emits)


def test_transparent_final_edit_is_reverted_to_pre_edit_output(monkeypatch):
    events = []
    before = types.SimpleNamespace(image=b"opaque-before", mime="image/png")
    transparent = types.SimpleNamespace(image=b"transparent-after", mime="image/png")

    async def fake_emit(pool, job_id, event_type, payload):
        events.append(dict(payload))

    async def unchanged(**kwargs):
        return kwargs["res"], False

    async def transparent_bust(**kwargs):
        return transparent, True

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_job, "_apply_untuck_pass", unchanged)
    monkeypatch.setattr(mannequin_job, "_apply_axis_qc", unchanged)
    monkeypatch.setattr(mannequin_job, "_apply_bust_pass", transparent_bust)
    monkeypatch.setattr(mannequin_job, "_apply_fabric_pass", unchanged)
    monkeypatch.setattr(
        mannequin_job.qc,
        "evaluate_canvas_alpha_qc",
        lambda data: (
            QcResult("retry", ["transparent_canvas"], {"transparentPixelCount": 7})
            if data == transparent.image
            else QcResult("pass")
        ),
    )

    selected, p2, spent = asyncio.run(mannequin_job._apply_edits(
        pool=object(),
        gemini=object(),
        s=types.SimpleNamespace(),
        job_id="j1",
        candidate="A",
        attempt=1,
        model="model",
        res=before,
        p2={"product_fidelity": 90},
        prod_imgs=[],
        match_img=None,
        fit_profile=None,
        profile_hash="hash",
        base_gender="women",
        calls_spent=0,
    ))

    assert selected is before
    assert p2 == {"product_fidelity": 90}
    assert spent == 1
    reverted = [event for event in events if event.get("status") == "edit_reverted"]
    assert reverted and reverted[0]["reason"] == "transparent_canvas"
