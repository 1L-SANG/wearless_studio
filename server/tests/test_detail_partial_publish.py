"""ADR-0017, 실제 부분 완료와 정산 및 저장 계약을 검증한다."""
import asyncio
import contextlib

import pytest

from app import repo
from app.agents import horizon_background, page_assembler
from app.workers import detail_page_job as dpj
from test_detail_incomplete_cuts_partial import _IntentDB, _R2, _run_job
from test_detail_incomplete_cuts_partial import _SqlConn
from test_similar_cut_repo import Connection
from conftest import auth_headers, patch_route_db
from app import routes
from app.cut_failures import CUT_FAILURE_REASONS, failure_info
import test_detail_incomplete_cuts_partial as incomplete_tests
from test_detail_page import _run_real_detail_job


@pytest.mark.parametrize("requested,completed,expected", [
    (14, 14, 266), (14, 13, 247), (6, 4, 76), (5, 3, 57),
    (3, 3, 95), (3, 2, 76), (1, 1, 95), (14, 0, 0),
])
def test_charge_subtracts_only_missing_units_from_the_reserved_quote(requested, completed, expected):
    calculate = getattr(dpj, "calculate_detail_charge", None)
    assert callable(calculate), "정산 공식을 한 곳에서 검증할 수 있어야 한다"
    assert calculate(completed=completed, reserved=max(requested, 5) * 19,
                     metadata={"aiCount": requested, "minCuts": 5, "perCutCost": 19},
                     fallback_per_cut=99) == expected


def test_partial_success_publishes_completed_cut_and_discards_checkpoints(monkeypatch):
    db = _IntentDB()
    db.install(monkeypatch)
    r2 = _R2()
    first = _run_job(monkeypatch, job_id="j-partial", r2=r2, fail_blocks={"b2"}, calls=[])
    assert "success" in first, "완성한 컷은 실패한 컷 때문에 버리지 않는다"
    assert "failure" not in first
    success = first["success"]
    assert success["charge"] == 19
    assert success["metadata"]["partial"] == {
        "requestedCuts": 2, "completedCuts": 1,
        "failedCuts": [{"blockId": "b2", "reason": "provider_error", "retryable": True}],
        "quoteCredits": 38, "creditsCharged": 19, "creditsNotCharged": 19,
    }
    assert first["cut_results"][0]["blockId"] == "b1"
    assert any("/ai/j-partial/" in key and "/ckpt/" not in key for key in r2.objects)
    assert not any("/ckpt/" in key for key in r2.objects)
    assert all(not row["live"] for row in db.rows.values())


def test_all_failed_still_releases_every_credit(monkeypatch):
    _IntentDB().install(monkeypatch)
    result = _run_job(monkeypatch, job_id="j-all", r2=_R2(), fail_blocks={"b1", "b2"}, calls=[])
    assert "success" not in result
    assert result["failure"]["code"] == "all_cuts_failed"
    assert result["failure"]["reserved"] == 38


def test_partial_lease_loss_retains_checkpoints_and_removes_unpublished_output(monkeypatch):
    db = _IntentDB()
    db.install(monkeypatch)
    r2 = _R2()
    result = _run_job(monkeypatch, job_id="lost-lease", r2=r2, fail_blocks={"b2"}, calls=[], lease_lost=True)
    assert "failure" not in result
    assert any("/ckpt/" in key for key in r2.objects)
    assert any(row["live"] for row in db.rows.values())
    assert not any("/ai/lost-lease/" in key and "/ckpt/" not in key for key in r2.objects)


def test_assembler_marks_only_failed_empty_images_without_changing_geometry():
    storyboard = [{"id": "b1", "source": "ai", "cutType": "product"},
                  {"id": "b2", "source": "ai", "cutType": "product"}]
    cuts = [{"blockId": "b1", "imageUrl": "/v1/assets/a/file"}]
    baseline = page_assembler.assemble(storyboard, cuts, [], {}, False)
    import inspect
    assert "failed_cuts" in inspect.signature(page_assembler.assemble).parameters
    marked = page_assembler.assemble(storyboard, cuts, [], {}, False,
        failed_cuts={"b2": failure_info("no_garment_photo")})
    images = [el for block in marked for el in block["elements"] if el["type"] == "image"]
    assert images[0].get("genFailed") is None
    assert images[1]["genFailed"] is True
    assert images[1]["genFailReason"] == "no_garment_photo"
    assert images[1]["genFailRetryable"] is True
    images[1].pop("genFailed")
    images[1].pop("genFailReason")
    images[1].pop("genFailRetryable")
    assert marked == baseline


