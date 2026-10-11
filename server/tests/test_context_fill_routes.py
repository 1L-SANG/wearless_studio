import asyncio
import contextlib
import copy

import pytest

from app import repo, routes
from conftest import auth_headers, patch_route_db


ASSET = "11111111-1111-4111-8111-111111111111"
SLOT = {"blockId": "frame", "elementId": "empty"}
EMPTY = {"id": "frame", "kind": "styling", "contentRole": "custom", "elements": [
    {"id": "empty", "type": "image", "src": None, "x": 500, "y": 0, "w": 500, "h": 600},
]}
PRODUCT = {"clothingType": "top", "colors": [{"id": "col1", "name": "화이트", "images": [
    {"id": "front", "slot": "Front", "assetId": ASSET},
]}]}


def setup_fill(monkeypatch, *, blocks=None, storyboard=None, product=None, active=False,
               previous=None, existing=None):
    seen = {"reserved": [], "created": [], "existing": existing, "lookups": 0}
    patch_route_db(monkeypatch, routes)

    async def project(*args): return {"id": "p1"}
    async def editor(*args): return copy.deepcopy([EMPTY] if blocks is None else blocks)
    async def board(*args): return storyboard or []
    async def garment(*args): return copy.deepcopy(PRODUCT if product is None else product)
    async def analysis(*args): return {"selectedModelId": "mB", "gender": "female"}
    async def page_active(*args): return active
    async def completed(*args): return previous or []
    async def find(*args, **kwargs):
        seen["lookups"] += 1
        return seen["existing"]
    async def recipes(*args): return {}
    async def asset(*args): return {"id": ASSET, "source": "ai", "metadata": {"cut_type": "styling"}}
    async def provenance(*args): return None
    async def create(conn, **kwargs):
        seen["created"].append(kwargs)
        if seen["existing"] is not None:
            return seen["existing"], False
        return {"id": "new-job", "payload": kwargs["payload"]}, True
    async def reserve(conn, user, amount):
        seen["reserved"].append(amount)
        return 100
    for name, fn in (("get_project", project), ("get_editor_blocks", editor), ("get_storyboard", board),
                     ("get_product", garment), ("get_analysis", analysis), ("has_active_detail_page_job", page_active),
                     ("list_completed_context_fill_jobs", completed), ("find_context_fill_job", find),
                     ("get_project_asset_for_user", asset), ("get_asset_facemarket_provenance", provenance),
                     ("create_job", create), ("reserve_credits", reserve)):
        monkeypatch.setattr(repo, name, fn, raising=False)
    # 공용 자산 읽기는 저장소 경계에서 대체하고 판단 함수는 실제로 실행한다.
    from app.agents import asset_recipe
    monkeypatch.setattr(asset_recipe, "resolve_asset_recipes", recipes)
    return seen


def post_fill(client, make_token, *, slot=SLOT, key="request-key", extra=None):
    headers = auth_headers(make_token)
    if key is not None: headers["Idempotency-Key"] = key
    return client.post("/v1/projects/p1/editor:generate-image", headers=headers,
                       json={"mode": "fill", "slot": slot, **(extra or {})})


@pytest.mark.parametrize("slot,key", [(None, "k"), ({}, "k"), ([], "k"),
    ({"blockId": "frame", "elementId": 1}, "k"), (SLOT, None), (SLOT, " ")])
def test_invalid_slot_or_missing_key_never_creates_or_reserves(client, make_token, monkeypatch, slot, key):
    seen = setup_fill(monkeypatch)
    response = post_fill(client, make_token, slot=slot, key=key)
    assert response.status_code == 400, response.text
    assert response.json()["error"] == {"code": "fill_slot_invalid", "message": "이 칸을 찾지 못했어요."}
    assert seen["created"] == [] and seen["reserved"] == []


