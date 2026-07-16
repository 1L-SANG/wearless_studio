"""fitProfile 스냅샷 계약 (fidelity 설계 D3·§E) — 라우트 생산, 워커 소비, 관측 이벤트.

핵심 불변식: 잡이 실제로 쓰는 프로필 = 잡 생성 시점 payload 스냅샷(경합·무음 유실 차단).
adjustedAxes 는 서버 diff 로만 산출(클라이언트 값 불신). legacy 잡(키 없음)만 analysis 폴백.
"""
import asyncio
import contextlib
import types

import app.routes as routes
from app.workers import mannequin_job
from app.agents import mannequin
from conftest import make_settings


class _Conn:
    async def commit(self):
        return None


def _no_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()
    monkeypatch.setattr(routes, "get_conn", fake_conn)


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _wire_route_fakes(monkeypatch, *, stored_profile, captured):
    async def fake_get_project(conn, uid, pid):
        return {"id": pid}

    async def fake_get_analysis(conn, pid):
        return {"fitProfile": stored_profile} if stored_profile is not None else {}

    async def fake_create_job(conn, **kw):
        captured.update(kw)
        return {"id": "job-1"}, True

    async def fake_get_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_reserve(conn, uid, cost):
        return {"balance": 10, "reserved": cost}

    async def fake_save_analysis(conn, pid, payload):
        captured["saved_analysis"] = payload
        return None

    async def fake_list_cuts(conn, uid, pid):
        return []

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    monkeypatch.setattr(routes.repo, "list_mannequin_cuts", fake_list_cuts)
    _no_db(monkeypatch)


def test_regenerate_snapshots_profile_and_server_computed_adjusted_axes(client, make_token, monkeypatch):
    captured = {}
    _wire_route_fakes(monkeypatch, stored_profile={
        "category": "top", "gender": "women", "source": "auto",
        "axes": {"fit": "regular", "length": "basic"},
    }, captured=captured)
    body = {"fitProfile": {
        "category": "top", "gender": "women", "source": "seller",
        "axes": {"fit": "slim", "length": "long"},
        "adjustedAxes": ["fit"],  # 클라이언트가 보낸 조정 목록은 무시돼야 함(서버 diff 정본)
    }}
    res = client.post("/v1/projects/p1/mannequins:regenerate", json=body, headers=_auth(make_token))
    assert res.status_code == 202, res.text
    snap = captured["payload"]["fitProfileSnapshot"]
    assert snap["version"] == 1
    assert snap["profile"]["axes"] == {"fit": "slim", "length": "long"}
    assert snap["profile"]["source"] == "seller"
    assert snap["adjustedAxes"] == ["fit", "length"]  # regular→slim, basic→long (서버 diff)
    assert captured["saved_analysis"]["fitProfile"]["axes"]["length"] == "long"  # UI 연속성 유지


def test_generate_snapshots_stored_profile_without_inventing(client, make_token, monkeypatch):
    captured = {}
    _wire_route_fakes(monkeypatch, stored_profile=None, captured=captured)
    res = client.post("/v1/projects/p1/mannequins:generate", headers=_auth(make_token))
    assert res.status_code == 202, res.text
    snap = captured["payload"]["fitProfileSnapshot"]
    assert snap == {"version": 1, "profile": None, "adjustedAxes": []}  # auto 값 발명 금지


# ---------- 워커 소비 ----------

class _FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()
        return _cm()


def _wire_worker(monkeypatch, *, analysis, payload, calls):
    async def get_product(conn, pid):
        return {"name": "티", "clothing_type": "top",
                "colors": [{"isBase": True, "images": [{"id": "a1", "slot": "Front"}]}]}

    async def get_analysis(conn, pid):
        return dict(analysis)

    async def get_asset_for_user(conn, uid, aid):
        return {"id": aid, "mime_type": "image/png", "r2_key": f"{aid}.png"}

    async def finalize_success(conn, **kw):
        calls["success"].append(kw)
        return {"data": kw["candidates"], "credits": 7}

    async def finalize_failure(conn, **kw):
        calls["failure"].append(kw)
        return True

    async def fake_emit(pool, job_id, event_type, p):
        calls["emits"].append((event_type, dict(p)))

    async def fake_run_candidate(**kw):
        calls["run"].append(kw)
        return {"asset_id": "as1", "bucket": "b", "key": "k.png", "mime": "image/png",
                "size": 3, "width": 1, "height": 1,
                "candidate": kw["candidate"], "base_fit": kw["base_fit"]}

    for name, fn in [("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_asset_for_user", get_asset_for_user),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)]:
        monkeypatch.setattr(mannequin_job.repo, name, fn)
    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_job, "_run_candidate", fake_run_candidate)
    settings = make_settings(base_mannequin_women_asset_id="bw", base_mannequin_men_asset_id="bm",
                             r2_bucket="bucket")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_FakePool(),
        r2=types.SimpleNamespace(get_bytes=lambda key: b"img"), gemini=None))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": payload}
    return app, job


