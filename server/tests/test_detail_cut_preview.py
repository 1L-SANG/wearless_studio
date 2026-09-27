"""상세페이지 생성 중 컷 미리보기 — 읽는 순간 권한을 다시 확인한다(2026-09-26, 오너 결정 b).

REAL(실존 모델) 얼굴 컷은 최종 권한 펜스 전까지 이벤트 원장에 주소·키·표식 id 를 싣지 않는다
(492cbc64 — 그 보안 테스트 3건은 test_detail_page_license_face.py 에 그대로 있다). 대신
GET /v1/projects/{p}/jobs/{j}/cuts/{block}/preview 가 요청마다 소유·잡 상태·출력 표식을 보고,
REAL 이면 라이선스를 지금 다시 확인한 뒤에만 바이트를 보낸다. 여기서 지키는 것:
  ① 주인은 받는다(no-store, 주소 없음) ② 남의 잡·다른 프로젝트·끝난 잡·다른 종류는 404
  ③ 라이선스 철회·만료는 403 이고 저장소를 읽지도 않는다 ④ 비-REAL 잡도 같은 라우트
  ⑤ 로그·응답 어디에도 저장소 키·주소가 없다 ⑥ 워커는 표식에 블록 id 만 적고 원장은 그대로다.
"""

import asyncio
import contextlib
import logging
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app.routes as routes
from app import detail_cut_preview, facemarket, repo
from app.agents import identity_source
from app.workers import detail_page_job as dpj
from conftest import FakeConn, auth_headers, make_settings, worker_job

USER = "user-1"                     # conftest make_token 기본 sub
OTHER_USER = "user-2"
PROJECT = str(uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
OTHER_PROJECT = str(uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))
JOB = str(uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"))
MODEL_ID = "22222222-2222-4222-8222-222222222222"
LIC_ID = "11111111-1111-4111-8111-111111111111"
ENROLLMENT_ID = "33333333-3333-4333-8333-333333333333"
CATEGORY = "일반 의류"
ASSET = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
KEY = f"users/{USER}/projects/{PROJECT}/ai/{JOB}/{ASSET}.png"
REAL_PAYLOAD = {
    "mode": "generate",
    "modelId": MODEL_ID,
    "brandUseCategory": CATEGORY,
    "_facemarket": {"modelId": MODEL_ID, "licenseId": LIC_ID},
}
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260926150000_ai_output_preview_block_ids.sql"
)


def _license_row(status="active", days_left=30):
    return {
        "id": LIC_ID,
        "model_id": MODEL_ID,
        "model_name": "김*늘",
        "status": status,
        "license_valid_until": datetime.now(timezone.utc) + timedelta(days=days_left),
        "unit_price": 14900,
        "vc_id": "vc-1",
        "allowed_use": [CATEGORY],
        "forbidden_use": [],
        "model_status": "verified",
        "assets_status": "ready",
        "current_enrollment_id": ENROLLMENT_ID,
        "license_enrollment_id": ENROLLMENT_ID,
        "enrollment_status": "passed",
        "match_policy_version": "policy-v1",
        "has_face_front": True,
        "has_grid_sedcard": True,
        "assets_current_evidence": True,
    }


class _R2:
    def __init__(self, data=b"\x89PNG-preview", fail=None):
        self.data = data
        self.fail = fail
        self.gets: list[str] = []

    def get_bytes(self, key):
        self.gets.append(key)
        if self.fail is not None:
            raise self.fail
        return self.data