@pytest.mark.parametrize("blocks,storyboard,product,active,status,code,message", [
    ([], [], PRODUCT, False, 409, "fill_slot_not_found", "저장 중이에요. 잠시 뒤 다시 눌러 주세요."),
    ([{**EMPTY, "elements": [{**EMPTY["elements"][0], "src": f"/v1/assets/{ASSET}/file"}]}], [], PRODUCT, False,
     409, "fill_slot_not_empty", "이미 사진이 들어 있는 칸이에요."),
    ([{**EMPTY, "elements": [{**EMPTY["elements"][0], "genFailed": True}]}], [], PRODUCT, False,
     400, "fill_slot_planned", "이 칸은 원래 콘티대로 다시 만들어요."),
    ([{**EMPTY, "kind": "info"}], [], PRODUCT, False, 400, "fill_slot_unsupported", "이 칸은 AI로 채울 수 없어요."),
    ([EMPTY], [], PRODUCT, True, 409, "page_generation_active", "상세페이지를 만드는 중이에요. 끝나면 눌러 주세요."),
    ([EMPTY], [], {"clothingType": "top", "colors": []}, False, 409, "fill_unavailable", "이 칸에 만들 수 있는 컷이 없어요."),
])
def test_rejections_have_exact_message_and_no_job_or_reservation(
    client, make_token, monkeypatch, blocks, storyboard, product, active, status, code, message,
):
    seen = setup_fill(monkeypatch, blocks=blocks, storyboard=storyboard, product=product, active=active)
    response = post_fill(client, make_token)
    assert response.status_code == status, response.text
    assert response.json()["error"] == {"code": code, "message": message}
    assert seen["created"] == [] and seen["reserved"] == []


def test_fill_uses_saved_context_only_and_keeps_wearing_model(client, make_token, monkeypatch):
    seen = setup_fill(monkeypatch)
    response = post_fill(client, make_token, extra={"cutType": "product", "colorId": "fake", "modelId": "fake",
        "exampleId": "fake", "retryBlockId": "fake", "refScope": "bg", "_contextFill": {"rule": "fake"}})
    assert response.status_code == 202, response.text
    payload = seen["created"][0]["payload"]
    assert payload["mode"] == "new" and payload["cutType"] == "styling"
    assert payload["modelId"] == "mB" and payload["colorId"] == "col1"
    assert payload["exampleId"] != "fake" and "retryBlockId" not in payload
    assert payload["_contextFill"]["slotKey"] == "frame:empty"
    assert payload["_contextFill"]["attempt"] == 0
    assert seen["created"][0]["metadata"]["contextFill"] == payload["_contextFill"]
    assert seen["reserved"] == [19]
    assert response.json()["contextFill"] == {"slot": SLOT, "label": payload["_contextFill"]["label"],
        "path": "new", "cutType": "styling", "colorId": "col1"}


@pytest.mark.parametrize("status", ["running", "done"])
def test_join_and_key_replay_return_original_decision_without_rejudging(
    client, make_token, monkeypatch, status,
):
    record = {"version": 1, "slot": SLOT, "slotKey": "frame:empty", "purpose": "coordination",
              "rule": "section_new", "label": "스타일링 · 풀샷", "path": "new", "colorId": "col1", "attempt": 0}
    original = {"id": "original", "status": status, "payload": {"mode": "new", "cutType": "styling", "_contextFill": record}}
    seen = setup_fill(monkeypatch, existing=original, blocks=[], product={})
    response = post_fill(client, make_token)
    assert response.status_code == 202, response.text
    assert response.json() == {"jobId": "original", "contextFill": {"slot": SLOT,
        "label": "스타일링 · 풀샷", "path": "new", "cutType": "styling", "colorId": "col1"}}
    assert seen["reserved"] == []