SNAP_PROFILE = {"category": "top", "gender": "women", "source": "seller",
                "axes": {"fit": "slim", "length": "long"}, "version": 1}


def test_worker_prefers_payload_snapshot_over_latest_analysis(monkeypatch):
    calls = {"success": [], "failure": [], "emits": [], "run": []}
    app, job = _wire_worker(
        monkeypatch,
        analysis={"targetGenders": ["women"],
                  "fitProfile": {"category": "top", "gender": "women", "source": "seller",
                                 "axes": {"fit": "over", "length": "crop"}}},  # 스냅샷 이후 바뀐 최신값
        payload={"mode": "regenerate",
                 "fitProfileSnapshot": {"version": 1, "profile": SNAP_PROFILE,
                                        "adjustedAxes": ["length"]}},
        calls=calls)
    asyncio.run(mannequin_job.run_mannequin_job(app, job))
    assert calls["failure"] == []
    only = calls["run"][0]
    assert only["fit_profile"]["axes"] == {"fit": "slim", "length": "long"}  # 최신 analysis 무시
    assert only["adjusted_axes"] == ("length",)
    assert only["fit_profile_source"] == "payload_snapshot"


def test_worker_null_snapshot_profile_is_authoritative(monkeypatch):
    calls = {"success": [], "failure": [], "emits": [], "run": []}
    app, job = _wire_worker(
        monkeypatch,
        analysis={"targetGenders": ["women"],
                  "fitProfile": {"category": "top", "gender": "women", "axes": {"fit": "over"}}},
        payload={"mode": "generate",
                 "fitProfileSnapshot": {"version": 1, "profile": None, "adjustedAxes": []}},
        calls=calls)
    asyncio.run(mannequin_job.run_mannequin_job(app, job))
    assert calls["failure"] == []
    assert calls["run"][0]["fit_profile"] is None  # analysis 재독 금지


def test_worker_legacy_job_without_snapshot_falls_back_to_analysis(monkeypatch):
    calls = {"success": [], "failure": [], "emits": [], "run": []}
    app, job = _wire_worker(
        monkeypatch,
        analysis={"targetGenders": ["women"],
                  "fitProfile": {"category": "top", "gender": "women", "axes": {"fit": "over"}}},
        payload={"mode": "generate"},
        calls=calls)
    asyncio.run(mannequin_job.run_mannequin_job(app, job))
    assert calls["failure"] == []
    only = calls["run"][0]
    assert only["fit_profile"]["axes"]["fit"] == "over"
    assert only["fit_profile_source"] == "legacy_analysis_fallback"


def test_worker_malformed_snapshot_fails_loudly(monkeypatch):
    calls = {"success": [], "failure": [], "emits": [], "run": []}
    app, job = _wire_worker(
        monkeypatch,
        analysis={"targetGenders": ["women"]},
        payload={"mode": "regenerate", "fitProfileSnapshot": {"version": 2, "profile": "??"}},
        calls=calls)
    asyncio.run(mannequin_job.run_mannequin_job(app, job))
    assert calls["run"] == []
    assert calls["failure"] and calls["failure"][0]["metadata"]["error"] == "invalid_fit_profile_snapshot"


# ---------- 관측 이벤트 (prompt_rendered 해시) ----------

_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c626001000000ffff030000060005"
    "57bfabd40000000049454e44ae426082")


def test_run_candidate_emits_prompt_rendered_hashes(monkeypatch):
    calls = {"emits": []}

    async def fake_emit(pool, job_id, event_type, p):
        calls["emits"].append((event_type, dict(p)))

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            return types.SimpleNamespace(image=_PNG_1PX, mime="image/png")

    class _R2:
        def put_bytes(self, key, data, mime):
            return None

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    settings = make_settings(r2_bucket="bucket")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_FakePool(), r2=_R2(), gemini=_Gemini()))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t"}

    result = asyncio.run(mannequin_job._run_candidate(
        app=app, job=job, candidate="A", base_fit="regular", base_gender="women",
        base_img=types.SimpleNamespace(mime="image/png", data=b"x"),
        prod_imgs=[], match_img=None, product_count=1,
        template="Dress ${baseGender} ${clothingType}.\n${imageManifest}",
        product={"name": "티"}, analysis={}, clothing_type="top",
        image_manifest="1. base", fit_profile=SNAP_PROFILE,
        adjusted_axes=("length",), fit_profile_source="payload_snapshot"))

    assert result is not None
    rendered = [p for e, p in calls["emits"] if e == "step" and p.get("status") == "prompt_rendered"]
    assert len(rendered) == 1
    ev = rendered[0]
    assert len(ev["profile_hash"]) == 64 and len(ev["prompt_hash"]) == 64
    assert ev["input_source"] == "payload_snapshot" and ev["attempt"] == 1
    # 원문 미포함(다이제스트만) — 이벤트 payload 에 프로필/프롬프트 문자열이 없어야 함
    assert "slim" not in str(ev) and "FIT PROFILE" not in str(ev)
