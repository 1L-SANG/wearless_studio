"""16칸 전환 뒤에도 **이미 통과한 옛 등록**이 그대로 살아 있어야 한다.

운영 실제 행(2026-09-14 기준): 등록 `05caa497` 은 18칸 사진 · 동의 `2026-09-v1` 로 **passed**,
모델 `ca5d2abd` 는 verified 다. 이 PR 은 슬롯 이름(sh_*)과 동의 버전(2026-09-v2)을 둘 다
바꾸므로, 그 등록이 걸려 있는 자리마다 옛 값을 계속 받는지 여기서 한 번에 잠근다.

깨지면 무슨 일이 나는가:
  · 자산 소스가 안 풀리면 — 그 모델의 REAL 자산 빌드·증거 해시가 못 맞는다.
  · 동일인 기준이 0장이면 — 얼굴 패스가 신원을 안 보고 지나간다(품질 게이트 상실).
  · 동의 버전을 단일 바인딩하면 — 라이브 카탈로그·라이선스 얼굴·썸네일에서 통째로 빠지고,
    cutover 는 그 모델을 legacy(=파기 대상)로 분류한다.
"""

import asyncio
import types

import pytest

from app import facemarket_cutover, facemarket_enrollment
from app.agents import identity_source
from app.facemarket_photos import (
    ASSET_SOURCE_SLOTS, preferred_photo_predicate, resolve_photo_rows,
)
from app.workers import fm_model_asset_job

#: 운영 `05caa497` 의 모양 — 18칸 이름으로 올라간 사진들.
EIGHTEEN_SLOTS = tuple(
    [f"face{i:02d}" for i in range(1, 9)]
    + [f"torso{i:02d}" for i in range(1, 6)]
    + [f"full{i:02d}" for i in range(1, 6)]
)
LEGACY_CONSENT = "2026-09-v1"
MODEL = "ca5d2abd-5a70-47c1-973b-10573cf60680"


def _photo(angle, **over):
    row = {"angle": angle, "r2_key": f"private/{angle}.jpg", "mime_type": "image/jpeg",
           "qc_status": "passed", "storage_state": "quarantine", "status": "asset_building"}
    row.update(over)
    return row


# ── 자산 소스 ───────────────────────────────────────────────────────────────
def test_the_asset_sources_still_resolve_from_the_eighteen_slot_names():
    """face01·face03·face05 가 sh_front·sh_34·sh_side 자리를 그대로 채운다."""
    rows = [_photo(slot) for slot in EIGHTEEN_SLOTS]

    faces = resolve_photo_rows(rows, ASSET_SOURCE_SLOTS)

    assert [row["angle"] for row in faces] == ["face01", "face03", "face05"]


def test_the_asset_job_takes_those_three_in_order():
    """공개 자산 잡은 정면·3/4·측면 순서로 세 장을 요구한다 — 옛 이름으로도 같은 결과여야 한다."""
    rows = [_photo(slot) for slot in EIGHTEEN_SLOTS]

    faces = fm_model_asset_job._ordered_faces(rows)

    assert faces is not None, "옛 등록의 자산 빌드가 막히면 안 된다"
    assert [row["angle"] for row in faces] == ["face01", "face03", "face05"]


def test_the_licence_face_predicate_reaches_the_eighteen_slot_row():
    """카탈로그 조인 술어에 face01 후보가 남아 있어야 그 등록의 얼굴이 계속 잡힌다."""
    predicate = preferred_photo_predicate("p", "e.id")

    assert "'face01'" in predicate and "'front'" in predicate and "'sh_front'" in predicate
    # 정식 행이 생기기 전까지는 옛 행이 그대로 선택된다(앞선 후보가 없으므로)
    assert "not exists" in predicate


# ── 동일인 기준 ─────────────────────────────────────────────────────────────
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, sql, params=None):
        return None

    async def fetchall(self):
        return list(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self._cur = _Cur(rows)

    def cursor(self):
        return self._cur


class _R2:
    def __init__(self):
        self.reads = []

    def get_bytes(self, key):
        self.reads.append(key)
        return f"bytes:{key}".encode()


def test_the_identity_reference_falls_back_to_the_old_frontal_slots(monkeypatch):
    """16칸 기준(sh_*)이 0장이면 옛 정면 4칸으로 내려간다 — 기준을 잃으면 게이트가 신원을 안 본다."""
    monkeypatch.setattr(identity_source, "_is_frontal", lambda data, model_dir: True)
    r2 = _R2()
    app = types.SimpleNamespace(state=types.SimpleNamespace(r2_face=r2))
    rows = [_photo(slot, storage_state="approved") for slot in EIGHTEEN_SLOTS]

    out = asyncio.run(identity_source.enrollment_reference_faces(app, _Conn(rows), MODEL))

    assert r2.reads == ["private/face01.jpg", "private/face02.jpg",
                        "private/face07.jpg", "private/face08.jpg"]
    assert len(out) == 4


# ── 동의 버전 ───────────────────────────────────────────────────────────────
def test_the_old_consent_version_is_still_a_current_one():
    assert LEGACY_CONSENT in facemarket_enrollment.ACCEPTED_BIOMETRIC_CONSENT_VERSIONS
    assert facemarket_enrollment.BIOMETRIC_CONSENT_VERSION != LEGACY_CONSENT, (
        "이 테스트가 의미를 가지려면 현재 버전이 옛 버전과 달라야 한다"
    )


@pytest.mark.parametrize("sql_name", ["_CURRENT_CARD_ELIGIBILITY", "_INITIAL_LEGACY_MODEL_SCOPE_SQL"])
def test_no_gate_binds_a_single_consent_version(sql_name):
    """카탈로그 자격과 cutover legacy 스코프 둘 다 목록을 받아야 한다.

    단일 바인딩으로 되돌리면 v1 로 기록된 운영 모델이 카탈로그에서 사라지고 파기 대상이 된다.
    """
    from app import facemarket

    sql = getattr(facemarket, sql_name, None) or getattr(facemarket_cutover, sql_name)
    assert "e.consent_version = any(%s)" in sql
    assert "e.consent_version = %s" not in sql


def test_the_cutover_does_not_treat_an_old_model_as_legacy():
    """legacy 스코프는 '현행이 아닌' 모델을 고른다 — 옛 이름·옛 동의가 현행으로 읽혀야 한다."""
    sql = facemarket_cutover._INITIAL_LEGACY_MODEL_SCOPE_SQL

    assert "'face01'" in sql, "정면 사진 후보에 옛 이름이 남아 있어야 한다"
    assert "'front'" in sql
    assert "p.angle = 'front'\n" not in sql, "단일 각도로 못박으면 새 스펙 모델이 legacy 가 된다"