def test_fill_similar_payload_passes_existing_vary_validation(client, make_token, monkeypatch):
    blocks = [{**EMPTY, "elements": [{"id": "anchor", "type": "image", "src": f"/v1/assets/{ASSET}/file",
               "x": 0, "y": 0, "w": 500, "h": 600}, *EMPTY["elements"]]}]
    seen = setup_fill(monkeypatch, blocks=blocks)
    from app.agents import asset_recipe
    async def recipes(*args):
        return {ASSET: {"origin": "editor_new", "recipe": {"sectionRole": "styling", "cutType": "styling",
            "colorId": "col1", "shot": "full"}, "cutType": "styling", "colorId": "col1", "upload": False,
            "realDerived": False, "sourceAssetId": None, "recipeFrom": "self"}}
    monkeypatch.setattr(asset_recipe, "resolve_asset_recipes", recipes)
    response = post_fill(client, make_token)
    assert response.status_code == 202, response.text
    payload = seen["created"][0]["payload"]
    assert payload["mode"] == "vary" and payload["variationMode"] == "similar" and payload["changes"] == []
    assert payload["source"] == {"src": f"/v1/assets/{ASSET}/file", "cutType": "styling"}
    assert payload["_similarCut"] == {"version": 1, "rootAssetId": ASSET, "sourceAssetId": ASSET}
    assert response.json()["contextFill"]["path"] == "similar"


