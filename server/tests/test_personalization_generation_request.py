import asyncio
from types import SimpleNamespace

from app.workers import personalization_generation_job as worker
from conftest import make_settings


class _Connection:
    def __init__(self):
        self.query = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def cursor(self):
        return self

    async def execute(self, query, _args):
        self.query = query

    async def fetchone(self):
        if "from personalization_profiles" in self.query:
            return {"status": "ready", "gender": "women"}
        return None

    async def fetchall(self):
        return [
            {"angle": angle, "r2_key": f"face-{angle}", "mime_type": "image/png"}
            for angle in ("front", "side", "angle45")
        ]

    async def commit(self):
        return None


class _Pool:
    def connection(self):
        return _Connection()


class _Storage:
    def __init__(self):
        self.puts = []

    def get_bytes(self, key):
        return key.encode()

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime, cache))


def test_personalization_provider_request_keeps_non_mannequin_2k_size(monkeypatch):
    captured = {}

    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            captured.update(model=model, prompt=prompt, images=images, size=size, kwargs=kwargs)
            return SimpleNamespace(image=b"generated", mime="image/png")

    async def asset(*_args):
        return {"r2_key": "product", "mime_type": "image/png"}

    async def finalize(*_args, **_kwargs):
        return "done"

    async def emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(worker.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(worker, "_finalize_success", finalize)
    monkeypatch.setattr(worker, "_emit", emit)
    face_storage, product_storage = _Storage(), _Storage()
    settings = make_settings(
        mannequin_image_size="1K", detail_cut_image_size="2K",
        model_image_high="gemini-3-pro-image",
    )
    app = SimpleNamespace(state=SimpleNamespace(
        settings=settings,
        pool=_Pool(),
        r2_face=face_storage,
        r2=product_storage,
        gemini=Provider(),
    ))
    job = {
        "id": "job", "user_id": "user", "project_id": None, "lease_token": "lease",
        "credits_reserved": 0,
        "payload": {
            "profileId": "profile", "generationId": "generation",
            "productImageAssetIds": ["product-asset"], "options": {},
        },
    }

    asyncio.run(worker.run_personalization_generation_job(app, job))

    assert settings.mannequin_image_size == "1K"
    assert captured["model"] == "gemini-3-pro-image"
    assert captured["size"] == "2K"
    assert len(captured["images"]) == 4
    assert len(face_storage.puts) == 1
