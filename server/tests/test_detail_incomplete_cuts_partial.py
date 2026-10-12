"""부분 실패(incomplete_cuts) 화면 — 사실대로 말하고, 완성 컷 보관본을 보여 주고, 다시 시도가 이어 쓴다
(2026-09-28, 2026-09-30 발표 전).

오너 결정 B(#420)는 그대로다: 한 컷이라도 실패하면 페이지를 내보내지 않고 예약 전액을 푼다.
바뀌는 것은 셀러가 보는 것뿐이다 —
  ① 문구: "이미지를 모두 만들지 못했어요"(거짓) → "N장 중 M장을 만들지 못했어요. 완성된 N−M장은 보관돼…"
  ② 폴링 응답(result)·error 이벤트: 실패·완성·보관 블록 id(키·주소는 없다)
  ③ 미리보기 라우트: 부분 실패 잡의 완성 컷을 **보관본(체크포인트 완성 컷)** 에서, 주인에게만,
     TTL 안에서만, REAL 이면 라이선스를 지금 다시 확인한 뒤에만 보낸다
  ④ 같은 콘티로 새 잡을 돌리면 보관본을 provider 호출 없이 싣고 실패한 컷만 새로 그린다(#394)
유료 호출 없음 — provider·R2·DB 는 전부 대역이다.
"""

import asyncio
import contextlib
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.routes as routes
from app import detail_cut_preview, facemarket, repo
from app.agents.gemini_image import GeminiError
from app.workers import cut_checkpoints as ckpts
from app.workers import detail_page_job as dpj
from conftest import FakeConn, auth_headers, make_settings


# ── 공통 대역 ────────────────────────────────────────────────────────────────


class _R2:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str, str | None]] = {}
        self.deleted: list[str] = []
        self.gets: list[str] = []

    def put_bytes(self, key, data, mime, cache=None):
        self.objects[key] = (bytes(data), mime, cache)

    def get_bytes(self, key):
        self.gets.append(key)
        if key not in self.objects:
            class _NoSuchKey(Exception):
                response = {"Error": {"Code": "NoSuchKey"},
                            "ResponseMetadata": {"HTTPStatusCode": 404}}
            raise _NoSuchKey()
        return self.objects[key][0]

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    def head(self, key):
        return {"size": 1} if key in self.objects else None

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"


