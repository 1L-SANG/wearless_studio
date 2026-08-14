import contextlib
import threading
import time
from io import BytesIO

import pytest
from PIL import Image
from psycopg import errors

import app.routes as routes


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _asset_ids(count):
    return [f"00000000-0000-0000-0000-{index:012d}" for index in range(1, count + 1)]


def _png(size=500, color=(40, 60, 80)):
    image = Image.new("RGB", (size, size), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _custom_row():
    return {
        "id": "custom_test_item",
        "name": "검정 스커트",
        "clothing_type": "bottom",
        "gender": "unisex",
        "category": "스커트",
        "color_name": "검정",
        "color_group": "black",
        "style_tags": [],
        "fit": "regular",
        "length": "midi",
        "color_brightness": 50,
        "sort_order": 0,
        "is_active": True,
        "is_custom": True,
        "image_asset_id": "00000000-0000-0000-0000-000000009999",
        "thumbnail_asset_id": "00000000-0000-0000-0000-000000000001",
        "image_key": "derived/grid.jpg",
        "thumb_key": "upload/first.png",
        # repo.get_custom_matching_item 이 실제로 돌려주는 키 이름(= 목록 조회와 같은 것).
        # _matching_item_to_api 와 삭제 정리가 같은 키를 읽어야 한다(2026-08-13 리뷰 M7).
        "image_meta": {
            "purpose": "custom_match_grid",
            "sourceAssetIds": _asset_ids(2),
        },
    }


class _Conn:
    def __init__(self, events):
        self.events = events

    async def commit(self):
        self.events.append("commit")


class _R2:
    def __init__(self, source_bytes, *, read_error=False, put_error=False):
        self.source_bytes = source_bytes
        self.read_error = read_error
        self.put_error = put_error
        self.puts = []
        self.deletes = []

    def get_bytes(self, key):
        if self.read_error:
            raise RuntimeError("read failed")
        return self.source_bytes[key]

    def put_bytes(self, key, data, mime, cache=None):
        if self.put_error:
            raise RuntimeError("put failed")
        self.puts.append((key, data, mime))

    def public_url(self, key):
        return f"https://img.example.com/{key}"

    def delete(self, key):
        self.deletes.append(key)


def _patch_post(
    monkeypatch,
    asset_ids,
    *,
    source_bytes=None,
    existing=False,
    unique_violation=False,
    missing_assets=False,
    read_error=False,
    put_error=False,
    delete_wins_precheck=False,
):
    events = []
    state = {"item": _custom_row() if existing else None, "delete_applied": False, "metadata": None, "payload": {
        "matchClothing": [{"id": "curated-1", "selected": True, "selOrder": 1}],
    }}
    source_bytes = source_bytes or {f"source/{asset_id}.png": _png() for asset_id in asset_ids}
    r2 = _R2(source_bytes, read_error=read_error, put_error=put_error)

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(events)

    async def get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def get_custom(conn, user_id, project_id):
        if delete_wins_precheck and "lock" in events and not state["delete_applied"]:
            state["item"] = None
            state["delete_applied"] = True
        return state["item"]

    async def get_uploads(conn, user_id, project_id, requested):
        rows = [
            {
                "id": asset_id,
                "r2_bucket": "wearless",
                "r2_key": f"source/{asset_id}.png",
                "mime_type": "image/png",
                "byte_size": len(source_bytes.get(f"source/{asset_id}.png", b"")),
            }
            for asset_id in requested
        ]
        return rows[:-1] if missing_assets else rows

    async def get_product(conn, project_id):
        return {"clothing_type": "top"}

    async def find_grid(conn, user_id, project_id, checksum):
        return None

    async def lock_project(conn, user_id, project_id):
        events.append("lock")
        return True

    async def set_order(conn, user_id, project_id, requested):
        events.append("source-order")
        assert requested == asset_ids

    async def insert_grid(conn, **kwargs):
        events.append("insert-grid")
        assert kwargs["source_asset_ids"] == asset_ids
        return {"id": kwargs["asset_id"]}

    async def insert_item(conn, **kwargs):
        events.append("insert-item")
        state["metadata"] = kwargs["metadata"]
        if unique_violation:
            raise errors.UniqueViolation("duplicate custom row")
        state["item"] = _custom_row()
        state["item"]["thumbnail_asset_id"] = asset_ids[0]
        state["item"]["thumb_key"] = f"source/{asset_ids[0]}.png"
        return state["item"]["id"]

    async def get_analysis(conn, project_id):
        return state["payload"]

    async def save_analysis(conn, project_id, payload):
        events.append("save-analysis")
        state["payload"] = payload
        return {"project_id": project_id, "payload": payload}

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes, "_r2", lambda request: r2)
    monkeypatch.setattr(routes.repo, "get_project", get_project)
    monkeypatch.setattr(routes.repo, "get_custom_matching_item", get_custom)
    monkeypatch.setattr(routes.repo, "get_uploaded_assets_for_project", get_uploads)
    monkeypatch.setattr(routes.repo, "get_product", get_product)
    monkeypatch.setattr(routes.repo, "find_custom_grid_asset", find_grid)
    monkeypatch.setattr(routes.repo, "lock_custom_match_project", lock_project)
    monkeypatch.setattr(routes.repo, "set_custom_match_source_order", set_order)
    monkeypatch.setattr(routes.repo, "insert_custom_grid_asset", insert_grid)
    monkeypatch.setattr(routes.repo, "insert_custom_matching_item", insert_item)
    monkeypatch.setattr(routes.repo, "get_analysis", get_analysis)
    monkeypatch.setattr(routes.repo, "save_analysis", save_analysis)
    return events, state, r2


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_custom_match_post_accepts_one_to_four_images(
    client, make_token, monkeypatch, count
):
    asset_ids = _asset_ids(count)
    events, state, r2 = _patch_post(monkeypatch, asset_ids)

    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token),
        json={"assetIds": asset_ids},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item"]["isCustom"] is True
    assert body["item"]["isCompatible"] is True
    assert body["item"]["selected"] is False
    assert body["item"]["thumb"].endswith(f"source/{asset_ids[0]}.png")
    assert state["payload"]["matchClothing"][0]["id"] == "custom_test_item"
    assert events.index("lock") < events.index("source-order")
    assert events.index("lock") < events.index("insert-grid")
    assert events.index("insert-grid") < events.index("insert-item")
    assert events.index("insert-item") < events.index("save-analysis") < events.index("commit")
    # D10 — AI 추론 없이 슬롯 기준 결정적 메타데이터
    assert state["metadata"] == {
        "name": "내 하의", "clothingType": "bottom", "category": "커스텀",
        "length": "regular", "colorName": "커스텀", "colorGroup": "gray",
    }
    # D11 — 내 옷은 마네킹 매칭 조정 스텝을 열지 않는다
    assert body["item"]["fitCategory"] is None
    assert len(r2.puts) == 1
    assert r2.puts[0][2] == "image/jpeg"