def _setup(monkeypatch, client, *, job=None, key=KEY, license_row=None, r2=None):
    """잡 1건(주인 USER)·표식 1건·라이선스 1행을 흉내 낸다. 호출 기록을 돌려준다."""
    calls = {"get_job": [], "preview_key": [], "resolve": []}
    job = job if job is not None else {
        "id": JOB, "user_id": USER, "project_id": PROJECT, "kind": "detail_page",
        "status": "running", "payload": dict(REAL_PAYLOAD),
    }

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield FakeConn()

    async def fake_get_job(conn, user_id, job_id):
        calls["get_job"].append((user_id, job_id))
        # 실제 repo.get_job 처럼 id 와 user_id 가 둘 다 맞아야 행이 나온다.
        if job and job["id"] == job_id and job["user_id"] == user_id:
            return dict(job)
        return None

    async def fake_preview_key(conn, *, job_id, block_id):
        calls["preview_key"].append((job_id, block_id))
        return key if block_id == "b1" else None

    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        calls["resolve"].append((model_id, license_id))
        return license_row if license_row is not None else _license_row()

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(repo, "get_job", fake_get_job)
    monkeypatch.setattr(repo, "get_detail_cut_preview_key", fake_preview_key)
    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    client.app.state.r2 = r2 if r2 is not None else _R2()
    return calls


def _url(project=PROJECT, job=JOB, block="b1"):
    return f"/v1/projects/{project}/jobs/{job}/cuts/{block}/preview"


# ── ① 주인은 받는다 ────────────────────────────────────────────────────────────


def test_owner_gets_real_cut_bytes_after_license_recheck(client, make_token, monkeypatch):
    calls = _setup(monkeypatch, client)

    res = client.get(_url(), headers=auth_headers(make_token))

    assert res.status_code == 200, res.text
    assert res.content == b"\x89PNG-preview"
    assert res.headers["content-type"].startswith("image/png")
    assert res.headers["cache-control"] == "private, no-store"
    assert res.headers.get("x-content-type-options") == "nosniff"
    # 서명 주소로 돌려보내지 않는다 — 브라우저가 볼 저장소 주소 자체가 없다.
    assert "location" not in res.headers
    assert calls["get_job"] == [(USER, JOB)]
    assert calls["preview_key"] == [(JOB, "b1")]
    # 성공 종결과 같은 라이선스 확인을 요청마다 한다(스냅샷의 모델·라이선스 그대로).
    assert calls["resolve"] == [(MODEL_ID, LIC_ID)]
    assert client.app.state.r2.gets == [KEY]


def test_preview_requires_login(client, monkeypatch):
    _setup(monkeypatch, client)
    res = client.get(_url())
    assert res.status_code == 401
    assert client.app.state.r2.gets == []


# ── ② 남의 것·다른 프로젝트·끝난 잡·다른 종류는 404 ──────────────────────────────


def test_other_users_job_is_404_and_never_reads_storage(client, make_token, monkeypatch):
    calls = _setup(monkeypatch, client)

    res = client.get(_url(), headers=auth_headers(lambda: make_token(sub=OTHER_USER)))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    assert calls["get_job"] == [(OTHER_USER, JOB)]
    assert calls["preview_key"] == [] and calls["resolve"] == []
    assert client.app.state.r2.gets == []


def test_job_of_another_project_is_404(client, make_token, monkeypatch):
    calls = _setup(monkeypatch, client)
    res = client.get(_url(project=OTHER_PROJECT), headers=auth_headers(make_token))
    assert res.status_code == 404
    assert calls["preview_key"] == []
    assert client.app.state.r2.gets == []


def test_finished_or_other_kind_jobs_are_404(client, make_token, monkeypatch):
    base = {"id": JOB, "user_id": USER, "project_id": PROJECT, "kind": "detail_page",
            "payload": dict(REAL_PAYLOAD)}
    for job in (
        {**base, "status": "done"},        # 성공 — 완료 병합의 안정 주소가 이긴다
        {**base, "status": "error"},       # 실패 — 출력은 지워지는 중
        {**base, "status": "cancelled"},
        {**base, "status": "running", "kind": "editor_image"},
    ):
        _setup(monkeypatch, client, job=job)
        res = client.get(_url(), headers=auth_headers(make_token))
        assert res.status_code == 404, job
        assert client.app.state.r2.gets == []


