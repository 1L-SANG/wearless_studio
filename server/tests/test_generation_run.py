"""Phase 1 — Generation Run + Output lineage (마네킹 경로 한정).

계약:
  · 플래그 off = 행 0. 기록기는 켜야만 존재한다.
  · shadow = **기록만**. 생성 결과·후보 선택·QC 판정에 개입하지 않고, 기록 실패도 삼킨다.
  · 프롬프트 전문은 DB 에 없다 — R2 object + sha256. 둘은 실제로 같은 바이트여야 한다.
  · settings 스냅샷에 시크릿이 없다(이름 기반 차단 + allowlist).
  · 채택본은 그것을 만든 호출과 연결된다 — 편집이 되돌려졌으면 **되돌아간 그 호출**과.
"""

import asyncio
import contextlib
import hashlib
import types

import cv2
import numpy as np
import pytest

from app import repo
from app.services import generation_run as gr
from app.workers import mannequin_job as mj
from conftest import make_settings

PROFILE = {"category": "top", "gender": "women", "source": "seller",
           "axes": {"fit": "regular"}, "version": 1}


def _png(bgr) -> bytes:
    ok, buf = cv2.imencode(".png", bgr)
    assert ok
    return buf.tobytes()


def _plain(v=235, size=(1264, 848)) -> bytes:
    return _png(np.full((size[0], size[1], 3), v, np.uint8))


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


def _run_job(monkeypatch, *, settings_kw=None, gemini_error=False, repo_overrides=None):
    """워커 1회 실행 → (runs, updates, r2_puts, finalize_calls)."""
    runs: list[dict] = []
    updates: list[dict] = []
    r2_puts: list[tuple] = []
    calls = {"success": [], "failure": []}
    cut_png = _plain()
    source_png = _plain(200)

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            if gemini_error:
                raise mj.GeminiError("provider down")
            return types.SimpleNamespace(image=cut_png, mime="image/png",
                                         latency_ms=1, usage={"totalTokenCount": 42})

    class _R2:
        def get_bytes(self, key):
            return {"bw.png": _plain(240), "front.png": source_png,
                    "back.png": source_png}[key]

        def put_bytes(self, key, data, mime, cache=None):
            r2_puts.append((key, data, mime))

    async def get_product(conn, project_id):
        return {"name": "무지 셔츠", "clothing_type": "top",
                "colors": [{"isBase": True, "images": [
                    {"id": "front", "slot": "Front"}, {"id": "back", "slot": "Back"}]}]}

    async def get_analysis(conn, project_id):
        return {"targetGenders": ["women"], "fit": "regular"}

    async def get_asset_for_user(conn, user_id, asset_id):
        return {
            "bw": {"id": "bw", "mime_type": "image/png", "r2_key": "bw.png"},
            "front": {"id": "front", "mime_type": "image/png", "r2_key": "front.png"},
            "back": {"id": "back", "mime_type": "image/png", "r2_key": "back.png"},
        }.get(asset_id)

    async def get_matching_item_asset(conn, item_id):
        return None

    async def finalize_success(conn, **kw):
        calls["success"].append(kw)
        return {"cuts": kw["candidates"], "available": 7}

    async def finalize_failure(conn, **kw):
        calls["failure"].append(kw)
        return True

    async def insert_run(conn, **kw):
        runs.append(kw)

    async def update_run(conn, **kw):
        updates.append(kw)

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    for name, fn in (("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_asset_for_user", get_asset_for_user),
                     ("get_matching_item_asset", get_matching_item_asset),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure),
                     ("insert_generation_run", insert_run),
                     ("update_generation_run", update_run)):
        monkeypatch.setattr(repo, name, (repo_overrides or {}).get(name, fn))
    monkeypatch.setattr(mj, "_emit", fake_emit)

    settings = make_settings(**{
        "base_mannequin_women_asset_id": "bw", "r2_bucket": "bucket",
        "mannequin_hybrid_composite": "off", "mannequin_max_attempts": 1,
        **(settings_kw or {})})
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_R2(), gemini=_Gemini()))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": {}}
    asyncio.run(mj.run_mannequin_job(app, job))
    return runs, updates, r2_puts, calls


# ── 플래그 ────────────────────────────────────────────────────────────────────

