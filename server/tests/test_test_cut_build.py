"""테스트컷 12장 자동 생성 — 꺼진 LoRA 로 얼굴만 다시 그린다.

이 파일이 지키는 것:
  · 12장 구성 = 보정 3종 × (확대 2 + 전신 2), 각 행에 보정이 적힌다
  · 일부만 성공하면 **성공분만 저장하고 'partial'** — 12장이 다 돼야 저장하는 게 아니다
  · **꺼진 LoRA**(enabled=false, status='ready')로 그린다. 셀러 경로는 이 모듈을 부르지 않는다
  · 파드가 없으면 실패가 아니라 대기다(상한을 넘겨야 실패)
  · 파기(biometric_purge)가 12장을 model_id 로 전부 지운다
GPU·R2·DB 는 전부 가짜다.
"""

import asyncio
import pathlib

import pytest

from app.agents import face_identity as fi
from app.services import test_cut_build as tcb
from app.workers import test_cut_build_reconciler as rec

MODEL_ID = "11111111-1111-1111-1111-111111111111"
KEYS = {"closeup": ("src/c1.png", "src/c2.png"), "fullbody": ("src/f1.png", "src/f2.png")}


class _Settings:
    fm_test_cut_source_closeup = "src/c1.png, src/c2.png"
    fm_test_cut_source_fullbody = "src/f1.png,src/f2.png"
    fm_test_cut_pod_wait_seconds = 1
    fm_face_qc_dir = None
    face_crop_upscale = True
    face_crop_pad = True
    face_mask_lock = False
    face_skin_negative_prompt = "NEG"
    fm_test_cut_build = "on"


class _R2:
    def __init__(self, missing=()):
        self.objects = {key: b"src-bytes" for values in KEYS.values() for key in values}
        for key in missing:
            self.objects.pop(key, None)
        self.puts = []

    def get_bytes(self, key):
        return self.objects[key]

    def put_bytes(self, key, data, mime, cache=None):
        self.objects[key] = data
        self.puts.append(key)

    def delete(self, key):
        self.objects.pop(key, None)


# ── 기준 원본 ───────────────────────────────────────────────────────────────
def test_the_sources_are_fixed_assets_read_from_settings():
    """사람이 바뀌어도 그대로여야 서로 비교가 된다 — 그래서 설정에서만 읽는다."""
    assert tcb.source_keys(_Settings()) == KEYS
    assert tcb.assert_sources(_Settings()) == KEYS


@pytest.mark.parametrize("closeup,fullbody", [
    ("", "src/f1.png,src/f2.png"),          # 한쪽이 비었다
    ("src/c1.png", "src/f1.png,src/f2.png"),  # 한 장뿐이다
    ("a,b,c", "src/f1.png,src/f2.png"),     # 세 장이다
])
def test_a_half_set_of_sources_never_starts(closeup, fullbody):
    """반쪽 묶음을 만들면 그 보정은 영영 못 보낸다 — 시작도 하지 않는다."""
    settings = _Settings()
    settings.fm_test_cut_source_closeup = closeup
    settings.fm_test_cut_source_fullbody = fullbody
    with pytest.raises(tcb.SourcesMissing):
        tcb.assert_sources(settings)


def test_an_unreadable_source_never_starts():
    with pytest.raises(tcb.SourcesMissing):
        asyncio.run(tcb.load_sources(_R2(missing=("src/f2.png",)), KEYS))


# ── 12장 구성 ───────────────────────────────────────────────────────────────
def test_the_plan_is_three_finishes_of_two_closeups_and_two_fullbodies():
    items = tcb.plan(KEYS)

    assert len(items) == tcb.EXPECTED_CUTS == 12
    assert {item["skin_finish"] for item in items} == set(fi.SKIN_FINISH_CODES)
    for code in fi.SKIN_FINISH_CODES:
        group = [item for item in items if item["skin_finish"] == code]
        assert sum(item["kind"] == "closeup" for item in group) == 2
        assert sum(item["kind"] == "fullbody" for item in group) == 2
        # 같은 보정 안에서 원본이 겹치면 사실상 같은 그림 두 장이다.
        assert len({item["source_key"] for item in group}) == 4
    # sort 는 0..11 로 겹치지 않는다 — (model_id, sort) 가 유니크다.
    assert sorted(item["sort"] for item in items) == list(range(12))


def test_every_cut_records_the_finish_that_made_it():
    """어느 장이 어느 보정인지 행에 없으면 관리자가 묶음으로 볼 수 없다."""
    assert all(item["skin_finish"] in fi.SKIN_FINISH_CODES for item in tcb.plan(KEYS))


# ── 셀러 경로 차단 ──────────────────────────────────────────────────────────
SELLER_PATHS = (
    "app/agents/cut_generator.py",
    "app/agents/face_identity.py",
    "app/agents/identity_source.py",
    "app/workers/editor_image_job.py",
    "app/workers/detail_page_job.py",
)


@pytest.mark.parametrize("path", SELLER_PATHS)
def test_the_seller_path_cannot_reach_the_disabled_lora(path):
    """★ 셀러 경로가 이걸 부르면 등록자가 확인도 안 한 얼굴이 팔린다.

    이 모듈은 **꺼진 LoRA** 로 spec 을 만든다. 셀러 컷은 identity_source.resolve_enabled_lora
    (enabled 를 거는 쪽)만 써야 한다 — 두 경로가 섞이면 승인 게이트가 무의미해진다.
    """
    text = (pathlib.Path(__file__).resolve().parents[1] / path).read_text()

    assert "test_cut_build" not in text, f"{path} 가 테스트컷 생성 모듈을 부른다"
    assert "ready_lora_spec" not in text, f"{path} 가 꺼진 LoRA spec 을 만든다"


