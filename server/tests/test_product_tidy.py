"""Offline product display contract. All storage/provider boundaries stay in memory."""
import asyncio
import copy
import hashlib
import io
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PIL import Image
from psycopg import OperationalError, errors

from app import image_usage, repo
from app.config import load_settings
from app.services import canonical_reference, fal_cutout, segmentation_types, sam_retry
from app.workers import sam_preprocess_job as worker
from conftest import make_settings, patch_route_db


def png(mode="RGBA", size=(32, 48)):
    image = Image.new(mode, size, (70, 90, 120, 255) if mode == "RGBA" else (70, 90, 120))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_product_tidy_default_and_fal_key_requirement(monkeypatch):
    for name in ("PRODUCT_TIDY", "FAL_KEY", "MATCHING_CUTOUT_PROVIDER",
                 "SAM_PREPROCESS_PROVIDER", "MATCHING_FLATLAY_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    assert load_settings().product_tidy == "off"
    monkeypatch.setenv("PRODUCT_TIDY", " on ")
    with pytest.raises(ValueError, match="FAL_KEY"):
        load_settings()
    monkeypatch.setenv("FAL_KEY", "fake-key")
    assert load_settings().product_tidy == "on"
    with pytest.raises(ValueError, match="FAL_KEY"):
        replace(make_settings(), product_tidy="on", fal_key=None)
    monkeypatch.setenv("PRODUCT_TIDY", "invalid")
    assert load_settings().product_tidy == "off"


class Cursor:
    def __init__(self, store):
        self.store, self.rows = store, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, sql, params=()):
        query = " ".join(sql.split()).lower()
        self.store.queries.append((query, params))
        self.rows = []
        if query.startswith(("savepoint", "release savepoint", "rollback to savepoint")):
            return
        if query.startswith("select") and "from assets" in query:
            rows = list(self.store.assets.values())
            if "where id = %s" in query:
                rows = [r for r in rows if r["id"] == params[0] and r["user_id"] == params[1]
                        and not r.get("deleted_at")]
                # Match get_asset_for_user's SQL projection, not the backing store.
                rows = [{key: row.get(key) for key in (
                    "id", "r2_bucket", "r2_key", "mime_type", "source", "metadata")}
                    for row in rows]
            elif "canonicaltype" in query:
                assert "user_id = %s" in query and "sourceassetid" in query
                if "product_tidy_input" in query:
                    owner, project_id, source_id, alias_id = params
                else:
                    owner, source_id, alias_id = params
                    project_id = None
                assert source_id == alias_id
                rows = [r for r in rows if r["user_id"] == owner
                        and (r.get("metadata", {}).get("canonicalType") == "canonical_cutout"
                             or project_id is not None and r.get("project_id") == project_id
                             and r.get("metadata", {}).get("canonicalType") == "product_tidy_input")
                        and r["metadata"].get("view") == "Front"
                        and (r["metadata"].get("sourceAssetId") == source_id
                             or alias_id in r["metadata"].get("sourceAssetIds", []))]
            elif "r2_key = %s" in query:
                owner, key = params
                rows = [r for r in rows if r["user_id"] == owner and r["r2_key"] == key]
            else:
                raise AssertionError(query)
            self.rows = [r for r in rows if not r.get("deleted_at")]
        elif query.startswith("insert into assets"):
            aid, uid, pid, source, bucket, key, mime, size, filename, meta = params
            if aid not in self.store.assets:
                if any(row["r2_key"] == key for row in self.store.assets.values()):
                    raise errors.UniqueViolation("assets_r2_key_key")
                row = dict(id=aid, user_id=uid, project_id=pid, source=source,
                           r2_bucket=bucket, r2_key=key, mime_type=mime, byte_size=size,
                           metadata=meta.obj)
                self.store.assets[aid] = row
                self.rows = [row]
        elif query.startswith("update assets set metadata"):
            if "jsonb_set" in query:
                sid, aid, owner, source_hash, primary_id, alias_id = params
                assert sid == primary_id == alias_id
                row = self.store.assets[aid]
                meta = row["metadata"]
                if (row["user_id"] == owner and meta.get("sourceHash") == source_hash
                        and meta.get("sourceAssetId") != sid
                        and sid not in meta.get("sourceAssetIds", [])):
                    row["metadata"] = {**meta, "sourceAssetIds": [*meta.get("sourceAssetIds", []), sid]}
            else:
                meta, aid = params
                self.store.assets[aid]["metadata"] = meta.obj
        else:
            raise AssertionError(query)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return Cursor(self.store)

    async def commit(self):
        self.store.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


