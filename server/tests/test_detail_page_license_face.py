"""FM-31 상세페이지 워커 — FaceMarket 라이선스 얼굴 주입 + AI 고지 분기.

제품 핵심("가짜 얼굴의 시대에 진짜를 공급")이 출력에서 성립하는지를 지킨다:
잠긴 라이선스의 얼굴이 **실제로** 컷 생성 입력에 들어가고, 그 사실이 고지에 정확히 반영되며,
라이선스 없는 기존 경로는 한 톨도 변하지 않는다(해커톤 필수 경로).
"""

import asyncio
import contextlib
import types
from datetime import datetime, timedelta, timezone

from app.workers import detail_page_job as dpj
from conftest import FakeR2, make_settings, worker_job

LIC_ID = "11111111-1111-4111-8111-111111111111"
FACE_KEY = "faces/model-1/lic-1.png"
FACE_BYTES = b"\x89PNG-FACE-BYTES"


def _license_row(status="active", days_left=30, key=FACE_KEY, name="김하늘"):
    return {"face_image_key": key, "status": status, "display_name": name,
            "license_valid_until": datetime.now(timezone.utc) + timedelta(days=days_left)}


class _Cur:
    def __init__(self, row):
        self._row = row
        self._sql = ""

    async def execute(self, sql, params=None):
        self._sql = " ".join(sql.split())
        return None

    async def fetchone(self):
        # _load_license_row(실존 소스 게이트용) 쿼리 → None: 이 테스트들은 실존 자산이 없어
        # LEGACY(단일 라이선스 얼굴) 경로를 유지한다. 그 외(=_load_license_face) → 라이선스 row.
        if "l.id::text as id" in self._sql and "l.model_id::text as model_id" in self._sql:
            return None
        return self._row

    async def fetchall(self):
        # resolve_real_model_assets(fm_model_assets 조인) → 빈 결과 = 실존 자산 없음 → None → LEGACY.
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    """fm_licenses 조회(커서)까지 흉내내는 커넥션."""

    def __init__(self, row):
        self._row = row

    async def commit(self):
        return None

    def cursor(self):
        return _Cur(self._row)


class _Pool:
    def __init__(self, row):
        self._row = row

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn(self._row)

        return _cm()


class _FaceR2:
    """비공개 얼굴 버킷. 어떤 키로 접근했는지 기록해 버킷 경계를 단언한다."""

    def __init__(self, raises=False):
        self.raises = raises
        self.gets: list[str] = []

    def get_bytes(self, key):
        self.gets.append(key)
        if self.raises:
            raise RuntimeError("no such object")
        return FACE_BYTES

    def put_bytes(self, key, data, mime):
        return None

    def delete(self, key):
        return None


def _app(license_row, *, face_r2=None, facemarket_enabled=True, with_face_storage=True):
    main_r2 = FakeR2()
    state = types.SimpleNamespace(
        settings=make_settings(gemini_api_key="x", r2_bucket="b",
                              facemarket_enabled=facemarket_enabled),
        pool=_Pool(license_row), r2=main_r2, gemini=types.SimpleNamespace(),
    )
    if with_face_storage:
        state.r2_face = face_r2 or _FaceR2()
    return types.SimpleNamespace(state=state), main_r2


def _patch_inputs(monkeypatch, captured, *, project, storyboard=None, product=None):
    async def fake_gp(conn, uid, pid):
        return project

    async def fake_sb(conn, pid):
        return storyboard or [{"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"}]

    async def fake_prod(conn, pid):
        return product or {"clothing_type": "top",
                           "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *,
                       analysis=None, manifest=None, has_face=False):
        captured.setdefault("calls", []).append(
            {"images": list(images), "manifest": manifest, "has_face": has_face,
             "block": cut_spec.get("id")})
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, *,
                      license_notice=None):
        captured["license_notice"] = license_notice
        captured["cut_results"] = cut_results
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_finalize_failure(conn, **kw):
        captured["failure"] = kw
        return {"status": "failed"}

    async def fake_emit(pool, job_id, et, payload):
        captured.setdefault("events", []).append((et, payload))

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_finalize_failure)
    monkeypatch.setattr(dpj, "_emit", fake_emit)