def test_block_without_output_or_malformed_ids_are_404(client, make_token, monkeypatch):
    calls = _setup(monkeypatch, client)
    assert client.get(_url(block="b-unknown"), headers=auth_headers(make_token)).status_code == 404
    assert client.get(_url(job="not-a-uuid"), headers=auth_headers(make_token)).status_code == 404
    assert client.get(_url(project="nope"), headers=auth_headers(make_token)).status_code == 404
    long_block = "x" * 201
    assert client.get(_url(block=long_block), headers=auth_headers(make_token)).status_code == 404
    # 형식이 틀린 id 는 DB 에 가기 전에 걸린다
    assert calls["get_job"] == [(USER, JOB)]
    assert client.app.state.r2.gets == []


def test_only_this_jobs_output_key_shape_is_served(client, make_token, monkeypatch):
    for bad in (
        f"users/{USER}/projects/{PROJECT}/ai/{JOB}/ckpt/{'0' * 64}/final.bin",   # 체크포인트
        f"users/{OTHER_USER}/projects/{PROJECT}/ai/{JOB}/{ASSET}.png",           # 남의 경로
        f"users/{USER}/projects/{PROJECT}/ai/other-job/{ASSET}.png",             # 다른 잡
        f"faces/{MODEL_ID}/face_front.png",                                      # 얼굴 버킷 키
    ):
        _setup(monkeypatch, client, key=bad)
        res = client.get(_url(), headers=auth_headers(make_token))
        assert res.status_code == 404, bad
        assert client.app.state.r2.gets == []


# ── ③ 라이선스가 지금 유효하지 않으면 403 — 저장소를 읽지도 않는다 ─────────────────


def test_revoked_license_is_403_and_bytes_are_never_read(client, make_token, monkeypatch):
    _setup(monkeypatch, client, license_row=_license_row(status="revoked"))

    res = client.get(_url(), headers=auth_headers(make_token))

    assert res.status_code == 403
    assert res.json()["error"]["code"] == "license_revoked"
    assert client.app.state.r2.gets == []


def test_expired_license_is_403(client, make_token, monkeypatch):
    _setup(monkeypatch, client, license_row=_license_row(days_left=-1))
    res = client.get(_url(), headers=auth_headers(make_token))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "license_expired"
    assert client.app.state.r2.gets == []


def test_real_job_without_license_snapshot_is_403(client, make_token, monkeypatch):
    job = {"id": JOB, "user_id": USER, "project_id": PROJECT, "kind": "detail_page",
           "status": "running", "payload": {"mode": "generate", "modelId": MODEL_ID}}
    calls = _setup(monkeypatch, client, job=job)
    res = client.get(_url(), headers=auth_headers(make_token))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "model_unavailable"
    assert calls["resolve"] == []
    assert client.app.state.r2.gets == []


def test_license_rechecked_on_every_request_not_cached(client, make_token, monkeypatch):
    """첫 요청은 유효, 그 사이 철회 → 두 번째 요청은 403. 한 번 통과를 기억하지 않는다."""
    calls = _setup(monkeypatch, client)
    statuses = iter(["active", "revoked"])

    async def flipping_resolve(conn, model_id, *, license_id=None, **_kwargs):
        calls["resolve"].append((model_id, license_id))
        return _license_row(status=next(statuses))

    monkeypatch.setattr(facemarket, "resolve_model_license", flipping_resolve)

    first = client.get(_url(), headers=auth_headers(make_token))
    second = client.get(_url(), headers=auth_headers(make_token))

    assert first.status_code == 200
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "license_revoked"
    assert len(calls["resolve"]) == 2
    assert client.app.state.r2.gets == [KEY]


# ── ④ 비-REAL 잡도 같은 라우트 — 라이선스를 찾지 않는다 ──────────────────────────


def test_non_real_job_is_served_without_license_lookup(client, make_token, monkeypatch):
    job = {"id": JOB, "user_id": USER, "project_id": PROJECT, "kind": "detail_page",
           "status": "running", "payload": {"mode": "generate", "modelId": "virtual-model-a"}}
    calls = _setup(monkeypatch, client, job=job)

    res = client.get(_url(), headers=auth_headers(make_token))

    assert res.status_code == 200
    assert res.headers["cache-control"] == "private, no-store"
    assert calls["resolve"] == []