class _IntentDB:
    """ai_output_cleanup_intents 의 체크포인트 표식 대역(test_detail_cut_checkpoints 와 같은 모양)."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.error_jobs: set[str] = set()

    async def create(self, conn, *, job_id, r2_key, ttl_seconds):
        row = {"id": f"intent-{len(self.rows) + 1}", "r2_key": r2_key, "job_id": job_id,
               "live": True}
        self.rows[r2_key] = row
        return row["id"]

    async def list(self, conn, *, user_id, project_id, key_pattern, limit=400):
        prefix = f"users/{user_id}/projects/{project_id}/ai/"
        return [{"id": r["id"], "r2_key": r["r2_key"], "job_id": r["job_id"]}
                for r in reversed(list(self.rows.values()))
                if r["live"] and r["job_id"] in self.error_jobs and r["r2_key"].startswith(prefix)]

    async def expiry(self, conn, r2_keys, *, ttl_seconds, extend_only=False):
        return None

    async def clear(self, conn, intent_id):
        for row in self.rows.values():
            if row["id"] == intent_id:
                row["live"] = False

    def install(self, monkeypatch):
        monkeypatch.setattr(repo, "create_ai_checkpoint_intent", self.create)
        monkeypatch.setattr(repo, "list_detail_cut_checkpoints", self.list)
        monkeypatch.setattr(repo, "set_ai_checkpoint_expiry", self.expiry)
        monkeypatch.setattr(repo, "clear_ai_output_cleanup_intent", self.clear)


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeConn()

        return _cm()


STORYBOARD = [
    {"id": "b1", "source": "ai", "cutType": "styling"},
    {"id": "b2", "source": "ai", "cutType": "product"},
]


def _run_job(monkeypatch, *, job_id, r2, fail_blocks, calls, lease_lost=False, reserved=38):
    """같은 프로젝트·같은 콘티로 상세 잡 1개를 돌린다. fail_blocks 의 컷은 provider 가 거절한다."""
    captured: dict = {"events": []}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [dict(b) for b in STORYBOARD]

    async def fake_prod(conn, pid):
        return {"name": "셔츠", "colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "a2"}]}]}

    async def fake_analysis(conn, pid):
        return {"suggestedName": "미니멀 코튼 셔츠"}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, **_kw):
        calls.append((job_id, cut_spec["id"]))
        if cut_spec["id"] in fail_blocks:
            raise GeminiError("temporary rejection", transient=False)
        return f"IMG-{cut_spec['id']}".encode(), "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["cut_results"] = cut_results
        return [{"id": "x", "kind": "benefit", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured["success"] = kw
        return None if lease_lost else {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_failure(conn, **kw):
        captured["failure"] = kw
        return True

    async def fake_emit(pool, jid, et, payload):
        captured["events"].append((et, payload))

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    # 셀러 상품 사진(입력) — 워커가 R2 에서 읽는다.
    r2.objects.setdefault("k/a1", (b"\x89PNG-product", "image/png", None))
    state = types.SimpleNamespace(
        settings=make_settings(gemini_api_key="x", r2_bucket="b", detail_cut_max_attempts=1,
                               detail_cut_checkpoint_enabled=True),
        pool=_Pool(), r2=r2, gemini=types.SimpleNamespace())
    job = {"id": job_id, "user_id": "u1", "project_id": "p1", "lease_token": "u1:tok",
           "credits_reserved": reserved, "payload": {}, "metadata": {"perCutCost": 19}}
    asyncio.run(dpj.run_detail_page_job(types.SimpleNamespace(state=state), job))
    return captured


# ── ①② 문구·결과·이벤트 + ④ 다시 시도가 보관본을 이어 쓴다 ──────────────────────


def test_partial_success_publishes_and_clears_retained_cuts(monkeypatch):
    """새 작업은 ADR-0017 성공이다. 아래 옛 incomplete_cuts 미리보기 검사는 유지한다."""
    db = _IntentDB()
    db.install(monkeypatch)
    r2 = _R2()
    calls = []
    first = _run_job(monkeypatch, job_id="j1", r2=r2, fail_blocks={"b2"}, calls=calls)
    assert "failure" not in first
    assert first["success"]["charge"] == 19
    partial = first["success"]["metadata"]["partial"]
    assert partial["requestedCuts"] == 2 and partial["completedCuts"] == 1
    assert partial["failedCuts"] == [{"blockId": "b2", "reason": "provider_error", "retryable": True}]
    assert partial["creditsNotCharged"] == 19
    assert not any("/ckpt/" in key for key in r2.objects)
    assert all(not row["live"] for row in db.rows.values())
    second = _run_job(monkeypatch, job_id="j2", r2=r2, fail_blocks=set(), calls=calls)
    assert calls == [("j1", "b1"), ("j1", "b2"), ("j2", "b1"), ("j2", "b2")]
    assert second["success"]["charge"] == 38
    assert "partial" not in second["success"]["metadata"]


def test_all_cuts_failed_keeps_the_old_message_and_payload(monkeypatch):
    db = _IntentDB()
    db.install(monkeypatch)
    captured = _run_job(monkeypatch, job_id="j1", r2=_R2(), fail_blocks={"b1", "b2"}, calls=[])
    failure = captured["failure"]
    assert failure["code"] == "all_cuts_failed"
    assert failure["message"].startswith("이미지를 모두 만들지 못했어요.")
    assert failure["metadata"] == {"error": "all_cuts_failed", "requestedCuts": 2}
    assert "result" not in failure and "job_metadata" not in failure


def test_incomplete_message_counts_only_what_is_really_retained():
    assert dpj._incomplete_cuts_message(20, 1, 19) == (
        "20장 중 1장을 만들지 못했어요. 완성된 19장은 보관돼 있어 다시 시도하면 추가 비용 없이 "
        "그대로 쓰고, 실패한 컷만 새로 만들어요. 이번 시도는 크레딧이 차감되지 않았어요.")
    assert dpj._incomplete_cuts_message(20, 1, 17) == (
        "20장 중 1장을 만들지 못했어요. 완성된 19장 중 17장은 보관돼 있어 다시 시도하면 그대로 쓰고, "
        "나머지는 새로 만들어요. 이번 시도는 크레딧이 차감되지 않았어요.")
    # 보관 창구가 꺼졌거나 저장에 실패했으면 "보관돼 있어요"라고 말하지 않는다
    assert dpj._incomplete_cuts_message(3, 1, 0) == (
        "3장 중 1장을 만들지 못했어요. 이번 시도는 크레딧이 차감되지 않았어요. 다시 시도하면 컷을 새로 만들어요.")


# ── repo: 같은 트랜잭션에서 result·서버 메타·이벤트 ────────────────────────────


class _SqlCursor:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class _SqlConn:
    def __init__(self, rows=None):
        self.log: list = []
        self.rows = list(rows or [])

    def cursor(self):
        return _SqlCursor(self.log, self.rows)


def test_repo_failure_writes_result_metadata_and_event_extras_in_one_transaction(monkeypatch):
    settled = []

    async def fake_settle(conn, **kw):
        settled.append(kw)

    monkeypatch.setattr(repo, "_settle_credits", fake_settle)
    conn = _SqlConn(rows=[{"id": "j1"}])
    ok = asyncio.run(repo.finalize_detail_page_failure(
        conn, job_id="j1", lease_token="t", user_id="u1", project_id="p1", reserved=38,
        settle_key="k", message="2장 중 1장을 만들지 못했어요.",
        metadata={"error": "incomplete_cuts"}, code="incomplete_cuts",
        result={"errorCode": "incomplete_cuts", "completedBlockIds": ["b1"]},
        job_metadata={"retainedCutKeys": {"b1": "users/u1/projects/p1/ai/j1/ckpt/x/final.bin"}},
        event_extra={"completedBlockIds": ["b1"], "failedBlockIds": ["b2"]}))
    assert ok is True
    assert settled[0]["charge"] == 0 and settled[0]["reserved"] == 38      # 전액 해제 그대로
    sqls = [sql for sql, _ in conn.log]
    event = next(params for sql, params in conn.log if "insert into job_events" in sql)
    assert event[1].obj == {"completedBlockIds": ["b1"], "failedBlockIds": ["b2"],
                            "code": "incomplete_cuts", "message": "2장 중 1장을 만들지 못했어요."}
    update = next((sql, params) for sql, params in conn.log
                  if sql.startswith("update jobs set result"))
    assert "metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb" in update[0]
    assert update[1][0].obj["completedBlockIds"] == ["b1"]
    assert "retainedCutKeys" in update[1][1].obj
    assert sqls.index(update[0]) > sqls.index(next(s for s in sqls if "status = 'error'" in s))


def test_repo_failure_without_extras_is_unchanged(monkeypatch):
    async def fake_settle(conn, **kw):
        return None

    monkeypatch.setattr(repo, "_settle_credits", fake_settle)
    conn = _SqlConn(rows=[{"id": "j1"}])
    asyncio.run(repo.finalize_detail_page_failure(
        conn, job_id="j1", lease_token="t", user_id="u1", project_id="p1", reserved=1,
        settle_key="k", message="m", metadata={}, code="generation_failed"))
    assert not any(sql.startswith("update jobs set result") for sql, _ in conn.log)
    event = next(params for sql, params in conn.log if "insert into job_events" in sql)
    assert event[1].obj == {"code": "generation_failed", "message": "m"}


@pytest.mark.parametrize('code', ['generation_failed', 'worker_shutdown', 'incomplete_cuts'])
def test_detail_failure_interrupts_claims_without_changing_credit_release(monkeypatch, code):
    import copy
    initial = {'attempt': 1, 'decision': 'UNKNOWN', 'unknownCause': 'timeout'}
    claimed = {'attempt': 1, 'status': 'CLAIMED', 'initialJudgment': initial}
    complete = {'attempt': 1, 'status': 'COMPLETE', 'judgment': {'decision': 'PASS'}}
    shots = {
        'inflight': {'phase': 'rejudging', 'judgmentRetryLease': 't',
                     'judgmentRetries': [complete, claimed], 'qc': {'judgments': [initial]}},
        'held': {'phase': 'held', 'judgmentRetries': [claimed], 'qc': {'passed': False}},
        'done': {'phase': 'accepted', 'judgmentRetries': [complete], 'qc': {'passed': True}},
        'phase-only': {'phase': 'rejudging', 'judgmentRetryLease': 't'},
    }
    before = copy.deepcopy(shots)
    conn = _SqlConn(rows=[{'id': 'j1'}, {'metadata': {'detailShots': shots, 'other': 'keep'}}])
    settled = []
    async def settle(conn, **kw):
        settled.append(kw)
    monkeypatch.setattr(repo, '_settle_credits', settle)
    assert asyncio.run(repo.finalize_detail_page_failure(
        conn, job_id='j1', lease_token='t', user_id='u1', project_id='p1', reserved=38,
        settle_key='k', message='m', metadata={'error': code}, code=code,
        result={'errorCode': code}, job_metadata={'retainedCutKeys': {'b': 'private'}},
        event_extra={'failedBlockIds': ['b']})) is True
    updates = [params for sql, params in conn.log if sql.startswith('update jobs set metadata = jsonb_set')]
    assert len(updates) == 1, 'Terminal detail retries must be cleaned in the same transaction'
    cleaned = updates[0][0].obj
    assert cleaned['inflight']['phase'] == 'held'
    assert cleaned['inflight']['judgmentRetryLease'] is None
    assert [row['status'] for row in cleaned['inflight']['judgmentRetries']] == ['COMPLETE', 'INTERRUPTED']
    assert cleaned['inflight']['qc']['judgments'] == [initial]
    assert cleaned['inflight']['qc']['judgmentRetries'] == cleaned['inflight']['judgmentRetries']
    assert cleaned['held']['phase'] == 'held'
    assert cleaned['held']['judgmentRetries'][-1]['status'] == 'INTERRUPTED'
    assert cleaned['done'] == before['done']
    assert cleaned['phase-only']['phase'] == 'held'
    assert cleaned['phase-only']['judgmentRetryLease'] is None
    assert settled == [{'user_id': 'u1', 'project_id': 'p1', 'job_id': 'j1', 'reserved': 38,
                        'charge': 0, 'action_key': 'detailPageGenerate.release',
                        'settle_key': 'k', 'metadata': {'error': code}}]
    event = next(params[1].obj for sql, params in conn.log if 'insert into job_events' in sql)
    assert event == {'failedBlockIds': ['b'], 'code': code, 'message': 'm'}


def test_detail_failure_with_lost_lease_does_not_clean_or_release(monkeypatch):
    async def settle(*args, **kwargs):
        pytest.fail('A lost lease cannot release credits')
    monkeypatch.setattr(repo, '_settle_credits', settle)
    conn = _SqlConn()
    assert asyncio.run(repo.finalize_detail_page_failure(
        conn, job_id='j1', lease_token='lost', user_id='u1', project_id='p1', reserved=38,
        settle_key='k', message='m', metadata={})) is False
    assert len(conn.log) == 1


def test_repo_retention_lookup_matches_the_reuse_conditions():
    conn = _SqlConn(rows=[{"ok": 1}])
    assert asyncio.run(repo.is_retained_detail_checkpoint(
        conn, user_id="u1", project_id="p1", r2_key="k")) is True
    sql, params = conn.log[0]
    for clause in ("i.r2_key = %s", "i.status = 'pending'", "i.not_before > now()",
                   "j.user_id = %s", "j.project_id = %s", "j.kind = 'detail_page'",
                   "j.status = 'error'"):
        assert clause in sql, clause
    assert params == ("k", "u1", "p1")
    assert asyncio.run(repo.is_retained_detail_checkpoint(
        _SqlConn(rows=[]), user_id="u1", project_id="p1", r2_key="k")) is False
    assert asyncio.run(repo.is_retained_detail_checkpoint(
        object(), user_id="u1", project_id="p1", r2_key="k")) is False


# ── ③ 미리보기 라우트 — 부분 실패 잡의 보관본 ─────────────────────────────────

USER = "user-1"                     # conftest make_token 기본 sub
OTHER_USER = "user-2"
PROJECT = str(uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
JOB = str(uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"))
PREV_JOB = str(uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"))
MODEL_ID = "22222222-2222-4222-8222-222222222222"
LIC_ID = "11111111-1111-4111-8111-111111111111"
ENROLLMENT_ID = "33333333-3333-4333-8333-333333333333"
CATEGORY = "일반 의류"
DIGEST = "d" * 64
#: 이어 받은 완성 컷은 앞선 실패 잡의 접두사 아래에 있다 — 그 모양도 받아야 한다.
CKPT_KEY = f"users/{USER}/projects/{PROJECT}/ai/{PREV_JOB}/ckpt/{DIGEST}/final.bin"
REAL_PAYLOAD = {"mode": "generate", "modelId": MODEL_ID, "brandUseCategory": CATEGORY,
                "_facemarket": {"modelId": MODEL_ID, "licenseId": LIC_ID}}


def _license_row(status="active"):
    return {
        "id": LIC_ID, "model_id": MODEL_ID, "model_name": "김*늘", "status": status,
        "license_valid_until": datetime.now(timezone.utc) + timedelta(days=30),
        "unit_price": 14900, "vc_id": "vc-1", "allowed_use": [CATEGORY], "forbidden_use": [],
        "model_status": "verified", "assets_status": "ready",
        "current_enrollment_id": ENROLLMENT_ID, "license_enrollment_id": ENROLLMENT_ID,
        "enrollment_status": "passed", "match_policy_version": "policy-v1",
        "has_face_front": True, "has_grid_sedcard": True, "assets_current_evidence": True,
    }


def _blob(image=b"\x89PNG-retained", *, digest=DIGEST, user=USER, project=PROJECT,
          stage="final", mime="image/png"):
    import hashlib
    header = {"v": ckpts.FORMAT_VERSION, "stage": stage, "digest": digest, "userId": user,
              "projectId": project, "jobId": PREV_JOB, "blockId": "b1", "mime": mime,
              "sha256": hashlib.sha256(image).hexdigest(), "meta": {}}
    return ckpts.pack(header, image)


def _failed_job(**overrides):
    job = {"id": JOB, "user_id": USER, "project_id": PROJECT, "kind": "detail_page",
           "status": "error", "payload": dict(REAL_PAYLOAD),
           "result": {"errorCode": "incomplete_cuts", "completedBlockIds": ["b1"],
                      "failedBlockIds": ["b2"]},
           "metadata": {"perCutCost": 19, "retainedCutKeys": {"b1": CKPT_KEY}}}
    job.update(overrides)
    return job


def _setup(monkeypatch, client, *, job=None, retained=True, license_row=None, blob=None):
    calls = {"retained": [], "resolve": [], "preview_key": []}
    job = job if job is not None else _failed_job()

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield FakeConn()

    async def fake_get_job(conn, user_id, job_id):
        if job and job["id"] == job_id and job["user_id"] == user_id:
            return dict(job)
        return None

    async def fake_retained(conn, *, user_id, project_id, r2_key):
        calls["retained"].append((user_id, project_id, r2_key))
        return retained

    async def fake_preview_key(conn, *, job_id, block_id):
        calls["preview_key"].append((job_id, block_id))
        return None

    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        calls["resolve"].append((model_id, license_id))
        return license_row if license_row is not None else _license_row()

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(repo, "get_job", fake_get_job)
    monkeypatch.setattr(repo, "is_retained_detail_checkpoint", fake_retained)
    monkeypatch.setattr(repo, "get_detail_cut_preview_key", fake_preview_key)
    monkeypatch.setattr(facemarket, "resolve_model_license", fake_resolve)
    r2 = _R2()
    r2.objects[CKPT_KEY] = (blob if blob is not None else _blob(), "application/octet-stream", None)
    client.app.state.r2 = r2
    return calls, r2


def _url(block="b1", job=JOB):
    return f"/v1/projects/{PROJECT}/jobs/{job}/cuts/{block}/preview"


def test_owner_gets_the_retained_cut_of_an_incomplete_job(client, make_token, monkeypatch):
    calls, r2 = _setup(monkeypatch, client)

    res = client.get(_url(), headers=auth_headers(make_token))

    assert res.status_code == 200, res.text
    assert res.content == b"\x89PNG-retained"            # 체크포인트 헤더는 벗기고 그림만
    assert res.headers["content-type"].startswith("image/png")
    assert res.headers["cache-control"] == "private, no-store"
    assert calls["retained"] == [(USER, PROJECT, CKPT_KEY)]
    assert calls["resolve"] == [(MODEL_ID, LIC_ID)]      # REAL — 라이선스를 지금 다시 확인
    assert calls["preview_key"] == []                    # 지워진 출력 표식은 보지 않는다
    assert r2.gets == [CKPT_KEY]
    assert "users/" not in res.headers.get("location", "")


def test_other_users_get_404_and_nothing_is_read(client, make_token, monkeypatch):
    calls, r2 = _setup(monkeypatch, client)
    res = client.get(_url(), headers=auth_headers(lambda: make_token(sub=OTHER_USER)))
    assert res.status_code == 404
    assert calls["retained"] == [] and calls["resolve"] == [] and r2.gets == []


def test_revoked_license_is_403_and_the_retained_cut_is_never_read(client, make_token, monkeypatch):
    calls, r2 = _setup(monkeypatch, client, license_row=_license_row(status="revoked"))
    res = client.get(_url(), headers=auth_headers(make_token))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "license_revoked"
    assert r2.gets == []


def test_gone_after_ttl_or_cleanup_is_404(client, make_token, monkeypatch):
    # 24시간이 지났거나, 다시 시도가 성공해 지웠거나, 리클레이머가 치웠다 → 표식이 없다
    calls, r2 = _setup(monkeypatch, client, retained=False)
    res = client.get(_url(), headers=auth_headers(make_token))
    assert res.status_code == 404
    assert calls["resolve"] == [] and r2.gets == []
    # 표식은 남았는데 객체가 이미 없다(파기 스윕 등) → 404(503 아님)
    calls, r2 = _setup(monkeypatch, client)
    r2.objects.clear()
    assert client.get(_url(), headers=auth_headers(make_token)).status_code == 404


def test_only_incomplete_failures_with_a_retained_final_are_served(client, make_token, monkeypatch):
    for job in (
        _failed_job(result={"errorCode": "all_cuts_failed"}),   # 전부 실패 — 보관본 안내 없음
        _failed_job(result=None),                               # 일반 실패
        _failed_job(status="cancelled"),
        _failed_job(status="done"),                             # 성공 — 안정 주소가 이긴다
        _failed_job(kind="editor_image"),
        _failed_job(metadata={"perCutCost": 19}),               # 보관 목록 없음
    ):
        calls, r2 = _setup(monkeypatch, client, job=job)
        assert client.get(_url(), headers=auth_headers(make_token)).status_code == 404, job
        assert r2.gets == []
    calls, r2 = _setup(monkeypatch, client)
    assert client.get(_url(block="b2"), headers=auth_headers(make_token)).status_code == 404
    assert r2.gets == []


def test_only_final_checkpoint_keys_of_this_user_and_project_are_served(client, make_token,
                                                                         monkeypatch):
    for bad in (
        f"users/{USER}/projects/{PROJECT}/ai/{PREV_JOB}/ckpt/{DIGEST}/base.bin",   # 베이스
        f"users/{OTHER_USER}/projects/{PROJECT}/ai/{PREV_JOB}/ckpt/{DIGEST}/final.bin",
        f"users/{USER}/projects/{str(uuid.uuid4())}/ai/{PREV_JOB}/ckpt/{DIGEST}/final.bin",
        f"users/{USER}/projects/{PROJECT}/ai/{JOB}/{uuid.uuid4()}.png",            # 지운 출력
        f"faces/{MODEL_ID}/face_front.png",
    ):
        job = _failed_job(metadata={"retainedCutKeys": {"b1": bad}})
        calls, r2 = _setup(monkeypatch, client, job=job)
        assert client.get(_url(), headers=auth_headers(make_token)).status_code == 404, bad
        assert calls["retained"] == [] and r2.gets == []


def test_a_tampered_or_foreign_checkpoint_header_is_404(client, make_token, monkeypatch):
    for blob in (
        _blob(digest="e" * 64),                  # 키와 다른 digest
        _blob(user=OTHER_USER),                  # 남의 헤더
        _blob(stage="base"),
        _blob(mime="text/html"),
        _blob()[:-1] + b"X",                     # 바이트 해시 불일치
        b"not a checkpoint",
    ):
        _setup(monkeypatch, client, blob=blob)
        res = client.get(_url(), headers=auth_headers(make_token))
        assert res.status_code == 404
        assert "users/" not in res.text


def test_running_jobs_still_use_the_output_path(client, make_token, monkeypatch):
    """생성 중 잡은 예전 그대로 출력 표식만 본다 — 보관본 경로를 타지 않는다."""
    calls, r2 = _setup(monkeypatch, client, job=_failed_job(status="running"))
    assert client.get(_url(), headers=auth_headers(make_token)).status_code == 404
    assert calls["preview_key"] == [(JOB, "b1")] and calls["retained"] == []


def test_is_retained_failure_is_strict():
    assert detail_cut_preview.is_retained_failure(_failed_job())
    assert not detail_cut_preview.is_retained_failure(_failed_job(status="running"))
    assert not detail_cut_preview.is_retained_failure(_failed_job(result={"errorCode": "x"}))
    assert not detail_cut_preview.is_retained_failure(None)