def test_retry_restores_worn_cut_side_style_from_saved_storyboard():
    saved = {"id": "side", "source": "ai", "cutType": "styling", "shot": "full",
             "direction": "side", "sideStyle": "threeQuarter", "colorId": "base"}
    editor = [{"elements": [{"type": "image", "src": None, "sourceBlockId": "side"}]}]
    restored = horizon_background.restore_failed_retry({"mode": "new", "retryBlockId": "side",
                                                        "sideStyle": "fake"}, [saved], editor)
    assert restored["sideStyle"] == "threeQuarter"
    assert restored["retryBlockId"] == "side"
    with pytest.raises(ValueError):
        horizon_background.restore_failed_retry({"mode": "new", "retryBlockId": "side"}, [saved], [])


def test_similar_child_cannot_be_retried_as_an_ordinary_new_image():
    saved = {"id": "child", "source": "ai", "cutType": "styling", "similarSourceBlockId": "root"}
    editor = [{"elements": [{"type": "image", "src": None, "sourceBlockId": "child"}]}]
    with pytest.raises(ValueError, match="similar_source_required"):
        horizon_background.restore_failed_retry({"mode": "new", "retryBlockId": "child"}, [saved], editor)


def test_similar_retry_requires_the_saved_original_image_and_an_empty_child_slot():
    storyboard = [{"id": "root", "source": "ai", "cutType": "styling"},
                  {"id": "child", "source": "ai", "cutType": "styling", "similarSourceBlockId": "root"}]
    editor = [{"elements": [{"type": "image", "src": "/v1/assets/root/file", "sourceBlockId": "root"},
                            {"type": "image", "src": None, "sourceBlockId": "child"}]}]
    request = {"mode": "vary", "variationMode": "similar", "retryBlockId": "child", "changes": [],
               "source": {"src": "/v1/assets/root/file", "cutType": "styling"}}
    result = horizon_background.restore_failed_retry(request, storyboard, editor)
    assert result["mode"] == "vary" and result["source"]["src"] == "/v1/assets/root/file"
    with pytest.raises(ValueError, match="similar_source_required"):
        horizon_background.restore_failed_retry({**request, "source": {"src": "/v1/assets/unrelated/file"}}, storyboard, editor)


def test_similar_retry_compares_asset_identity_across_client_host_and_cache_versions():
    root = "11111111-1111-4111-8111-111111111111"
    storyboard = [{"id": "child", "source": "ai", "cutType": "styling", "similarSourceBlockId": "root"}]
    editor = [{"elements": [{"type": "image", "sourceBlockId": "root", "src": f"/v1/assets/{root}/file?e=1"},
                            {"type": "image", "sourceBlockId": "child", "src": None}]}]
    request = {"mode": "vary", "variationMode": "similar", "retryBlockId": "child", "changes": [],
               "source": {"src": f"https://api.wearless.test/v1/assets/{root}/file?e=2"}}
    assert horizon_background.restore_failed_retry(request, storyboard, editor)["mode"] == "vary"


@pytest.mark.parametrize("metadata,reserved,expected", [
    ({}, 95, 38), ({"perCutCost": 19}, 95, 38),
    ({"aiCount": 3, "minCuts": 5, "perCutCost": 19}, 50, 50),
])
def test_legacy_and_reservation_cap(metadata, reserved, expected):
    assert dpj.calculate_detail_charge(completed=2, reserved=reserved, metadata=metadata,
                                      fallback_per_cut=19) == expected


def test_every_published_reason_has_one_of_the_two_categories():
    assert set(CUT_FAILURE_REASONS.values()) == {"retryable", "dependent"}
    for reason, category in CUT_FAILURE_REASONS.items():
        assert failure_info(reason) == {"reason": reason, "retryable": category == "retryable"}
    assert failure_info("confirmed_gpt_unknown_contract") == {"reason": "confirmed_gpt_rejected", "retryable": True}