# ── ⑤ 로그·응답에 저장소 키·주소가 없다 ─────────────────────────────────────────


def test_no_storage_key_or_url_is_logged(client, make_token, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _setup(monkeypatch, client)
    ok = client.get(_url(), headers=auth_headers(make_token))
    _setup(monkeypatch, client, r2=_R2(fail=RuntimeError(f"boom while reading {KEY}")))
    down = client.get(_url(), headers=auth_headers(make_token))
    _setup(monkeypatch, client, license_row=_license_row(status="revoked"))
    denied = client.get(_url(), headers=auth_headers(make_token))

    assert ok.status_code == 200
    assert down.status_code == 503
    assert down.json()["error"]["code"] == "preview_unavailable"
    assert denied.status_code == 403
    logged = "\n".join(
        f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert KEY not in logged
    assert f"users/{USER}/" not in logged
    assert "X-Amz-" not in logged and "https://" not in logged
    for res in (down, denied):
        assert KEY not in res.text and "users/" not in res.text


def test_missing_object_is_404_not_503(client, make_token, monkeypatch):
    class _NoSuchKey(Exception):
        response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}

    _setup(monkeypatch, client, r2=_R2(fail=_NoSuchKey()))
    res = client.get(_url(), headers=auth_headers(make_token))
    assert res.status_code == 404


# ── repo·마이그레이션 ────────────────────────────────────────────────────────────


class _SqlCursor:
    def __init__(self, log, row=None):
        self.log, self.row = log, row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return self.row


class _SqlConn:
    def __init__(self, row=None):
        self.log = []
        self.row = row

    def cursor(self):
        return _SqlCursor(self.log, self.row)


def test_repo_tag_appends_block_once_and_lookup_is_pending_and_job_scoped():
    conn = _SqlConn()
    asyncio.run(repo.tag_ai_output_preview_block(conn, intent_id="i1", block_id="b1"))
    sql, params = conn.log[0]
    assert "update ai_output_cleanup_intents" in sql
    assert "array_append(preview_block_ids" in sql
    assert "not (%s::text = any(preview_block_ids))" in sql
    assert params == ("b1", "i1", "b1")

    conn = _SqlConn(row={"r2_key": KEY})
    got = asyncio.run(repo.get_detail_cut_preview_key(conn, job_id=JOB, block_id="b1"))
    sql, params = conn.log[0]
    assert got == KEY
    assert "where job_id = %s" in sql and "status = 'pending'" in sql
    assert "= any(preview_block_ids)" in sql
    assert params == (JOB, "b1")

    # 커서 없는 커넥션(스텁)·빈 값은 조용히 아무것도 안 한다
    assert asyncio.run(repo.get_detail_cut_preview_key(object(), job_id=JOB, block_id="b1")) is None
    asyncio.run(repo.tag_ai_output_preview_block(object(), intent_id="i1", block_id="b1"))
    empty = _SqlConn()
    asyncio.run(repo.tag_ai_output_preview_block(empty, intent_id=None, block_id="b1"))
    assert empty.log == []


def test_migration_adds_preview_block_ids_to_server_only_cleanup_intents():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "alter table public.ai_output_cleanup_intents" in sql
    assert "add column if not exists preview_block_ids text[] not null default '{}'" in sql
    # 새 테이블·공개 권한을 만들지 않는다(정리 표식 테이블은 RLS + anon/authenticated 권한 없음).
    assert "create table" not in sql and "grant" not in sql


def test_is_real_job_is_conservative():
    assert detail_cut_preview.is_real_job(REAL_PAYLOAD)
    assert detail_cut_preview.is_real_job({"modelId": MODEL_ID})          # 스냅샷 없어도 REAL
    assert detail_cut_preview.is_real_job({"_facemarket": {}})
    assert not detail_cut_preview.is_real_job({"modelId": "virtual-model-a"})
    assert not detail_cut_preview.is_real_job({})
    assert not detail_cut_preview.is_real_job(None)


# ── ⑥ 워커: 표식에 블록 id 만 적고, 원장(이벤트)은 492cbc64 그대로 ────────────────


class _WorkerCur:
    def __init__(self, row):
        self._row = row

    async def execute(self, sql, params=None):
        return None

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _WorkerConn:
    def __init__(self, row):
        self._row = row

    async def commit(self):
        return None

    def cursor(self):
        return _WorkerCur(self._row)


class _WorkerPool:
    def __init__(self, row):
        self._row = row

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _WorkerConn(self._row)

        return _cm()


class _WorkerR2:
    def __init__(self):
        self.puts: list[str] = []

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append(key)

    def get_bytes(self, key):
        return b"\x89PNG"

    def delete(self, key):
        return None

    def head(self, key):
        return None

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"


def _run_real_worker(monkeypatch, *, storyboard, tag=None):
    captured: dict = {"tags": [], "intents": {}}
    row = _license_row()

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False, "facemarket_license_id": "later-lock"}

    async def fake_sb(conn, pid):
        return storyboard

    async def fake_prod(conn, pid):
        return {"clothing_type": "top",
                "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, **_kw):
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["cut_results"] = cut_results
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured["finalized"] = kw
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_failure(conn, **kw):
        captured["failure"] = kw
        return {"status": "failed"}

    async def fake_emit(pool, job_id, et, payload):
        captured.setdefault("events", []).append((et, payload))

    async def fake_intent(conn, *, job_id, r2_key):
        intent_id = f"i{len(captured['intents']) + 1}"
        captured["intents"][intent_id] = r2_key
        return intent_id

    async def fake_tag(conn, *, intent_id, block_id):
        if tag is not None:
            await tag(conn, intent_id=intent_id, block_id=block_id)
        captured["tags"].append((intent_id, block_id))

    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        return row

    async def fake_verify(app, license_row, **kwargs):
        return None

    async def fake_assets(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": "current/face_front.png", "mime": "image/png", "bucket": "face"},
            {"key": "current/grid_sedcard.png", "mime": "image/png", "bucket": "face"},
        ]

    async def fake_lock(conn):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.repo, "create_ai_output_cleanup_intent", fake_intent)
    monkeypatch.setattr(dpj.repo, "tag_ai_output_preview_block", fake_tag)
    monkeypatch.setattr(dpj.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
    monkeypatch.setattr(dpj, "_emit", fake_emit)
    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_assets)

    r2 = _WorkerR2()
    state = types.SimpleNamespace(
        settings=make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        pool=_WorkerPool(row), r2=r2, gemini=types.SimpleNamespace(),
        r2_face=types.SimpleNamespace(get_bytes=lambda key: b"\x89PNG-FACE"),
    )
    job = worker_job(dict(REAL_PAYLOAD), credits_reserved=7)
    asyncio.run(dpj.run_detail_page_job(types.SimpleNamespace(state=state), job))
    return captured, r2


