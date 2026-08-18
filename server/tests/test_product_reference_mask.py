"""파는 옷을 **올린 사진으로** 식별한다 — 색감 마스크의 주상품 레퍼런스.

2026-08-18. 코디 하의를 함께 입은 컷에서 색감 조정이 연달아 막혔다. 실패한 컷의 픽셀을 직접
재보니 "여기가 새로 생긴 옷이다"라는 증거의 74.8%가 **청바지**를 가리키고 있었다 — 연회색
티셔츠는 회색 마네킹과 색차가 거의 없고, 파란 데님은 확 튀기 때문이다. 세로 위치(밴드)만으로
파는 옷을 고르는 한 이 사진에서는 순위가 계속 코디 옷을 1등으로 올린다.

그래서 셀러가 올린 주상품 사진의 배경을 지운 컷아웃(canonical cutout)을 채점에 넣는다.
"위쪽에 있으니 상의겠지"가 아니라 "올린 티셔츠와 같은 색이니 그 티셔츠다"로 판정이 바뀐다.

**SAM 을 유도하지는 않는다.** Base-Diff 와 똑같이 채점 입력일 뿐이고, 프롬프트·상자·교집합
어디에도 들어가지 않는다.
"""

import app.routes as routes
from app.services import canonical_reference as cr

from conftest import patch_route_db


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _product(images):
    return {
        "id": "product-1",
        "project_id": "p1",
        "name": "반팔 티셔츠",
        "clothing_type": "top",
        "colors": [{"isBase": True, "images": images}],
        "measurements": [],
        "measurements_unknown": False,
        "upload_complete": True,
    }


FRONT_AND_BACK = [{"slot": "Front", "id": "img-front"}, {"slot": "Back", "id": "img-back"}]


def _patch_product(client, make_token, monkeypatch, *, saved, jobs):
    async def fake_get_project(_conn, _user_id, project_id):
        return {"id": project_id}

    async def fake_save_product(_conn, _project_id, _user_id, _fields):
        return saved

    async def fake_create_job(_conn, **kwargs):
        jobs.append(kwargs)
        return {"id": "job-1"}, True

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "save_product", fake_save_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    patch_route_db(monkeypatch, routes)
    return client.patch("/v1/projects/p1/product", headers=_auth(make_token),
                        json={"name": "반팔 티셔츠"})


def test_confirming_the_product_photos_queues_the_cutout(client, make_token, monkeypatch):
    """게스트 흐름은 /public/analyze 로 분석해서 프로젝트 analyze 라우트를 타지 않는다.

    그래서 컷아웃을 거는 지점이 사진이 서버에 확정되는 이 라우트에도 있어야 한다. 없으면
    (2026-08-18 실측) sam_preprocess 는 10일에 한 번 돌고, 주상품 레퍼런스는 영영 없다.
    """
    jobs = []
    res = _patch_product(client, make_token, monkeypatch,
                         saved=_product(FRONT_AND_BACK), jobs=jobs)

    assert res.status_code == 200, res.text
    assert [j["kind"] for j in jobs] == ["sam_preprocess"]
    assert jobs[0]["credits_reserved"] == 0, "컷아웃은 무과금 보조 인프라다"


def test_a_product_without_photographs_queues_nothing(client, make_token, monkeypatch):
    """분할할 사진이 없으면 걸지 않는다 — 빈 잡이 멱등키를 선점하면 진짜 사진이 못 들어온다."""
    jobs = []
    res = _patch_product(client, make_token, monkeypatch, saved=_product([]), jobs=jobs)

    assert res.status_code == 200, res.text
    assert jobs == []


def test_swapping_the_front_photograph_asks_for_a_fresh_cutout():
    """멱등키가 현재 사진을 물어야 한다 — 앞면을 갈아끼우면 옛 옷의 실루엣은 그 순간 무효다."""
    before = cr.preprocess_idempotency_key("p1", _product(FRONT_AND_BACK))
    after = cr.preprocess_idempotency_key(
        "p1", _product([{"slot": "Front", "id": "img-front-2"},
                        {"slot": "Back", "id": "img-back"}]))

    assert before and after and before != after
    assert cr.preprocess_idempotency_key("p1", _product([])) is None


