"""피부 보정 단계 — 등록자가 고르고, 셀러는 못 바꾼다.

  100 = 지금 그대로(네거티브 없음 + 얼굴 크롭 ESRGAN 100%)
   50 = 네거티브 켬 + ESRGAN 결과와 Lanczos 확대본을 0.5로 섞음
    0 = 네거티브 켬 + ESRGAN 끔

2026-09-16 16장 실측에서 피부 결을 실제로 바꾼 손잡이는 **네거티브 문구**였다(피부 결 +11~19%,
닮은 점수 변화는 ±0.03 오차 범위). 업스케일러 강도는 100↔50 차이가 거의 없다 — 단계 이름값이다.

이 파일이 지키는 것:
  · 100 은 **이 PR 이전과 바이트가 같다**(단계가 기본 경로를 건드리면 안 된다)
  · 0 은 확대기를 아예 **안 부른다**(돈·시간도 안 쓴다)
  · 단계마다 파드로 가는 네거티브 문구가 다르다
  · 0 이 게이트에 떨어지면 50 으로 **한 번만** 더 그린다 — 100 까지 올리지 않는다
  · 레시피 해시가 단계마다 다르다(컷 원장에서 되짚을 수 있어야 한다)
GPU 는 쓰지 않는다 — 확대기는 가짜 함수, 렌더는 가짜 백엔드다.
"""

import asyncio

import numpy as np
import pytest
from PIL import Image

from app.agents import face_identity as fi
from app.agents import face_recipe

NEG = "airbrushed skin, retouched skin, smooth plastic skin, beauty filter"


def _plan(w=1024, h=1536, box=(360.0, 300.0, 180.0, 240.0)):
    return fi.plan_from_box(w, h, box, yaw_proxy=0.05, eye_dist=60.0, expression="neutral")


def _photo(w=1024, h=1536, seed=7):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))


def _up(calls):
    def upscale(image, k):
        calls.append((image.size, k))
        # 원본과 확실히 다른 그림 — 섞였는지 눈이 아니라 픽셀로 본다.
        return Image.new("RGB", (image.width * k, image.height * k), (255, 0, 0))
    return upscale


# ── 단계 해석 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("level,expected", [
    (100, (100, 1.0, "")),
    (50, (50, 0.5, NEG)),
    (0, (0, 0.0, NEG)),
])
def test_each_level_maps_to_a_blend_and_a_negative(level, expected):
    assert fi.skin_finish_plan(level, NEG) == expected


@pytest.mark.parametrize("level", [None, 7, -1, "50", 1000])
def test_an_unknown_level_falls_back_to_the_default(level):
    """모르는 값에서 조용히 다른 그림이 나오면 안 된다 — 기본은 '지금 그대로'다."""
    assert fi.skin_finish_plan(level, NEG) == (100, 1.0, "")


# ── 크롭 확대 ───────────────────────────────────────────────────────────────
def test_blend_one_is_byte_identical_to_the_upscaler_result():
    """★ 100 단계는 이 PR 이전 경로다 — 섞기 연산을 타면 안 된다."""
    calls = []
    plan, photo = _plan(), _photo()
    out, meta = fi.crop_1024_with_meta(photo, plan, upscaler=_up(calls), blend=1.0)

    expected = Image.new("RGB", (1080, 1080), (255, 0, 0)).resize((fi.CROP, fi.CROP), Image.LANCZOS)
    assert out.tobytes() == expected.tobytes()
    assert meta["applied"] is True and meta["blend"] == 1.0
    assert calls == [((540, 540), 2)]


def test_blend_zero_never_calls_the_upscaler():
    """0 단계는 확대기를 부르지 않는다 — 파드 왕복 한 번과 그 시간이 통째로 빠진다."""
    calls = []
    out, meta = fi.crop_1024_with_meta(_photo(), _plan(), upscaler=_up(calls), blend=0.0)

    assert calls == []
    assert meta == {"applied": False, "method": "lanczos", "k": 1, "side": 540, "blend": 0.0}
    # 확대기를 안 준 것과 같은 그림이어야 한다.
    assert out.tobytes() == fi.crop_1024(_photo(), _plan()).tobytes()