def test_flag_off_writes_no_generation_run_rows(monkeypatch):
    """기본값 off — 기록 코드가 배선돼도 행은 하나도 생기지 않는다(기존 동작 불변)."""
    runs, updates, r2_puts, calls = _run_job(monkeypatch)
    assert runs == [] and updates == []
    assert not any(k.endswith(".txt") for k, _d, _m in r2_puts)  # 프롬프트 업로드도 없음
    assert calls["success"], "생성 자체는 정상 완료해야 한다"
    for cand in calls["success"][0]["candidates"]:
        assert cand.get("generation_run_id") is None


def test_shadow_records_one_row_per_provider_call(monkeypatch):
    runs, updates, _puts, calls = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"})
    assert runs, "shadow 인데 provider 호출 기록이 없다"
    assert all(r["kind"] == "mannequin_generate" for r in runs)
    assert all(r["job_id"] == "j1" and r["project_id"] == "p1" and r["user_id"] == "u1"
               for r in runs)
    # 호출 1건 = 시작 1행 + 종결 1건
    assert len(updates) == len(runs)
    assert all(u["status"] == "succeeded" for u in updates)
    assert all(isinstance(u["latency_ms"], int) for u in updates)
    assert all(u["usage"] == {"totalTokenCount": 42} for u in updates)


def test_shadow_does_not_change_the_shipped_cut(monkeypatch):
    """기록 유무가 산출물을 바꾸면 그건 관측기가 아니다."""
    _r, _u, puts_off, calls_off = _run_job(monkeypatch)
    _r2, _u2, puts_on, calls_on = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"})
    img_off = [d for k, d, _m in puts_off if not k.endswith(".txt")]
    img_on = [d for k, d, _m in puts_on if not k.endswith(".txt")]
    assert img_off == img_on
    strip = lambda cs: [{k: v for k, v in c.items() if k != "generation_run_id"} for c in cs]
    a = strip(calls_off["success"][0]["candidates"])
    b = strip(calls_on["success"][0]["candidates"])
    assert [{k: v for k, v in c.items() if k not in ("asset_id", "key")} for c in a] == \
           [{k: v for k, v in c.items() if k not in ("asset_id", "key")} for c in b]


# ── 프롬프트: DB 에 전문 없음 / R2 와 해시 일치 ─────────────────────────────────

def test_prompt_body_never_reaches_the_database(monkeypatch):
    runs, _u, puts, _c = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"})
    prompt_bodies = [d.decode("utf-8") for k, d, _m in puts if k.endswith(".txt")]
    assert prompt_bodies, "프롬프트가 R2 에 올라가지 않았다"
    body = prompt_bodies[0]
    assert len(body) > 50, "프롬프트가 비정상적으로 짧다 — 전제 확인"
    for row in runs:
        flat = repr(row)
        assert body not in flat
        # 앞 80자만으로도 새면 안 된다(부분 유출 방지)
        assert body[:80] not in flat


def test_prompt_sha256_matches_the_r2_object_bytes(monkeypatch):
    runs, _u, puts, _c = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"})
    by_key = {k: d for k, d, _m in puts if k.endswith(".txt")}
    assert by_key
    for row in runs:
        key = row["prompt_r2_key"]
        assert key in by_key, "행이 가리키는 R2 키가 실제로 없다"
        assert row["prompt_sha256"] == hashlib.sha256(by_key[key]).hexdigest()


def test_prompt_r2_key_is_scoped_to_owner_and_job(monkeypatch):
    runs, _u, _p, _c = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"})
    for row in runs:
        assert row["prompt_r2_key"].startswith("users/u1/projects/p1/genruns/j1/")


def test_prompt_upload_failure_keeps_the_row_and_the_generation(monkeypatch):
    """R2 가 죽어도 기록은 sha256 으로 남고 생성은 계속된다."""
    runs: list[dict] = []

    class BoomR2:
        def put_bytes(self, key, data, mime, cache=None):
            raise RuntimeError("r2 down")

    async def insert_run(conn, **kw):
        runs.append(kw)

    monkeypatch.setattr(repo, "insert_generation_run", insert_run)
    monkeypatch.setattr(repo, "update_generation_run", lambda conn, **kw: _noop())
    logger = gr.RunLogger(pool=_Pool(), r2=BoomR2(), job_id="j", project_id="p",
                          user_id="u", enabled=True)
    run_id = asyncio.run(logger.begin(kind="mannequin_generate", prompt="hello"))
    assert run_id and runs and runs[0]["prompt_r2_key"] is None
    assert runs[0]["prompt_sha256"] == hashlib.sha256(b"hello").hexdigest()