# 2026-08-13 리뷰 M7 — 등록 응답의 cutoutStatus 가 저장 payload 에 그대로 굳는다.
# 키 이름이 어긋나 있으면 누끼가 곧 돌 상황에서도 항상 "failed" 로 박혀 버렸다.
@pytest.mark.parametrize(("flag", "expected"), [("on", "processing"), ("off", "failed")])
def test_custom_match_post_reports_the_cutout_status_it_is_about_to_start(
    client, make_token, monkeypatch, flag, expected
):
    import dataclasses

    asset_ids = _asset_ids(1)
    _, state, _ = _patch_post(monkeypatch, asset_ids)
    client.app.state.settings = dataclasses.replace(
        client.app.state.settings, matching_cutout=flag
    )

    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token), json={"assetIds": asset_ids},
    )

    assert response.status_code == 200, response.text
    assert response.json()["item"]["cutoutStatus"] == expected
    assert state["payload"]["matchClothing"][0]["cutoutStatus"] == expected


def test_custom_match_post_runs_mandatory_qc_in_parallel_threads(
    client, make_token, monkeypatch
):
    asset_ids = _asset_ids(4)
    _patch_post(monkeypatch, asset_ids)
    lock = threading.Lock()
    active = 0
    max_active = 0

    def slow_qc(raw):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        return routes.input_qc.InputQcResult("pass")

    monkeypatch.setattr(routes.input_qc, "evaluate_input_qc", slow_qc)
    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token), json={"assetIds": asset_ids},
    )

    assert response.status_code == 200, response.text
    assert max_active > 1


