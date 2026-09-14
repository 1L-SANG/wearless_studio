"""동일인 검사 기준 = 등록 얼굴 사진 **여러 장**의 중앙값, 문턱은 0.45.

왜 바꿨나(2026-09-13 운영 실측): 기준이 승인 자산 face_front **한 장**이면 그 한 장의 촬영 조건이
곧 문턱이 된다. 같은 사람인데 등록일(9/2)과 실제 사진(9/11)이 다르다는 이유만으로 SFace 가
0.586~0.665 로 내려갔고, v7-1500 로 만든 운영 테스트컷 8장이 0.482~0.605 라 **전부** 옛 문턱 0.60 에
걸려 컷이 한 장도 안 나왔다. 같은 컷을 9/11 기준셋으로 재면 0.70~0.77 이다 — 얼굴이 아니라 기준이
문제였다. 남(gpt-image 바탕)은 0.05~0.20 이라 문턱을 내려도 오탐 여지가 크다.
"""

import asyncio
import types

import numpy as np
import pytest

from app.agents import face_identity as fi
from app.agents import identity_source


# ── 기준셋 로딩 ──────────────────────────────────────────────────────────────
class _Cur:
    def __init__(self, rows, fail=False):
        self._rows, self._fail = rows, fail
        self.sql = []

    async def execute(self, sql, params=None):
        if self._fail:
            raise RuntimeError("relation missing")
        self.sql.append(" ".join(sql.split()))

    async def fetchall(self):
        return list(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows, fail=False):
        self._cur = _Cur(rows, fail)

    def cursor(self):
        return self._cur


class _R2:
    def __init__(self, missing=()):
        self.missing = set(missing)
        self.reads = []

    def get_bytes(self, key):
        self.reads.append(key)
        if key in self.missing:
            raise FileNotFoundError(key)
        return f"bytes:{key}".encode()


def _app(r2):
    return types.SimpleNamespace(state=types.SimpleNamespace(r2_face=r2))


def _photo(angle, key=None, qc="passed", state="quarantine"):
    return {"angle": angle, "r2_key": key or f"k/{angle}.jpg", "qc_status": qc, "storage_state": state}


MODEL = "ca5d2abd-5a70-47c1-973b-10573cf60680"


def _load(rows, r2=None, yaw=None, **kw):
    """yaw: r2 키 → yaw_proxy. 주면 그 값으로 정면 판정을 흉내낸다(YuNet weights 없이 돈다)."""
    r2 = r2 or _R2()
    import contextlib
    from unittest import mock
    ctx = contextlib.nullcontext()
    if yaw is not None:
        def fake(data, model_dir=None):
            key = data.decode().split("bytes:")[1]
            return None if key not in yaw else _Det(yaw[key])
        ctx = mock.patch.object(identity_source, "_is_frontal",
                                lambda data, model_dir: (fake(data) is None
                                                         or fake(data).yaw_proxy < 0.25))
    with ctx:
        out = asyncio.run(identity_source.enrollment_reference_faces(_app(r2), _Conn(rows), MODEL, **kw))
    return out, r2


class _Det:
    def __init__(self, yaw):
        self.yaw_proxy = yaw


def test_only_the_frontal_slots_are_used():
    """face03(3/4)·face05(옆)·face04/06 은 기준이 아니다 — SFace 가 정면 임베딩이라서."""
    rows = [_photo(f"face{i:02d}") for i in range(1, 9)]
    out, r2 = _load(rows)
    assert r2.reads == ["k/face01.jpg", "k/face02.jpg", "k/face07.jpg", "k/face08.jpg"]
    assert len(out) == 4
    assert identity_source.IDENTITY_REFERENCE_SLOTS == ("face01", "face02", "face07", "face08")