def test_blend_half_sits_between_lanczos_and_the_upscaler():
    plan, photo = _plan(), _photo()
    lanczos = np.asarray(fi.crop_1024(photo, plan), np.float32)
    esrgan = np.asarray(fi.crop_1024_with_meta(photo, plan, upscaler=_up([]), blend=1.0)[0], np.float32)
    mixed = np.asarray(fi.crop_1024_with_meta(photo, plan, upscaler=_up([]), blend=0.5)[0], np.float32)

    # 두 끝 사이에 있고, 어느 쪽과도 같지 않다.
    assert abs(mixed - lanczos).mean() > 1.0
    assert abs(mixed - esrgan).mean() > 1.0
    assert abs(mixed - (lanczos + esrgan) / 2).mean() < 2.0


def test_the_blend_is_clamped():
    """설정 오타로 3.0 이 와도 확대기 결과를 넘어서지 않는다."""
    _out, meta = fi.crop_1024_with_meta(_photo(), _plan(), upscaler=_up([]), blend=3.0)
    assert meta["blend"] == 1.0


# ── 파드로 가는 네거티브 문구 ───────────────────────────────────────────────
class _Backend:
    """render 를 기록만 하는 가짜 파드. control 을 그대로 돌려준다."""

    def __init__(self):
        self.calls = []

    def render(self, control, prompt, seed, base=None, gen_mask=None, negative_prompt=None):
        self.calls.append({"seed": seed, "negative_prompt": negative_prompt})
        return control.copy()


class _OldBackend:
    """네거티브를 모르는 옛 백엔드. 인자를 넘기면 TypeError 로 컷이 죽는다."""

    def __init__(self):
        self.calls = 0

    def render(self, control, prompt, seed):
        self.calls += 1
        return control.copy()


def _run(backend, skin_finish):
    buf = __import__("io").BytesIO()
    _photo().save(buf, "PNG")
    return fi.run_face_pass(buf.getvalue(), backend, None, skin_finish=skin_finish, skin_negative=NEG)


@pytest.mark.parametrize("level,expected", [(100, None), (50, NEG), (0, NEG)])
def test_the_pod_gets_the_negative_of_its_level(level, expected, monkeypatch):
    backend = _Backend()
    monkeypatch.setattr(fi, "prepare_image", lambda *a, **k: (_photo(), _plan(), {"skipped_reason": None}))
    _run(backend, level)

    assert backend.calls, "렌더가 아예 안 불렸다"
    assert {call["negative_prompt"] for call in backend.calls} == {expected}


def test_an_old_backend_is_never_handed_the_new_argument(monkeypatch):
    """마스크 잠금과 같은 규칙 — 모르는 백엔드에 새 인자를 넘기면 그 컷이 TypeError 로 죽는다."""
    backend = _OldBackend()
    monkeypatch.setattr(fi, "prepare_image", lambda *a, **k: (_photo(), _plan(), {"skipped_reason": None}))
    _run(backend, 0)
    assert backend.calls > 0


def test_the_level_is_recorded_in_the_result_meta(monkeypatch):
    backend = _Backend()
    monkeypatch.setattr(fi, "prepare_image", lambda *a, **k: (_photo(), _plan(), {"skipped_reason": None}))
    assert _run(backend, 50).meta["skin_finish"] == 50
    assert _run(backend, None).meta["skin_finish"] == 100


# ── 레시피 ─────────────────────────────────────────────────────────────────
def test_each_level_has_its_own_recipe_id():
    ids = {level: face_recipe.recipe_id(face_recipe.recipe_fields(skin_finish=level))
           for level in fi.SKIN_FINISH_LEVELS}
    assert len(set(ids.values())) == len(ids), f"단계가 달라도 같은 해시다: {ids}"
    assert face_recipe.recipe_fields()["skin_finish"] == 100