@pytest.mark.parametrize("asset_ids", [[], _asset_ids(5), [_asset_ids(1)[0]] * 2])
def test_custom_match_post_rejects_invalid_asset_id_counts(
    client, make_token, asset_ids
):
    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token),
        json={"assetIds": asset_ids},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("failed_gate", ["user", "project", "source", "deleted"])
def test_custom_match_post_hides_any_failed_asset_gate(
    client, make_token, monkeypatch, failed_gate
):
    asset_ids = _asset_ids(2)
    _patch_post(monkeypatch, asset_ids, missing_assets=True)
    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token),
        json={"assetIds": asset_ids},
    )
    assert response.status_code == 404, failed_gate
    assert response.json()["error"]["code"] == "not_found"


def test_custom_match_post_mandatory_qc_is_fail_closed(
    client, make_token, monkeypatch
):
    asset_ids = _asset_ids(1)
    source = {f"source/{asset_ids[0]}.png": _png(399)}
    _patch_post(monkeypatch, asset_ids, source_bytes=source)
    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token), json={"assetIds": asset_ids},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "input_quality"


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"read_error": True}, "custom_match_storage_unavailable"),
        ({"put_error": True}, "custom_match_storage_unavailable"),
    ],
)
def test_custom_match_post_maps_dependency_failures_to_503(
    client, make_token, monkeypatch, options, code
):
    asset_ids = _asset_ids(1)
    _patch_post(monkeypatch, asset_ids, **options)
    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token), json={"assetIds": asset_ids},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == code


def test_custom_match_post_stops_before_compose_when_row_exists(
    client, make_token, monkeypatch
):
    asset_ids = _asset_ids(1)
    events, _, _ = _patch_post(monkeypatch, asset_ids, existing=True)
    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token), json={"assetIds": asset_ids},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "custom_match_item_exists"
    assert "lock" in events
    assert "insert-item" not in events


def test_custom_match_post_continues_when_locked_delete_wins_precheck(
    client, make_token, monkeypatch
):
    asset_ids = _asset_ids(1)
    events, _, _ = _patch_post(
        monkeypatch, asset_ids, existing=True, delete_wins_precheck=True,
    )
    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token), json={"assetIds": asset_ids},
    )
    assert response.status_code == 200, response.text
    assert events.count("lock") == 2
    assert events.index("lock") < events.index("insert-item")


def test_custom_match_post_maps_partial_unique_violation_to_409(
    client, make_token, monkeypatch
):
    asset_ids = _asset_ids(1)
    _patch_post(monkeypatch, asset_ids, unique_violation=True)
    response = client.post(
        "/v1/projects/p1/analysis/custom-match-item",
        headers=_auth(make_token), json={"assetIds": asset_ids},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "custom_match_item_exists"


def test_custom_match_delete_is_locked_atomic_and_idempotent(
    client, make_token, monkeypatch
):
    events = []
    state = {
        "item": _custom_row(),
        "payload": {
            "matchClothing": [
                {"id": "custom_test_item", "selected": True, "selOrder": 1},
                {"id": "curated-2", "selected": True, "selOrder": 2},
            ],
            "fitProfile": {
                "category": "top",
                "matchingFit": {"clothingId": "custom_test_item", "axes": {}},
            },
        },
    }
    r2 = _R2({})

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(events)

    async def lock(conn, user_id, project_id):
        events.append("lock")
        return True

    async def get_custom(conn, user_id, project_id):
        return state["item"]

    async def get_analysis(conn, project_id):
        return state["payload"]

    async def delete_item(conn, user_id, project_id):
        events.append("delete-row")
        state["item"] = None

    async def save_analysis(conn, project_id, payload):
        events.append("save-analysis")
        state["payload"] = payload
        return {"project_id": project_id, "payload": payload}

    async def soft_delete(conn, user_id, project_id, asset_ids):
        events.append("soft-delete-assets")
        return [{"id": "grid", "r2_key": "derived/grid.jpg"}]

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes, "_r2", lambda request: r2)
    monkeypatch.setattr(routes.repo, "lock_custom_match_project", lock)
    monkeypatch.setattr(routes.repo, "get_custom_matching_item", get_custom)
    monkeypatch.setattr(routes.repo, "get_analysis", get_analysis)
    monkeypatch.setattr(routes.repo, "delete_custom_matching_item", delete_item)
    monkeypatch.setattr(routes.repo, "save_analysis", save_analysis)
    monkeypatch.setattr(routes.repo, "soft_delete_unreferenced_custom_assets", soft_delete)

    first = client.delete(
        "/v1/projects/p1/analysis/custom-match-item", headers=_auth(make_token)
    )
    second = client.delete(
        "/v1/projects/p1/analysis/custom-match-item", headers=_auth(make_token)
    )

    assert first.status_code == 204 and second.status_code == 204
    assert events[0:5] == ["lock", "delete-row", "save-analysis", "soft-delete-assets", "commit"]
    assert events[5:] == ["lock", "commit"]
    remaining = state["payload"]["matchClothing"]
    assert remaining == [{"id": "curated-2", "selected": True, "selOrder": 1}]
    assert "matchingFit" not in state["payload"]["fitProfile"]
    assert r2.deletes == ["derived/grid.jpg"]