def test_legacy_three_photo_enrollment_uses_front_only():
    """옛 등록은 front→face01 만 남는다. angle45(→face03)·side(→face05)는 목록에 없다.

    실측: front+angle45 중앙값이면 테스트컷이 0.435~0.514 로 떨어져 0.45 에서 2장이 탈락했다.
    front 1장만 쓰면 0.51~0.60 이다.
    """
    out, r2 = _load([_photo("front"), _photo("angle45"), _photo("side")])
    assert r2.reads == ["k/front.jpg"]
    assert len(out) == 1


def test_a_turned_photo_in_a_frontal_slot_is_dropped():
    """슬롯 이름만 믿지 않는다 — 촬영 실수로 정면 슬롯에 각도 사진이 오면 yaw 로 뺀다."""
    rows = [_photo("face01"), _photo("face02"), _photo("face07")]
    out, r2 = _load(rows, yaw={"k/face01.jpg": 0.04, "k/face02.jpg": 0.41, "k/face07.jpg": 0.12})
    assert len(r2.reads) == 3, "읽어 봐야 각도를 잴 수 있다"
    assert len(out) == 2, "0.41 짜리 한 장이 빠진다"


def test_when_yaw_cannot_be_measured_the_photo_is_kept():
    """판정 불가는 통과로 본다 — 기준을 잃는 쪽이 더 나쁘다."""
    from unittest import mock
    with mock.patch.object(identity_source, "_is_frontal", side_effect=lambda d, m: True):
        out, _ = _load([_photo("face01")])
    assert len(out) == 1
    # 진짜 구현도 detect 실패·예외를 통과로 본다
    with mock.patch("app.agents.face_identity.detect_face", return_value=None):
        assert identity_source._is_frontal(b"x", None) is True


@pytest.mark.parametrize("bad", [
    {"qc": "failed"},                 # QC 미통과 사진은 기준이 아니다
    {"state": "delete_pending"},      # 파기 예정
    {"key": "   "},                   # 키가 비었다
])
def test_unusable_photos_are_left_out(bad):
    out, _ = _load([_photo("face01"), _photo("face02", **bad)])
    assert len(out) == 1


def test_a_missing_object_does_not_lose_the_rest():
    """한 장이 R2 에 없어도 나머지로 간다 — 기준이 줄어들 뿐 컷은 막지 않는다."""
    out, r2 = _load([_photo(s) for s in ("face01", "face02", "face07")],
                    r2=_R2(missing={"k/face02.jpg"}))
    assert len(out) == 2 and len(r2.reads) == 3


def test_no_face_bucket_means_no_references():
    out = asyncio.run(identity_source.enrollment_reference_faces(
        types.SimpleNamespace(state=types.SimpleNamespace(r2_face=None)), _Conn([]), MODEL))
    assert out == []


def test_a_broken_lookup_falls_back_instead_of_raising():
    r2 = _R2()
    out = asyncio.run(identity_source.enrollment_reference_faces(_app(r2), _Conn([], fail=True), MODEL))
    assert out == [] and r2.reads == []


def test_a_bad_model_id_is_refused_before_the_query():
    r2 = _R2()
    assert asyncio.run(identity_source.enrollment_reference_faces(_app(r2), _Conn([]), "not-a-uuid")) == []


def test_no_frontal_reference_falls_back_to_the_single_asset():
    """정면 기준이 0장이면 [] — 호출자가 예전 face_front 한 장 경로로 간다."""
    out, r2 = _load([_photo("angle45"), _photo("side"), _photo("face03")])
    assert out == [] and r2.reads == []


def test_the_query_pins_the_models_current_enrollment():
    """다른 등록 회차의 사진이 기준으로 섞이면 안 된다 — 조인이 current_enrollment_id 로 묶인다."""
    conn = _Conn([_photo("face01")])
    asyncio.run(identity_source.enrollment_reference_faces(_app(_R2()), conn, MODEL))
    sql = conn._cur.sql[0]
    assert "m.current_enrollment_id = p.enrollment_id" in sql
    assert "where m.id = %s" in sql


