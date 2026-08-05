from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import psycopg
import pytest

import scripts.export_runtime_ab_project_inputs as exporter
from scripts.export_runtime_ab_project_inputs import (
    _qa_user_id,
    anonymized_project_tag,
    choose_candidates,
    normalize_clothing_type,
    parse_selected_mannequin,
    postgrest_uuid_in,
    product_image_pairs,
    selected_virtual_model,
)


def test_normalize_clothing_type_accepts_only_supported_aliases():
    assert normalize_clothing_type("상의") == "top"
    assert normalize_clothing_type(" BOTTOM ") == "bottom"
    assert normalize_clothing_type("아우터") == "outer"
    assert normalize_clothing_type("dress") == "dress"
    assert normalize_clothing_type("accessory") is None


def test_selected_virtual_model_excludes_real_or_missing_identity():
    assert selected_virtual_model({"selectedModelId": "mA"}) == "mA"
    assert selected_virtual_model({"selected_model_id": "mC"}) == "mC"
    assert selected_virtual_model({"selectedModelId": "mD"}) == "mD"
    assert selected_virtual_model({"selectedModelId": "real-model-uuid"}) is None
    assert selected_virtual_model({"selectedModelId": ["mA"]}) is None
    assert selected_virtual_model({}) is None


def test_product_image_pairs_uses_base_colour_and_service_slot_order():
    colors = [
        {
            "id": "non-base",
            "images": [{"id": "wrong-front", "slot": "Front"}],
        },
        {
            "id": "base",
            "isBase": True,
            "images": [
                {"id": "detail", "slot": "Detail"},
                {"id": "back", "slot": "Back"},
                {"id": "front", "slot": "Front"},
            ],
        },
    ]
    assert product_image_pairs(colors) == [
        ("Front", "front"),
        ("Back", "back"),
        ("Detail", "detail"),
    ]


def test_choose_candidates_prioritizes_structural_signal_then_recency():
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new = datetime(2026, 2, 1, tzinfo=timezone.utc)
    candidates = [
        {
            "normalized_clothing_type": "top",
            "diversity_signals": [],
            "product_assets": [{"slot": "Front"}, {"slot": "Back"}],
            "updated_at": new,
            "marker": "new-plain",
        },
        {
            "normalized_clothing_type": "top",
            "diversity_signals": ["stripe", "logo"],
            "product_assets": [{"slot": "Front"}],
            "updated_at": old,
            "marker": "old-structured",
        },
        {
            "normalized_clothing_type": "bottom",
            "diversity_signals": ["denim"],
            "product_assets": [{"slot": "Front"}],
            "updated_at": new,
            "marker": "bottom",
        },
    ]
    chosen = choose_candidates(candidates)
    assert chosen["top"]["marker"] == "old-structured"
    assert chosen["bottom"]["marker"] == "bottom"


def test_project_tag_is_stable_and_does_not_embed_raw_identifier():
    raw = "11111111-1111-1111-1111-111111111111"
    tag = anonymized_project_tag(raw)
    assert tag == anonymized_project_tag(raw)
    assert tag.startswith("qa-")
    assert raw not in tag


def test_selected_mannequin_parser_is_strict():
    assert parse_selected_mannequin("A-1") == ("A", 1)
    assert parse_selected_mannequin("B-20") == ("B", 20)
    assert parse_selected_mannequin("C-1") is None
    assert parse_selected_mannequin("A-0") is None
    assert parse_selected_mannequin("A-1;drop table") is None


def test_postgrest_uuid_filter_validates_and_canonicalizes():
    value = "11111111-1111-1111-1111-111111111111"
    assert postgrest_uuid_in([value]) == f"in.({value})"

    with pytest.raises(RuntimeError, match="invalid identifier"):
        postgrest_uuid_in(["not-a-uuid"])


def test_admin_lookup_keeps_only_exact_fixed_qa_email():
    qa_id = "11111111-1111-1111-1111-111111111111"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/admin/users"
        assert request.url.params["email"] == "qa-smoke@wearless.kr"
        return httpx.Response(
            200,
            json={
                "users": [
                    {"id": "22222222-2222-2222-2222-222222222222", "email": "other@example.com"},
                    {"id": qa_id, "email": "qa-smoke@wearless.kr"},
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert _qa_user_id(
            client,
            base_url="https://example.supabase.co",
            headers={"Authorization": "Bearer redacted"},
        ) == qa_id


def test_loader_falls_back_to_rest_only_for_postgres_connection_failure(monkeypatch):
    settings = SimpleNamespace(database_url="postgresql://offline")

    def offline(_settings):
        raise psycopg.OperationalError("offline")

    sentinel = ([{"safe": True}], {"source_rest": 1})
    monkeypatch.setattr(exporter, "_load_candidates_db", offline)
    monkeypatch.setattr(exporter, "_load_candidates_rest", lambda _settings: sentinel)
    assert exporter._load_candidates(settings) == sentinel


def test_loader_does_not_hide_non_connection_database_bugs(monkeypatch):
    settings = SimpleNamespace(database_url="postgresql://live")

    def broken(_settings):
        raise RuntimeError("query bug")

    monkeypatch.setattr(exporter, "_load_candidates_db", broken)
    monkeypatch.setattr(
        exporter,
        "_load_candidates_rest",
        lambda _settings: pytest.fail("REST fallback must not mask a live DB query bug"),
    )
    with pytest.raises(RuntimeError, match="query bug"):
        exporter._load_candidates(settings)