# 2026-08-13 리뷰 I4 — 누끼 스왑 뒤 삭제하면 원본 업로드·원본 grid·파생 컷이 전부
# 회수돼야 한다. 정리 대상은 오직 "현재 image asset 의 metadata"에서 나온다.
def test_custom_match_delete_reclaims_originals_after_a_cutout_swap(
    client, make_token, monkeypatch
):
    from app.services import matching_cutout

    uploads = _asset_ids(2)
    swapped = _custom_row()
    swapped["image_asset_id"] = "cutout-grid"
    swapped["thumbnail_asset_id"] = "cutout-thumb"
    swapped["image_meta"] = matching_cutout.metadata_for(
        source_hash="fingerprint", source_asset_id="old-grid",
        matching_item_id=swapped["id"], purpose=matching_cutout.GRID_PURPOSE,
        source_asset_ids=[*uploads, "old-grid"],
    )
    seen = {}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn([])

    async def lock(conn, user_id, project_id):
        return True

    async def get_custom(conn, user_id, project_id):
        return swapped

    async def get_analysis(conn, project_id):
        return {"matchClothing": []}

    async def noop(*args, **kwargs):
        return None

    async def save_analysis(conn, project_id, payload):
        return {"project_id": project_id, "payload": payload}

    async def soft_delete(conn, user_id, project_id, asset_ids):
        seen["asset_ids"] = asset_ids
        return []

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes, "_r2", lambda request: _R2({}))
    monkeypatch.setattr(routes.repo, "lock_custom_match_project", lock)
    monkeypatch.setattr(routes.repo, "get_custom_matching_item", get_custom)
    monkeypatch.setattr(routes.repo, "get_analysis", get_analysis)
    monkeypatch.setattr(routes.repo, "delete_custom_matching_item", noop)
    monkeypatch.setattr(routes.repo, "save_analysis", save_analysis)
    monkeypatch.setattr(routes.repo, "soft_delete_unreferenced_custom_assets", soft_delete)

    response = client.delete(
        "/v1/projects/p1/analysis/custom-match-item", headers=_auth(make_token)
    )

    assert response.status_code == 204
    assert set(seen["asset_ids"]) == {
        "cutout-grid", "cutout-thumb", *uploads, "old-grid",
    }


def test_custom_match_delete_collapses_legacy_two_item_selection():
    payload = {
        "matchClothing": [
            {"id": "custom_test_item", "selected": False},
            {"id": "curated-main", "selected": True, "selOrder": 1},
            {"id": "curated-sub", "selected": True, "selOrder": 2},
        ]
    }

    normalized = routes._analysis_without_custom(payload, "custom_test_item")

    assert normalized["matchClothing"] == [
        {"id": "curated-main", "selected": True, "selOrder": 1},
        {"id": "curated-sub", "selected": False},
    ]