# ── 경계: 키만 건넨다 ────────────────────────────────────────────────────────

def test_the_client_sends_the_product_cutout_as_a_key_never_bytes(monkeypatch):
    """SAM 경계는 신뢰된 R2 키만 받는다 — 바이트도, URL 도 보내지 않는다."""
    import httpx

    from app.services import sam_client

    sent = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "ready", "maskKey": "k"}

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, _url, json=None, **_kw):
            sent.update(json or {})
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    class _S:
        sam_service_url = "http://sam2:8080"
        sam_internal_token = "t"
        sam_request_timeout_s = 5.0

    import asyncio
    asyncio.run(sam_client.segment_worn_garment(
        _S(), source_key="cuts/a.jpg", base_key="seed/base.png", clothing_type="top",
        matching_side="bottom", product_key="derived/canonical/front.png"))

    assert sent["productKey"] == "derived/canonical/front.png"
    assert set(sent) == {"sourceKey", "baseKey", "clothingType", "subCategory",
                         "matchingSide", "productKey"}


# ── 채점: 올린 옷과 닮았는가 ─────────────────────────────────────────────────

import io  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from sam_service import worn_garment as W  # noqa: E402

SHAPE = (200, 120)
TOP_BOX = (46, 96, 36, 84)        # y 0.23~0.48 — 연회색 티셔츠
BOT_BOX = (96, 184, 40, 80)       # y 0.48~0.92 — 파란 데님 (실서버 컷과 같은 배치)
SHIRT_BGR = (206, 206, 206)
DENIM_BGR = (140, 70, 35)


def _cut(top=None, bot=None):
    """회색 마네킹 위 착장. 티셔츠는 마네킹과 거의 같은 색이다 — 그게 이 사건의 핵심이다."""
    h, w = SHAPE
    img = np.full((h, w, 3), 245, np.uint8)
    img[int(h * .05):int(h * .98), int(w * .30):int(w * .70)] = 200
    for box, colour in ((top, SHIRT_BGR), (bot, DENIM_BGR)):
        if box:
            y0, y1, x0, x1 = box
            img[y0:y1, x0:x1] = colour
    return img


def _region(box):
    m = np.zeros(SHAPE, bool)
    y0, y1, x0, x1 = box
    m[y0:y1, x0:x1] = True
    return m


def _cutout_png(colour):
    """셀러가 올린 사진의 배경 제거본 흉내 — 원본 픽셀 + 알파(RGBA), 서비스가 만드는 형식."""
    rgba = np.zeros((60, 40, 4), np.uint8)
    rgba[..., :3] = colour[::-1]                     # BGR -> RGB
    rgba[10:50, 8:32, 3] = 255                       # 옷만 불투명
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def test_the_uploaded_garment_tells_the_scorer_which_region_is_the_product():
    """올린 티셔츠와 색이 같은 영역은 높게, 코디 데님 영역은 낮게 나와야 한다."""
    gen = _cut(TOP_BOX, BOT_BOX)
    signature = W.product_signature(_cutout_png(SHIRT_BGR))
    assert signature is not None

    shirt = W.product_affinity(gen, _region(TOP_BOX), signature)
    denim = W.product_affinity(gen, _region(BOT_BOX), signature)

    assert shirt > 0.8, f"올린 옷과 같은 색인데 {shirt}"
    assert denim < 0.2, f"코디 데님인데 {denim}"


def test_an_unusable_cutout_leaves_the_scoring_exactly_as_it_was():
    """빈 컷아웃·깨진 PNG 는 판정 불가다 — 근거 없이 점수를 흔들면 안 된다."""
    assert W.product_signature(b"") is None
    assert W.product_signature(b"not a png") is None