async def _noop():
    return None


# ── 입력 스냅샷 / 설정 스냅샷 ──────────────────────────────────────────────────

def test_input_asset_snapshot_carries_id_slot_and_checksum(monkeypatch):
    runs, _u, _p, _c = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"})
    gen_rows = [r for r in runs if r["kind"] == "mannequin_generate"]
    assert gen_rows
    assets = gen_rows[0]["input_assets"]
    assert assets, "생성 호출에 입력 asset 스냅샷이 없다"
    for a in assets:
        assert a["assetId"] and a["slot"]
        assert isinstance(a["sha256"], str) and len(a["sha256"]) == 64
    assert {a["slot"] for a in assets} >= {"Front"}


def test_input_snapshot_hashes_the_bytes_that_actually_went_out():
    ref = types.SimpleNamespace(asset_id="a1", slot="Front", image=b"abc")
    out = gr.input_asset_snapshot([ref])
    assert out == [{"assetId": "a1", "slot": "Front",
                    "sha256": hashlib.sha256(b"abc").hexdigest()}]


def test_settings_snapshot_contains_no_secrets():
    s = make_settings()
    snap = gr.settings_snapshot(s)
    assert snap, "allowlist 스냅샷이 비어 있다"
    banned = ("key", "secret", "token", "password", "credential", "url", "dsn")
    for name, value in snap.items():
        assert not any(b in name.lower() for b in banned), f"의심 필드 유출: {name}"
        assert isinstance(value, (str, int, float, bool)) or value is None
    # 실제 시크릿 값이 우연히라도 섞이지 않았는지 — 값 대조
    for attr in dir(s):
        if any(b in attr.lower() for b in banned):
            v = getattr(s, attr, None)
            if isinstance(v, str) and len(v) >= 8:
                assert v not in snap.values(), f"시크릿 값 유출: {attr}"


def test_settings_snapshot_ignores_non_allowlisted_fields():
    s = make_settings()
    object.__setattr__(s, "openai_api_key", "sk-should-never-appear") \
        if hasattr(s, "__dict__") else None
    snap = gr.settings_snapshot(s)
    assert "openai_api_key" not in snap
    assert "sk-should-never-appear" not in snap.values()


# ── 실패 경로 ─────────────────────────────────────────────────────────────────

def test_provider_error_marks_the_run_failed(monkeypatch):
    runs, updates, _p, calls = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"}, gemini_error=True)
    assert runs, "호출 직전 기록이 없으면 실패 원인을 재현할 수 없다"
    assert updates and all(u["status"] == "failed" for u in updates)
    assert all("GeminiError" in (u["provider_error"] or "") for u in updates)
    assert calls["failure"], "provider 실패 잡은 실패로 종결돼야 한다"


def test_recorder_failures_never_break_generation(monkeypatch):
    """DB 가 죽어도 컷은 나간다 — 관측기는 생성 경로를 죽이지 않는다."""
    async def boom(conn, **kw):
        raise RuntimeError("db down")

    runs, updates, _p, calls = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"},
        repo_overrides={"insert_generation_run": boom, "update_generation_run": boom})
    assert calls["success"], "기록 실패가 생성을 죽였다"
    for cand in calls["success"][0]["candidates"]:
        assert cand.get("generation_run_id") is None  # 기록 실패 → 연결 없음


# ── 산출물 연결 ───────────────────────────────────────────────────────────────

def test_adopted_cut_is_linked_to_the_call_that_made_its_bytes(monkeypatch):
    runs, _u, _p, calls = _run_job(
        monkeypatch, settings_kw={"generation_run_log": "shadow"})
    ids = {r["run_id"] for r in runs}
    cands = calls["success"][0]["candidates"]
    assert cands
    for cand in cands:
        assert cand["generation_run_id"] in ids