@pytest.mark.parametrize("partial", [None, {
    "requestedCuts": 3, "completedCuts": 2,
    "failedCuts": [{"blockId": "failed", "reason": "provider_error", "retryable": True}],
    "quoteCredits": 95, "creditsCharged": 76, "creditsNotCharged": 19,
}])
def test_success_persists_same_partial_in_result_done_event_and_credit_metadata(monkeypatch, partial):
    conn = _SqlConn(rows=[{"id": "j"}])
    settled = []
    async def consume(conn, **kwargs):
        settled.append(kwargs)
        return 123
    monkeypatch.setattr(repo, "_consume_buckets", consume)
    metadata = {"partial": partial} if partial else {}
    asyncio.run(repo.finalize_detail_page_success(conn, job_id="j", lease_token="lease",
        user_id="u", project_id="p", editor_blocks=[{"elements": []}], cut_assets=[],
        reserved=95, charge=76, metadata=metadata))
    result = next(params[0].obj for sql, params in conn.log if sql.startswith("update jobs set status = 'done'"))
    event = next(params[1].obj for sql, params in conn.log if sql.startswith("insert into job_events"))
    assert result == event
    assert result["creditsCharged"] == 76 and result["credits"] == 123
    assert settled[0]["reserved"] == 95 and settled[0]["charge"] == 76
    if partial:
        assert result["partial"] == partial
        assert settled[0]["metadata"]["partial"] == partial
    else:
        assert "partial" not in result


def test_existing_active_retry_joins_without_creating_another_job():
    existing = {"id": "active", "status": "running"}
    conn = Connection(None, None, None, existing)
    assert asyncio.run(repo.create_job(conn, user_id="u", project_id="p", kind="editor_image",
        payload={"mode": "new", "retryBlockId": "b"}, idempotency_key="different-key",
        credits_reserved=19, metadata={})) == (existing, False)
    sql, args = conn.calls[0]
    assert "pg_advisory_xact_lock" in sql
    assert args == ("editor-image-request:u:p",)
    lookup, params = next((sql, args) for sql, args in conn.calls if "payload->>'retryBlockId' = %s" in sql)
    assert "payload->>'retryBlockId' = %s" in lookup
    assert "status in ('pending', 'running')" in lookup
    assert params == ("u", "p", "b")
    assert not any("insert into jobs" in sql for sql, _ in conn.calls)


def test_same_idempotency_key_replays_its_original_job_even_after_a_later_retry_starts():
    original = {"id": "original", "status": "done", "kind": "editor_image", "project_id": "p",
                "payload": {"mode": "new", "retryBlockId": "b"}}
    later = {"id": "later", "status": "running"}
    class RequestConnection(Connection):
        async def execute(self, sql, params):
            await super().execute(sql, params)
            self.current = original if "idempotency_key = %s" in sql else later
        async def fetchone(self):
            return self.current
    conn = RequestConnection()
    result = asyncio.run(repo.create_job(conn, user_id="u", project_id="p", kind="editor_image",
        payload={"mode": "new", "retryBlockId": "b"}, idempotency_key="original-key",
        credits_reserved=19, metadata={}))
    assert result == (original, False)