# ── 0 → 50 폴백 ────────────────────────────────────────────────────────────
class _Settings:
    fm_face_qc_dir = None
    face_crop_upscale = True
    face_crop_pad = True
    face_mask_lock = False
    face_skin_negative_prompt = NEG
    face_identity_backend_url = "http://pod.test/render"
    face_pass_real_wait_seconds = 1


def _spec(skin_finish):
    return fi.FaceIdentitySpec("lora.safetensors", backend_url="http://pod.test/render",
                               skin_finish=skin_finish)


def _apply(monkeypatch, outcomes, spec):
    """run_face_pass 를 대본으로 갈아 끼우고 apply_face_pass 를 돌린다."""
    seen = []

    def fake_run(image, backend, expression, **kwargs):
        seen.append(kwargs.get("skin_finish"))
        return outcomes.pop(0)

    monkeypatch.setattr(fi, "run_face_pass", fake_run)
    monkeypatch.setattr(fi, "resolve_backend", lambda settings, live: _Backend())
    async def ready(*a, **k):
        return "http://pod.test/render"
    monkeypatch.setattr(fi, "wait_for_backend", ready)
    outcome = {}
    try:
        image, _mime = asyncio.run(
            fi.apply_face_pass(_Settings(), b"x", "image/png", spec, outcome=outcome))
        return seen, outcome, image
    except fi.FacePassUnavailable as exc:
        return seen, outcome, exc


def _result(applied, skin_finish, tries=1):
    return fi.FacePassResult(b"out" if applied else b"x", "image/png", applied,
                             {"applied": applied, "skin_finish": skin_finish,
                              "tries": [{"gate": "identity_low"}] * tries})


def test_level_zero_retries_once_at_fifty(monkeypatch):
    """★ 0 단계만 컷이 비면 안 된다 — 확대기를 안 써서 게이트가 더 자주 걸린다."""
    seen, outcome, image = _apply(monkeypatch,
                                  [_result(False, 0), _result(True, 50)], _spec(0))

    assert seen == [0, 50], "첫 시도는 spec 의 단계(0), 두 번째만 50 이어야 한다"
    assert image == b"out"
    assert outcome["face_pass"] == "applied"


def test_the_retry_never_climbs_to_a_hundred(monkeypatch):
    """50 도 떨어지면 거기서 끝이다. '있는 그대로'를 고른 사람에게 가장 매끈한 단계를 조용히
    내보내면 그건 다른 상품이다."""
    seen, _outcome, raised = _apply(monkeypatch,
                                    [_result(False, 0), _result(False, 50)], _spec(0))

    assert seen == [0, 50]
    assert isinstance(raised, fi.FacePassUnavailable)


@pytest.mark.parametrize("level", [50, 100])
def test_other_levels_do_not_retry(monkeypatch, level):
    seen, _outcome, raised = _apply(monkeypatch, [_result(False, level)], _spec(level))

    assert seen == [level], "단계를 안 올린다"
    assert isinstance(raised, fi.FacePassUnavailable)


def test_a_backend_error_is_not_a_skin_finish_retry(monkeypatch):
    """tries 가 비면 렌더 자체가 안 된 것이다 — 단계를 올려 봐야 소용없다(기존 재대기 경로)."""
    empty = fi.FacePassResult(b"x", "image/png", False, {"skin_finish": 0, "tries": []})
    seen, _outcome, raised = _apply(monkeypatch, [empty, empty], _spec(0))

    assert 50 not in seen
    assert isinstance(raised, fi.FacePassUnavailable)