def test_reference_face_bytes_prefers_photos_then_falls_back(monkeypatch):
    """변형 컷 경로: 등록 사진이 있으면 그것, 없으면 예전처럼 승인 face_front 한 장."""
    calls = []

    async def photos(app, conn, model_id, *, model_dir=None):
        calls.append("photos")
        return [b"a", b"b"]

    monkeypatch.setattr(identity_source, "enrollment_reference_faces", photos)
    assert asyncio.run(identity_source.reference_face_bytes(_app(_R2()), _Conn([]), MODEL, {})) == [b"a", b"b"]
    assert calls == ["photos"]

    async def none(app, conn, model_id, *, model_dir=None):
        return []

    monkeypatch.setattr(identity_source, "enrollment_reference_faces", none)
    # license_row 가 dict 가 아니면 옛 경로도 [] — 폴백이 예외로 죽지 않는다
    assert asyncio.run(identity_source.reference_face_bytes(_app(_R2()), _Conn([]), MODEL, None)) == []


# ── 문턱 ────────────────────────────────────────────────────────────────────
def test_threshold_is_the_recalibrated_value_with_its_evidence():
    import pathlib
    assert fi.GATE_IDENTITY_MIN == 0.45
    src = pathlib.Path(fi.__file__).read_text(encoding="utf-8")
    block = src.split("GATE_IDENTITY_MIN")[0]
    # 근거 수치가 주석에 남아 있어야 다음 사람이 이 값을 되짚을 수 있다
    for token in ("0.586", "0.795", "0.05", "0.363", "2026-09-13"):
        assert token in block, token


def test_the_threshold_keeps_the_same_person_and_still_rejects_others():
    """2026-09-13 실측 분포로 고정: 같은 사람 최저 0.482 는 통과, 남 최고 0.20 은 탈락."""
    same_person = [0.482, 0.543, 0.547, 0.559, 0.563, 0.576, 0.588, 0.605]
    different_person = [0.05, 0.12, 0.20]
    assert all(v >= fi.GATE_IDENTITY_MIN for v in same_person)
    assert all(v < fi.GATE_IDENTITY_MIN for v in different_person)
    # 남과의 여유가 SFace 공식 권장선(0.363)보다 넓다
    assert fi.GATE_IDENTITY_MIN - max(different_person) > 0.363 - max(different_person) - 0.001


# ── 기준이 없거나 한 장뿐일 때 ───────────────────────────────────────────────
def _emb(seed):
    rng = np.random.default_rng(seed)
    return tuple(float(v) for v in rng.normal(size=128))


def test_no_usable_reference_means_the_gate_does_not_judge_identity():
    """기준이 하나도 없으면 references=None 그대로 → identity=None → 신원 검사 생략(컷은 계속 만든다)."""
    spec = fi.FaceIdentitySpec("lora/k.safetensors", "ohwx man")
    assert fi.with_references(spec, [b"not-an-image"]).references is None
    assert fi.identity_score(None, None) is None
    assert fi.identity_score(None, ()) is None


@pytest.mark.parametrize("scores,expected", [
    ([0.4], 0.4),                              # 한 장이면 그 값
    ([0.3, 0.5, 0.9], 0.5),                    # 홀수면 가운데
    ([0.3, 0.5, 0.7, 0.9], 0.6),               # 짝수면 가운데 둘의 평균
])
def test_the_score_is_the_median_over_the_reference_set(monkeypatch, scores, expected):
    """여러 장을 쓰는 이유가 이것이다 — 한 장이 튀어도 중앙값이 문턱을 대신 정하지 않는다."""
    # 기준 i 번은 첫 원소가 i 다 — cosine 스텁이 그걸로 어느 기준인지 알아낸다(float32 변환에 안전).
    refs = [tuple([float(i)] + [0.0] * 127) for i in range(len(scores))]
    monkeypatch.setattr(fi, "face_embedding", lambda image, det, model_dir=None: np.zeros(128, np.float32))
    monkeypatch.setattr(fi, "cosine", lambda a, b: scores[int(round(float(b[0])))])
    assert fi.identity_score(object(), refs, det=object(), model_dir=None) == pytest.approx(expected)