def _assert_no_prefinal_output_reference(events):
    # test_detail_page_license_face._assert_no_prefinal_output_reference 와 같은 기준(492cbc64).
    blob = repr(events)
    assert "previewUrl" not in blob
    assert "/v1/assets/" not in blob
    assert "https://r2.test/" not in blob
    assert "users/" not in blob
    assert "cleanup_intent" not in blob
    assert "intent-" not in blob
    assert "'i1'" not in blob and "'i2'" not in blob


def test_worker_tags_real_cut_block_without_touching_the_ledger(monkeypatch):
    captured, r2 = _run_real_worker(
        monkeypatch,
        storyboard=[{"id": "b1", "source": "ai", "cutType": "horizon", "shot": "full"}],
    )

    assert captured.get("failure") is None, captured.get("failure")
    (intent_id, key), = captured["intents"].items()
    assert key == r2.puts[0]
    # 정리 표식에 블록 id 만 적는다 — 미리보기 라우트가 이걸로 키를 찾는다.
    assert captured["tags"] == [(intent_id, "b1")]
    dones = [p for et, p in captured["events"] if et == "step" and p.get("status") == "cut_done"]
    assert [d["blockId"] for d in dones] == ["b1"]
    _assert_no_prefinal_output_reference(captured["events"])


def test_duplicate_real_cut_tags_the_same_output_for_the_copy(monkeypatch):
    """복제 자리는 새 출력이 없다 — 원본 출력의 정리 표식에 복제 블록 id 를 더해, 두 자리 모두
    같은 그림을 미리보기로 받는다. 이벤트에는 여전히 주소가 없다(REAL)."""
    events, intents, tags = [], {}, []

    async def fake_emit(pool, job_id, et, payload):
        events.append((et, payload))

    async def fake_gen(settings, gemini, cut_spec, product, images, **_kw):
        return b"IMGDATA", "image/png"

    async def fake_intent(conn, *, job_id, r2_key):
        intent_id = f"i{len(intents) + 1}"
        intents[intent_id] = r2_key
        return intent_id

    async def fake_tag(conn, *, intent_id, block_id):
        tags.append((intent_id, block_id))

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.repo, "create_ai_output_cleanup_intent", fake_intent)
    monkeypatch.setattr(dpj.repo, "tag_ai_output_preview_block", fake_tag)
    r2 = _WorkerR2()
    state = types.SimpleNamespace(
        settings=make_settings(gemini_api_key="x", r2_bucket="b"),
        pool=_WorkerPool(None), r2=r2, gemini=types.SimpleNamespace())
    images = [dpj.InlineImage("image/png", b"front")]
    worn = {"source": "ai", "sectionId": "section-a", "sectionRole": "studio",
            "cutType": "horizon", "shot": "full", "direction": "front",
            "pose": "auto", "refScope": "all"}

    def item(spec):
        # (block, images, manifest, has_face, product_images, space_set_plate,
        #  strict_space_scene_qc, passthrough, confirmed_packet, real_identity_attached)
        return (spec, images, "manifest", True, images, None, False, None, None, True)

    prepared = [item({**worn, "id": "worn"}), item({**worn, "id": "worn-copy"})]
    assert dpj._duplicate_source_indexes([p[0] for p in prepared], "top") == [None, 0]

    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:tok",
           "credits_reserved": 1, "metadata": {"perCutCost": 1}}
    (cut_results, cut_assets, *_rest) = asyncio.run(dpj._gen_cuts(
        types.SimpleNamespace(state=state), job, prepared,
        {"name": "셔츠", "clothingType": "top"}, {}))

    assert [c["blockId"] for c in cut_results] == ["worn", "worn-copy"]
    assert len(cut_assets) == 1 and len(r2.puts) == 1
    assert cut_assets[0]["metadata"]["facemarket_real_derived"] is True
    assert tags == [("i1", "worn"), ("i1", "worn-copy")]
    dones = [p for et, p in events if et == "step" and p.get("status") == "cut_done"]
    assert [d["blockId"] for d in dones] == ["worn", "worn-copy"]
    _assert_no_prefinal_output_reference(events)


def test_worker_tag_failure_never_breaks_the_cut(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)

    async def broken_tag(conn, *, intent_id, block_id):
        raise RuntimeError('column "preview_block_ids" does not exist')

    captured, r2 = _run_real_worker(
        monkeypatch,
        storyboard=[{"id": "b1", "source": "ai", "cutType": "horizon", "shot": "full"}],
        tag=broken_tag,
    )

    assert captured.get("failure") is None, captured.get("failure")
    assert captured["finalized"]["cut_assets"], "컷은 그대로 저장·정산된다"
    assert captured["tags"] == []
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "cut preview tag skipped" in logged
    assert r2.puts[0] not in logged                          # 키를 로그에 남기지 않는다
    _assert_no_prefinal_output_reference(captured["events"])