# ── DB → spec 배선 ─────────────────────────────────────────────────────────
class _Cur:
    """skin_finish 컬럼이 있는/없는 DB 를 흉내낸다."""

    def __init__(self, row, *, has_column=True):
        self.row, self.has_column = row, has_column
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        self.queries.append(" ".join(sql.split()))
        if "skin_finish" in sql and not self.has_column:
            raise RuntimeError('column "skin_finish" does not exist')

    async def fetchone(self):
        if "skin_finish" in self.queries[-1]:
            return self.row
        return {k: v for k, v in self.row.items() if k != "skin_finish"}


class _Conn:
    def __init__(self, cur):
        self._cur = cur
        self.rollbacks = 0

    def cursor(self):
        return self._cur

    async def rollback(self):
        self.rollbacks += 1


_ROW = {"id": "l1", "version": 1, "lora_r2_key": "facemarket/loras/a.safetensors",
        "lora_sha256": "a" * 64, "bucket": "face", "trigger_token": "ohwx man",
        "hair_length": "short", "hair_color": "black", "hair_texture": "straight",
        "face_shape": "oval", "jaw_line": "defined", "trained_steps": 1800,
        "skin_finish": 50}


def _resolved(cur):
    from app.agents import identity_source

    conn = _Conn(cur)

    async def go():
        return await identity_source.resolve_enabled_lora(
            conn, "11111111-1111-1111-1111-111111111111")

    return asyncio.run(go()), conn


def test_the_enabled_lora_row_carries_the_models_level(monkeypatch):
    from app.agents import identity_source

    async def no_pod(*a, **k):
        return None

    monkeypatch.setattr(identity_source, "_active_face_backend_url", no_pod)
    row, _conn = _resolved(_Cur(dict(_ROW)))

    assert row["skin_finish"] == 50
    assert fi.face_identity_from_lora_row(row).skin_finish == 50


def test_a_db_without_the_column_still_gets_a_face_pass(monkeypatch):
    """★ 컬럼 하나 때문에 얼굴 패스가 통째로 꺼지면 안 된다 — 그 순간 실존 모델 컷이 죽는다.

    배포는 마이그레이션이 먼저라 짧은 창이지만, 그 창에서 조용히 꺼지면 원인을 못 찾는다.
    """
    from app.agents import identity_source

    async def no_pod(*a, **k):
        return None

    monkeypatch.setattr(identity_source, "_active_face_backend_url", no_pod)
    cur = _Cur(dict(_ROW), has_column=False)
    row, conn = _resolved(cur)

    assert row is not None and row["lora_r2_key"] == _ROW["lora_r2_key"]
    assert "skin_finish" not in row
    assert conn.rollbacks == 1, "실패한 트랜잭션을 안 되돌리면 다음 질의가 통째로 죽는다"
    # 단계를 모르면 기본값으로 간다 — 지금 그대로.
    assert fi.face_identity_from_lora_row(row).skin_finish is None
    assert fi.skin_finish_plan(fi.face_identity_from_lora_row(row).skin_finish, NEG)[0] == 100


# ── 마이그레이션 ───────────────────────────────────────────────────────────
def test_the_migration_only_adds_and_keeps_todays_behaviour():
    """추가만 하는 마이그레이션이다 — 기본값 100 이 지금 동작이라 옛 코드가 그대로 돌아도 같다."""
    import pathlib

    sql = (pathlib.Path(__file__).resolve().parents[2]
           / "supabase/migrations/20260916100000_fm_skin_finish.sql").read_text()

    assert "add column if not exists skin_finish int not null default 100" in sql
    assert "check (skin_finish in (0, 50, 100))" in sql
    # 컷 쪽은 null 을 허용한다 — 옛 컷은 단계가 없다.
    assert "add column if not exists skin_finish int;" in sql
    assert "skin_finish is null or skin_finish in (0, 50, 100)" in sql
    # 기존 데이터를 고치거나 지우지 않는다.
    for forbidden in ("drop column", "delete from", "update public.fm_models set"):
        assert forbidden not in sql.lower(), forbidden
