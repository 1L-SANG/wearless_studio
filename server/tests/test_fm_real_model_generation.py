from types import SimpleNamespace

import pytest

from app import routes
from app.config import load_settings
from conftest import make_settings, patch_route_db


REAL_MODEL = "11111111-1111-1111-1111-111111111111"
MESSAGE = "실제 모델 생성은 지금 제공하지 않아요. AI 모델로 바꿔 주세요."


def test_real_model_generation_defaults_on(monkeypatch):
    monkeypatch.delenv("FM_REAL_MODEL_GENERATION", raising=False)
    assert getattr(make_settings(), "fm_real_model_generation", None) == "on"
    assert getattr(load_settings(), "fm_real_model_generation", None) == "on"


@pytest.mark.parametrize("value", ["on", "off"])
def test_real_model_generation_reads_env(monkeypatch, value):
    monkeypatch.setenv("FM_REAL_MODEL_GENERATION", value)
    assert getattr(load_settings(), "fm_real_model_generation", None) == value


@pytest.fixture
def generation_boundary(client, monkeypatch):
    client.app.state.settings = SimpleNamespace(**{
        **vars(client.app.state.settings),
        "facemarket_enabled": True,
        "fm_real_model_generation": "off",
    })
    seen = {"jobs": [], "reservations": [], "demand": []}
    state = {"model": REAL_MODEL, "cached": []}

    async def project(conn, uid, pid):
        return {"id": pid}

    async def analysis(conn, pid):
        return {"selectedModelId": state["model"], "brandUseCategory": "일반 의류"}

    async def storyboard(conn, pid):
        return [{"id": "b1", "source": "ai", "sectionRole": "studio", "cutType": "horizon", "shot": "full", "direction": "front"}]

    async def cached(conn, pid):
        return state["cached"]

    async def product(conn, pid):
        return {"clothingType": "top"}

    async def job(conn, **kwargs):
        seen["jobs"].append(kwargs)
        return {"id": "job1"}, True

    async def reserve(conn, uid, cost):
        seen["reservations"].append(cost)
        return 100

    async def demand(*args, **kwargs):
        seen["demand"].append(kwargs)

    async def license(*args, **kwargs):
        return None

    for name, fn in {"get_project": project, "get_analysis": analysis, "get_storyboard": storyboard,
                     "get_editor_blocks": cached, "get_product": product, "create_job": job,
                     "reserve_credits": reserve}.items():
        monkeypatch.setattr(routes.repo, name, fn)
    monkeypatch.setattr(routes.facemarket, "note_holder_demand", demand)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", license)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", license)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", lambda *args, **kwargs: None)
    patch_route_db(monkeypatch, routes)
    return seen, state


@pytest.mark.parametrize("cut_type", ["horizon", "styling"])
def test_off_refuses_editor_real_model_without_job_charge_or_demand(client, make_token, generation_boundary, cut_type):
    seen, _ = generation_boundary
    response = client.post("/v1/projects/p1/editor:generate-image",
                           headers={"Authorization": f"Bearer {make_token()}"},
                           json={"mode": "new", "cutType": cut_type, "modelId": REAL_MODEL})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "real_model_unavailable"
    assert response.json()["error"]["message"] == MESSAGE
    assert seen == {"jobs": [], "reservations": [], "demand": []}


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("fm_enabled", [False, True])
def test_off_refuses_detail_before_cache_job_charge_or_demand(client, make_token, generation_boundary, cached, fm_enabled):
    seen, state = generation_boundary
    client.app.state.settings.facemarket_enabled = fm_enabled
    state["cached"] = [{"id": "existing", "source": "ai"}] if cached else []
    response = client.post("/v1/projects/p1/detail-page:generate", headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "real_model_unavailable"
    assert response.json()["error"]["message"] == MESSAGE
    assert seen == {"jobs": [], "reservations": [], "demand": []}


@pytest.mark.parametrize("endpoint", ["editor:generate-image", "detail-page:generate"])
def test_off_keeps_virtual_model_generation(client, make_token, generation_boundary, endpoint):
    seen, state = generation_boundary
    state["model"] = "mA"
    options = {"json": {"mode": "new", "cutType": "horizon", "modelId": "mA"}} if endpoint.startswith("editor") else {}
    response = client.post(f"/v1/projects/p1/{endpoint}", headers={"Authorization": f"Bearer {make_token()}"}, **options)
    assert response.status_code == 202, response.text
    assert response.json() == {"jobId": "job1"}
    assert len(seen["jobs"]) == 1
    assert seen["jobs"][0]["payload"]["modelId"] == "mA"
    assert seen["reservations"] == [19 if endpoint.startswith("editor") else 95]
    assert seen["demand"] == []