# ── 라이선스 없는 기존 경로 = 무변경 (해커톤 필수 경로) ──────────────────────
def test_no_license_project_attaches_no_face_and_keeps_default_notice(monkeypatch):
    """절대 금지 항목의 회귀 가드: 라이선스 없는 프로젝트는 얼굴 첨부 0,
    has_face=False, license_notice=None, 정산·차감 그대로."""
    captured = {}
    _patch_inputs(monkeypatch, captured, project={"copywriting": False})  # facemarket_license_id 없음
    app, _ = _app(_license_row())

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    call = captured["calls"][0]
    assert call["has_face"] is False
    assert len(call["images"]) == 1                     # 상품 1장뿐 — 얼굴 미첨부
    assert "MODEL FACE" not in call["manifest"]
    assert captured["license_notice"] is None           # 기존 AI 고지 문구 유지
    assert captured["charge"] == 1                      # 차감 계약 그대로
    assert app.state.r2_face.gets == []                 # 얼굴 버킷 접근조차 없음


def test_facemarket_disabled_never_loads_face(monkeypatch):
    # 킬스위치: FACEMARKET_ENABLED=false 면 라이선스가 잠겨 있어도 얼굴 경로 미진입.
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, _ = _app(_license_row(), facemarket_enabled=False)

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["calls"][0]["has_face"] is False
    assert captured["license_notice"] is None
    assert app.state.r2_face.gets == []


# ── 얼굴 주입 ────────────────────────────────────────────────────────────────
def test_licensed_project_injects_face_into_cut_input(monkeypatch):
    """제품 핵심: 잠긴 라이선스의 얼굴이 실제 생성 입력에 들어간다."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, main_r2 = _app(_license_row())

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    call = captured["calls"][0]
    assert call["has_face"] is True
    # 얼굴 바이트가 실제 첨부됐고, 위치는 옷 근거 뒤(매니페스트와 lockstep)
    assert [im.data for im in call["images"]] == [b"\x89PNG-bytes", FACE_BYTES]
    assert call["images"][-1].mime == "image/png"   # 키 확장자 역매핑(fm_licenses 에 mime 컬럼 부재)
    assert "MODEL FACE" in call["manifest"]
    assert call["manifest"].index("PRODUCT") < call["manifest"].index("MODEL FACE")
    # 얼굴은 **비공개** 버킷에서만 — 공개 메인 버킷으로 얼굴 키를 찾지 않는다
    assert app.state.r2_face.gets == [FACE_KEY]
    assert FACE_KEY not in getattr(main_r2, "gets", [])


def test_licensed_project_ai_notice_states_real_model(monkeypatch):
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, _ = _app(_license_row(name="김하늘"))

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    # faceCuts/totalCuts 는 고지의 **범위 주장** 근거다 — assembler 가 이 둘로
    # '가상인물 아님'(전 컷) vs '일부 컷' 을 가른다.
    assert captured["license_notice"] == {
        "modelName": "김*늘", "licenseId": LIC_ID, "faceCuts": 1, "totalCuts": 1,
    }


def test_face_is_attached_only_to_cuts_that_show_it(monkeypatch):
    # product 컷(사람 금지)·거울샷 기본(폰이 가림)에는 얼굴을 붙이지 않는다.
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID},
                  storyboard=[
                      {"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"},
                      {"id": "b2", "source": "ai", "cutType": "product", "shot": "ghost"},
                      {"id": "b3", "source": "ai", "cutType": "mirror", "shot": "full"},
                  ])
    app, _ = _app(_license_row())

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=3)))

    by_block = {c["block"]: c for c in captured["calls"]}
    assert by_block["b1"]["has_face"] is True and len(by_block["b1"]["images"]) == 2
    assert by_block["b2"]["has_face"] is False and len(by_block["b2"]["images"]) == 1
    assert by_block["b3"]["has_face"] is False and len(by_block["b3"]["images"]) == 1
    # 얼굴이 담긴 컷이 하나라도 성공했으므로 고지는 실제 모델 문구
    assert captured["license_notice"] is not None


# ── verify-before-use 시점 갭 (해지된 얼굴이 생성돼 나가면 회수 불가) ────────
def test_revoked_license_at_worker_time_injects_no_face(monkeypatch):
    """게이트(요청 시점) 통과 후 해지된 라이선스 — 워커가 재확인해 얼굴을 쓰지 않는다.
    한 번 생성되면 공개 URL 로 나가 회수가 불가능하다."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, _ = _app(_license_row(status="revoked"))

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["calls"][0]["has_face"] is False
    assert app.state.r2_face.gets == []                 # 바이트를 읽지도 않는다
    assert captured["license_notice"] is None           # 허위 고지 금지 — 기본 문구
    assert captured["charge"] == 1                      # 컷 생성 자체는 성공(부분 성공 계약)