def test_joined_request_key_replays_the_terminal_job_without_another_reservation(client, make_token, monkeypatch):
    class RequestsDB:
        def __init__(self):
            self.jobs = [{"id": "job-a", "status": "running", "idempotency_key": "p1:editor_image:A",
                          "payload": {"id": "cut", "source": "ai", "cutType": "product", "taxonomyVersion": 3,
                                      "contentRole": "productOverview", "sectionRole": "product", "direction": "front",
                                      "shot": "ghost", "sideStyle": None, "faceExposure": None, "matchIds": [],
                                      "outerClosureState": None, "exampleId": None, "exampleSelectionOrigin": None,
                                      "mode": "new", "retryBlockId": "cut"}, "metadata": {}}]
            self.inserts = 0
            self.current = None
        def cursor(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def commit(self): return None
        async def execute(self, sql, params=None):
            self.current = None
            if "idempotency_key = %s" in sql:
                self.current = next((job for job in self.jobs if job["idempotency_key"] == params[0]), None)
            elif sql.lstrip().startswith("select") and "editorRetryRequestKeys" in sql:
                keys = params[-1].obj
                self.current = next((job for job in self.jobs if all(key in job["metadata"].get("editorRetryRequestKeys", []) for key in keys)), None)
            elif "status in ('pending', 'running')" in sql:
                self.current = next((job for job in self.jobs if job["status"] in {"pending", "running"}
                                     and job["payload"]["retryBlockId"] == params[-1]), None)
            elif sql.lstrip().startswith("update jobs") and "editorRetryRequestKeys" in sql:
                job = next(job for job in self.jobs if job["id"] == params[1])
                remembered = job["metadata"].setdefault("editorRetryRequestKeys", [])
                for key in params[0].obj:
                    if key not in remembered: remembered.append(key)
            elif "insert into jobs" in sql:
                self.inserts += 1
                self.current = {"id": f"new-{self.inserts}", "status": "pending", "payload": params[3].obj,
                                "idempotency_key": params[4], "metadata": params[6].obj}
                self.jobs.append(self.current)
        async def fetchone(self): return self.current
    db = RequestsDB()
    reserved = []
    async def project(*args): return {"id": "p1"}
    async def storyboard(*args): return [{"id": "cut", "source": "ai", "cutType": "product"}]
    async def blocks(*args): return [{"elements": [{"type": "image", "sourceBlockId": "cut", "src": None}]}]
    async def analysis(*args): return {}
    async def reserve(conn, user, amount): reserved.append(amount); return 100
    @contextlib.asynccontextmanager
    async def connection(request): yield db
    for name, fn in (("get_project", project), ("get_storyboard", storyboard), ("get_editor_blocks", blocks),
                     ("get_analysis", analysis), ("reserve_credits", reserve)):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(routes, "get_conn", connection)
    request = {"mode": "new", "retryBlockId": "cut"}
    def post(key):
        response = client.post("/v1/projects/p1/editor:generate-image", json=request,
                               headers={**auth_headers(make_token), "Idempotency-Key": key})
        assert response.status_code == 202, response.text
        return response.json()["jobId"]
    assert post("B") == "job-a"
    assert reserved == [] and db.inserts == 0
    db.jobs[0]["status"] = "done"  # B의 접수 응답을 잃은 뒤 원래 작업이 완료됐다.
    assert post("B") == "job-a"
    assert post("A") == "job-a"
    assert db.jobs[0]["idempotency_key"] == "p1:editor_image:A"
    assert reserved == [] and db.inserts == 0
    assert post("C") == "new-1"  # 새 의도는 별도의 새 키다. 과거 작업으로 바꾸지 않는다.
    assert len(reserved) == 1 and db.inserts == 1


@pytest.mark.parametrize("cut_type", ["product", "styling", "horizon"])
@pytest.mark.parametrize("slot_exists,created", [(False, False), (True, True), (True, False)])
def test_retry_route_restores_saved_settings_and_reserves_only_for_a_new_job(
    client, make_token, monkeypatch, cut_type, slot_exists, created,
):
    saved = {"id": "b", "source": "ai", "cutType": cut_type, "colorId": "saved-color",
             "direction": "side", "sideStyle": "threeQuarter", "modelId": "mB"}
    editor = [{"elements": [{"type": "image", "sourceBlockId": "b", "src": None}]}] if slot_exists else []
    seen = {"reserved": []}
    async def project(*args): return {"id": "p"}
    async def storyboard(*args): return [saved]
    async def blocks(*args): return editor
    async def analysis(*args): return {}
    async def create(conn, **kwargs):
        assert slot_exists
        seen.update(kwargs)
        return {"id": "existing" if not created else "new"}, created
    async def reserve(conn, user_id, amount):
        assert slot_exists
        seen["reserved"].append(amount)
        return 99
    for name, fn in (("get_project", project), ("get_storyboard", storyboard), ("get_editor_blocks", blocks),
                     ("get_analysis", analysis), ("create_job", create), ("reserve_credits", reserve)):
        monkeypatch.setattr(repo, name, fn)
    patch_route_db(monkeypatch, routes)
    response = client.post("/v1/projects/p/editor:generate-image", json={"mode": "new", "retryBlockId": "b",
                          "cutType": "fake", "colorId": "fake", "sideStyle": "fake"}, headers=auth_headers(make_token))
    assert response.status_code == (202 if slot_exists else 400), response.text
    if slot_exists:
        assert seen["payload"]["cutType"] == cut_type
        assert seen["payload"]["colorId"] == "saved-color"
        if cut_type in {"styling", "horizon"}:
            assert seen["payload"]["sideStyle"] == "threeQuarter"
        assert len(seen["reserved"]) == int(created)


@pytest.mark.parametrize("failed,completed,reason", [
    ({"b2"}, 2, "provider_error"), ({"b1"}, 1, "duplicate_cut_source_failed"),
])
def test_duplicate_slots_count_as_page_images_and_never_as_an_extra_credit_unit(monkeypatch, failed, completed, reason):
    monkeypatch.setattr(incomplete_tests, "STORYBOARD", [
        {"id": "b1", "source": "ai", "cutType": "styling"},
        {"id": "clone", "source": "ai", "cutType": "styling"},
        {"id": "b2", "source": "ai", "cutType": "product"},
    ])
    _IntentDB().install(monkeypatch)
    calls = []
    result = _run_job(monkeypatch, job_id="duplicates", r2=_R2(), fail_blocks=failed, calls=calls)
    success = result["success"]
    partial = success["metadata"]["partial"]
    assert partial["requestedCuts"] == 3 and partial["completedCuts"] == completed
    assert len(success["cut_assets"]) == 1
    assert success["charge"] == 19 and partial["creditsNotCharged"] == 19
    assert {block_id for _, block_id in calls} == {"b1", "b2"}
    assert partial["failedCuts"][-1]["reason"] == reason


def test_failed_similar_source_leaves_its_child_empty_and_excludes_both_units(monkeypatch):
    monkeypatch.setattr(incomplete_tests, "STORYBOARD", [
        {"id": "b1", "source": "ai", "cutType": "styling"},
        {"id": "child", "source": "ai", "cutType": "styling", "similarSourceBlockId": "b1"},
        {"id": "b2", "source": "ai", "cutType": "product"},
    ])
    _IntentDB().install(monkeypatch)
    calls = []
    result = _run_job(monkeypatch, job_id="dependency", r2=_R2(), fail_blocks={"b1"}, calls=calls, reserved=57)
    success = result["success"]
    partial = success["metadata"]["partial"]
    assert partial["requestedCuts"] == 3 and partial["completedCuts"] == 1
    assert success["charge"] == 19 and partial["creditsNotCharged"] == 38
    assert partial["failedCuts"] == [
        {"blockId": "b1", "reason": "provider_error", "retryable": True},
        {"blockId": "child", "reason": "similar_cut_source_failed", "retryable": False},
    ]
    assert {block_id for _, block_id in calls} == {"b1", "b2"}
    assert [cut["blockId"] for cut in result["cut_results"]] == ["b2"]


def test_real_partial_without_a_completed_real_cut_never_settles_a_license(monkeypatch):
    result = _run_real_detail_job(monkeypatch, [
        {"id": "real", "source": "ai", "cutType": "styling", "direction": "front", "shot": "full"},
        {"id": "product", "source": "ai", "cutType": "product", "direction": "front", "shot": "ghost"},
    ], credits_reserved=38, failing_ids={"real"})
    assert "failure" not in result
    assert result["finalize"]["charge"] == 19
    assert result["settlements"] == []
    assert all(not asset["metadata"]["facemarket_real_derived"] for asset in result["finalize"]["cut_assets"])


def test_real_notice_counts_completed_duplicate_images(monkeypatch):
    result = _run_real_detail_job(monkeypatch, [
        {"id": "real", "source": "ai", "cutType": "styling", "direction": "front", "shot": "full"},
        {"id": "clone", "source": "ai", "cutType": "styling", "direction": "front", "shot": "full"},
        {"id": "failed", "source": "ai", "cutType": "product", "direction": "front", "shot": "ghost"},
    ], credits_reserved=2, failing_ids={"failed"})
    assert result["assembly"]["license_notice"]["totalCuts"] == 2
    assert result["assembly"]["license_notice"]["faceCuts"] == 2
    assert len(result["finalize"]["cut_assets"]) == 1
    assert len(result["settlements"]) == 1
