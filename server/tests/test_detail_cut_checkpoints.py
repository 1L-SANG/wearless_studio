"""상세 컷 비용 절감(2026-09-23) — 부분 실패 격리(A) · 각도 사진 사전 확인(C) · 컷 체크포인트(D).

오너 원칙: "생성 비용을 최대한 아껴야 한다. 이미 만든 베이스컷이 있으면 그대로 쓴다."
그날 운영: ECS 배포가 상세 잡을 죽여(worker_shutdown) 끝난 컷 $1.53·$0.53 어치를 버렸고,
"다시 시도"는 전부 처음부터 다시 그렸다.

유료 호출·이미지 생성 없음 — provider·R2·DB 는 전부 대역이다.
"""

import asyncio
import contextlib
import types

import pytest

from app import repo
from app.agents import cut_generator as cg
from app.agents import face_angle_swap as angle
from app.agents import face_identity as fi
from app.agents.gemini_image import InlineImage
from app.r2 import PRIVATE_NO_STORE
from app.workers import cut_checkpoints as ckpts
from app.workers import detail_page_job as dpj
from conftest import make_settings

USER, PROJECT = "u1", "p1"
LORA = fi.FaceIdentitySpec("facemarket/loras/m/v1.safetensors", "ohwx man", sha256="ab" * 32)
PRODUCT = {"clothingType": "top", "colors": []}
#: 얼굴이 담기는 착용컷 — 얼굴 패스 대상(_face_fits). horizon 이 아니라 목 보정(유료)도 안 탄다.
STYLING = {"id": "b1", "source": "ai", "cutType": "styling", "direction": "front",
           "shot": "medium", "refScope": "all", "modelId": "mX", "faceExposure": "show"}
PRODUCT_IMG = InlineImage("image/png", b"product-front")


def _settings(**overrides):
    base = dict(gemini_api_key="x", r2_bucket="b", face_identity_enabled=True,
                real_horizon_neck_repair_enabled=False, detail_cut_max_attempts=1,
                detail_cut_checkpoint_enabled=True)
    base.update(overrides)
    return make_settings(**base)


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


class _R2:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str, str | None]] = {}
        self.deleted: list[str] = []
        self.fail_get = False

    def put_bytes(self, key, data, mime, cache=None):
        self.objects[key] = (bytes(data), mime, cache)

    def get_bytes(self, key):
        if self.fail_get:
            raise RuntimeError("r2 down")
        return self.objects[key][0]

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    def head(self, key):
        return {"size": 1} if key in self.objects else None

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"