@pytest.fixture
def env(monkeypatch):
    store = SimpleNamespace(assets={}, objects={"raw": png("RGB"), "canonical": png()},
                            product={"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "src"}]}]},
                            commits=0, render_calls=0, finalized={}, latest=None, queries=[])
    store.assets["src"] = dict(id="src", user_id="u", project_id="p", source="upload",
                               r2_key="raw", metadata={})
    conn = Connection(store)
    r2 = SimpleNamespace(get_bytes=lambda key: store.objects[key],
                         put_bytes=lambda key, data, mime: store.objects.__setitem__(key, data),
                         head=lambda key: {"size": len(store.objects[key]), "mime": "image/png"}
                         if key in store.objects else None,
                         public_url=lambda key: f"https://images.wearless.kr/{key}")
    settings = SimpleNamespace(product_tidy="on", fal_key="test", r2_bucket="bucket")
    app = SimpleNamespace(state=SimpleNamespace(pool=SimpleNamespace(connection=lambda: conn),
                                               r2=r2, settings=settings))
    job = dict(id="job", project_id="p", user_id="u", lease_token="lease", payload={})

    async def product(*_):
        return store.product

    async def latest(*_):
        return store.latest

    async def finish(_conn, **kw):
        store.finalized = kw
        store.latest = {**job, **kw, "kind": "sam_preprocess", "finished_at": datetime.now(timezone.utc),
                        "created_at": datetime.now(timezone.utc)}

    async def segment(_settings, r2, views):
        return {"Front": segmentation_types.SamViewResult(
            view="Front", ready=True, cutout_key="canonical", source_hash=hashlib.sha256(store.objects["raw"]).hexdigest(),
            model_version="sam", algorithm_version="v1", width=32, height=48, byte_size=len(png()), cached=False)}

    monkeypatch.setattr(repo, "get_product", product)
    monkeypatch.setattr(repo, "get_latest_job_generation", latest)
    monkeypatch.setattr(repo, "finalize_uncharged_job", finish)
    monkeypatch.setattr(worker, "load_settings", lambda: settings)
    monkeypatch.setattr(worker.fal_api, "configured", lambda _: True)
    monkeypatch.setattr(worker.fal_cutout, "segment_garment", segment)
    return SimpleNamespace(store=store, conn=conn, r2=r2, settings=settings, app=app, job=job)


def renderer(monkeypatch, env, error=None, output=None):
    from app.services import product_tidy

    async def render(settings, cutout, *, transparent=False):
        env.store.render_calls += 1
        assert transparent is True and cutout == png()
        assert env.store.commits >= 1, "canonical cutout must be committed before Qwen"
        assert image_usage.current_job_id() == "job"
        assert image_usage._ctx.get().stage == "product_tidy"
        if error:
            raise error
        return output if output is not None else png()

    monkeypatch.setattr(product_tidy.qwen_flatlay, "render", render)


def run(env):
    asyncio.run(worker.run_sam_preprocess_job(env.app, env.job))


def visuals(env):
    from app.services import product_tidy
    return asyncio.run(product_tidy.visuals(env.conn, env.r2, env.settings, user_id="u",
                                           project_id="p", product=env.store.product))["front"]


def padded_tidy_png():
    image = Image.new("RGBA", (768, 1024), (0, 0, 0, 0))
    image.paste((70, 90, 120, 255), (184, 212, 584, 812))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_board_is_trimmed_while_full_tidy_and_mannequin_reference_keep_the_canvas(env, monkeypatch):
    from app.agents.gemini_image import InlineImage
    from app.agents.product_reference import ProductReference
    from app.workers import mannequin_job

    original = padded_tidy_png()
    renderer(monkeypatch, env, output=original)
    run(env)
    tidy = next(a for a in env.store.assets.values() if a["metadata"].get("canonicalType") == "product_tidy")
    board = next(a for a in env.store.assets.values() if a["metadata"].get("canonicalType") == "product_tidy_board")
    assert env.store.objects[tidy["r2_key"]] == original
    with Image.open(io.BytesIO(env.store.objects[board["r2_key"]])) as image:
        assert image.size == (341, 512)
        assert image.getchannel("A").getbbox() == (0, 0, 341, 512)
    assert visuals(env) == dict(tidyStatus="ready", tidyUrl=env.r2.public_url(board["r2_key"]),
                               cutoutStatus="ready", cutoutUrl=None, width=341, height=512)
    env.settings.mannequin_pattern_flat_reference = "on"
    reference, verdict = asyncio.run(mannequin_job._load_pattern_flat_reference(
        env.app, job=env.job, product={"name": "바둑판 니트"}, analysis={}, generation_path="fresh",
        product_refs=[ProductReference("Front", "src", InlineImage("image/png", env.store.objects["raw"]))]))
    assert verdict == {"status": "used", "assetId": tidy["id"]}
    assert reference.data == mannequin_job._tidy_on_white(original).data
    assert env.store.objects[tidy["r2_key"]] == original


@pytest.mark.parametrize("legacy", [True, False])
def test_missing_board_falls_back_to_full_tidy_without_get_side_effects(env, monkeypatch, legacy):
    renderer(monkeypatch, env, output=padded_tidy_png())
    run(env)
    tidy = next(a for a in env.store.assets.values() if a["metadata"].get("canonicalType") == "product_tidy")
    if legacy:
        tidy["metadata"].pop("boardAssetId", None)
    else:
        env.store.assets.pop(tidy["metadata"]["boardAssetId"])
    before = copy.deepcopy(env.store.objects), env.store.commits
    def forbidden(*_):
        pytest.fail("GET must only read asset metadata")
    env.r2.get_bytes = env.r2.put_bytes = env.r2.head = forbidden
    assert visuals(env)["tidyUrl"] == env.r2.public_url(tidy["r2_key"])
    assert (visuals(env)["width"], visuals(env)["height"]) == (768, 1024)
    assert (env.store.objects, env.store.commits) == before


def test_off_does_not_render_and_visuals_reports_off(env, monkeypatch):
    env.settings.product_tidy = "off"
    renderer(monkeypatch, env)
    run(env)
    assert env.store.render_calls == 0
    assert visuals(env)["tidyStatus"] == "off"


def test_success_keeps_full_and_board_assets_and_retries_reuse_them(env, monkeypatch):
    renderer(monkeypatch, env)
    env.job["payload"] = {"mode": "canonical_cutout", "productTidyRequired": True}
    run(env)
    run(env)
    tidy = [a for a in env.store.assets.values() if a["metadata"].get("canonicalType") == "product_tidy"]
    assert len(tidy) == 1 and env.store.render_calls == 1
    assert env.store.finalized["product_tidy_capable"] is True
    row = tidy[0]
    board = next(a for a in env.store.assets.values() if a["metadata"].get("canonicalType") == "product_tidy_board")
    assert row["source"] == "derived"
    assert row["r2_key"] == f"users/u/projects/p/derived/{row['id']}.png"
    assert row["metadata"] == {
        "canonicalType": "product_tidy", "view": "Front", "sourceAssetId": "src",
        "sourceHash": hashlib.sha256(env.store.objects["raw"]).hexdigest(),
        "cutoutAssetId": next(a["id"] for a in env.store.assets.values() if a["r2_key"] == "canonical"),
        "producer": "fal-qwen-product-tidy", "algorithmVersion": "product-tidy-v1-qwen",
        "r2Key": row["r2_key"], "width": 32, "height": 48, "boardAssetId": board["id"]}
    assert Image.open(io.BytesIO(env.store.objects[row["r2_key"]])).mode == "RGBA"
    assert visuals(env) == dict(tidyStatus="ready", tidyUrl=f"https://images.wearless.kr/{board['r2_key']}",
                               cutoutStatus="ready", cutoutUrl=None, width=32, height=48)


@pytest.mark.parametrize("error", [ValueError("generated garment mask is too small"),
                                      ValueError("generated garment mask is too large"),
                                      fal_cutout.NoGarmentMask("second segmentation failed"),
                                      OSError("image decode failed")])
def test_tidy_failure_keeps_canonical_and_is_visible(env, monkeypatch, error):
    renderer(monkeypatch, env, error)
    run(env)
    assert env.store.finalized["result"]["recorded"]["Front"]
    assert env.store.finalized["result"]["productTidy"]["state"] == "failed"
    assert not sam_retry.job_is_retryable(env.store.latest)
    assert visuals(env)["tidyStatus"] == "failed"
    assert visuals(env)["cutoutStatus"] == "ready"


def test_outage_retries_without_resegmenting_front_and_has_an_upper_bound(env, monkeypatch):
    renderer(monkeypatch, env, segmentation_types.SamUnavailable("fal down"))
    run(env)
    assert sam_retry.job_is_retryable(env.store.latest)
    assert visuals(env)["tidyStatus"] == "processing"

    async def no_segment(*_):
        pytest.fail("already committed front must not be segmented again")

    monkeypatch.setattr(worker.fal_cutout, "segment_garment", no_segment)
    env.job["payload"] = {"retry": sam_retry.MAX_RETRIES}
    run(env)
    assert not sam_retry.budget_left(env.store.latest)
    assert visuals(env)["tidyStatus"] == "failed"
    assert visuals(env)["cutoutStatus"] == "ready"


def test_replaced_front_never_returns_old_tidy(env, monkeypatch):
    renderer(monkeypatch, env)
    run(env)
    env.store.assets["replacement"] = {**env.store.assets["src"], "id": "replacement", "r2_key": "replacement-raw"}
    env.store.objects["replacement-raw"] = png("RGB", (40, 60))
    env.store.product["colors"][0]["images"][0]["id"] = "replacement"
    front = visuals(env)
    assert front["tidyUrl"] is None and front["cutoutStatus"] != "ready"
    assert front["tidyStatus"] != "ready"


def test_same_content_with_a_new_upload_id_reuses_tidy(env, monkeypatch):
    renderer(monkeypatch, env)
    run(env)
    env.store.assets["new-src"] = {**env.store.assets["src"], "id": "new-src"}
    env.store.product["colors"][0]["images"][0]["id"] = "new-src"
    run(env)
    assert env.store.render_calls == 1 and visuals(env)["tidyStatus"] == "ready"


def test_identical_photos_from_two_sellers_keep_separate_ready_tidy_results(env, monkeypatch):
    from app.services import product_tidy

    renderer(monkeypatch, env)
    first_product = copy.deepcopy(env.store.product)
    run(env)
    first = visuals(env)
    first_assets = copy.deepcopy(env.store.assets)

    env.store.assets["src-2"] = dict(id="src-2", user_id="u2", project_id="p2",
                                     source="upload", r2_key="raw-2", metadata={})
    env.store.objects["raw-2"] = env.store.objects["raw"]
    env.store.product["colors"][0]["images"][0]["id"] = "src-2"
    env.job.update(user_id="u2", project_id="p2")
    run(env)
    second = asyncio.run(product_tidy.visuals(
        env.conn, env.r2, env.settings, user_id="u2", project_id="p2",
        product=env.store.product))["front"]
    assert second["tidyStatus"] == "ready"
    assert second["tidyUrl"].startswith("https://images.wearless.kr/users/u2/projects/p2/derived/")
    assert second["tidyUrl"] != first["tidyUrl"]
    assert second["cutoutUrl"] is None
    assert env.store.render_calls == 2
    assert all(env.store.assets[aid] == row for aid, row in first_assets.items())
    display_input = next(row for row in env.store.assets.values()
                         if row.get("user_id") == "u2"
                         and row.get("metadata", {}).get("canonicalType") == "product_tidy_input")
    assert display_input["r2_key"].startswith("users/u2/projects/p2/derived/")
    assert not canonical_reference.is_current(
        display_input["metadata"], source_asset_id="src-2",
        source_hash=display_input["metadata"]["sourceHash"])

    # The next generation can find its own display input and reuse the finished PNG.
    run(env)
    assert env.store.render_calls == 2
    first_again = asyncio.run(product_tidy.visuals(
        env.conn, env.r2, env.settings, user_id="u", project_id="p",
        product=first_product))["front"]
    assert first_again == first
    env.settings.product_tidy = "off"
    off = asyncio.run(product_tidy.visuals(
        env.conn, env.r2, env.settings, user_id="u2", project_id="p2",
        product=env.store.product))["front"]
    assert off["tidyStatus"] == "off" and off["tidyUrl"] is None
    assert off["cutoutStatus"] != "ready"


@pytest.mark.parametrize("failed_kind", ["product_tidy", "product_tidy_board"])
def test_temporary_final_db_failure_retries_saved_png_without_another_generation(env, monkeypatch, failed_kind):
    renderer(monkeypatch, env)
    original = repo.create_asset
    failed = False
    writes = []
    original_put = env.r2.put_bytes

    def put(key, data, mime):
        writes.append(key)
        original_put(key, data, mime)

    monkeypatch.setattr(env.r2, "put_bytes", put)

    async def insert(*args, **kwargs):
        nonlocal failed
        if not failed and (kwargs.get("metadata") or {}).get("canonicalType") == failed_kind:
            failed = True
            raise OperationalError("temporary DB outage")
        return await original(*args, **kwargs)

    monkeypatch.setattr(repo, "create_asset", insert)
    run(env)
    assert sam_retry.job_is_retryable(env.store.latest)
    assert visuals(env)["tidyStatus"] == "processing"
    env.job["payload"] = {"retry": 1}
    run(env)
    assert visuals(env)["tidyStatus"] == "ready"
    assert env.store.render_calls == 1
    assert len(writes) == len(set(writes)) == 2


def test_board_storage_failure_reuses_full_canvas_without_another_generation(env, monkeypatch):
    original = padded_tidy_png()
    renderer(monkeypatch, env, output=original)
    put = env.r2.put_bytes
    failed = False
    def fail_board_once(key, data, mime):
        nonlocal failed
        if data != original and not failed:
            failed = True
            raise OSError("temporary board storage outage")
        put(key, data, mime)
    monkeypatch.setattr(env.r2, "put_bytes", fail_board_once)
    run(env)
    assert sam_retry.job_is_retryable(env.store.latest)
    assert original in env.store.objects.values()
    env.job["payload"] = {"retry": 1}
    run(env)
    assert env.store.render_calls == 1
    assert (visuals(env)["width"], visuals(env)["height"]) == (341, 512)


def test_processing_window_expires_and_terminal_failures_do_not_spin(env):
    env.store.latest = dict(status="pending", created_at=datetime.now(timezone.utc), payload={}, result=None)
    assert visuals(env)["tidyStatus"] == "processing"
    assert visuals(env)["cutoutStatus"] == "processing"
    env.store.latest["created_at"] -= timedelta(minutes=11)
    assert visuals(env)["tidyStatus"] == "failed"
    assert visuals(env)["cutoutStatus"] == "failed"
    env.store.latest.update(status="done", created_at=datetime.now(timezone.utc),
                            finished_at=datetime.now(timezone.utc) - timedelta(minutes=31),
                            result={"state": "unavailable"})
    assert visuals(env)["tidyStatus"] == "failed"


def test_visuals_owner_authorization_precedes_storage(client, make_token, monkeypatch):
    import app.routes as routes
    patch_route_db(monkeypatch, routes)

    async def missing(*_):
        return None

    monkeypatch.setattr(repo, "get_project", missing)
    response = client.get("/v1/projects/other/product/visuals", headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 404
    assert client.get("/v1/projects/other/product/visuals").status_code == 401


def test_visuals_route_returns_off_contract(client, make_token, monkeypatch):
    import app.routes as routes
    patch_route_db(monkeypatch, routes)

    async def project(*_):
        return {"id": "p"}

    async def product(*_):
        return {}

    monkeypatch.setattr(repo, "get_project", project)
    monkeypatch.setattr(repo, "get_product", product)
    client.app.state.r2 = SimpleNamespace()
    response = client.get("/v1/projects/p/product/visuals", headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert response.json() == {"front": dict(tidyStatus="off", tidyUrl=None, cutoutStatus="none",
                                           cutoutUrl=None, width=0, height=0)}


def test_on_enqueue_has_a_new_identity_and_visuals_reads_the_same_generation(env, monkeypatch):
    import app.routes as routes
    from app.services import product_tidy
    seen = {}

    async def create(_conn, **kw):
        seen.update(kw)
        return {}, True

    async def latest(_conn, uid, key):
        assert uid == "u" and key == seen["idempotency_key"]
        return {"status": "pending", "created_at": datetime.now(timezone.utc), "result": None}

    monkeypatch.setattr(repo, "create_job", create)
    monkeypatch.setattr(repo, "get_latest_job_generation", latest)
    asyncio.run(routes._enqueue_canonical_cutout(env.conn, user_id="u", project_id="p",
                                                product=env.store.product, tidy_enabled=True))
    old_key = canonical_reference.preprocess_idempotency_key("p", env.store.product)
    assert seen["idempotency_key"] == old_key + ":product-tidy-v1-qwen:required-v2"
    assert seen["payload"] == {"mode": "canonical_cutout", "productTidyRequired": True}
    assert seen["credits_reserved"] == 0 and seen["kind"] == "sam_preprocess"
    assert visuals(env)["tidyStatus"] == "processing"


def test_off_enqueue_keeps_ordinary_payload_and_identity(env, monkeypatch):
    import app.routes as routes
    seen = {}

    async def create(_conn, **kw):
        seen.update(kw)
        return {}, True

    monkeypatch.setattr(repo, "create_job", create)
    assert asyncio.run(routes._enqueue_canonical_cutout(
        env.conn, user_id="u", project_id="p", product=env.store.product))
    assert seen["payload"] == {"mode": "canonical_cutout"}
    assert seen["idempotency_key"] == canonical_reference.preprocess_idempotency_key("p", env.store.product)


def test_required_job_on_off_worker_waits_without_provider_or_source_reads(env, monkeypatch):
    env.settings.product_tidy = "off"
    env.job["payload"] = {"mode": "canonical_cutout", "productTidyRequired": True, "retry": 3}
    renderer(monkeypatch, env)

    async def forbidden(*_a, **_kw):
        pytest.fail("an incapable worker must release the job before source or provider work")

    monkeypatch.setattr(repo, "get_product", forbidden)
    monkeypatch.setattr(worker.fal_cutout, "segment_garment", forbidden)
    run(env)
    assert env.store.render_calls == 0
    assert env.store.finalized["product_tidy_capable"] is False
    assert env.store.finalized["result"] == {"state": "pending", "reason": "product_tidy_worker_required"}
    assert env.job["payload"]["retry"] == 3


@pytest.mark.parametrize("status", ["pending", "running"])
def test_on_enqueue_upgrades_same_photo_active_ordinary_job(env, monkeypatch, status):
    import app.routes as routes
    old_key = canonical_reference.preprocess_idempotency_key("p", env.store.product)
    active = dict(id="active", user_id="u", project_id="p", status=status,
                  idempotency_key=old_key, payload={"mode": "canonical_cutout", "retry": 2})

    async def create(_conn, **kw):
        return active.copy(), False

    async def upgrade(_conn, **kw):
        assert kw["job_id"] == "active"
        assert kw["user_id"] == "u" and kw["project_id"] == "p"
        assert old_key in kw["compatible_keys"]
        active["payload"]["productTidyRequired"] = True
        active["idempotency_key"] = kw["idempotency_key"]
        return True

    monkeypatch.setattr(repo, "create_job", create)
    monkeypatch.setattr(repo, "require_product_tidy_job", upgrade, raising=False)
    assert asyncio.run(routes._enqueue_canonical_cutout(
        env.conn, user_id="u", project_id="p", product=env.store.product, tidy_enabled=True))
    assert active["payload"] == {"mode": "canonical_cutout", "retry": 2, "productTidyRequired": True}
    assert active["idempotency_key"] == old_key + ":product-tidy-v1-qwen:required-v2"


def test_on_enqueue_retries_if_ordinary_job_finishes_before_upgrade(env, monkeypatch):
    import app.routes as routes
    old_key = canonical_reference.preprocess_idempotency_key("p", env.store.product)
    created = []

    async def create(_conn, **kw):
        created.append(kw)
        if len(created) == 1:
            return dict(id="old", status="running", idempotency_key=old_key,
                        payload={"mode": "canonical_cutout"}), False
        return dict(id="new", **kw), True

    async def finished(*_a, **_kw):
        return None

    monkeypatch.setattr(repo, "create_job", create)
    monkeypatch.setattr(repo, "require_product_tidy_job", finished, raising=False)
    assert asyncio.run(routes._enqueue_canonical_cutout(
        env.conn, user_id="u", project_id="p", product=env.store.product, tidy_enabled=True))
    assert len(created) == 2
    assert created[1]["payload"]["productTidyRequired"] is True


def test_on_enqueue_does_not_rename_another_photo_active_job(env, monkeypatch):
    import app.routes as routes
    active = dict(id="other-photo", status="running", idempotency_key="p:sam_preprocess:another-digest",
                  payload={"mode": "canonical_cutout"})
    before = copy.deepcopy(active)

    async def create(_conn, **kw):
        return active, False

    async def incompatible(*_a, **_kw):
        return False

    monkeypatch.setattr(repo, "create_job", create)
    monkeypatch.setattr(repo, "require_product_tidy_job", incompatible, raising=False)
    asyncio.run(routes._enqueue_canonical_cutout(
        env.conn, user_id="u", project_id="p", product=env.store.product, tidy_enabled=True))
    assert active == before


def test_tidy_asset_identity_is_deterministic_and_scoped():
    from app.services import product_tidy
    import uuid
    first = product_tidy.asset_id("u", "p", "hash")
    assert uuid.UUID(first).version == 5
    assert first == product_tidy.asset_id("u", "p", "hash")
    assert len({first, product_tidy.asset_id("u2", "p", "hash"),
                product_tidy.asset_id("u", "p2", "hash"), product_tidy.asset_id("u", "p", "hash2")}) == 4


def test_back_outage_does_not_repeat_a_terminal_tidy_failure(env, monkeypatch):
    renderer(monkeypatch, env, ValueError("mask too small"))
    env.store.assets["back"] = {**env.store.assets["src"], "id": "back", "r2_key": "raw"}
    env.store.product["colors"][0]["images"].append({"slot": "Back", "id": "back"})
    original_segment = worker.fal_cutout.segment_garment

    async def segment(settings, r2, views):
        result = await original_segment(settings, r2, views)
        if "Front" not in views:
            result.pop("Front", None)
        result["Back"] = segmentation_types.SamViewResult(view="Back", ready=False, code="unavailable")
        return result

    monkeypatch.setattr(worker.fal_cutout, "segment_garment", segment)
    run(env)
    assert env.store.finalized["result"]["state"] == "unavailable"
    terminal = env.store.finalized["result"]["productTidy"]
    assert terminal["state"] == "failed"
    env.job["payload"] = {"retry": 1, "_productTidy": terminal}
    run(env)
    assert env.store.render_calls == 1
    assert env.store.finalized["result"]["productTidy"]["state"] == "failed"


def test_terminal_tidy_verdict_stays_failed_while_only_back_is_retrying(env, monkeypatch):
    renderer(monkeypatch, env, ValueError("mask too small"))
    run(env)
    terminal = env.store.finalized["result"]["productTidy"]
    env.store.latest.update(status="running", result=None,
                            payload={"retry": 1, "_productTidy": terminal},
                            created_at=datetime.now(timezone.utc))
    front = visuals(env)
    assert front["cutoutStatus"] == "ready"
    assert front["tidyStatus"] == "failed"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("has_cutout", [False, True])
def test_visuals_never_downloads_images_and_off_never_queries_tidy(env, monkeypatch, enabled, has_cutout):
    renderer(monkeypatch, env)
    if has_cutout:
        run(env)
    env.settings.product_tidy = "on" if enabled else "off"
    env.store.queries.clear()
    def forbidden(*_):
        pytest.fail("GET visuals must not download original, canonical, or tidy bytes")
    env.r2.get_bytes = forbidden
    monkeypatch.setattr(repo, "create_job", forbidden)
    monkeypatch.setattr(repo, "require_product_tidy_job", forbidden)
    if not enabled:
        from app.services import product_tidy
        monkeypatch.setattr(product_tidy, "asset_id", forbidden)
    for _ in range(3):
        front = visuals(env)
        assert front["tidyStatus"] == (("ready" if has_cutout else "failed") if enabled else "off")
        assert front["cutoutStatus"] == ("ready" if has_cutout else "none")
        assert (front["width"], front["height"]) == ((32, 48) if has_cutout else (0, 0))
    if not enabled:
        assert all("product_tidy" not in query for query, _ in env.store.queries)
        assert all("where id = %s" not in query or params[0] == "src" for query, params in env.store.queries)


def test_worker_uses_sam_source_hash_without_downloading_original_again(env, monkeypatch):
    renderer(monkeypatch, env)
    get_bytes = env.r2.get_bytes
    reads = []
    def storage(key):
        reads.append(key)
        assert key != "raw", "SAM already computes source_hash; worker must not fetch the original again"
        return get_bytes(key)
    env.r2.get_bytes = storage
    run(env)
    assert env.store.finalized["result"]["productTidy"]["state"] == "ready"
    assert reads == ["canonical"]


def test_visuals_rejects_tidy_bound_to_another_front_id_even_when_hash_is_equal(env, monkeypatch):
    renderer(monkeypatch, env)
    run(env)
    tidy = next(a for a in env.store.assets.values() if a["metadata"].get("canonicalType") == "product_tidy")
    tidy["metadata"]["sourceAssetId"] = "other-upload"
    front = visuals(env)
    assert front["tidyStatus"] != "ready" and front["tidyUrl"] is None


def test_visuals_rejects_tidy_when_canonical_hash_changes(env, monkeypatch):
    renderer(monkeypatch, env)
    run(env)
    cutout = next(a for a in env.store.assets.values() if a["metadata"].get("canonicalType") == "canonical_cutout")
    cutout["metadata"]["sourceHash"] = "new-sam-hash"
    front = visuals(env)
    assert front["tidyStatus"] != "ready" and front["tidyUrl"] is None


def test_same_content_bindings_keep_previous_photo_ids_ready_without_restarting_old_jobs(env, monkeypatch):
    renderer(monkeypatch, env)
    run(env)
    for source_id in ("second", "third"):
        env.store.assets[source_id] = {**env.store.assets["src"], "id": source_id}
        env.store.product["colors"][0]["images"][0]["id"] = source_id
        run(env)
        assert visuals(env)["tidyStatus"] == "ready"
    # Revisiting an old photo ID joins its completed job. GET must still be ready.
    for source_id in ("src", "second", "third"):
        env.store.product["colors"][0]["images"][0]["id"] = source_id
        assert visuals(env)["tidyStatus"] == "ready"
    assert env.store.render_calls == 1
    tidy = [a for a in env.store.assets.values() if a["metadata"].get("canonicalType") == "product_tidy"]
    assert len(tidy) == 1 and tidy[0]["metadata"]["sourceAssetId"] == "src"


@pytest.mark.parametrize("error, status", [(ValueError("mask too small"), "failed"),
                                            (segmentation_types.SamUnavailable("fal down"), "processing")])
def test_reuploaded_canonical_stays_ready_even_when_new_tidy_fails(env, monkeypatch, error, status):
    env.settings.product_tidy = "off"
    run(env)  # Original upload has a canonical, without a tidy.
    env.store.assets["new-src"] = {**env.store.assets["src"], "id": "new-src"}
    env.store.product["colors"][0]["images"][0]["id"] = "new-src"
    env.settings.product_tidy = "on"
    renderer(monkeypatch, env, error)
    run(env)
    def forbidden(*_):
        pytest.fail("GET must read persisted canonical binding, not image bytes")
    env.r2.get_bytes = forbidden
    front = visuals(env)
    assert front["cutoutStatus"] == "ready"
    assert front["tidyStatus"] == status


def test_binding_failure_cannot_rollback_canonical_or_start_qwen(env, monkeypatch):
    from app.services import product_tidy
    renderer(monkeypatch, env)
    async def binding(*_args, **_kwargs):
        assert env.store.commits >= 1, "optional metadata binding must follow canonical commit"
        raise OSError("metadata store unavailable")
    monkeypatch.setattr(product_tidy, "bind_source", binding)
    run(env)
    assert env.store.finalized["result"]["recorded"]["Front"]
    assert env.store.finalized["result"]["productTidy"]["state"] == "unavailable"
    assert env.store.render_calls == 0
    assert sam_retry.job_is_retryable(env.store.latest)