def test_expired_license_at_worker_time_injects_no_face(monkeypatch):
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, _ = _app(_license_row(days_left=-1))

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["calls"][0]["has_face"] is False
    assert captured["license_notice"] is None


# ── 우아한 강등 (잡 전체 실패 금지) ──────────────────────────────────────────
def test_dangling_face_key_degrades_to_faceless_generation(monkeypatch):
    """개인화 파기가 얼굴 R2 객체만 지우면 face_image_key 가 dangling 이 된다
    (마이그레이션 주석의 '의도된 우아한 강등'). 잡은 죽지 않고 얼굴 없이 완료."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, _ = _app(_license_row(), face_r2=_FaceR2(raises=True))

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["calls"][0]["has_face"] is False
    assert captured["license_notice"] is None
    assert captured["charge"] == 1                      # 완료 — 잡 전체 실패가 아니다


def test_missing_face_storage_degrades_without_public_bucket_fallback(monkeypatch):
    # 얼굴=생체 PII → r2_face 미설정 시 공개 버킷 폴백 금지. 얼굴 없이 생성으로 강등.
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, main_r2 = _app(_license_row(), with_face_storage=False)

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["calls"][0]["has_face"] is False
    assert captured["license_notice"] is None


def test_all_face_cuts_failing_fails_the_job_without_false_notice(monkeypatch):
    """얼굴 컷이 전부 실패하면 빈 페이지를 완료하지 않고 실패·환불한다."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})

    async def failing_gen(settings, gemini, cut_spec, product, images, *,
                          analysis=None, manifest=None, has_face=False):
        raise RuntimeError("gen fail")

    monkeypatch.setattr(dpj.cut_generator, "generate", failing_gen)
    app, _ = _app(_license_row())

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert "license_notice" not in captured             # 조립 전에 중단 → 허위 고지 없음
    assert captured["failure"]["code"] == "all_cuts_failed"
    assert captured["failure"]["reserved"] == 1        # 실패 종결에서 예약액 전부 환불


# ── 옷 근거 가드가 얼굴로 우회되지 않는다 (ADR-0004) ─────────────────────────
def test_face_never_bypasses_garment_truth_guard(monkeypatch):
    """상품 사진도 마네킹도 없으면 얼굴이 있어도 생성하지 않는다 — 얼굴을 스킵 표식
    앞이나 빈 리스트에 넣으면 `if not images` 가드가 무력화돼 옷 근거 0으로 생성이 돈다."""
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID},
                  product={"clothing_type": "top", "colors": []})  # 상품 이미지 0 · 마네킹 없음
    app, _ = _app(_license_row())

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured.get("calls") is None                # 생성 호출 자체가 없어야 한다
    assert "license_notice" not in captured
    assert captured["failure"]["code"] == "all_cuts_failed"


# ── PII: 얼굴 바이트·키가 이벤트에 새지 않는다 ───────────────────────────────
def test_face_bytes_and_key_never_appear_in_job_events(monkeypatch):
    captured = {}
    _patch_inputs(monkeypatch, captured,
                  project={"copywriting": False, "facemarket_license_id": LIC_ID})
    app, _ = _app(_license_row())

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    blob = repr(captured["events"])
    assert FACE_KEY not in blob
    assert "PNG-FACE-BYTES" not in blob
    assert "faces/" not in blob