def test_run_id_lookup_follows_the_image_bytes_not_the_last_call():
    """편집이 회귀로 되돌려지면 채택본은 이전 호출의 것이다 — 바이트로 역참조한다."""
    logger = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    logger._by_image[hashlib.sha256(b"first").hexdigest()] = "run-1"
    logger._by_image[hashlib.sha256(b"edited").hexdigest()] = "run-2"
    assert logger.run_id_for_image(b"first") == "run-1"   # 되돌아간 경우
    assert logger.run_id_for_image(b"edited") == "run-2"
    assert logger.run_id_for_image(b"unknown") is None


def test_disabled_logger_is_inert():
    logger = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=False)
    assert asyncio.run(logger.begin(kind="mannequin_generate", prompt="x")) is None
    assert logger.run_id_for_image(b"x") is None


# ── finalize: 같은 tx 안에서 cut ↔ run 연결 ───────────────────────────────────

class _FakeCursor:
    """finalize 가 실행한 SQL 을 기록하는 최소 커서. select 는 고정 응답."""

    def __init__(self, sink):
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        self.sink.append((" ".join(sql.split()), params))
        self._last = sql

    async def fetchone(self):
        low = self._last.lower()
        if "for update" in low:
            return {"id": "job-1"}
        if "max(version)" in low:
            return {"v": 3}
        if "returning id" in low:
            return {"id": "cut-uuid-1"}
        return None


class _FakeConn:
    def __init__(self, sink):
        self.sink = sink

    def cursor(self):
        return _FakeCursor(self.sink)


def _finalize(monkeypatch, candidate_extra: dict):
    sink: list[tuple] = []

    async def consume(conn, **kw):
        return 10

    monkeypatch.setattr(repo, "_consume_buckets", consume)
    cand = {"asset_id": "a-1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 1, "width": 2, "height": 3, "candidate": "A", "base_fit": "regular",
            "qc_scores": None, **candidate_extra}
    out = asyncio.run(repo.finalize_mannequin_success(
        _FakeConn(sink), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        candidates=[cand], reserved=2, charge=2, metadata={}))
    return sink, out


def test_finalize_links_output_to_run_and_cut(monkeypatch):
    sink, out = _finalize(monkeypatch, {"generation_run_id": "run-9"})
    inserts = [(s, p) for s, p in sink if s.startswith("insert into generation_outputs")]
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert params[0] == "run-9"          # generation_run_id
    assert params[1] == "p1"             # project_id
    assert params[2] == "cut-uuid-1"     # returning id 로 받은 실제 컷 uuid
    assert params[3] == "a-1"            # asset_id
    # 봉투(계약 §3.3)는 오염되지 않는다
    assert "generation_run_id" not in out["cuts"][0]
    assert set(out["cuts"][0]) == {"id", "src", "candidate", "version", "baseFit",
                                   "fitAdjust", "lengthAdjust", "matchAdjust", "qcScores"}


def test_finalize_writes_no_output_row_without_a_run(monkeypatch):
    sink, _out = _finalize(monkeypatch, {})
    assert not [s for s, _p in sink if s.startswith("insert into generation_outputs")]
    assert not [s for s, _p in sink if "savepoint" in s]


def test_output_insert_is_savepointed_so_a_failure_cannot_abort_the_cut(monkeypatch):
    """migration 미적용 환경에서 컷 저장이 통째로 날아가면 안 된다."""
    sink, _out = _finalize(monkeypatch, {"generation_run_id": "run-9"})
    order = [s for s, _p in sink]
    assert any(s.startswith("savepoint genout_insert") for s in order)
    assert any(s.startswith("release savepoint genout_insert") for s in order)
    i_save = next(i for i, s in enumerate(order) if s.startswith("savepoint genout_insert"))
    i_ins = next(i for i, s in enumerate(order)
                 if s.startswith("insert into generation_outputs"))
    assert i_save < i_ins, "insert 가 savepoint 밖에 있으면 tx 전체가 abort 된다"


@pytest.mark.parametrize("name", gr.SETTINGS_ALLOWLIST)
def test_allowlisted_setting_exists_on_settings(name):
    """allowlist 가 실제 Settings 필드와 어긋나면 스냅샷이 조용히 빈다."""
    assert hasattr(make_settings(), name), f"Settings 에 없는 필드: {name}"