def test_scoring_is_byte_identical_without_a_product_reference():
    """레퍼런스가 없으면 v3 채점 그대로다 — 이 기능은 있을 때만 말을 얹는다."""
    gen = _cut(TOP_BOX, BOT_BOX)
    band = W.matching_core_band("top", "bottom")
    ev = W.evidence_mask(W.diff_map(_cut(), gen), W.diff_roi("top", band))
    fig = W.figure_silhouette(gen)
    mask = W.fill_holes(_region(TOP_BOX))

    without = W.score_candidate(mask, ev, fig, "top", band)
    with_none = W.score_candidate(mask, ev, fig, "top", band, product=None)

    assert with_none["score"] == without["score"]
    assert with_none["productMatch"] == 0.0


def test_the_uploaded_garment_beats_the_evidence_when_the_evidence_is_wrong(monkeypatch):
    """이 사건 그대로: 증거가 전부 청바지를 가리켜도 파는 옷을 고른다.

    연회색 티셔츠는 회색 마네킹과 색차가 거의 없어 Base-Diff 가 못 본다(합성에서도 증거의
    100%가 y 0.47~0.59, 즉 데님이다). 위치·증거만으로는 순위가 코디 옷을 1등으로 올린다.
    """
    shirt, jeans = _region(TOP_BOX), _region(BOT_BOX)

    monkeypatch.setattr(W, "generate_candidates", lambda *_a, **_k: [jeans, shirt])
    monkeypatch.setattr(W, "refine", lambda _seg, _rgb, m: m)
    ok, base_buf = cv2.imencode(".png", _cut())
    ok2, gen_buf = cv2.imencode(".png", _cut(TOP_BOX, BOT_BOX))
    assert ok and ok2

    out = W.produce(object(), gen_buf.tobytes(), base_buf.tobytes(), clothing_type="top",
                    matching_side="bottom", product=_cutout_png(SHIRT_BGR))

    assert out.selected_rank == 0, "레퍼런스가 있으면 티셔츠가 1등이어야 한다"
    from PIL import Image
    chosen = np.array(Image.open(io.BytesIO(out.png)).convert("L")) > 127
    assert chosen[70, 60] and not chosen[140, 60], "티셔츠는 잡고 데님은 두어야 한다"


# ── 실서버 실측 Lab 로 고정한다 (2026-08-18, 프로젝트 f8aeedea A-1) ──────────
#
# 합성 색으로만 맞추면 통과하는데 실사진에서 무너진다. 실제로 한 번 무너졌다 —
# 히스토그램 교집합은 **데님을 티셔츠보다 높게** 줬다. 색 분산이 넓은 영역이 좁은 시그니처를
# 더 많이 덮기 때문이다. 그래서 실측 Lab 을 테스트에 박는다.
#
#   올린 티셔츠(사진)  L=184  a=129.4  b=125.4
#   렌더된 티셔츠      L=215  a=128.7  b=129.2   ← 같은 옷인데 L 이 +31 (노출/렌더 차이)
#   렌더된 데님        L=109  a=127.3  b=119.2
#   맨 마네킹          L=220  a=128.5  b=130.0

MEASURED = {
    "uploaded_shirt": (184, 129, 125),
    "rendered_shirt": (215, 129, 129),
    "rendered_denim": (109, 127, 119),
    "bare_mannequin": (220, 129, 130),
}


def _lab_patch(lab, spread=2, size=(40, 40)):
    """실측 Lab 평균 주변에 약간의 분산을 준 균일 패치 (BGR)."""
    rng = np.random.default_rng(7)
    plane = np.stack([
        np.clip(rng.normal(v, spread, size), 0, 255) for v in lab
    ], axis=-1).astype(np.uint8)
    return cv2.cvtColor(plane, cv2.COLOR_LAB2BGR)


def _affinity_of(region_lab, *, spread=2):
    ok, png = cv2.imencode(".png", _lab_patch(MEASURED["uploaded_shirt"]))
    assert ok
    signature = W.product_signature(png.tobytes())
    patch = _lab_patch(region_lab, spread=spread)
    return W.product_affinity(patch, np.ones(patch.shape[:2], bool), signature)