def test_the_ready_lora_query_is_scoped_to_one_model_and_ignores_enabled():
    """model_id 로만 찾고 enabled 는 안 본다 — 학습 직후엔 아직 꺼져 있다."""
    import inspect

    sql = inspect.getsource(tcb.ready_lora_spec)
    assert "where model_id = %s and status = 'ready'" in sql
    assert "and l.enabled" not in sql and "and enabled" not in sql


# ── 렌더 ────────────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, applied):
        self.applied = applied
        self.image = b"rendered" if applied else b"src-bytes"
        self.mime = "image/png"
        self.meta = {"reason": None if applied else "gate_failed"}


def _spec():
    return fi.FaceIdentitySpec("lora.safetensors", backend_url="http://pod.test/render")


def test_the_render_passes_the_finish_straight_through(monkeypatch):
    """보정은 spec 이 아니라 **렌더 인자**로 간다 — 12장이 저마다 다른 보정이라서다."""
    seen = []

    def fake_run(image, backend, **kwargs):
        seen.append(kwargs["skin_finish"])
        return _Result(True)

    monkeypatch.setattr(fi, "run_face_pass", fake_run)
    monkeypatch.setattr(fi, "resolve_backend", lambda settings, live: object())
    out = asyncio.run(tcb.render_variant(_Settings(), _spec(), b"x", "image/png", "texture",
                                         render_url="http://pod.test/render"))

    assert seen == ["texture"]
    assert out == (b"rendered", "image/png")


def test_a_cut_that_misses_the_gate_is_none_not_an_error(monkeypatch):
    """12장 중 한 장이 안 된 것뿐이다 — 셀러 경로처럼 예외를 올리면 런이 통째로 죽는다."""
    monkeypatch.setattr(fi, "run_face_pass", lambda *a, **k: _Result(False))
    monkeypatch.setattr(fi, "resolve_backend", lambda settings, live: object())

    assert asyncio.run(tcb.render_variant(_Settings(), _spec(), b"x", "image/png", "prod",
                                          render_url="http://pod.test/render")) is None


# ── 마무리 상태 ─────────────────────────────────────────────────────────────
class _Cur:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        self.store.append((" ".join(sql.split()), tuple(params)))

    async def fetchone(self):
        return None

    async def fetchall(self):
        return []


class _Conn:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def cursor(self):
        return _Cur(self.calls)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.mark.parametrize("produced,attempts,expected", [
    (12, 1, "done"),
    (7, 1, "partial"),
    (1, 1, "partial"),
    (0, 1, "queued"),     # 재시도가 남았다
    (0, 2, "failed"),     # 두 번 해 보고도 0장이면 사람이 봐야 한다
])
def test_the_finish_state_follows_how_many_were_made(produced, attempts, expected):
    conn = _Conn()
    status = asyncio.run(rec.finish_build(conn, "b1", produced=produced, error=None,
                                          attempts=attempts))

    assert status == expected
    update = next(sql for sql, _ in conn.calls if sql.startswith("update fm_test_cut_builds"))
    assert "set status = %s, produced = %s" in update


def test_a_partial_run_is_not_retried_automatically():
    """같은 게이트에 또 떨어질 값이면 돈만 나간다 — 관리자가 보고 다시 생성을 누른다."""
    conn = _Conn()
    assert asyncio.run(rec.finish_build(conn, "b1", produced=5, error=None, attempts=1)) == "partial"


# ── 파드 대기 ───────────────────────────────────────────────────────────────
def test_a_missing_pod_is_a_wait_not_a_failure():
    """파드가 없는 건 실패가 아니라 대기다 — 상한을 넘겨야 실패로 남긴다."""
    import inspect

    source = inspect.getsource(rec.TestCutBuildReconciler._execute)
    assert "wait_for_backend" in source
    assert "budget_seconds=budget" in source
    assert "fm_test_cut_pod_wait_seconds" in source


def test_the_queue_only_runs_behind_a_flag():
    app = type("App", (), {"state": type("S", (), {"settings": _Settings()})()})()
    assert rec.TestCutBuildReconciler(app).enabled is True
    app.state.settings.fm_test_cut_build = "off"
    assert rec.TestCutBuildReconciler(app).enabled is False


# ── 파기 ────────────────────────────────────────────────────────────────────
def test_the_purge_clears_every_cut_by_model_id():
    """12장이 되어도 파기 범위는 그대로여야 한다 — model_id 한 줄로 지운다."""
    text = (pathlib.Path(__file__).resolve().parents[1]
            / "app/services/biometric_purge.py").read_text()

    assert "select r2_key as k from fm_model_test_cuts where model_id = any(%s)" in text
    assert "delete from fm_model_test_cuts where model_id = any(%s)" in text
    # 보정이나 승인 여부로 좁히지 않는다 — 좁히면 남는 얼굴이 생긴다.
    for narrowing in ("skin_finish", "approved", "limit"):
        block = text[text.index("delete from fm_model_test_cuts"):]
        assert narrowing not in block[:120]