class _IntentDB:
    """ai_output_cleanup_intents 의 체크포인트 표식 대역 — repo 함수 세 개를 갈아 끼운다."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.error_jobs: set[str] = set()
        self.ttl_calls: list[tuple[list[str], int, bool]] = []

    async def create(self, conn, *, job_id, r2_key, ttl_seconds):
        row = {"id": f"intent-{len(self.rows) + 1}", "r2_key": r2_key, "job_id": job_id,
               "ttl": ttl_seconds, "live": True}
        self.rows[r2_key] = row
        return row["id"]

    async def list(self, conn, *, user_id, project_id, key_pattern, limit=400):
        prefix = f"users/{user_id}/projects/{project_id}/ai/"
        return [
            {"id": r["id"], "r2_key": r["r2_key"], "job_id": r["job_id"]}
            for r in reversed(list(self.rows.values()))
            if r["live"] and r["job_id"] in self.error_jobs and r["r2_key"].startswith(prefix)
        ]

    async def expiry(self, conn, r2_keys, *, ttl_seconds, extend_only=False):
        self.ttl_calls.append((list(r2_keys), ttl_seconds, extend_only))
        if ttl_seconds == 0:
            for key in r2_keys:
                if key in self.rows:
                    self.rows[key]["live"] = False

    async def clear(self, conn, intent_id):
        for key, row in list(self.rows.items()):
            if row["id"] == intent_id:
                row["live"] = False
                row["cleared"] = True

    def install(self, monkeypatch):
        monkeypatch.setattr(repo, "create_ai_checkpoint_intent", self.create)
        monkeypatch.setattr(repo, "list_detail_cut_checkpoints", self.list)
        monkeypatch.setattr(repo, "set_ai_checkpoint_expiry", self.expiry)
        monkeypatch.setattr(repo, "clear_ai_output_cleanup_intent", self.clear)


def _app(settings=None, r2=None):
    state = types.SimpleNamespace(settings=settings or _settings(), pool=_Pool(),
                                  r2=r2 or _R2(), gemini=None)
    return types.SimpleNamespace(state=state)


def _job(job_id):
    return {"id": job_id, "user_id": USER, "project_id": PROJECT, "lease_token": "t",
            "credits_reserved": 1, "metadata": {"perCutCost": 1}}


class _Gemini:
    def __init__(self, image=b"BASE"):
        self.calls = 0
        self.image = image

    async def generate_content_image(self, model, prompt, images, image_size, **_kw):
        self.calls += 1
        return types.SimpleNamespace(image=self.image, mime="image/png")


def _events(monkeypatch):
    events = []

    async def fake_emit(_pool, _job_id, event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    return events


def _real_item(block=STYLING):
    # (block, images, manifest, has_face, product_images, plate, strict, passthrough,
    #  confirmed_packet, real_identity_attached)
    return (dict(block), [PRODUCT_IMG], "1. PRODUCT (Front)", True, [PRODUCT_IMG],
            None, False, None, None, True)


# ── A. 한 컷의 예상 밖 예외는 그 컷만 ─────────────────────────────────────────
def test_one_crashing_cut_does_not_fail_the_page(monkeypatch):
    """R2 저장 오류 같은 예상 밖 예외 — 예전에는 gather 가 페이지 전체를 죽였다."""
    events = _events(monkeypatch)

    async def fake_generate(settings, gemini, block, product, images, **_kw):
        return (b"BAD" if block["id"] == "bad" else b"GOOD"), "image/png"

    class _FlakyR2(_R2):
        def put_bytes(self, key, data, mime, cache=None):
            if data == b"BAD":
                raise RuntimeError("r2 put failed")
            super().put_bytes(key, data, mime, cache)

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    app = _app(_settings(detail_cut_checkpoint_enabled=False), _FlakyR2())
    prepared = [
        ({"id": "good", "cutType": "product", "shot": "ghost"}, [PRODUCT_IMG], "m", False, [PRODUCT_IMG]),
        ({"id": "bad", "cutType": "product", "shot": "ghost", "direction": "back"},
         [PRODUCT_IMG], "m", False, [PRODUCT_IMG]),
    ]
    cut_results, cut_assets, *_ = asyncio.run(
        dpj._gen_cuts(app, _job("j1"), prepared, PRODUCT, {}))

    assert [r["blockId"] for r in cut_results] == ["good"]
    assert len(cut_assets) == 1                      # 정산 단위도 성공 컷만
    assert ("step", {"blockId": "bad", "status": "cut_failed"}) in events
    progress = [p for kind, p in events if kind == "progress"]
    assert progress[-1]["done"] == 2                 # 실패 컷도 진행 분모에 들어간다


def test_worker_shutdown_is_not_swallowed_by_cut_isolation(monkeypatch):
    _events(monkeypatch)

    async def cancelled(*_a, **_kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(dpj.cut_generator, "generate", cancelled)
    app = _app(_settings(detail_cut_checkpoint_enabled=False))
    prepared = [({"id": "x", "cutType": "product", "shot": "ghost"}, [PRODUCT_IMG], "m",
                 False, [PRODUCT_IMG])]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dpj._gen_cuts(app, _job("j1"), prepared, PRODUCT, {}))


# ── C. 결정적 각도 실패는 베이스 값을 내기 전에 ───────────────────────────────
def _angle_spec(**photos):
    return angle.AngleSwapSpec(photos=angle.AnglePhotos(**photos), backend=object())


@pytest.mark.parametrize("block,photos,expected", [
    ({"direction": "back"}, {"side_nose_left": b"s"}, "no_angle_photo"),
    ({"direction": "back"}, {"back": b"b"}, None),
    ({"direction": "side", "sideStyle": "profile"}, {"back": b"b"}, "no_angle_photo"),
    # 한 장만 있으면 코 방향(그림)에 달렸다 — 미리 모른다, 기존대로 생성
    ({"direction": "side", "sideStyle": "profile"}, {"side_nose_right": b"r"}, None),
    # 사선은 각도 교체가 아니라 얼굴 패스로 간다
    ({"direction": "side", "sideStyle": "threeQuarter"}, {"back": b"b"}, None),
    ({"direction": "front"}, {}, None),
])
def test_angle_precheck_mirrors_the_swap_route(block, photos, expected):
    spec = {**STYLING, "shot": "full", **block}
    assert cg.angle_swap_missing_photo(spec, PRODUCT, _angle_spec(**photos)) == expected
    # 각도 교체 자리가 없으면(기능 off·백엔드 없음) 판정하지 않는다 — generate() 도 그 경로를 안 탄다
    assert cg.angle_swap_missing_photo(spec, PRODUCT, None) is None


def test_back_cut_without_back_photo_fails_before_paying_for_the_base(monkeypatch):
    events = _events(monkeypatch)

    async def forbidden_generate(*_a, **_kw):
        raise AssertionError("base must not be generated when the swap cannot run")

    async def no_pod(pool):
        return None

    from app.agents import identity_source

    monkeypatch.setattr(dpj.cut_generator, "generate", forbidden_generate)
    monkeypatch.setattr(identity_source, "active_angle_pod_id", no_pod)
    settings = _settings(face_angle_swap_enabled=True, face_angle_endpoint_id="ep1",
                         face_runpod_api_key="k", detail_cut_checkpoint_enabled=False)
    back = {**STYLING, "direction": "back", "shot": "full"}
    result = asyncio.run(dpj._gen_cuts(
        _app(settings), _job("j1"), [_real_item(back)], PRODUCT, {},
        angle_photos={"sh_side": b"side-only"}, selected_model_id="mX"))

    assert result[0] == [] and result[1] == []
    assert ("step", {"blockId": "b1", "status": "cut_failed",
                     "reason": "no_angle_photo"}) in events


# ── D. generate() 의 베이스 자리 ──────────────────────────────────────────────
def test_generate_keeps_the_base_before_post_processing(monkeypatch):
    order = []

    class Recording(cg.BaseCheckpoint):
        async def keep(self, image, mime):
            order.append(("keep", image))
            await super().keep(image, mime)

    async def fake_pass(settings, image, mime, spec, *, outcome=None, url_provider=None, **_kw):
        order.append(("face_pass", image))
        return b"FACED", "image/png"

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", fake_pass)
    hook = Recording()
    gemini = _Gemini()
    out = asyncio.run(cg.generate(_settings(), gemini, STYLING, PRODUCT, [PRODUCT_IMG],
                                  face_identity_spec=LORA, base_checkpoint=hook))
    assert out == (b"FACED", "image/png")
    assert order == [("keep", b"BASE"), ("face_pass", b"BASE")]
    assert hook.image == (b"BASE", "image/png") and gemini.calls == 1


def test_generate_with_a_kept_base_skips_the_provider(monkeypatch):
    async def fake_pass(settings, image, mime, spec, *, outcome=None, url_provider=None, **_kw):
        assert image == b"PAID-BASE"
        return b"FACED", "image/png"

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", fake_pass)
    gemini = _Gemini()
    out = asyncio.run(cg.generate(
        _settings(), gemini, STYLING, PRODUCT, [PRODUCT_IMG], face_identity_spec=LORA,
        base_checkpoint=cg.BaseCheckpoint((b"PAID-BASE", "image/png"))))
    assert out == (b"FACED", "image/png")
    assert gemini.calls == 0


def test_base_fingerprint_follows_the_provider_request():
    s = _settings()
    fp = lambda block=STYLING, settings=s, images=(PRODUCT_IMG,), **kw: cg.base_fingerprint(  # noqa: E731
        settings, block, PRODUCT, list(images), **kw)
    base = fp()
    assert base == fp()                                            # 결정적
    assert base == fp({**STYLING, "id": "other-block"})            # 블록 위치는 상관없다
    assert base == fp(face_pass_outcome={}, face_pass_url_provider=lambda: None)
    assert base != fp({**STYLING, "direction": "side"})            # 스펙이 다르면 다른 베이스
    assert base != fp(settings=_settings(model_image_high="gemini-other-image"))   # 모델
    assert base != fp(images=(InlineImage("image/png", b"new-photo"),))  # 셀러가 사진을 바꿨다
    assert base != fp(manifest="1. PRODUCT (Front)\n2. MATCHING")  # 프롬프트(첨부 목록)가 다르다
    with pytest.raises(ValueError):
        fp({**STYLING, "cutType": "nope"})


def test_job_identity_separates_real_licenses_and_virtual():
    row = {"id": "lic-1", "model_id": "m1", "current_enrollment_id": "e1",
           "match_policy_version": "v1"}
    real = ckpts.job_identity("REAL", selected_model_id="m1", license_row=row, lora_spec=LORA,
                              angle_photos={"sh_back": b"b"})
    assert real == ckpts.job_identity("REAL", selected_model_id="m1", license_row=row,
                                      lora_spec=LORA, angle_photos={"sh_back": b"b"})
    assert real != ckpts.job_identity("VIRTUAL", selected_model_id="m1")
    assert real != ckpts.job_identity("REAL", selected_model_id="m1",
                                      license_row={**row, "id": "lic-2"}, lora_spec=LORA,
                                      angle_photos={"sh_back": b"b"})
    assert real != ckpts.job_identity("REAL", selected_model_id="m1", license_row=row,
                                      lora_spec=LORA, angle_photos={"sh_back": b"new"})
    assert ckpts.job_identity("VIRTUAL", selected_model_id="mA") != ckpts.job_identity(
        "VIRTUAL", selected_model_id="mB")


def test_pack_roundtrip_and_key_shape():
    blob = ckpts.pack({"v": 1, "x": "y"}, b"PAYLOAD")
    assert ckpts.unpack(blob) == ({"v": 1, "x": "y"}, b"PAYLOAD")
    assert ckpts.unpack(b"garbage") is None
    assert ckpts.unpack(blob[:12]) is None
    digest = "a" * 64
    key = ckpts.checkpoint_key(USER, PROJECT, "j1", digest, "base")
    assert key == f"users/{USER}/projects/{PROJECT}/ai/j1/ckpt/{digest}/base.bin"
    assert ckpts.parse_key(key, user_id=USER, project_id=PROJECT, job_id="j1") == (digest, "base")
    # 다른 프로젝트·다른 잡의 키로는 읽히지 않는다
    assert ckpts.parse_key(key, user_id=USER, project_id="p2", job_id="j1") is None
    assert ckpts.parse_key(key, user_id=USER, project_id=PROJECT, job_id="j2") is None


# ── D. 체크포인트 창구 ───────────────────────────────────────────────────────
async def _store(app, job_id, identity="id-1"):
    return await ckpts.CutCheckpointStore.open(app, _job(job_id), identity=identity)


@pytest.mark.parametrize('verdict,expected', [
    ({'passed': True, 'decision': 'PASS', 'fingerprint': 'source-target-policy'}, True),
    ({'passed': False, 'decision': 'FAIL', 'fingerprint': 'source-target-policy'}, False),
    ({'passed': False, 'decision': 'UNKNOWN', 'fingerprint': 'source-target-policy'}, False),
    ({'passed': True, 'decision': 'PASS'}, False),
])
def test_detail_final_reuse_requires_same_verified_policy(monkeypatch, verdict, expected):
    db = _IntentDB()
    db.install(monkeypatch)
    app = _app(_settings(), _R2())
    async def exercise():
        first = await _store(app, 'j1')
        cut = await first.for_verified_detail('source-target-policy', 'detail1')
        await cut.save_final(b'CHECKED', 'image/png', {
            'garmentQc': None, 'cutQc': verdict, 'warnings': [], 'neckRepair': None, 'facePass': {},
        })
        db.error_jobs.add('j1')
        next_store = await _store(app, 'j2')
        same = await next_store.for_verified_detail('source-target-policy', 'detail1')
        assert (same.final is not None) is expected
        assert (await next_store.for_verified_detail('changed-policy', 'detail1')).final is None
        assert (await next_store.for_verified_detail('source-target-policy', 'different-block')).final is None
        assert same.base.image is None  # Never reuse an unjudged/rejected candidate.
    asyncio.run(exercise())


def test_store_is_off_without_the_flag_or_a_database(monkeypatch):
    app = _app(_settings(detail_cut_checkpoint_enabled=False))
    assert asyncio.run(_store(app, "j1")) is None
    # 커서 없는 커넥션(스텁) → 조회가 None → 이어하기 없이 오늘처럼 생성
    assert asyncio.run(_store(_app(), "j1")) is None


def test_saved_base_uses_a_24h_ttl_and_a_private_object(monkeypatch):
    db = _IntentDB()
    db.install(monkeypatch)
    app = _app()
    store = asyncio.run(_store(app, "j1"))
    key = asyncio.run(store.save("c" * 64, "base", b"BASE", "image/png", {}, block_id="b1"))

    assert key == f"users/{USER}/projects/{PROJECT}/ai/j1/ckpt/{'c' * 64}/base.bin"
    assert db.rows[key]["ttl"] == 24 * 3600 == ckpts.TTL_SECONDS
    data, mime, cache = app.state.r2.objects[key]
    assert mime == "application/octet-stream" and cache == PRIVATE_NO_STORE
    header, payload = ckpts.unpack(data)
    assert payload == b"BASE" and header["projectId"] == PROJECT and header["stage"] == "base"


def test_save_without_a_cleanup_intent_never_uploads(monkeypatch):
    db = _IntentDB()
    db.install(monkeypatch)

    async def no_intent(conn, **_kw):
        return None

    monkeypatch.setattr(repo, "create_ai_checkpoint_intent", no_intent)
    app = _app()
    store = asyncio.run(_store(app, "j1"))
    assert asyncio.run(store.save("c" * 64, "base", b"BASE", "image/png", {})) is None
    assert app.state.r2.objects == {}          # 표식 없는 객체는 아무도 안 지운다 — 올리지 않는다


def _roundtrip(monkeypatch, *, second_identity="id-1", second_project=PROJECT, tamper=None,
               fail_get=False):
    """잡 j1 이 베이스를 남기고 error 로 끝난 뒤, 새 잡 j2 가 같은 컷을 찾는다."""
    db = _IntentDB()
    db.install(monkeypatch)
    r2 = _R2()
    s = _settings()
    app = _app(s, r2)
    first = asyncio.run(_store(app, "j1"))
    cut = asyncio.run(first.for_cut(STYLING, s, PRODUCT, [PRODUCT_IMG], {"face_identity_spec": LORA},
                                    real_identity_attached=True))
    asyncio.run(cut.base.keep(b"BASE", "image/png"))
    db.error_jobs.add("j1")
    if tamper:
        key = next(iter(r2.objects))
        r2.objects[key] = (tamper(r2.objects[key][0]),) + r2.objects[key][1:]
    r2.fail_get = fail_get
    job2 = {**_job("j2"), "project_id": second_project}
    second = asyncio.run(ckpts.CutCheckpointStore.open(app, job2, identity=second_identity))
    again = asyncio.run(second.for_cut(STYLING, s, PRODUCT, [PRODUCT_IMG],
                                       {"face_identity_spec": LORA}, real_identity_attached=True))
    return db, again


def test_a_failed_jobs_base_is_found_by_the_next_job(monkeypatch):
    db, again = _roundtrip(monkeypatch)
    assert again.final is None and again.base_reused
    assert again.base.image == (b"BASE", "image/png")
    # 이어 쓴 체크포인트는 TTL 을 다시 늘린다(연속 실패에도 남는다)
    assert db.ttl_calls[-1][1:] == (ckpts.TTL_SECONDS, True)


@pytest.mark.parametrize("kwargs", [
    {"second_identity": "other-identity"},             # 다른 신원(REAL↔VIRTUAL·다른 라이선스)
    {"second_project": "p2"},                          # 다른 프로젝트
    {"fail_get": True},                                # R2 읽기 실패
    {"tamper": lambda blob: blob[:-1] + b"X"},         # 바이트 해시 불일치
    {"tamper": lambda blob: b"not a checkpoint"},      # 모양이 다름
])
def test_mismatches_regenerate_instead_of_reusing(monkeypatch, kwargs):
    _db, again = _roundtrip(monkeypatch, **kwargs)
    assert again.base.image is None and not again.base_reused and again.final is None


def test_checkpoints_of_a_successful_job_are_not_reused(monkeypatch):
    """성공한 잡의 컷은 이미 나갔다 — 같은 그림을 두 번 팔지 않는다(조회는 error 잡만)."""
    db = _IntentDB()
    db.install(monkeypatch)
    s = _settings()
    app = _app(s)
    first = asyncio.run(_store(app, "j1"))
    cut = asyncio.run(first.for_cut(STYLING, s, PRODUCT, [PRODUCT_IMG], {},
                                    real_identity_attached=False))
    asyncio.run(cut.save_final(b"FINAL", "image/png", {"garmentQc": None, "cutQc": None,
                                                       "warnings": [], "neckRepair": None,
                                                       "facePass": {}}))
    # j1 이 성공 → 지금 지운다, 그리고 error 가 아니다
    asyncio.run(first.discard_all())
    second = asyncio.run(_store(app, "j2"))
    again = asyncio.run(second.for_cut(STYLING, s, PRODUCT, [PRODUCT_IMG], {},
                                       real_identity_attached=False))
    assert again.final is None and again.base.image is None
    assert all(row.get("cleared") for row in db.rows.values())
    assert app.state.r2.objects == {}


def test_digest_ignores_an_invented_name_but_not_the_block(monkeypatch):
    """상품명이 비어 있으면 잡이 LLM 으로 짓는다 — 시도마다 다르고 성공해야 저장된다.
    그 이름이 지문에 들어가면 "다시 시도"가 한 번도 이어 쓰지 못한다."""
    _IntentDB().install(monkeypatch)
    s = _settings()
    app = _app(s)

    async def digest(name, auto_named):
        store = await ckpts.CutCheckpointStore.open(app, _job("j1"), identity="id",
                                                    auto_named=auto_named)
        cut = await store.for_cut(STYLING, s, {**PRODUCT, "name": name}, [PRODUCT_IMG], {},
                                  real_identity_attached=False)
        return cut.base_digest

    assert asyncio.run(digest("린넨 셔츠", True)) == asyncio.run(digest("코튼 셔츠", True))
    # 같은 설정이라도 다른 블록 자리의 그림은 가져오지 않는다
    store = asyncio.run(ckpts.CutCheckpointStore.open(app, _job("j1"), identity="id"))
    other = asyncio.run(store.for_cut({**STYLING, "id": "b2"}, s, PRODUCT, [PRODUCT_IMG], {},
                                      real_identity_attached=False))
    same = asyncio.run(store.for_cut(STYLING, s, PRODUCT, [PRODUCT_IMG], {},
                                     real_identity_attached=False))
    assert other.base_digest != same.base_digest
    # 셀러가 직접 붙인 이름은 요청의 일부다 — 바뀌면 새로 그린다
    assert asyncio.run(digest("린넨 셔츠", False)) != asyncio.run(digest("코튼 셔츠", False))


def test_a_failed_delete_falls_back_to_the_reclaimer(monkeypatch):
    """지우기가 실패하면 표식을 지우지 않고 지금 만료만 시킨다 — 리클레이머가 이어서 지운다."""
    db = _IntentDB()
    db.install(monkeypatch)

    class _StuckR2(_R2):
        def delete(self, key):
            raise RuntimeError("r2 down")

    app = _app(r2=_StuckR2())
    store = asyncio.run(_store(app, "j1"))
    key = asyncio.run(store.save("e" * 64, "final", b"FINAL", "image/png", {}))
    asyncio.run(store.discard_all())
    assert not db.rows[key].get("cleared")
    assert db.ttl_calls[-1] == ([key], 0, False)
    assert key in app.state.r2.objects


@pytest.mark.parametrize("exc,survives", [
    (fi.FacePassUnavailable("pod_not_ready"), True),
    (fi.FacePassUnavailable("backend_error"), True),
    (angle.AngleSwapUnavailable("backend_error"), True),
    (fi.FacePassUnavailable("gate_failed"), False),        # 그림 탓 — 같은 베이스면 같은 답
    (fi.FacePassUnavailable("skipped_yaw"), False),
    (angle.AngleSwapUnavailable("tone_off"), False),
    (angle.AngleSwapUnavailable("no_angle_photo"), False),  # 옆모습 코 방향이 사진과 다름
    (RuntimeError("?"), False),                            # 모르면 새로 그린다
])
def test_base_survives_only_infrastructure_failures(exc, survives):
    assert ckpts.base_survives(exc) is survives


# ── D. 워커 흐름: 실패 → 베이스 이어 쓰기 → 완성 컷 채택 ─────────────────────
def test_detail_cuts_resume_from_base_then_adopt_the_final(monkeypatch):
    db = _IntentDB()
    db.install(monkeypatch)
    r2 = _R2()
    s = _settings()
    app = _app(s, r2)
    face_results = [fi.FacePassUnavailable("pod_not_ready"), (b"FACED", "image/png")]
    face_inputs = []

    async def fake_pass(settings, image, mime, spec, *, outcome=None, url_provider=None, **_kw):
        face_inputs.append(image)
        result = face_results.pop(0)
        if isinstance(result, Exception):
            if outcome is not None:
                outcome["face_pass"] = "failed:pod_not_ready"
            raise result
        if outcome is not None:
            outcome["face_pass"] = "applied"
            outcome["face_recipe"] = "recipe-1"
        return result

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", fake_pass)
    identity = ckpts.job_identity("REAL", selected_model_id="mX",
                                  license_row={"id": "lic-1"}, lora_spec=LORA)

    def run(job_id, gemini):
        app.state.gemini = gemini
        events = _events(monkeypatch)
        store = asyncio.run(_store(app, job_id, identity))
        out = asyncio.run(dpj._gen_cuts(
            app, _job(job_id), [_real_item()], PRODUCT, {}, face_identity_spec=LORA,
            selected_model_id="mX", cut_checkpoints=store))
        return out, events, store

    # 1) j1: gpt-image 베이스는 냈는데 얼굴 파드가 안 떴다 → 컷 실패, 베이스는 남는다(TTL)
    g1 = _Gemini(b"PAID-BASE")
    (results, assets, *_), events, store1 = run("j1", g1)
    assert results == [] and assets == [] and g1.calls == 1
    base_keys = [k for k in r2.objects if k.endswith("/base.bin")]
    assert len(base_keys) == 1 and db.rows[base_keys[0]]["live"]
    assert not r2.deleted                                   # 바로 지우지 않는다
    db.error_jobs.add("j1")

    # 2) j2: 같은 컷 → provider 는 안 부르고 얼굴 패스만 다시 → 완성 컷을 남기고 베이스는 만료
    g2 = _Gemini(b"SHOULD-NOT-BE-USED")
    (results, assets, face_cuts, *_), events, store2 = run("j2", g2)
    assert g2.calls == 0
    assert face_inputs == [b"PAID-BASE", b"PAID-BASE"]
    assert len(results) == 1 and len(assets) == 1 and face_cuts == 1
    assert ("step", {"blockId": "b1", "status": "cut_checkpoint_reused", "stage": "base"}) in events
    assert assets[0]["metadata"]["face_pass"] == "applied"
    final_keys = [k for k in r2.objects if k.endswith("/final.bin")]
    assert len(final_keys) == 1 and "/ai/j2/ckpt/" in final_keys[0]
    assert db.rows[base_keys[0]].get("cleared")             # 완성 컷이 생겼으니 베이스는 지운다
    assert base_keys[0] in r2.deleted
    db.error_jobs.add("j2")                                 # 예: 저장 직후 배포로 잡이 죽었다

    # 3) j3: 완성 컷을 그대로 싣는다 — provider·얼굴 패스·QC 호출 0, 정산 단위는 그대로 1컷
    g3 = _Gemini(b"SHOULD-NOT-BE-USED")
    (results, assets, face_cuts, *_), events, store3 = run("j3", g3)
    assert g3.calls == 0 and len(face_inputs) == 2
    assert len(results) == 1 and len(assets) == 1 and face_cuts == 1
    assert ("step", {"blockId": "b1", "status": "cut_checkpoint_reused", "stage": "final"}) in events
    assert any(kind == "step" and p.get("status") == "cut_done" for kind, p in events)
    stored = r2.objects[assets[0]["key"]]
    assert stored[0] == b"FACED" and stored[2] == PRIVATE_NO_STORE   # REAL 컷은 private 그대로
    assert assets[0]["metadata"]["face_pass"] == "applied"
    assert assets[0]["metadata"]["face_recipe"] == "recipe-1"
    assert "/ai/j3/" in assets[0]["key"]                    # 새 잡의 자산 키로 나간다


def test_image_dependent_failure_drops_the_base(monkeypatch):
    db = _IntentDB()
    db.install(monkeypatch)
    r2 = _R2()
    s = _settings()
    app = _app(s, r2)
    app.state.gemini = _Gemini()

    async def gate_failed(settings, image, mime, spec, *, outcome=None, url_provider=None, **_kw):
        raise fi.FacePassUnavailable("gate_failed")

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", gate_failed)
    _events(monkeypatch)
    store = asyncio.run(_store(app, "j1"))
    asyncio.run(dpj._gen_cuts(app, _job("j1"), [_real_item()], PRODUCT, {},
                              face_identity_spec=LORA, selected_model_id="mX",
                              cut_checkpoints=store))
    (base_key,) = [k for k in db.rows if k.endswith("/base.bin")]
    assert db.rows[base_key].get("cleared") and base_key in r2.deleted   # 다음 시도는 새로 그린다


def test_retry_within_the_job_reuses_the_base_after_an_infra_failure(monkeypatch):
    """각도 교체 backend_error 는 일반 예외 재시도로 간다 — 두 번째 시도는 베이스 값 없이 후처리만."""
    db = _IntentDB()
    db.install(monkeypatch)
    s = _settings(detail_cut_max_attempts=2, face_angle_swap_enabled=True,
                  face_angle_endpoint_id="ep1", face_runpod_api_key="k")
    app = _app(s)
    gemini = _Gemini()
    app.state.gemini = gemini
    swaps = []

    async def fake_swap(image, mime, *, direction, photos, backend, model_dir=None, seed=42,
                        outcome=None):
        swaps.append(image)
        if len(swaps) == 1:
            raise angle.AngleSwapUnavailable("backend_error")
        return b"SWAPPED", "image/png"

    async def no_pod(pool):
        return None

    from app.agents import identity_source

    monkeypatch.setattr(cg.face_angle_swap, "swap", fake_swap)
    monkeypatch.setattr(identity_source, "active_angle_pod_id", no_pod)
    _events(monkeypatch)
    back = {**STYLING, "direction": "back", "shot": "full"}
    store = asyncio.run(_store(app, "j1"))
    results, assets, *_ = asyncio.run(dpj._gen_cuts(
        app, _job("j1"), [_real_item(back)], PRODUCT, {}, angle_photos={"sh_back": b"b"},
        selected_model_id="mX", cut_checkpoints=store))
    assert gemini.calls == 1 and swaps == [b"BASE", b"BASE"]
    assert len(assets) == 1