class JobsDB:
    """저장소 SQL 경계의 작은 메모리 대역. 판단과 create_job은 실제 코드다."""
    def __init__(self, jobs):
        self.jobs = copy.deepcopy(jobs)
        self.current = None
        self.calls = []
        self.inserts = 0
    def cursor(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self.current = None
        if "idempotency_key = %s" in sql and "editorFillRequestKeys" in sql:
            self.current = next((j for j in self.jobs if j.get("idempotency_key") == params[2]
                                or all(k in j["metadata"].get("editorFillRequestKeys", []) for k in params[-1].obj)), None)
        elif "idempotency_key = %s" in sql:
            self.current = next((j for j in self.jobs if j.get("idempotency_key") == params[0]), None)
        elif sql.lstrip().startswith("select") and "editorFillRequestKeys" in sql:
            self.current = next((j for j in self.jobs if all(k in j["metadata"].get("editorFillRequestKeys", [])
                                for k in params[-1].obj)), None)
        elif "payload->'_contextFill'->>'slotKey' = %s" in sql:
            self.current = next((j for j in self.jobs if j["status"] in {"pending", "running"}
                                and j["payload"].get("_contextFill", {}).get("slotKey") == params[-1]), None)
        elif sql.lstrip().startswith("update jobs") and "editorFillRequestKeys" in sql:
            job = next(j for j in self.jobs if j["id"] == params[1])
            keys = job["metadata"].setdefault("editorFillRequestKeys", [])
            keys.extend(k for k in params[0].obj if k not in keys)
        elif "insert into jobs" in sql:
            self.inserts += 1
            self.current = {"id": "new", "status": "pending", "payload": params[3].obj,
                            "idempotency_key": params[4], "metadata": params[6].obj}
            self.jobs.append(self.current)
    async def fetchone(self): return self.current
    async def commit(self): return None


def test_repository_joins_slot_and_remembers_join_key_after_completion():
    original = {"id": "original", "status": "running", "idempotency_key": "key-a", "metadata": {},
                "payload": {"_contextFill": {"slotKey": "frame:empty", "label": "고스트샷 · 앞면"}}}
    db = JobsDB([original])
    async def create(key, slot="frame:empty"):
        return await repo.create_job(db, user_id="u", project_id="p", kind="editor_image",
            payload={"_contextFill": {"slotKey": slot}}, idempotency_key=key, credits_reserved=19,
            metadata={})
    row, created = asyncio.run(create("key-b"))
    assert not created and row["id"] == "original" and db.inserts == 0
    assert db.jobs[0]["metadata"]["editorFillRequestKeys"] == ["key-b"]
    assert db.jobs[0]["idempotency_key"] == "key-a"
    db.jobs[0]["status"] = "done"
    row, created = asyncio.run(create("key-b"))
    assert not created and row["id"] == "original" and db.inserts == 0
    row, created = asyncio.run(create("key-c"))
    assert created and row["id"] == "new" and db.inserts == 1


def test_different_slots_create_independent_jobs():
    db = JobsDB([{"id": "original", "status": "running", "idempotency_key": "key-a", "metadata": {},
                  "payload": {"_contextFill": {"slotKey": "frame:first"}}}])
    row, created = asyncio.run(repo.create_job(db, user_id="u", project_id="p", kind="editor_image",
        payload={"_contextFill": {"slotKey": "frame:second"}}, idempotency_key="key-b", credits_reserved=19,
        metadata={}))
    assert created and row["id"] == "new"


def test_success_result_and_done_event_include_slot_receipt(monkeypatch):
    from test_asset_privacy_metadata import _Conn
    conn = _Conn()
    charged = []
    async def consume(conn, **kwargs):
        charged.append(kwargs)
        return 81
    monkeypatch.setattr(repo, "_consume_buckets", consume)
    record = {"version": 1, "slot": SLOT, "slotKey": "frame:empty", "purpose": "productOverview",
              "rule": "ghost_order", "label": "고스트샷 · 앞면", "path": "new", "colorId": "col1"}
    result = asyncio.run(repo.finalize_editor_image_success(conn, job_id="j", lease_token="lease", user_id="u",
        project_id="p", image={"asset_id": ASSET, "bucket": "r2", "key": "output.png", "mime": "image/png",
                               "metadata": {"context_fill": record}}, group="col1", cut_type="product",
        reserved=19, charge=19, metadata={"contextFill": {**record, "attempt": 0}}))
    receipt = {"slot": SLOT, "label": "고스트샷 · 앞면", "colorId": "col1"}
    assert result["contextFill"] == receipt
    envelope = next(params[0].obj for sql, params in conn.statements if sql.startswith("update jobs set status = 'done'"))
    event = next(params[1].obj for sql, params in conn.statements if sql.startswith("insert into job_events"))
    assert envelope == event and envelope["data"]["contextFill"] == receipt
    assert envelope["credits"] == 81 and envelope["creditsCharged"] == 19
    assert charged[0]["reserved"] == 19 and charged[0]["charge"] == 19


def test_fill_join_completion_race_keeps_original_job_and_no_new_reservation(client, make_token, monkeypatch):
    create_job, find_job = repo.create_job, repo.find_context_fill_job
    record = {"version": 1, "slot": SLOT, "slotKey": "frame:empty", "purpose": "coordination",
              "rule": "section_new", "label": "스타일링 · 풀샷", "path": "new", "colorId": "col1", "attempt": 0}
    db = JobsDB([{"id": "original", "status": "running", "idempotency_key": "p1:editor_image:A", "metadata": {},
                  "payload": {"mode": "new", "cutType": "styling", "_contextFill": record}}])
    seen = setup_fill(monkeypatch)
    @contextlib.asynccontextmanager
    async def connection(request): yield db
    async def completes_after_lookup(*args):
        original = await find_job(*args)
        # 접수 연결은 작업을 찾았지만 응답 전에 작업자가 성공 종결했다.
        db.jobs[0]["status"] = "done"
        return original
    monkeypatch.setattr(routes, "get_conn", connection)
    monkeypatch.setattr(repo, "find_context_fill_job", completes_after_lookup)
    monkeypatch.setattr(repo, "create_job", create_job)
    response = post_fill(client, make_token, key="B")
    assert response.status_code == 202, response.text
    assert response.json()["jobId"] == "original"
    assert db.inserts == 0 and seen["reserved"] == []
    assert db.jobs[0]["metadata"]["editorFillRequestKeys"] == ["p1:editor_image:B"]
    # 완료 뒤 같은 키를 재전송해도 저장된 칸 상태와 관계없이 같은 작업이다.
    response = post_fill(client, make_token, key="B")
    assert response.json()["jobId"] == "original" and db.inserts == 0