def test_the_same_garment_still_matches_after_the_render_shifts_its_exposure():
    """사진과 렌더 사이 L 이 +31 벌어져도 같은 옷이다 — 노출 차이로 자기 옷을 놓치면 안 된다."""
    assert _affinity_of(MEASURED["rendered_shirt"]) > 0.6


def test_the_coordinating_denim_loses_to_the_product_on_real_measured_colour():
    """이 사건의 판정 그 자체. 실측 색으로 데님이 티셔츠를 이기면 B 는 아무것도 못 고친다.

    데님은 색 분산이 넓다(b 표준편차 3.7 vs 1.1). 넓은 분포가 좁은 시그니처를 더 많이 덮는
    측정치를 쓰면 순서가 뒤집힌다 — 실제로 히스토그램 교집합이 그랬다.
    """
    shirt = _affinity_of(MEASURED["rendered_shirt"])
    denim = _affinity_of(MEASURED["rendered_denim"], spread=4)

    assert shirt > denim * 3, f"티셔츠 {shirt:.3f} vs 데님 {denim:.3f}"


def _bgr_of(lab):
    px = np.array([[list(lab)]], np.uint8)
    return tuple(int(v) for v in cv2.cvtColor(px, cv2.COLOR_LAB2BGR)[0, 0])


def _measured_cut(dressed=True):
    """실측 Lab 로 칠한 컷. 티셔츠는 마네킹과 L 이 5밖에 안 벌어진다 — 사건 그대로다."""
    h, w = SHAPE
    img = np.full((h, w, 3), 245, np.uint8)
    img[int(h * .05):int(h * .98), int(w * .30):int(w * .70)] = _bgr_of(
        MEASURED["bare_mannequin"])
    if dressed:
        y0, y1, x0, x1 = TOP_BOX
        img[y0:y1, x0:x1] = _bgr_of(MEASURED["rendered_shirt"])
        y0, y1, x0, x1 = BOT_BOX
        img[y0:y1, x0:x1] = _bgr_of(MEASURED["rendered_denim"])
    return img


def _measured_cutout_png():
    rgba = np.zeros((40, 30, 4), np.uint8)
    rgba[..., :3] = np.array(_bgr_of(MEASURED["uploaded_shirt"]), np.uint8)[::-1]
    rgba[..., 3] = 255
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def test_with_the_real_colours_the_product_wins_outright_instead_of_by_fallback(monkeypatch):
    """실측 색·실측 노출 편차로도 파는 옷이 **1등**이어야 한다.

    v3 는 여기서 상하의 한 덩어리를 1등으로 올린다(0.718 > 셔츠 0.630). 그 덩어리는 거부되고
    폴백이 셔츠를 건지지만, 그건 운이다 — 실제 SAM 후보 수십 개 사이에서는 상한 3등 안에
    셔츠가 없을 수 있다. 레퍼런스가 있으면 순위가 처음부터 옳아야 한다.
    """
    shirt, jeans = _region(TOP_BOX), _region(BOT_BOX)
    outfit = shirt | jeans

    monkeypatch.setattr(W, "generate_candidates", lambda *_a, **_k: [outfit, jeans, shirt])
    monkeypatch.setattr(W, "refine", lambda _seg, _rgb, m: m)
    ok, base_buf = cv2.imencode(".png", _measured_cut(dressed=False))
    ok2, gen_buf = cv2.imencode(".png", _measured_cut())
    assert ok and ok2

    out = W.produce(object(), gen_buf.tobytes(), base_buf.tobytes(), clothing_type="top",
                    matching_side="bottom", product=_measured_cutout_png())

    assert out.selected_rank == 0 and out.vetoed_attempts == 0
    assert out.product_match > 0.6
    from PIL import Image
    chosen = np.array(Image.open(io.BytesIO(out.png)).convert("L")) > 127
    assert chosen[70, 60] and not chosen[140, 60]
