import time
import contextlib
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

AUDIENCE = "authenticated"


def auth_headers(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


class FakeConn:
    async def commit(self):
        return None


def patch_route_db(monkeypatch, routes_module):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield FakeConn()

    monkeypatch.setattr(routes_module, "get_conn", fake_conn)


class FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeConn()

        return _cm()


class FakeR2:
    def get_bytes(self, key):
        return b"\x89PNG-bytes"

    def put_bytes(self, key, data, mime, cache=None):
        return None

    def delete(self, key):
        return None


class FakeGemini:
    pass


def fake_worker_app(settings, *, r2=None, gemini=None):
    state = types.SimpleNamespace(
        settings=settings,
        pool=FakePool(),
        r2=r2 or FakeR2(),
        gemini=gemini or FakeGemini(),
    )
    return types.SimpleNamespace(state=state)


def worker_job(payload=None, *, credits_reserved=1):
    return {
        "id": "j1",
        "user_id": "u1",
        "project_id": "p1",
        "lease_token": "u1:tok",
        "credits_reserved": credits_reserved,
        "payload": payload or {},
    }


@pytest.fixture(scope="session")
def keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def make_settings(**overrides) -> Settings:
    base = dict(
        app_env="prod",
        supabase_url="https://example.supabase.co",
        jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        jwt_audience=AUDIENCE,
        cors_origins=["http://localhost:5173"],
        database_url=None,
        r2_account_id=None,
        r2_access_key_id=None,
        r2_secret_access_key=None,
        r2_bucket=None,
        r2_endpoint=None,
        r2_public_base=None,
        # 운영 기본은 bestof. 관련 없는 기존 워커 테스트는 외부 vision 판정을 호출하지 않게
        # 테스트 기본만 명시적으로 off로 두고 QC 테스트에서 모드를 개별 활성화한다.
        garment_qc_mode="off",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def client(keypair):
    private_key, public_key = keypair
    app = create_app(make_settings())
    # 테스트에서는 JWKS 네트워크 대신 테스트 공개키로 검증
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app)


@pytest.fixture()
def make_token(keypair):
    private_key, _ = keypair

    def _make(sub="user-1", aud=AUDIENCE, exp_offset=3600, **extra):
        claims = {
            "sub": sub,
            "aud": aud,
            "exp": int(time.time()) + exp_offset,
            **extra,
        }
        return jwt.encode(claims, private_key, algorithm="ES256")

    return _make


# ── shadow calibration 공용 fixture (Phase 3 P0-C 9/N) ─────────────────────
# trusted 판정은 중앙 verifier 만 만든다. 그래서 "신뢰되는 리포트"를 보려면 실제
# 파일·manifest 를 세워 verifier 를 통과시켜야 한다 — 느슨한 fixture 로는 못 만든다.

import json as _json
import pathlib as _pathlib

import pytest as _pytest

_SERVER = _pathlib.Path(__file__).resolve().parents[1]


def _shadow_modules():
    import importlib.util

    def load(name, rel):
        spec = importlib.util.spec_from_file_location(name, _SERVER / rel)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    return load("_sc_fixture", "scripts/shadow_collect.py"), \
        load("_sm_fixture", "scripts/shadow_manifest.py")


@_pytest.fixture
def shadow_dataset(tmp_path):
    """실제 source/output/samples/manifest 를 갖춘 데이터셋 factory.

    → build(rows=None, n=2) -> {dir, source_dir, rows, manifest, verification}
    source 는 **사본**이라 테스트가 실제 바이트를 바꿔 볼 수 있다(정본 무수정).
    """
    import shutil

    from app import shadow_cases as scases
    from app import shadow_verification as sv
    from app.config import load_settings

    SC, SM = _shadow_modules()
    origin_dir = _SERVER.parent / "public" / "assets" / "fit-examples"

    def build(n=2, *, mutate_rows=None, dataset_id="ds", label_all=None):
        src_dir = tmp_path / "sources"
        src_dir.mkdir(exist_ok=True)
        origin = sorted(origin_dir.glob("*.jpg"))[0]
        shutil.copy(origin, src_dir / origin.name)
        ds = tmp_path / dataset_id
        ds.mkdir(exist_ok=True)
        s = load_settings()
        raw = (src_dir / origin.name).read_bytes()
        rows = []
        for i, (name, ch) in enumerate(scases.VARY_CASES[:n]):
            prep = scases.generation_prepared(s, ch)
            vp = scases.vision_prepared(ch)
            ob = b"PNG-" + bytes([i])
            (ds / f"{name}.png").write_bytes(ob)
            rows.append({"id": name, "output_id": f"o{i}", "case": name,
                         "source": origin.name, "source_kind": "editor_asset",
                         "edit_type": SC.editor_vary.edit_type_for(ch),
                         "image_calls": 1, "vision_calls": 1,
                         "edit_qc_result": {"decision": "pass",
                                            "vision": {"meta": {"status": "ok"}}},
                         "provenance": SC._provenance(
                             prep, case_name=name, changes=ch, attempt=1,
                             source_bytes=raw, output_bytes=ob,
                             vision_meta={"promptSha256": vp.prompt_sha256,
                                          "provider": "p", "status": "ok"})})
        if mutate_rows:
            mutate_rows(rows)
        (ds / "samples.jsonl").write_text(
            "".join(_json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        manifest = SM.build(str(ds / "samples.jsonl"), dataset_id=dataset_id,
                            invalid_reasons=[], image_usd=0, vision_usd=0,
                            collected_at="t", command=None, source_dir=src_dir)
        (ds / "manifest.json").write_text(_json.dumps(manifest, ensure_ascii=False))
        verification = sv.verify_dataset(
            manifest=manifest, rows=rows, samples_path=ds / "samples.jsonl",
            source_dir=src_dir)
        # 라벨은 raw row 에 심지 않는다 — typed bind 만이 검증된 라벨을 만든다.
        labeled = None
        if label_all:
            from app import blinded_audit as ba
            eff = {(dataset_id, str(r["id"])): ba.make_label(
                sample=r, label=(label_all if isinstance(label_all, str)
                                 else "fidelity_pass"),
                reviewer_id="t", dataset_id=dataset_id, now=1.0)
                for r in verification.rows}
            labeled, _q = sv.bind_verified_labels(verification, eff)
        return {"dir": ds, "source_dir": src_dir, "rows": rows,
                "manifest": manifest, "verification": verification,
                "labeled": labeled, "source_name": origin.name}

    return build
